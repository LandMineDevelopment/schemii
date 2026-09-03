from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from schemii.common.connections.models import (
    PostgresConnectionCreate,
    PostgresConnectionUpdate,
)
from schemii.common.connections.postgres_store import PostgresConnectionRepository
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import (
    ConnectionConflictError,
    ConnectionInUseError,
    ConnectionLimitError,
    ConnectionNotFoundError,
    InMemoryConnectionRepository,
)
from schemii.common.metadata.crypto import CredentialCipher
from schemii.schemii.workspaces.models import WorkspaceCreateRecord
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class RecordingCursor:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.row = {
            "id": "pg_" + "1" * 32,
            "owner_id": "owner",
            "revision": 1,
            "name": "Reporting",
            "host": "localhost",
            "port": 5432,
            "database_name": "analytics",
            "username": "reader",
            "ssl_mode": "require",
            "connect_timeout": 10,
            "created_at": now,
            "updated_at": now,
        }
        self.statements: list[str] = []
        self._result: dict[str, Any] | None = None
        self.deleted = False

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        del parameters
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        if normalized.startswith(
            (
                "SELECT * FROM metadata.postgres_connections",
                "SELECT revision FROM metadata.postgres_connections",
            )
        ):
            self._result = None if self.deleted else dict(self.row)
        elif normalized.startswith("SELECT connection.*"):
            self._result = None if self.deleted else {
                **self.row,
                "credential_stored": False,
            }
        elif normalized.startswith("UPDATE metadata.postgres_connections"):
            self.row["revision"] += 1
            self.row["updated_at"] = datetime.now(timezone.utc)
            self._result = dict(self.row)
        elif normalized.startswith("SELECT EXISTS"):
            self._result = {"credential_stored": False}
        elif normalized.startswith("DELETE FROM metadata.postgres_connections"):
            self.deleted = True
            self._result = None
        else:  # pragma: no cover - protects this focused adapter from query drift.
            raise AssertionError(f"unexpected statement: {normalized}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._result


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = RecordingCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def postgres_repository(
    connection: RecordingConnection,
) -> PostgresConnectionRepository:
    return PostgresConnectionRepository(
        lambda: connection,
        CredentialCipher(bytes(range(32))),
    )


def request(password: str | None = "secret") -> PostgresConnectionCreate:
    return PostgresConnectionCreate(
        name="Reporting",
        host="localhost",
        database="analytics",
        username="reader",
        password=password,
        ssl_mode="require",
    )


def test_connections_are_owner_scoped_and_profiles_are_redacted() -> None:
    repository = InMemoryConnectionRepository()
    profile = repository.create("owner-a", request())

    assert repository.list("owner-a") == [profile]
    assert repository.list("owner-b") == []
    assert "password" not in profile.model_dump()
    assert repository.resolve("owner-a", profile.id).password.get_secret_value() == "secret"

    try:
        repository.get("owner-b", profile.id)
    except ConnectionNotFoundError:
        pass
    else:  # pragma: no cover - assertion branch.
        raise AssertionError("another owner accessed the connection")


def test_update_preserves_replaces_and_removes_password() -> None:
    repository = InMemoryConnectionRepository()
    original = repository.create("owner", request())

    renamed = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(
            expected_revision=original.revision,
            name="Renamed",
        ),
    )
    assert renamed.revision == 2
    assert repository.resolve("owner", original.id).password.get_secret_value() == "secret"

    replaced = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(expected_revision=2, password="new secret"),
    )
    assert replaced.credential_stored is True
    assert repository.resolve("owner", original.id).password.get_secret_value() == "new secret"

    passwordless = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(expected_revision=3, password=None),
    )
    assert passwordless.credential_stored is False
    assert repository.resolve("owner", original.id).password is None


def test_stale_updates_and_deletes_fail_closed() -> None:
    repository = InMemoryConnectionRepository()
    profile = repository.create("owner", request())
    repository.update(
        "owner",
        profile.id,
        PostgresConnectionUpdate(expected_revision=1, name="Changed"),
    )

    for operation in (
        lambda: repository.update(
            "owner",
            profile.id,
            PostgresConnectionUpdate(expected_revision=1, name="Stale"),
        ),
        lambda: repository.delete("owner", profile.id, 1),
    ):
        try:
            operation()
        except ConnectionConflictError as error:
            assert error.current_revision == 2
        else:  # pragma: no cover - assertion branch.
            raise AssertionError("stale mutation was accepted")


def test_connection_targets_are_exact_and_single_host() -> None:
    exact = PostgresConnectionCreate(
        name=" Reporting ",
        host=" localhost ",
        database=" analytics ",
        username=" reader ",
    )

    assert exact.name == "Reporting"
    assert exact.host == "localhost"
    assert exact.database == " analytics "
    assert exact.username == " reader "
    assert exact.ssl_mode.value == "verify-full"

    with pytest.raises(ValidationError):
        PostgresConnectionCreate(
            name="Reporting",
            host="primary,replica",
            database="analytics",
            username="reader",
        )

    with pytest.raises(ValidationError, match="4096 UTF-8 bytes"):
        PostgresConnectionCreate(
            name="Reporting",
            host="localhost",
            database="analytics",
            username="reader",
            password="\N{SNOWMAN}" * 4096,
        )

    with pytest.raises(ValidationError):
        PostgresConnectionCreate(
            name="Reporting",
            host="localhost",
            port=True,
            database="analytics",
            username="reader",
        )


