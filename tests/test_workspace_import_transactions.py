from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from schemii.common.connections.store import ConnectionInUseError
from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.migrations.repository import InMemoryMigrationRepository
from schemii.schemii.migrations.service import MigrationService
from schemii.schemii.workspaces.models import SchemiiWorkspaceCreate
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceDesignBootstrap,
    WorkspaceImportBaseline,
    WorkspaceImportTargetChangedError,
    WorkspaceMutationBlockedError,
    WorkspaceNotFoundError,
    WorkspaceStorageUnavailableError,
)


OWNER_ID = "owner"
CONNECTION_ID = "pg_" + "1" * 32
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _catalog():
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=NOW,
    )


def _import_records() -> tuple[WorkspaceDesignBootstrap, WorkspaceImportBaseline]:
    catalog = _catalog()
    imported = import_postgres_catalog(catalog)
    return (
        WorkspaceDesignBootstrap(
            content=imported.content,
            layout=imported.layout,
            import_summary=imported.summary,
        ),
        WorkspaceImportBaseline(
            connection_revision=1,
            content=imported.content,
            catalog=catalog,
            complete=True,
            issues=[],
        ),
    )


def _request() -> SchemiiWorkspaceCreate:
    return SchemiiWorkspaceCreate(
        name="Imported",
        connection_id=CONNECTION_ID,
        database="analytics",
        namespace="public",
    )


def test_in_memory_import_commits_workspace_design_layout_and_baseline_together() -> None:
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    migrations = InMemoryMigrationRepository(designs)
    bootstrap, baseline = _import_records()

    workspace = workspaces.create_import(
        OWNER_ID,
        _request(),
        bootstrap=bootstrap,
        baseline=baseline,
        baselines=migrations,
    )

    assert workspaces.get(OWNER_ID, workspace.id) == workspace
    assert designs.get(OWNER_ID, workspace.id).content == bootstrap.content
    assert designs.get_layout(OWNER_ID, workspace.id).content == bootstrap.layout
    saved_baseline = migrations.current_baseline(OWNER_ID, workspace.id)
    assert saved_baseline is not None
    assert saved_baseline.content == baseline.content
    assert saved_baseline.connection_revision == baseline.connection_revision


def test_in_memory_import_rolls_back_workspace_and_design_when_baseline_fails() -> None:
    class FailingBaselines:
        workspace_id: str | None = None

        def create_import_baseline(self, **values: Any) -> None:
            assert values["metadata_cursor"] is None
            self.workspace_id = values["workspace_id"]
            raise RuntimeError("baseline write failed")

    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    bootstrap, baseline = _import_records()

    failing = FailingBaselines()
    with pytest.raises(RuntimeError, match="baseline write failed"):
        workspaces.create_import(
            OWNER_ID,
            _request(),
            bootstrap=bootstrap,
            baseline=baseline,
            baselines=failing,
        )

    assert workspaces.list(OWNER_ID) == []
    assert failing.workspace_id is not None
    restored_design, restored_layout = designs.initialize(
        OWNER_ID,
        failing.workspace_id,
        bootstrap.content,
        bootstrap.layout,
    )
    assert restored_design.revision == 1
    assert restored_layout.revision == 1


def test_migration_service_installs_workspace_lifecycle_guard() -> None:
    class ExecutionAuthority:
        active = True

        def has_active_execution(self, owner_id: str, workspace_id: str) -> bool:
            assert owner_id == OWNER_ID
            assert workspace_id.startswith("ws_")
            return self.active

    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    authority = ExecutionAuthority()
    MigrationService(
        repository=authority,  # type: ignore[arg-type]
        connections=SimpleNamespace(),  # type: ignore[arg-type]
        postgres=SimpleNamespace(),  # type: ignore[arg-type]
        workspaces=workspaces,
        designs=designs,
    )
    bootstrap, _ = _import_records()
    workspace = workspaces.create(OWNER_ID, _request(), bootstrap=bootstrap)

    with pytest.raises(WorkspaceMutationBlockedError):
        workspaces.delete(OWNER_ID, workspace.id, workspace.revision)
    assert workspaces.get(OWNER_ID, workspace.id) == workspace

    authority.active = False
    workspaces.delete(OWNER_ID, workspace.id, workspace.revision)
    with pytest.raises(WorkspaceNotFoundError):
        workspaces.get(OWNER_ID, workspace.id)
    assert designs.get(OWNER_ID, workspace.id).revision == 0
    assert designs.get_layout(OWNER_ID, workspace.id).revision == 0


