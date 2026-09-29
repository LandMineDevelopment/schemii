"""Owned real PostgreSQL blocking/admission/recovery oracles for metadata."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
from threading import Event, Lock
from time import monotonic, sleep, time
from uuid import uuid4

import psycopg
import pytest

from schemii.common.auth.routes import AccountCreate
from schemii.common.auth.service import AuthService
from schemii.common.errors import MetadataCapacityError, MetadataStorageUnavailableError
from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.database import (
    MetadataConnectionFactory,
    MetadataReadinessProbe,
)


def runtime_factory(postgres_metadata, **policy) -> MetadataConnectionFactory:
    config = MetadataConfig.from_env(postgres_metadata.environment)
    assert config is not None
    return MetadataConnectionFactory(
        replace(config, application_name="metadata_limits_" + uuid4().hex, **policy)
    )


def assert_backends_closed(postgres_metadata, application_name: str) -> None:
    deadline = monotonic() + 3
    while True:
        with postgres_metadata.connection_factory() as connection:
            count = connection.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s",
                (application_name,),
            ).fetchone()["n"]
        if count == 0:
            return
        assert monotonic() < deadline, f"{count} owned metadata backends remain"
        sleep(0.02)


def test_statement_deadline_closes_backend_and_recovers(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(postgres_metadata, statement_timeout_ms=250)
    started = monotonic()
    try:
        with pytest.raises(MetadataStorageUnavailableError) as failure:
            with factory() as connection:
                connection.execute("SELECT pg_sleep(10)")
        elapsed = monotonic() - started
        assert isinstance(failure.value.__cause__, psycopg.errors.QueryCanceled)
        assert 0.15 <= elapsed < 2
        assert factory.admission_snapshot().active == 0
        assert_backends_closed(postgres_metadata, factory._application_name)
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("statement_timeout_seconds", elapsed)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_auth_advisory_lock_failure_is_bounded_and_session_recovers(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, statement_timeout_ms=1_500, lock_timeout_ms=150
    )
    service = AuthService(factory, enabled=True, setup_token="integration-unused")
    username = "it_limits_" + uuid4().hex
    user = service.create_user(
        AccountCreate(
            username=username,
            display_name="Owned admission fixture",
            password="integration-limits-password",
        )
    )
    # Own the exact session row; avoid an anonymous global sign-in audit fixture.
    token = uuid4().hex
    with postgres_metadata.connection_factory() as connection:
        connection.execute(
            "INSERT INTO metadata.auth_sessions VALUES (%s,%s,%s)",
            (hashlib.sha256(token.encode()).hexdigest(), user["id"], time() + 3600),
        )
    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (0x53434841555448,))
            started = monotonic()
            with pytest.raises(MetadataStorageUnavailableError) as failure:
                service.logout(token)
            elapsed = monotonic() - started
            assert isinstance(failure.value.__cause__, psycopg.errors.LockNotAvailable)
            assert 0.1 <= elapsed < 2
            assert factory.admission_snapshot().active == 0
            assert_backends_closed(postgres_metadata, factory._application_name)
            # Targeted authenticated reads do not acquire the global mutation lock.
            assert service.resolve(token)["id"] == user["id"]
        assert service.resolve(token)["id"] == user["id"]
        assert service.capabilities(user["id"]) == []
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        service.logout(token)
        assert service.resolve(token) is None
        record_property("auth_lock_wait_seconds", elapsed)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "DELETE FROM metadata.auth_audit WHERE actor_id=%s OR target_id=%s",
                (user["id"], user["id"]),
            )
            connection.execute(
                "DELETE FROM metadata.auth_accounts WHERE user_id=%s", (user["id"],)
            )
            connection.execute(
                "DELETE FROM metadata.auth_login_attempts WHERE username=%s",
                (username,),
            )
            connection.execute("DELETE FROM metadata.users WHERE id=%s", (user["id"],))
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_concurrent_native_calls_preserve_readiness_budget_and_recover(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(postgres_metadata, maximum_connections=2)
    readiness_factory = runtime_factory(
        postgres_metadata, maximum_connections=1, statement_timeout_ms=2_000
    )
    release = Event()
    attempts_finished = Event()
    attempts_lock = Lock()
    attempts = 0

    def work():
        nonlocal attempts
        connection = None
        try:
            try:
                connection = factory()
                connection.execute("SELECT 1 AS admitted")
            except MetadataCapacityError:
                return "rejected"
            finally:
                with attempts_lock:
                    attempts += 1
                    if attempts == 10:
                        attempts_finished.set()
            assert release.wait(5)
            return "admitted"
        finally:
            if connection is not None:
                connection.close()

    started = monotonic()
    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(work) for _ in range(10)]
            try:
                assert attempts_finished.wait(5)
                snapshot = factory.admission_snapshot()
                assert (
                    snapshot.active,
                    snapshot.peak_active,
                    snapshot.admitted,
                    snapshot.rejected,
                ) == (2, 2, 2, 8)
                with postgres_metadata.connection_factory() as connection:
                    row = connection.execute(
                        "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s",
                        (factory._application_name,),
                    ).fetchone()
                    assert row["n"] == 2
                ready_started = monotonic()
                MetadataReadinessProbe(readiness_factory)()
                assert monotonic() - ready_started < 2
                with pytest.raises(MetadataCapacityError):
                    factory()
            finally:
                release.set()
            assert [future.result() for future in futures].count("rejected") == 8
        assert factory.admission_snapshot().active == 0
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("burst_seconds", monotonic() - started)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        release.set()
        factory.close()
        readiness_factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)
    assert_backends_closed(postgres_metadata, readiness_factory._application_name)


def test_shutdown_drains_owned_native_waiter_without_releasing_early(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, lock_timeout_ms=700
    )
    native_started = Event()
    lock = int(uuid4().hex[:12], 16)

    def blocked_work() -> None:
        with factory() as connection:
            native_started.set()
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))

    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(blocked_work)
                assert native_started.wait(5)
                assert factory.admission_snapshot().active == 1
                started = monotonic()
                factory.close()
                elapsed = monotonic() - started
                assert 0.4 < elapsed < 3
                with pytest.raises(MetadataStorageUnavailableError):
                    future.result()
                assert factory.admission_snapshot().active == 0
                with pytest.raises(
                    MetadataStorageUnavailableError, match="shutting down"
                ):
                    factory()
        record_property("shutdown_drain_seconds", elapsed)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_canceled_async_lock_wait_retains_permit_until_native_timeout(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, lock_timeout_ms=700
    )
    native_started = Event()
    native_finished = Event()
    failures: list[Exception] = []
    lock = int(uuid4().hex[:12], 16)

    def blocked_work() -> None:
        try:
            with factory() as connection:
                native_started.set()
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
        except Exception as error:
            failures.append(error)
        finally:
            native_finished.set()

    async def cancel_waiter() -> None:
        task = asyncio.create_task(asyncio.to_thread(blocked_work))
        assert await asyncio.to_thread(native_started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not native_finished.is_set()
        assert factory.admission_snapshot().active == 1
        with pytest.raises(MetadataCapacityError):
            factory()
        assert await asyncio.to_thread(native_finished.wait, 3)

    started = monotonic()
    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            asyncio.run(cancel_waiter())
        assert len(failures) == 1
        assert isinstance(failures[0], MetadataStorageUnavailableError)
        assert isinstance(failures[0].__cause__, psycopg.errors.LockNotAvailable)
        assert factory.admission_snapshot().active == 0
        assert_backends_closed(postgres_metadata, factory._application_name)
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("canceled_native_wait_seconds", monotonic() - started)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)