def test_connection_repository_has_a_bounded_owner_capacity() -> None:
    repository = InMemoryConnectionRepository(max_connections_per_owner=1)
    repository.create("owner", request())

    with pytest.raises(ConnectionLimitError) as caught:
        repository.create("owner", request())

    assert caught.value.limit == 1


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_postgres_mutation_guard_runs_after_row_lock_and_before_change(
    operation: str,
) -> None:
    connection = RecordingConnection()
    repository = postgres_repository(connection)
    callback_cursors: list[RecordingCursor] = []

    def guard(cursor, owner_id, connection_id, guarded_operation, changed_fields) -> None:
        assert cursor is connection.cursor_instance
        assert owner_id == "owner"
        assert connection_id == connection.cursor_instance.row["id"]
        assert guarded_operation == operation
        assert changed_fields == ({"name"} if operation == "update" else set())
        assert cursor.statements[-1].endswith("FOR UPDATE")
        assert not any(
            statement.startswith(("UPDATE", "DELETE"))
            for statement in cursor.statements
        )
        callback_cursors.append(cursor)

    repository.set_mutation_guard(guard)
    connection_id = connection.cursor_instance.row["id"]

    if operation == "update":
        updated = repository.update(
            "owner",
            connection_id,
            PostgresConnectionUpdate(expected_revision=1, name="Renamed"),
        )
        assert updated.revision == 2
    else:
        repository.delete("owner", connection_id, expected_revision=1)
        assert connection.cursor_instance.deleted is True

    assert callback_cursors == [connection.cursor_instance]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_postgres_mutation_guard_veto_rolls_back_without_revision_change() -> None:
    connection = RecordingConnection()
    repository = postgres_repository(connection)

    def veto(*_args: object) -> None:
        raise ConnectionInUseError({"activeWork": 1})

    repository.set_mutation_guard(veto)

    with pytest.raises(ConnectionInUseError) as caught:
        repository.update(
            "owner",
            connection.cursor_instance.row["id"],
            PostgresConnectionUpdate(expected_revision=1, name="Must not apply"),
        )

    assert caught.value.dependencies == {"activeWork": 1}
    assert connection.cursor_instance.row["revision"] == 1
    assert not any(
        statement.startswith("UPDATE")
        for statement in connection.cursor_instance.statements
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_service_registers_durable_product_guard() -> None:
    connection = RecordingConnection()
    repository = postgres_repository(connection)
    calls: list[tuple[RecordingCursor, str]] = []

    class Provider:
        dependency_name = "workspaces"

        def count_for_connection(self, owner_id: str, connection_id: str) -> int:
            del owner_id, connection_id
            return 0

        def guard_connection_mutation(
            self, cursor, owner_id, connection_id, operation, changed_fields
        ) -> None:
            del owner_id, connection_id
            assert changed_fields == frozenset()
            calls.append((cursor, operation))

    ConnectionService(repository, (Provider(),))
    repository.delete(
        "owner",
        connection.cursor_instance.row["id"],
        expected_revision=1,
    )

    assert calls == [(connection.cursor_instance, "delete")]


def test_in_memory_dependency_check_and_live_use_lock_are_preserved() -> None:
    repository = InMemoryConnectionRepository()
    profile = repository.create("owner", request())

    class Dependency:
        dependency_name = "workspaces"

        def __init__(self) -> None:
            self.count = 1

        def count_for_connection(self, owner_id: str, connection_id: str) -> int:
            assert (owner_id, connection_id) == ("owner", profile.id)
            return self.count

    dependency = Dependency()
    service = ConnectionService(repository, (dependency,))
    with pytest.raises(ConnectionInUseError):
        service.delete("owner", profile.id, expected_revision=1)
    assert repository.get("owner", profile.id).revision == 1

    dependency.count = 0
    started = threading.Event()
    completed = threading.Event()

    def update() -> None:
        started.set()
        service.update(
            "owner",
            profile.id,
            PostgresConnectionUpdate(expected_revision=1, name="Renamed"),
        )
        completed.set()

    with service.use("owner", profile.id):
        worker = threading.Thread(target=update)
        worker.start()
        assert started.wait(1)
        assert not completed.wait(0.05)

    worker.join(timeout=1)
    assert completed.is_set()
    assert repository.get("owner", profile.id).name == "Renamed"


def test_workspace_dependency_snapshot_reports_lifecycle_blocker() -> None:
    workspaces = InMemoryWorkspaceRepository()
    workspace = workspaces.create(
        "owner",
        WorkspaceCreateRecord(
            name="analytics.public",
            connection_id="pg_" + "1" * 32,
            database="analytics",
            namespace="public",
        ),
        expected_connection_revision=1,
    )
    workspaces.set_mutation_guard(
        lambda owner_id, workspace_id: (
            owner_id == "owner" and workspace_id == workspace.id
        )
    )

    dependencies = workspaces.dependencies_for_connection(
        "owner",
        "pg_" + "1" * 32,
    )

    assert len(dependencies) == 1
    assert dependencies[0].resource_id == workspace.id
    assert dependencies[0].target == "analytics.public"
    assert dependencies[0].deletion_blocked is True
    assert dependencies[0].blocking_reason is not None