class _MetadataState:
    def __init__(
        self,
        *,
        connection_revision: int = 1,
        workspace_revision: int | None = None,
        active_execution: bool = False,
    ) -> None:
        self.connection_revision = connection_revision
        self.workspace_revision = workspace_revision
        self.active_execution = active_execution
        self.pending: list[str] = []
        self.durable: list[str] = []


class _MetadataCursor:
    def __init__(self, connection: "_MetadataConnection") -> None:
        self.connection = connection
        self._row: dict[str, Any] | None = None

    def __enter__(self) -> "_MetadataCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> None:
        statement = " ".join(query.split())
        self.connection.statements.append(statement)
        self._row = None
        if "FROM metadata.postgres_connections" in statement:
            self._row = {
                "revision": self.connection.state.connection_revision,
                "database_name": "analytics",
            }
        elif statement.startswith("SELECT id FROM metadata.users"):
            self._row = {"id": OWNER_ID}
        elif "AS workspace_count" in statement:
            self._row = {"workspace_count": 0}
        elif statement.startswith("SELECT revision FROM schemii.workspaces"):
            self._row = (
                {"revision": self.connection.state.workspace_revision}
                if self.connection.state.workspace_revision is not None
                else None
            )
        elif "FROM schemii.migration_executions" in statement:
            self._row = {"active": self.connection.state.active_execution}
        elif statement.startswith("INSERT INTO schemii.workspaces"):
            workspace_id, _, name, mode = parameters
            self.connection.state.pending.append("workspace")
            self._row = {
                "id": workspace_id,
                "revision": 1,
                "name": name,
                "mode": mode,
                "created_at": NOW,
                "updated_at": NOW,
            }
        elif statement.startswith("INSERT INTO schemii.workspace_targets"):
            self.connection.state.pending.append("target")
        elif statement.startswith("INSERT INTO schemii.workspace_designs"):
            self.connection.state.pending.append("design")
        elif statement.startswith("INSERT INTO schemii.workspace_design_layouts"):
            self.connection.state.pending.append("layout")
        elif statement.startswith("INSERT INTO schemii.workspace_design_imports"):
            self.connection.state.pending.append("provenance")
        elif statement.startswith("DELETE FROM schemii.workspaces"):
            self.connection.state.pending.append("workspace_delete")

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _MetadataConnection:
    def __init__(self, state: _MetadataState) -> None:
        self.state = state
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.cursor_instance = _MetadataCursor(self)

    def cursor(self) -> _MetadataCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1
        self.state.durable.extend(self.state.pending)
        self.state.pending.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.state.pending.clear()

    def close(self) -> None:
        self.closed = True


