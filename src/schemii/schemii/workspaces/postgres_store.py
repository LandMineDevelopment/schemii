"""Durable metadata PostgreSQL adapter for Schemii workspaces."""

from __future__ import annotations

import secrets
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from schemii.common.metadata.users import ensure_local_metadata_user

from .models import (
    SchemiiWorkspace,
    SchemiiWorkspaceCreate,
    SchemiiWorkspaceLayoutUpdate,
    TablePosition,
)
from .store import (
    MAX_TABLE_POSITIONS_PER_OWNER,
    MAX_WORKSPACES_PER_OWNER,
    WorkspaceConflictError,
    WorkspaceLimitError,
    WorkspaceNotFoundError,
    WorkspaceRepositoryError,
    WorkspaceStorageUnavailableError,
)


class PostgresWorkspaceRepository:
    """Persist detached or targeted workspaces without copying live catalogs."""

    dependency_name = "schemiiWorkspaces"

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        max_workspaces_per_owner: int = MAX_WORKSPACES_PER_OWNER,
        max_table_positions_per_owner: int = MAX_TABLE_POSITIONS_PER_OWNER,
    ) -> None:
        if max_workspaces_per_owner < 1 or max_table_positions_per_owner < 1:
            raise ValueError("workspace limits must be positive")
        self._connection_factory = connection_factory
        self._max_workspaces_per_owner = max_workspaces_per_owner
        self._max_table_positions_per_owner = max_table_positions_per_owner

    def list(self, owner_id: str) -> list[SchemiiWorkspace]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT workspace.*,
                           target.connection_id,
                           target.database_name,
                           target.namespace
                    FROM schemii.workspaces AS workspace
                    LEFT JOIN schemii.workspace_targets AS target
                      ON target.owner_id = workspace.owner_id
                     AND target.workspace_id = workspace.id
                    WHERE workspace.owner_id = %s
                    ORDER BY workspace.created_at, workspace.id
                    """,
                    (owner_id,),
                )
                rows = cursor.fetchall()
                if not rows:
                    return []
                positions = self._owner_positions(cursor, owner_id)
                return [
                    self._workspace(row, positions.get(row["id"], []))
                    for row in rows
                ]

    def get(self, owner_id: str, workspace_id: str) -> SchemiiWorkspace:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._select_workspace(cursor, owner_id, workspace_id)
                if row is None:
                    raise WorkspaceNotFoundError("Schemii workspace was not found")
                return self._workspace(
                    row,
                    self._workspace_positions(cursor, owner_id, workspace_id),
                )

    def create(
        self,
        owner_id: str,
        request: SchemiiWorkspaceCreate,
    ) -> SchemiiWorkspace:
        workspace_id = f"ws_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                ensure_local_metadata_user(cursor, owner_id)
                cursor.execute(
                    """
                    SELECT count(*) AS workspace_count
                    FROM schemii.workspaces
                    WHERE owner_id = %s
                    """,
                    (owner_id,),
                )
                if int(cursor.fetchone()["workspace_count"]) >= self._max_workspaces_per_owner:
                    raise WorkspaceLimitError(
                        "workspace",
                        self._max_workspaces_per_owner,
                    )
                cursor.execute(
                    """
                    INSERT INTO schemii.workspaces (id, owner_id, name)
                    VALUES (%s, %s, %s)
                    RETURNING *
                    """,
                    (workspace_id, owner_id, request.name),
                )
                row = cursor.fetchone()
                if request.connection_id is not None:
                    cursor.execute(
                        """
                        INSERT INTO schemii.workspace_targets (
                            owner_id, workspace_id, connection_id,
                            database_name, namespace
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            owner_id,
                            workspace_id,
                            request.connection_id,
                            request.database,
                            request.namespace,
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO schemii.workspace_designs (workspace_id, owner_id)
                    VALUES (%s, %s)
                    """,
                    (workspace_id, owner_id),
                )
                cursor.execute(
                    """
                    INSERT INTO schemii.workspace_design_layouts (workspace_id, owner_id)
                    VALUES (%s, %s)
                    """,
                    (workspace_id, owner_id),
                )
                row.update(
                    connection_id=request.connection_id,
                    database_name=request.database,
                    namespace=request.namespace,
                )
                return self._workspace(row, [])

    def update_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiWorkspaceLayoutUpdate,
    ) -> SchemiiWorkspace:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                ensure_local_metadata_user(cursor, owner_id)
                cursor.execute(
                    """
                    SELECT *
                    FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise WorkspaceNotFoundError("Schemii workspace was not found")
                if current["revision"] != request.expected_revision:
                    raise WorkspaceConflictError(current["revision"])
                cursor.execute(
                    """
                    SELECT count(*) AS position_count
                    FROM schemii.workspace_table_positions
                    WHERE owner_id = %s AND workspace_id <> %s
                    """,
                    (owner_id, workspace_id),
                )
                other_positions = int(cursor.fetchone()["position_count"])
                if (
                    other_positions + len(request.tables)
                    > self._max_table_positions_per_owner
                ):
                    raise WorkspaceLimitError(
                        "table position",
                        self._max_table_positions_per_owner,
                    )
                cursor.execute(
                    """
                    DELETE FROM schemii.workspace_table_positions
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (owner_id, workspace_id),
                )
                if request.tables:
                    cursor.executemany(
                        """
                        INSERT INTO schemii.workspace_table_positions (
                            owner_id, workspace_id, table_name, ordinal, x, y
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                owner_id,
                                workspace_id,
                                table.name,
                                ordinal,
                                table.x,
                                table.y,
                            )
                            for ordinal, table in enumerate(request.tables)
                        ],
                    )
                cursor.execute(
                    """
                    UPDATE schemii.workspaces
                    SET revision = revision + 1,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND id = %s
                    RETURNING *
                    """,
                    (owner_id, workspace_id),
                )
                row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT connection_id, database_name, namespace
                    FROM schemii.workspace_targets
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (owner_id, workspace_id),
                )
                target = cursor.fetchone()
                row.update(
                    connection_id=target["connection_id"] if target else None,
                    database_name=target["database_name"] if target else None,
                    namespace=target["namespace"] if target else None,
                )
                return self._workspace(
                    row,
                    [table.model_copy(deep=True) for table in request.tables],
                )

    def delete(self, owner_id: str, workspace_id: str, expected_revision: int) -> None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT revision
                    FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise WorkspaceNotFoundError("Schemii workspace was not found")
                if current["revision"] != expected_revision:
                    raise WorkspaceConflictError(current["revision"])
                cursor.execute(
                    """
                    DELETE FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    """,
                    (owner_id, workspace_id),
                )

    def count_for_connection(self, owner_id: str, connection_id: str) -> int:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*) AS workspace_count
                    FROM schemii.workspace_targets
                    WHERE owner_id = %s AND connection_id = %s
                    """,
                    (owner_id, connection_id),
                )
                return int(cursor.fetchone()["workspace_count"])

    def _select_workspace(
        self,
        cursor: Any,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT workspace.*,
                   target.connection_id,
                   target.database_name,
                   target.namespace
            FROM schemii.workspaces AS workspace
            LEFT JOIN schemii.workspace_targets AS target
              ON target.owner_id = workspace.owner_id
             AND target.workspace_id = workspace.id
            WHERE workspace.owner_id = %s AND workspace.id = %s
            """,
            (owner_id, workspace_id),
        )
        return cursor.fetchone()

    def _owner_positions(
        self,
        cursor: Any,
        owner_id: str,
    ) -> dict[str, list[TablePosition]]:
        cursor.execute(
            """
            SELECT workspace_id, table_name, x, y
            FROM schemii.workspace_table_positions
            WHERE owner_id = %s
            ORDER BY workspace_id, ordinal
            """,
            (owner_id,),
        )
        grouped: dict[str, list[TablePosition]] = defaultdict(list)
        for row in cursor.fetchall():
            grouped[row["workspace_id"]].append(self._position(row))
        return grouped

    def _workspace_positions(
        self,
        cursor: Any,
        owner_id: str,
        workspace_id: str,
    ) -> list[TablePosition]:
        cursor.execute(
            """
            SELECT table_name, x, y
            FROM schemii.workspace_table_positions
            WHERE owner_id = %s AND workspace_id = %s
            ORDER BY ordinal
            """,
            (owner_id, workspace_id),
        )
        return [self._position(row) for row in cursor.fetchall()]

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise WorkspaceStorageUnavailableError(
                "Saved Schemii workspaces are temporarily unavailable"
            ) from error
        try:
            yield connection
            connection.commit()
        except WorkspaceRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise WorkspaceStorageUnavailableError(
                "Saved Schemii workspaces are temporarily unavailable"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _position(row: dict[str, Any]) -> TablePosition:
        return TablePosition(
            name=row["table_name"],
            x=float(row["x"]),
            y=float(row["y"]),
        )

    @staticmethod
    def _workspace(
        row: dict[str, Any],
        positions: list[TablePosition],
    ) -> SchemiiWorkspace:
        return SchemiiWorkspace(
            id=row["id"],
            revision=row["revision"],
            name=row["name"],
            connection_id=row["connection_id"],
            database=row["database_name"],
            namespace=row["namespace"],
            tables=positions,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
