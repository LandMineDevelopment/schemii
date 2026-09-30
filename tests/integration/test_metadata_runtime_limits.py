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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.api.errors import ApiProblem, install_api_error_handlers
from schemii.common.auth.routes import AccountCreate
from schemii.common.auth.service import AuthService, COOKIE
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.errors import MetadataCapacityError, MetadataStorageUnavailableError
from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.database import (
    MetadataConnectionFactory,
    MetadataMigrationError,
    MetadataReadinessProbe,
)
from schemii.common.metadata.factory import create_metadata_repositories
import schemii.common.metadata.factory as factory_module
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.schemii.metadata import MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE
from schemii.schemoo.metadata.migrations import (
    MIGRATION_PACKAGE as SCHEMOO_MIGRATION_PACKAGE,
)
from schemii.schemer.metadata.migrations import (
    MIGRATION_PACKAGE as SCHEMER_MIGRATION_PACKAGE,
)
from schemii.schemii.ai.repository import PostgresAiRepository
from schemii.schemii.bulk_jobs.repository import JobRepository


MIGRATION_PACKAGES = (
    COMMON_MIGRATION_PACKAGE,
    SCHEMII_MIGRATION_PACKAGE,
    SCHEMOO_MIGRATION_PACKAGE,
    SCHEMER_MIGRATION_PACKAGE,
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
                "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s AND pid <> pg_backend_pid()",
                (application_name,),
            ).fetchone()["n"]
        if count == 0:
            return
        assert monotonic() < deadline, f"{count} owned metadata backends remain"
        sleep(0.02)


def effective_deadlines(connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT name,setting FROM pg_settings WHERE name IN "
        "('statement_timeout','lock_timeout','idle_in_transaction_session_timeout')"
    ).fetchall()
    return {row["name"]: int(row["setting"]) for row in rows}


@pytest.mark.parametrize("repository_type", ["bulk", "ai"])
def test_real_repository_statement_deadline_is_retryable_and_recovers(
    postgres_metadata, repository_type, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, statement_timeout_ms=250, lock_timeout_ms=1000
    )
    repository = (
        JobRepository(factory)
        if repository_type == "bulk"
        else PostgresAiRepository(factory)
    )
    table = "schemii.bulk_jobs" if repository_type == "bulk" else "schemii.ai_chats"
    app = FastAPI()
    install_api_error_handlers(app)

    @app.get("/owned")
    def endpoint():
        return (
            repository.list(postgres_metadata.owner_id, "owned-workspace")
            if repository_type == "bulk"
            else repository.list_chats(postgres_metadata.owner_id)
        )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            with postgres_metadata.connection_factory() as blocker:
                blocker.execute("LOCK TABLE " + table + " IN ACCESS EXCLUSIVE MODE")
                started = monotonic()
                response = client.get("/owned")
                elapsed = monotonic() - started
                assert 0.15 < elapsed < 2
                assert response.status_code == 503
                assert (
                    response.json()["error"]["code"] == "metadata_storage_unavailable"
                )
                assert response.json()["error"]["retryable"] is True
                assert "set-cookie" not in response.headers
                assert factory.admission_snapshot().active == 0
                assert_backends_closed(postgres_metadata, factory._application_name)
            assert client.get("/owned").json() == []
        record_property("repository_type", repository_type)
        record_property("repository_deadline_seconds", elapsed)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