def test_postgres_import_rolls_back_every_staged_record_on_baseline_failure() -> None:
    class FailingBaselines:
        def create_import_baseline(self, **values: Any) -> None:
            assert values["metadata_cursor"] is connection.cursor_instance
            connection.state.pending.append("baseline")
            raise RuntimeError("baseline write failed")

    state = _MetadataState()
    connection = _MetadataConnection(state)
    repository = PostgresWorkspaceRepository(lambda: connection)
    bootstrap, baseline = _import_records()

    with pytest.raises(WorkspaceStorageUnavailableError):
        repository.create_import(
            OWNER_ID,
            _request(),
            bootstrap=bootstrap,
            baseline=baseline,
            baselines=FailingBaselines(),
        )

    assert state.pending == []
    assert state.durable == []
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_postgres_import_commits_baseline_on_the_workspace_transaction() -> None:
    class RecordingBaselines:
        def create_import_baseline(self, **values: Any) -> None:
            assert values["metadata_cursor"] is connection.cursor_instance
            connection.state.pending.append("baseline")

    state = _MetadataState()
    connection = _MetadataConnection(state)
    repository = PostgresWorkspaceRepository(lambda: connection)
    bootstrap, baseline = _import_records()

    workspace = repository.create_import(
        OWNER_ID,
        _request(),
        bootstrap=bootstrap,
        baseline=baseline,
        baselines=RecordingBaselines(),
    )

    assert workspace.connection_id == CONNECTION_ID
    assert state.pending == []
    assert state.durable == [
        "workspace",
        "target",
        "design",
        "layout",
        "provenance",
        "baseline",
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_postgres_import_rejects_changed_connection_before_staging_workspace() -> None:
    class UnusedBaselines:
        def create_import_baseline(self, **values: Any) -> None:
            pytest.fail(f"baseline must not be written: {values}")

    state = _MetadataState(connection_revision=2)
    connection = _MetadataConnection(state)
    repository = PostgresWorkspaceRepository(lambda: connection)
    bootstrap, baseline = _import_records()

    with pytest.raises(WorkspaceImportTargetChangedError) as changed:
        repository.create_import(
            OWNER_ID,
            _request(),
            bootstrap=bootstrap,
            baseline=baseline,
            baselines=UnusedBaselines(),
        )

    assert changed.value.expected_revision == 1
    assert changed.value.current_revision == 2
    assert state.pending == []
    assert state.durable == []
    assert connection.rollbacks == 1


def test_postgres_workspace_delete_is_blocked_by_durable_active_execution() -> None:
    state = _MetadataState(workspace_revision=3, active_execution=True)
    connection = _MetadataConnection(state)
    repository = PostgresWorkspaceRepository(lambda: connection)

    with pytest.raises(WorkspaceMutationBlockedError) as blocked:
        repository.delete(OWNER_ID, "ws_" + "3" * 32, expected_revision=3)

    assert blocked.value.operation == "deleted"
    assert state.pending == []
    assert state.durable == []
    assert connection.commits == 0
    assert connection.rollbacks == 1


class _ConnectionGuardCursor:
    def __init__(self, *, workspace_count: int, active_count: int) -> None:
        self.workspace_count = workspace_count
        self.active_count = active_count
        self._result: str | None = None
        self.statements: list[str] = []

    def execute(self, query: str, parameters: tuple[Any, ...]) -> None:
        statement = " ".join(query.split())
        self.statements.append(statement)
        self._result = "active" if "active_count" in statement else "workspaces"

    def fetchall(self) -> list[dict[str, str]]:
        assert self._result == "workspaces"
        return [{"id": f"ws_{index}"} for index in range(self.workspace_count)]

    def fetchone(self) -> dict[str, int]:
        assert self._result == "active"
        return {"active_count": self.active_count}


def test_connection_update_is_blocked_only_while_target_execution_is_active() -> None:
    active = _ConnectionGuardCursor(workspace_count=2, active_count=1)

    with pytest.raises(ConnectionInUseError) as blocked:
        PostgresWorkspaceRepository.guard_connection_mutation(
            active,
            OWNER_ID,
            CONNECTION_ID,
            "update",
        )

    assert blocked.value.dependencies == {"schemiiWorkspaces": 1}
    assert "FOR UPDATE OF workspace" in active.statements[0]

    inactive = _ConnectionGuardCursor(workspace_count=2, active_count=0)
    PostgresWorkspaceRepository.guard_connection_mutation(
        inactive,
        OWNER_ID,
        CONNECTION_ID,
        "update",
    )


def test_connection_delete_is_blocked_by_any_locked_workspace_reference() -> None:
    cursor = _ConnectionGuardCursor(workspace_count=2, active_count=0)

    with pytest.raises(ConnectionInUseError) as blocked:
        PostgresWorkspaceRepository.guard_connection_mutation(
            cursor,
            OWNER_ID,
            CONNECTION_ID,
            "delete",
        )

    assert blocked.value.dependencies == {"schemiiWorkspaces": 2}
    assert len(cursor.statements) == 1
