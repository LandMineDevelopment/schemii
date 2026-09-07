"""Owner-scoped Schemii workspace repository and prototype implementation."""

from __future__ import annotations

import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

from schemii.common.connections.dependencies import ConnectionDependentResource
from schemii.common.errors import MetadataStorageUnavailableError
from schemii.common.postgres.models import PostgresCatalog
from schemii.schemii.designs.models import (
    SchemiiDesignContent,
    SchemiiDesignLayoutContent,
)
from schemii.schemii.designs.store import DesignRepository

from .models import (
    SchemiiWorkspace,
    WorkspaceCreateRecord,
    SchemiiWorkspaceLayoutUpdate,
    TableColumnDisplayOrder,
    WorkspaceImportSummary,
    WorkspaceMetadataUpdate,
)

MAX_WORKSPACES_PER_OWNER = 1_000
MAX_TABLE_POSITIONS_PER_OWNER = 100_000
MAX_COLUMN_DISPLAY_ORDER_ENTRIES_PER_OWNER = 100_000


@dataclass(frozen=True, slots=True)
class WorkspaceDesignBootstrap:
    """Validated initial design state written with a brand-new workspace."""

    content: SchemiiDesignContent
    layout: SchemiiDesignLayoutContent
    import_summary: WorkspaceImportSummary


@dataclass(frozen=True, slots=True)
class WorkspaceImportBaseline:
    """The synchronization point that must commit with an imported workspace."""

    connection_revision: int
    content: SchemiiDesignContent
    catalog: PostgresCatalog
    complete: bool
    issues: list[dict[str, Any]]


class WorkspaceImportBaselineStore(Protocol):
    """Persist an import baseline inside the workspace's metadata transaction."""

    def create_import_baseline(
        self,
        *,
        metadata_cursor: Any | None,
        owner_id: str,
        workspace_id: str,
        connection_id: str,
        database: str,
        namespace: str,
        baseline: WorkspaceImportBaseline,
    ) -> Any: ...


class WorkspaceRepositoryError(RuntimeError):
    """Base error for owner-scoped workspace operations."""


class WorkspaceNotFoundError(WorkspaceRepositoryError):
    """The requested workspace does not exist for this owner."""


class WorkspaceConflictError(WorkspaceRepositoryError):
    """The workspace changed after it was read."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("Schemii workspace changed in another request")


class WorkspaceLimitError(WorkspaceRepositoryError):
    """The prototype owner has reached a bounded workspace resource limit."""

    def __init__(self, category: str, limit: int) -> None:
        self.category = category
        self.limit = limit
        super().__init__(f"The Schemii {category} limit has been reached")


class WorkspaceStorageUnavailableError(
    WorkspaceRepositoryError,
    MetadataStorageUnavailableError,
):
    """Durable workspace metadata cannot currently be read or changed."""


class WorkspaceImportTargetChangedError(WorkspaceRepositoryError):
    """The selected connection changed after PostgreSQL was inspected."""

    def __init__(
        self,
        *,
        expected_revision: int,
        current_revision: int | None,
        expected_database: str,
        current_database: str | None,
    ) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.expected_database = expected_database
        self.current_database = current_database
        super().__init__(
            "The PostgreSQL connection changed while its catalog was being imported"
        )


class WorkspaceMutationBlockedError(WorkspaceRepositoryError):
    """A workspace lifecycle mutation would invalidate an active execution."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"The workspace cannot be {operation} while a migration execution is active"
        )


class WorkspaceTargetExistsError(WorkspaceRepositoryError):
    """A concurrent request already created the owner's exact target design."""

    def __init__(self, workspace: SchemiiWorkspace) -> None:
        self.workspace = workspace
        super().__init__("The PostgreSQL workspace already exists")


@runtime_checkable
class WorkspaceRepository(Protocol):
    dependency_name: str

    def list(self, owner_id: str) -> list[SchemiiWorkspace]: ...

    def get(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace: ...

    def rename(self, owner_id: str, workspace_id: str, request: WorkspaceMetadataUpdate) -> SchemiiWorkspace: ...

    def find_by_target(
        self,
        owner_id: str,
        connection_id: str,
        database: str,
        namespace: str,
    ) -> SchemiiWorkspace | None: ...

    def create(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap | None = None,
        expected_connection_revision: int | None = None,
    ) -> SchemiiWorkspace: ...

    def create_import(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap,
        baseline: WorkspaceImportBaseline,
        baselines: WorkspaceImportBaselineStore,
    ) -> SchemiiWorkspace: ...

    def update_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiWorkspaceLayoutUpdate,
    ) -> SchemiiWorkspace: ...

    def delete(self, owner_id: str, workspace_id: str, expected_revision: int) -> None: ...

    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...

    def dependencies_for_connection(
        self,
        owner_id: str,
        connection_id: str,
    ) -> tuple[ConnectionDependentResource, ...]: ...