@pytest.mark.parametrize("repository_type", ["bulk", "ai"])
def test_repository_native_context_commits_and_rolls_back_owned_writes(
    postgres_metadata, repository_type
):
    factory = runtime_factory(postgres_metadata)
    repository = (
        JobRepository(factory)
        if repository_type == "bulk"
        else PostgresAiRepository(factory)
    )
    transaction = (
        repository._cursor if repository_type == "bulk" else repository._transaction
    )
    owner = postgres_metadata.owner_id
    try:
        with transaction() as executor:
            executor.execute(
                "INSERT INTO metadata.users(id,display_name) VALUES (%s,%s)",
                (owner, "committed"),
            )
        with postgres_metadata.connection_factory() as connection:
            assert (
                connection.execute(
                    "SELECT display_name FROM metadata.users WHERE id=%s", (owner,)
                ).fetchone()["display_name"]
                == "committed"
            )
        failure = ApiProblem(409, "owned_conflict", "Owned transaction conflict")
        with pytest.raises(ApiProblem) as raised:
            with transaction() as executor:
                executor.execute(
                    "UPDATE metadata.users SET display_name=%s WHERE id=%s",
                    ("must roll back", owner),
                )
                raise failure
        assert raised.value is failure
        with postgres_metadata.connection_factory() as connection:
            assert (
                connection.execute(
                    "SELECT display_name FROM metadata.users WHERE id=%s", (owner,)
                ).fetchone()["display_name"]
                == "committed"
            )
        assert factory.admission_snapshot().active == 0
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_real_auth_saturation_keeps_session_and_authoritative_revocation(
    postgres_metadata, record_property
):
    factory = runtime_factory(postgres_metadata, maximum_connections=1)
    service = AuthService(factory, enabled=True, setup_token="integration-unused")
    username = "it_pressure_" + uuid4().hex
    role_id = "role_pressure_" + uuid4().hex
    user = service.create_user(
        AccountCreate(
            username=username,
            display_name="Owned pressure account",
            password="integration-pressure-password",
        )
    )
    token = uuid4().hex
    try:
        with service.store.transaction(write=True) as state:
            state["roles"][role_id] = dict(
                id=role_id,
                name=role_id,
                capabilities=["schemii:access"],
                user_ids=[user["id"]],
                connections=[],
                dashboards=[],
            )
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "INSERT INTO metadata.auth_sessions VALUES (%s,%s,%s)",
                (hashlib.sha256(token.encode()).hexdigest(), user["id"], time() + 3600),
            )
        app = FastAPI()
        app.state.auth = service
        install_api_error_handlers(app)
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/api/v1/schemii/owned")
        def read_owned():
            with factory() as connection:
                return connection.execute("SELECT 42 AS value").fetchone()

        @app.get("/api/v1/readiness")
        def readiness():
            postgres_metadata.repositories.check_readiness()
            return {"ready": True}

        with TestClient(app, base_url="https://localhost:8001") as client:
            client.cookies.set(COOKIE, token)
            with factory():
                started = monotonic()
                response = client.get("/api/v1/schemii/owned")
                elapsed = monotonic() - started
                assert elapsed < 1
                assert response.status_code == 503
                assert response.json()["error"]["retryable"] is True
                assert response.json()["error"]["code"] == "metadata_capacity_exceeded"
                assert "set-cookie" not in response.headers
                assert client.cookies.get(COOKIE) == token
                assert client.get("/api/v1/readiness").json() == {"ready": True}
            assert client.get("/api/v1/schemii/owned").json() == {"value": 42}
            with service.store.transaction(write=True) as state:
                state["roles"][role_id]["capabilities"] = []
            assert client.get("/api/v1/schemii/owned").status_code == 403
            service.logout(token)
            assert client.get("/api/v1/schemii/owned").status_code == 401
        record_property("auth_saturated_response_seconds", elapsed)
    finally:
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "DELETE FROM metadata.auth_roles WHERE id=%s", (role_id,)
            )
            connection.execute(
                "DELETE FROM metadata.auth_audit WHERE actor_id=%s OR target_id=%s",
                (user["id"], user["id"]),
            )
            connection.execute(
                "DELETE FROM metadata.auth_accounts WHERE user_id=%s", (user["id"],)
            )
            connection.execute("DELETE FROM metadata.users WHERE id=%s", (user["id"],))
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_composition_keeps_migration_and_readiness_budgets_separate(
    postgres_metadata, monkeypatch, record_property
) -> None:
    environment = dict(
        postgres_metadata.environment,
        SCHEMII_METADATA_STATEMENT_TIMEOUT_MS="100",
        SCHEMII_METADATA_LOCK_TIMEOUT_MS="20",
        SCHEMII_METADATA_IDLE_TRANSACTION_TIMEOUT_MS="3000",
        SCHEMII_METADATA_MAXIMUM_CONNECTIONS="1",
    )
    original_migrate = factory_module.MetadataMigrator.migrate
    migration_factories = []
    migration_settings = {}

    def observed_migrate(migrator):
        migration_factories.append(migrator._connection_factory)
        with migrator._connection_factory() as connection:
            migration_settings.update(effective_deadlines(connection))
            # This would exceed normal metadata's 100ms statement allowance.
            connection.execute("SELECT pg_sleep(0.2)")
        return original_migrate(migrator)

    monkeypatch.setattr(factory_module.MetadataMigrator, "migrate", observed_migrate)
    repositories = create_metadata_repositories(
        environment, migration_packages=MIGRATION_PACKAGES
    )
    normal = repositories.connection_factory
    readiness = repositories.readiness_probe._connection_factory
    assert normal is not None
    try:
        assert migration_settings == {
            "statement_timeout": 120_000,
            "lock_timeout": 30_000,
            "idle_in_transaction_session_timeout": 120_000,
        }
        assert len(migration_factories) == 1
        assert migration_factories[0].admission_snapshot().closed
        assert migration_factories[0].admission_snapshot().active == 0
        with normal() as occupied:
            assert effective_deadlines(occupied) == {
                "statement_timeout": 100,
                "lock_timeout": 20,
                "idle_in_transaction_session_timeout": 3000,
            }
            with pytest.raises(MetadataCapacityError):
                normal()
            started = monotonic()
            repositories.check_readiness()
            ready_elapsed = monotonic() - started
            assert ready_elapsed < 2
            with readiness() as connection:
                ready_settings = effective_deadlines(connection)
                assert ready_settings == {
                    "statement_timeout": 2_000,
                    "lock_timeout": 1_000,
                    "idle_in_transaction_session_timeout": 2_000,
                }
        with pytest.raises(MetadataStorageUnavailableError):
            with normal() as connection:
                connection.execute("SELECT pg_sleep(0.2)")
        record_property("composed_readiness_seconds", ready_elapsed)
        record_property("migration_deadlines_ms", json.dumps(migration_settings))
        record_property("readiness_deadlines_ms", json.dumps(ready_settings))
    finally:
        repositories.close()
    assert normal.admission_snapshot().closed
    assert readiness.admission_snapshot().closed
    assert_backends_closed(postgres_metadata, normal._application_name)
    assert_backends_closed(postgres_metadata, readiness._application_name)


