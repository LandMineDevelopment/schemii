"""Native connection lifetime, failure mapping and separate control budgets."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from threading import Event, Lock
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import psycopg
import pytest

from schemii.common.api.errors import install_api_error_handlers
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.auth.service import COOKIE
from schemii.common.errors import MetadataCapacityError, MetadataStorageUnavailableError
from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.database import MetadataConnectionFactory
from schemii.schemii.ai.repository import PostgresAiRepository
from schemii.schemii.bulk_jobs.repository import JobRepository


class NativeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.commit_error: Exception | None = None
        self.close_error: Exception | None = None

    def __enter__(self) -> NativeConnection:
        return self

    def __exit__(self, exc_type, _exc_value, _traceback) -> None:
        if exc_type is None and self.commit_error is not None:
            raise self.commit_error
        self.close()

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class TransactionConnection(NativeConnection):
    def __init__(self) -> None:
        super().__init__()
        self._autocommit = False
        self.autocommit_error: Exception | None = None

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        if self.autocommit_error is not None:
            raise self.autocommit_error
        self._autocommit = value


@pytest.fixture
def config(tmp_path) -> MetadataConfig:
    password = tmp_path / "password"
    password.write_text("test-only-password\n")
    return MetadataConfig(
        dsn="host=unused dbname=unused",
        password_file=str(password),
        encryption_key_file=str(tmp_path / "key"),
        maximum_connections=2,
        shutdown_timeout_seconds=1,
    )


def test_concurrent_admission_rejects_before_connect_and_recovers(config) -> None:
    attempt_finished = Event()
    release = Event()
    counter_lock = Lock()
    attempts = 0
    connected: list[NativeConnection] = []

    def connect(*_args, **_kwargs):
        connection = NativeConnection()
        with counter_lock:
            connected.append(connection)
        return connection

    factory = MetadataConnectionFactory(config, connect=connect)

    def work() -> str:
        nonlocal attempts
        connection = None
        outcome = "admitted"
        try:
            connection = factory()
        except MetadataCapacityError:
            outcome = "rejected"
        finally:
            with counter_lock:
                attempts += 1
                if attempts == 12:
                    attempt_finished.set()
        if connection is not None:
            assert release.wait(5)
            connection.close()
        return outcome

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(work) for _ in range(12)]
        try:
            assert attempt_finished.wait(5)
            snapshot = factory.admission_snapshot()
            assert (
                snapshot.active,
                snapshot.peak_active,
                snapshot.admitted,
                snapshot.rejected,
            ) == (2, 2, 2, 10)
            assert len(connected) == 2
            assert not any(connection.closed for connection in connected)
        finally:
            release.set()
        assert [future.result() for future in futures].count("rejected") == 10
    assert factory.admission_snapshot().active == 0
    with factory():
        assert factory.admission_snapshot().active == 1
    assert factory.admission_snapshot().active == 0
    assert all(connection.closed for connection in connected)
    factory.close()


def test_autocommit_assignment_changes_native_transaction_mode(config) -> None:
    native = TransactionConnection()
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: native)
    with factory() as connection:
        assert connection.autocommit is False
        connection.autocommit = True
        assert native.autocommit is True
        native.autocommit = False
        assert connection.autocommit is False
        connection.autocommit = True
        connection.autocommit = False
        assert native.autocommit is False
        assert factory.admission_snapshot().active == 1
    assert native.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


def test_autocommit_native_rejection_remains_visible_and_keeps_permit(config) -> None:
    native = TransactionConnection()
    failure = psycopg.ProgrammingError("transaction is already in progress")
    native.autocommit_error = failure
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: native)
    with factory() as connection:
        with pytest.raises(psycopg.ProgrammingError) as raised:
            connection.autocommit = True
        assert raised.value is failure
        assert connection.autocommit is False
        assert native.autocommit is False
        assert not native.closed
        assert factory.admission_snapshot().active == 1
    assert native.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


@pytest.mark.parametrize("blocked_during", ["connect", "work", "close"])
def test_to_thread_cancellation_keeps_native_permit_until_close(
    config, blocked_during
) -> None:
    blocked = Event()
    release = Event()
    native_finished = Event()
    connection = NativeConnection()

    def pause() -> None:
        blocked.set()
        assert release.wait(5)

    def connect(*_args, **_kwargs):
        if blocked_during == "connect":
            pause()
        return connection

    original_close = connection.close

    def close() -> None:
        if blocked_during == "close":
            pause()
        original_close()

    connection.close = close
    factory = MetadataConnectionFactory(replace(config, maximum_connections=1), connect)

    def work() -> None:
        try:
            with factory():
                if blocked_during == "work":
                    pause()
        finally:
            native_finished.set()

    async def cancel_request() -> None:
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            assert await asyncio.to_thread(blocked.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not native_finished.is_set()
            assert factory.admission_snapshot().active == 1
            with pytest.raises(MetadataCapacityError):
                factory()
        finally:
            release.set()
            assert await asyncio.to_thread(native_finished.wait, 5)

    asyncio.run(cancel_request())
    assert connection.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


def test_failed_connect_and_failed_commit_close_without_leaking_admission(
    config,
) -> None:
    def fail_connect(*_args, **_kwargs):
        raise psycopg.OperationalError("private host/password diagnostic")

    factory = MetadataConnectionFactory(config, fail_connect)
    with pytest.raises(
        MetadataStorageUnavailableError, match="temporarily unavailable"
    ) as failure:
        factory()
    assert "private" not in str(failure.value)
    assert factory.admission_snapshot().active == 0
    assert factory.admission_snapshot().connection_failures == 1

    connection = NativeConnection()
    connection.commit_error = psycopg.OperationalError("private diagnostic")
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: connection)
    with pytest.raises(MetadataStorageUnavailableError):
        with factory():
            pass
    assert connection.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


def test_close_failure_does_not_release_a_live_native_connection(config) -> None:
    native = NativeConnection()
    native.close_error = RuntimeError("close did not finish")
    factory = MetadataConnectionFactory(
        replace(config, maximum_connections=1), lambda *_a, **_kw: native
    )
    connection = factory()
    with pytest.raises(RuntimeError, match="did not finish"):
        connection.close()
    assert factory.admission_snapshot().active == 1
    with pytest.raises(MetadataCapacityError):
        factory()
    native.close_error = None
    connection.close()
    connection.close()
    assert native.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


@pytest.mark.parametrize(
    "failure",
    [psycopg.errors.UniqueViolation("unique"), HTTPException(409, "conflict")],
)
def test_transaction_contract_errors_remain_original(config, failure) -> None:
    native = NativeConnection()
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: native)
    with pytest.raises(type(failure)) as raised:
        with factory():
            raise failure
    assert raised.value is failure
    assert native.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


def test_shutdown_rejects_new_work_and_never_discards_live_permits(config) -> None:
    native = NativeConnection()
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: native)
    connection = factory()
    with pytest.raises(MetadataStorageUnavailableError, match="shutdown timed out"):
        factory.close()
    assert factory.admission_snapshot().closed
    assert factory.admission_snapshot().active == 1
    assert not native.closed
    with pytest.raises(MetadataStorageUnavailableError, match="shutting down"):
        factory()
    connection.close()
    factory.close()
    assert native.closed
    assert factory.admission_snapshot().active == 0


@pytest.mark.parametrize("stage", ["resolve", "capabilities", "endpoint"])
@pytest.mark.parametrize(
    "failure_type", [MetadataCapacityError, MetadataStorageUnavailableError]
)
def test_auth_metadata_pressure_is_retryable_without_cookie_loss(
    stage, failure_type
) -> None:
    failure: Exception | None = failure_type("do not disclose private diagnostics")
    application = FastAPI()
    user = {"id": "user_test"}

    def resolve(token):
        if stage == "resolve" and failure is not None:
            raise failure
        return user if token == "existing-session" else None

    def capabilities(_user):
        if stage == "capabilities" and failure is not None:
            raise failure
        return ["schemii:access"]

    application.state.auth = SimpleNamespace(
        enabled=True, resolve=resolve, capabilities=capabilities
    )
    install_api_error_handlers(application)
    application.add_middleware(AuthenticationMiddleware)

    @application.delete("/api/v1/common/query-executions/owned")
    def cancel():
        if stage == "endpoint" and failure is not None:
            raise failure
        return {"canceled": True}

    with TestClient(
        application,
        base_url="https://localhost:8001",
        headers={"Origin": "https://localhost:8001"},
    ) as client:
        client.cookies.set(COOKIE, "existing-session")
        response = client.delete("/api/v1/common/query-executions/owned")
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is True
        assert response.json()["error"]["code"] == (
            "metadata_capacity_exceeded"
            if failure_type is MetadataCapacityError
            else "metadata_storage_unavailable"
        )
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Retry-After"] == "1"
        assert "set-cookie" not in response.headers
        assert "private" not in response.text
        assert client.cookies.get(COOKIE) == "existing-session"
        failure = None
        assert client.delete("/api/v1/common/query-executions/owned").json() == {
            "canceled": True
        }


@pytest.mark.parametrize("repository_type", ["bulk", "ai"])
@pytest.mark.parametrize(
    "failure_type",
    [psycopg.errors.QueryCanceled, psycopg.errors.IdleInTransactionSessionTimeout],
)
def test_repository_transaction_deadline_uses_safe_error_owner(
    config, repository_type, failure_type
):
    class BlockedCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, *_):
            raise failure_type("private SQL diagnostic")

    class BlockedConnection(NativeConnection):
        def cursor(self):
            return BlockedCursor()

        def transaction(self):
            return nullcontext()

    native = BlockedConnection()
    factory = MetadataConnectionFactory(config, lambda *_a, **_kw: native)
    repository = (
        JobRepository(factory)
        if repository_type == "bulk"
        else PostgresAiRepository(factory)
    )
    app = FastAPI()
    install_api_error_handlers(app)

    @app.get("/owned")
    def endpoint():
        return (
            repository.list("owner", "workspace")
            if repository_type == "bulk"
            else repository.list_chats("owner")
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/owned")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "metadata_storage_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "private" not in response.text
    assert native.closed
    assert factory.admission_snapshot().active == 0
    factory.close()


def test_application_lifespan_closes_metadata_after_migration_worker(monkeypatch):
    from test_runtime_hardening import application_services
    from schemii.common.connections.store import InMemoryConnectionRepository
    from schemii.common.metadata.factory import MetadataRepositories
    from schemii.main import MigrationExecutionWorker, create_app

    order = []
    original_stop = MigrationExecutionWorker.stop

    async def stop_worker(worker):
        await original_stop(worker)
        order.append("migration-worker-stopped")

    def close_metadata():
        assert order == ["migration-worker-stopped"]
        order.append("metadata-closed")

    monkeypatch.setattr(MigrationExecutionWorker, "stop", stop_worker)
    metadata = MetadataRepositories(
        connections=InMemoryConnectionRepository(), close_runtime=close_metadata
    )
    with TestClient(create_app(application_services(metadata=metadata))):
        assert order == []
    assert order == ["migration-worker-stopped", "metadata-closed"]