class InMemoryWorkspaceRepository:
    """Ephemeral workspace adapter retained as a useful test implementation."""

    dependency_name = "schemiiWorkspaces"

    def __init__(
        self,
        *,
        max_workspaces_per_owner: int = MAX_WORKSPACES_PER_OWNER,
        max_table_positions_per_owner: int = MAX_TABLE_POSITIONS_PER_OWNER,
        max_column_display_order_entries_per_owner: int = (
            MAX_COLUMN_DISPLAY_ORDER_ENTRIES_PER_OWNER
        ),
        designs: DesignRepository | None = None,
    ) -> None:
        if (
            max_workspaces_per_owner < 1
            or max_table_positions_per_owner < 1
            or max_column_display_order_entries_per_owner < 1
        ):
            raise ValueError("workspace limits must be positive")
        self._records: dict[str, dict[str, SchemiiWorkspace]] = {}
        self._lock = threading.RLock()
        self._max_workspaces_per_owner = max_workspaces_per_owner
        self._max_table_positions_per_owner = max_table_positions_per_owner
        self._max_column_display_order_entries_per_owner = (
            max_column_display_order_entries_per_owner
        )
        self._designs = designs
        self._mutation_guard: Callable[[str, str], bool] | None = None

    def set_mutation_guard(self, guard: Callable[[str, str], bool]) -> None:
        """Install the migration authority used by process-local lifecycle changes."""

        self._mutation_guard = guard

    @contextmanager
    def execution_claim_guard(
        self,
        owner_id: str,
        workspace_id: str,
    ) -> Iterator[None]:
        """Serialize a process-local execution claim with lifecycle mutation."""

        with self._lock:
            self._record(owner_id, workspace_id)
            yield

    def list(self, owner_id: str) -> list[SchemiiWorkspace]:
        with self._lock:
            records = [
                workspace.model_copy(deep=True)
                for workspace in self._records.get(owner_id, {}).values()
            ]
        return sorted(records, key=lambda workspace: workspace.created_at)

    def get(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace:
        with self._lock:
            return self._record(owner_id, workspace_id).model_copy(deep=True)

    def find_by_target(
        self,
        owner_id: str,
        connection_id: str,
        database: str,
        namespace: str,
    ) -> SchemiiWorkspace | None:
        with self._lock:
            for workspace in self._records.get(owner_id, {}).values():
                if (
                    workspace.connection_id == connection_id
                    and workspace.database == database
                    and workspace.namespace == namespace
                ):
                    return workspace.model_copy(deep=True)
        return None

    def create(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap | None = None,
        expected_connection_revision: int | None = None,
    ) -> SchemiiWorkspace:
        del expected_connection_revision
        with self._lock:
            owner_records = self._records.setdefault(owner_id, {})
            if len(owner_records) >= self._max_workspaces_per_owner:
                raise WorkspaceLimitError("workspace", self._max_workspaces_per_owner)
            now = datetime.now(timezone.utc)
            workspace = SchemiiWorkspace(
                id=f"ws_{secrets.token_hex(16)}",
                revision=1,
                name=request.name,
                connection_id=request.connection_id,
                database=request.database,
                namespace=request.namespace,
                tables=[],
                column_orders=[],
                import_summary=(
                    bootstrap.import_summary.model_copy(deep=True)
                    if bootstrap is not None
                    else None
                ),
                created_at=now,
                updated_at=now,
            )
            if bootstrap is not None:
                if self._designs is None:
                    raise WorkspaceStorageUnavailableError(
                        "Imported workspace persistence is not configured"
                    )
                self._designs.initialize(
                    owner_id,
                    workspace.id,
                    bootstrap.content,
                    bootstrap.layout,
                )
            owner_records[workspace.id] = workspace
            return workspace.model_copy(deep=True)

    def create_import(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap,
        baseline: WorkspaceImportBaseline,
        baselines: WorkspaceImportBaselineStore,
    ) -> SchemiiWorkspace:
        """Commit every process-local import record as one observable mutation."""

        if (
            request.connection_id is None
            or request.database is None
            or request.namespace is None
        ):
            raise ValueError("Imported workspaces require a complete PostgreSQL target")
        if self._designs is None:
            raise WorkspaceStorageUnavailableError(
                "Imported workspace persistence is not configured"
            )
        with self._lock:
            owner_records = self._records.setdefault(owner_id, {})
            existing = self.find_by_target(
                owner_id,
                request.connection_id,
                request.database,
                request.namespace,
            )
            if existing is not None:
                raise WorkspaceTargetExistsError(existing)
            if len(owner_records) >= self._max_workspaces_per_owner:
                raise WorkspaceLimitError("workspace", self._max_workspaces_per_owner)
            now = datetime.now(timezone.utc)
            workspace = SchemiiWorkspace(
                id=f"ws_{secrets.token_hex(16)}",
                revision=1,
                name=request.name,
                connection_id=request.connection_id,
                database=request.database,
                namespace=request.namespace,
                tables=[],
                column_orders=[],
                import_summary=bootstrap.import_summary.model_copy(deep=True),
                created_at=now,
                updated_at=now,
            )
            owner_records[workspace.id] = workspace
            initialized = False
            try:
                self._designs.initialize(
                    owner_id,
                    workspace.id,
                    bootstrap.content,
                    bootstrap.layout,
                )
                initialized = True
                baselines.create_import_baseline(
                    metadata_cursor=None,
                    owner_id=owner_id,
                    workspace_id=workspace.id,
                    connection_id=request.connection_id,
                    database=request.database,
                    namespace=request.namespace,
                    baseline=baseline,
                )
            except Exception:
                owner_records.pop(workspace.id, None)
                if initialized:
                    discard = getattr(self._designs, "discard_initialization", None)
                    if not callable(discard):
                        raise WorkspaceStorageUnavailableError(
                            "Imported workspace rollback is not configured"
                        )
                    discard(owner_id, workspace.id)
                raise
            return workspace.model_copy(deep=True)

    def rename(self, owner_id: str, workspace_id: str, request: WorkspaceMetadataUpdate) -> SchemiiWorkspace:
        with self._lock:
            current = self._record(owner_id, workspace_id)
            if current.revision != request.expected_revision:
                raise WorkspaceConflictError(current.revision)
            updated = current.model_copy(update={
                "name": request.name,
                "revision": current.revision + 1,
                "updated_at": datetime.now(timezone.utc),
            }, deep=True)
            self._records[owner_id][workspace_id] = updated
            return updated.model_copy(deep=True)

    def update_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiWorkspaceLayoutUpdate,
    ) -> SchemiiWorkspace:
        with self._lock:
            current = self._record(owner_id, workspace_id)
            if current.revision != request.expected_revision:
                raise WorkspaceConflictError(current.revision)
            other_position_count = sum(
                len(workspace.tables)
                for candidate_id, workspace in self._records[owner_id].items()
                if candidate_id != workspace_id
            )
            if other_position_count + len(request.tables) > self._max_table_positions_per_owner:
                raise WorkspaceLimitError(
                    "table position",
                    self._max_table_positions_per_owner,
                )
            column_orders = current.column_orders
            if request.column_orders is not None:
                other_order_entries = sum(
                    sum(len(order.columns) for order in workspace.column_orders)
                    for candidate_id, workspace in self._records[owner_id].items()
                    if candidate_id != workspace_id
                )
                requested_entries = sum(
                    len(order.columns) for order in request.column_orders
                )
                if (
                    other_order_entries + requested_entries
                    > self._max_column_display_order_entries_per_owner
                ):
                    raise WorkspaceLimitError(
                        "column display order entry",
                        self._max_column_display_order_entries_per_owner,
                    )
                column_orders = [
                    TableColumnDisplayOrder.model_validate(order).model_copy(deep=True)
                    for order in request.column_orders
                ]
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "tables": [table.model_copy(deep=True) for table in request.tables],
                    "column_orders": column_orders,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            self._records[owner_id][workspace_id] = SchemiiWorkspace.model_validate(updated)
            return updated.model_copy(deep=True)

    def delete(self, owner_id: str, workspace_id: str, expected_revision: int) -> None:
        with self._lock:
            current = self._record(owner_id, workspace_id)
            if current.revision != expected_revision:
                raise WorkspaceConflictError(current.revision)
            if self._mutation_guard is not None and self._mutation_guard(
                owner_id, workspace_id
            ):
                raise WorkspaceMutationBlockedError("deleted")
            if self._designs is not None:
                discard = getattr(self._designs, "discard_initialization", None)
                if callable(discard):
                    discard(owner_id, workspace_id)
            del self._records[owner_id][workspace_id]

    def count_for_connection(self, owner_id: str, connection_id: str) -> int:
        with self._lock:
            return sum(
                workspace.connection_id == connection_id
                for workspace in self._records.get(owner_id, {}).values()
            )

    def dependencies_for_connection(
        self,
        owner_id: str,
        connection_id: str,
    ) -> tuple[ConnectionDependentResource, ...]:
        with self._lock:
            resources: list[ConnectionDependentResource] = []
            for workspace in self._records.get(owner_id, {}).values():
                if workspace.connection_id != connection_id:
                    continue
                blocked = bool(
                    self._mutation_guard is not None
                    and self._mutation_guard(owner_id, workspace.id)
                )
                resources.append(
                    ConnectionDependentResource(
                        provider=self.dependency_name,
                        kind="workspace",
                        resource_id=workspace.id,
                        revision=workspace.revision,
                        name=workspace.name,
                        target=(
                            f"{workspace.database}.{workspace.namespace}"
                            if workspace.database and workspace.namespace
                            else None
                        ),
                        deletion_blocked=blocked,
                        blocking_reason=(
                            "An active or unreconciled migration must finish first."
                            if blocked
                            else None
                        ),
                    )
                )
            return tuple(resources)

    def _record(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace:
        try:
            return self._records[owner_id][workspace_id]
        except KeyError as error:
            raise WorkspaceNotFoundError("Schemii workspace was not found") from error