def test_migration_lock_has_its_own_bounded_allowance(
    postgres_metadata, record_property
) -> None:
    environment = dict(
        postgres_metadata.environment,
        SCHEMII_METADATA_STATEMENT_TIMEOUT_MS="100",
        SCHEMII_METADATA_LOCK_TIMEOUT_MS="20",
        SCHEMII_METADATA_MIGRATION_LOCK_TIMEOUT_MS="300",
    )
    started = monotonic()
    with postgres_metadata.connection_factory() as blocker:
        blocker.execute("SELECT pg_advisory_xact_lock(%s)", (0x534348454D4949,))
        with pytest.raises(MetadataMigrationError) as failure:
            create_metadata_repositories(
                environment, migration_packages=MIGRATION_PACKAGES
            )
    elapsed = monotonic() - started
    assert isinstance(failure.value.__cause__, psycopg.errors.LockNotAvailable)
    assert 0.2 < elapsed < 2
    assert_backends_closed(postgres_metadata, "schemii-metadata-migrations")
    recovered = create_metadata_repositories(
        environment, migration_packages=MIGRATION_PACKAGES
    )
    recovered.check_readiness()
    recovered.close()
    record_property("migration_lock_wait_seconds", elapsed)


def test_idle_transaction_deadline_closes_backend_but_keeps_native_lease(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, idle_transaction_timeout_ms=200
    )
    connection = factory()
    try:
        connection.execute("SELECT 1")
        started = monotonic()
        # PostgreSQL owns this timeout, not a Python timer or an async cancellation.
        assert_backends_closed(postgres_metadata, factory._application_name)
        elapsed = monotonic() - started
        assert 0.1 < elapsed < 2
        assert factory.admission_snapshot().active == 1
        with pytest.raises(MetadataCapacityError):
            factory()
        with pytest.raises(MetadataStorageUnavailableError) as failure:
            with connection:
                connection.execute("SELECT 42 AS recovered")
        assert isinstance(
            failure.value.__cause__, psycopg.errors.IdleInTransactionSessionTimeout
        )
        assert factory.admission_snapshot().active == 0
        with factory() as recovered:
            assert (
                recovered.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("idle_transaction_timeout_seconds", elapsed)
    finally:
        connection.close()
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


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
