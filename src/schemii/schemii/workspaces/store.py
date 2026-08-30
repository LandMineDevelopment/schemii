"""Owner-scoped Schemii workspace repository and prototype implementation."""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .models import (
    SchemiiWorkspace,
    SchemiiWorkspaceCreate,
    SchemiiWorkspaceLayoutUpdate,
)

MAX_WORKSPACES_PER_OWNER = 1_000
MAX_TABLE_POSITIONS_PER_OWNER = 100_000


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


@runtime_checkable
class WorkspaceRepository(Protocol):
    dependency_name: str

    def list(self, owner_id: str) -> list[SchemiiWorkspace]: ...

    def get(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace: ...

    def create(
        self, owner_id: str, request: SchemiiWorkspaceCreate
    ) -> SchemiiWorkspace: ...

    def update_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiWorkspaceLayoutUpdate,
    ) -> SchemiiWorkspace: ...

    def delete(self, owner_id: str, workspace_id: str, expected_revision: int) -> None: ...

    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...


class InMemoryWorkspaceRepository:
    """Ephemeral workspace adapter retained as a useful test implementation."""

    dependency_name = "schemiiWorkspaces"

    def __init__(
        self,
        *,
        max_workspaces_per_owner: int = MAX_WORKSPACES_PER_OWNER,
        max_table_positions_per_owner: int = MAX_TABLE_POSITIONS_PER_OWNER,
    ) -> None:
        if max_workspaces_per_owner < 1 or max_table_positions_per_owner < 1:
            raise ValueError("workspace limits must be positive")
        self._records: dict[str, dict[str, SchemiiWorkspace]] = {}
        self._lock = threading.RLock()
        self._max_workspaces_per_owner = max_workspaces_per_owner
        self._max_table_positions_per_owner = max_table_positions_per_owner

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

    def create(
        self, owner_id: str, request: SchemiiWorkspaceCreate
    ) -> SchemiiWorkspace:
        with self._lock:
            owner_records = self._records.setdefault(owner_id, {})
            if len(owner_records) >= self._max_workspaces_per_owner:
                raise WorkspaceLimitError("workspace", self._max_workspaces_per_owner)
            now = datetime.now(timezone.utc)
            workspace = SchemiiWorkspace(
                id=f"ws_{secrets.token_hex(16)}",
                revision=1,
                connection_id=request.connection_id,
                database=request.database,
                namespace=request.namespace,
                tables=[],
                created_at=now,
                updated_at=now,
            )
            owner_records[workspace.id] = workspace
            return workspace.model_copy(deep=True)

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
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "tables": [table.model_copy(deep=True) for table in request.tables],
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
            del self._records[owner_id][workspace_id]

    def count_for_connection(self, owner_id: str, connection_id: str) -> int:
        with self._lock:
            return sum(
                workspace.connection_id == connection_id
                for workspace in self._records.get(owner_id, {}).values()
            )

    def _record(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace:
        try:
            return self._records[owner_id][workspace_id]
        except KeyError as error:
            raise WorkspaceNotFoundError("Schemii workspace was not found") from error
