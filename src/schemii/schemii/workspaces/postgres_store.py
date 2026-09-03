"""Durable metadata PostgreSQL adapter for Schemii workspaces."""

from __future__ import annotations

import json
import secrets
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Literal

from schemii.common.connections.store import ConnectionInUseError
from schemii.common.metadata.users import ensure_local_metadata_user
from schemii.schemii.designs.store import (
    authored_content_document,
    design_fingerprint,
    design_object_ids,
    validate_design_content,
)

from .models import (
    SchemiiWorkspace,
    WorkspaceCreateRecord,
    SchemiiWorkspaceLayoutUpdate,
    TableColumnDisplayOrder,
    TablePosition,
    WorkspaceImportIssue,
    WorkspaceImportSummary,
)
from .store import (
    MAX_COLUMN_DISPLAY_ORDER_ENTRIES_PER_OWNER,
    MAX_TABLE_POSITIONS_PER_OWNER,
    MAX_WORKSPACES_PER_OWNER,
    WorkspaceConflictError,
    WorkspaceImportBaseline,
    WorkspaceImportBaselineStore,
    WorkspaceImportTargetChangedError,
    WorkspaceLimitError,
    WorkspaceMutationBlockedError,
    WorkspaceNotFoundError,
    WorkspaceRepositoryError,
    WorkspaceStorageUnavailableError,
    WorkspaceDesignBootstrap,
)


class PostgresWorkspaceRepository:
    """Persist local and PostgreSQL-backed editable workspaces."""

    dependency_name = "schemiiWorkspaces"

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        max_workspaces_per_owner: int = MAX_WORKSPACES_PER_OWNER,
        max_table_positions_per_owner: int = MAX_TABLE_POSITIONS_PER_OWNER,
        max_column_display_order_entries_per_owner: int = (
            MAX_COLUMN_DISPLAY_ORDER_ENTRIES_PER_OWNER
        ),
    ) -> None:
        if (
            max_workspaces_per_owner < 1
            or max_table_positions_per_owner < 1
            or max_column_display_order_entries_per_owner < 1
        ):
            raise ValueError("workspace limits must be positive")
        self._connection_factory = connection_factory
        self._max_workspaces_per_owner = max_workspaces_per_owner
        self._max_table_positions_per_owner = max_table_positions_per_owner
        self._max_column_display_order_entries_per_owner = (
            max_column_display_order_entries_per_owner
        )

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
                column_orders = self._owner_column_orders(cursor, owner_id)
                imports = self._owner_import_summaries(cursor, owner_id)
                return [
                    self._workspace(
                        row,
                        positions.get(row["id"], []),
                        column_orders.get(row["id"], []),
                        imports.get(row["id"]),
                    )
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
                    self._workspace_column_orders(cursor, owner_id, workspace_id),
                    self._workspace_import_summary(cursor, owner_id, workspace_id),
                )

    def create(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap | None = None,
        expected_connection_revision: int | None = None,
    ) -> SchemiiWorkspace:
        self._validate_bootstrap(bootstrap)
        if request.connection_id is not None and expected_connection_revision is None:
            raise ValueError(
                "Targeted workspaces require the inspected connection revision"
            )
        workspace_id = f"ws_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                if request.connection_id is not None:
                    assert request.database is not None
                    assert expected_connection_revision is not None
                    self._lock_target_connection(
                        cursor,
                        owner_id,
                        request.connection_id,
                        request.database,
                        expected_connection_revision,
                    )
                return self._insert_workspace(
                    cursor,
                    owner_id,
                    workspace_id,
                    request,
                    bootstrap=bootstrap,
                )

    def create_import(
        self,
        owner_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap,
        baseline: WorkspaceImportBaseline,
        baselines: WorkspaceImportBaselineStore,
    ) -> SchemiiWorkspace:
        """Commit target validation, design state, provenance, and baseline together."""

        if (
            request.connection_id is None
            or request.database is None
            or request.namespace is None
        ):
            raise ValueError("Imported workspaces require a complete PostgreSQL target")
        self._validate_bootstrap(bootstrap)
        workspace_id = f"ws_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._lock_target_connection(
                    cursor,
                    owner_id,
                    request.connection_id,
                    request.database,
                    baseline.connection_revision,
                )
                workspace = self._insert_workspace(
                    cursor,
                    owner_id,
                    workspace_id,
                    request,
                    bootstrap=bootstrap,
                )
                baselines.create_import_baseline(
                    metadata_cursor=cursor,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    connection_id=request.connection_id,
                    database=request.database,
                    namespace=request.namespace,
                    baseline=baseline,
                )
                return workspace

    def _insert_workspace(
        self,
        cursor: Any,
        owner_id: str,
        workspace_id: str,
        request: WorkspaceCreateRecord,
        *,
        bootstrap: WorkspaceDesignBootstrap | None,
    ) -> SchemiiWorkspace:
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
            raise WorkspaceLimitError("workspace", self._max_workspaces_per_owner)
        cursor.execute(
            """
            INSERT INTO schemii.workspaces (id, owner_id, name)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (
                workspace_id,
                owner_id,
                request.name,
            ),
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
        if bootstrap is None:
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
        else:
            cursor.execute(
                """
                INSERT INTO schemii.workspace_designs (
                    workspace_id, owner_id, revision, content, fingerprint
                )
                VALUES (%s, %s, 1, %s::jsonb, %s)
                """,
                (
                    workspace_id,
                    owner_id,
                    json.dumps(
                        authored_content_document(bootstrap.content),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    design_fingerprint(bootstrap.content),
                ),
            )
            cursor.execute(
                """
                INSERT INTO schemii.workspace_design_layouts (
                    workspace_id, owner_id, revision, design_revision, objects
                )
                VALUES (%s, %s, 1, 1, %s::jsonb)
                """,
                (
                    workspace_id,
                    owner_id,
                    json.dumps(
                        bootstrap.layout.model_dump(mode="json")["objects"],
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            summary = bootstrap.import_summary
            cursor.execute(
                """
                INSERT INTO schemii.workspace_design_imports (
                    workspace_id, owner_id, catalog_fingerprint,
                    catalog_captured_at, complete, imported_objects, issues
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    workspace_id,
                    owner_id,
                    summary.catalog_fingerprint,
                    summary.catalog_captured_at,
                    summary.complete,
                    json.dumps(
                        summary.imported_objects,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    json.dumps(
                        [issue.model_dump(mode="json") for issue in summary.issues],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
        row.update(
            connection_id=request.connection_id,
            database_name=request.database,
            namespace=request.namespace,
        )
        return self._workspace(
            row,
            [],
            [],
            bootstrap.import_summary if bootstrap is not None else None,
        )

    @staticmethod
    def _validate_bootstrap(bootstrap: WorkspaceDesignBootstrap | None) -> None:
        if bootstrap is None:
            return
        validate_design_content(bootstrap.content)
        allowed = design_object_ids(bootstrap.content)
        if any(
            allowed.get(position.object_id) != position.layer
            for position in bootstrap.layout.objects
        ):
            raise WorkspaceStorageUnavailableError(
                "Imported workspace layout contains an invalid object reference"
            )

    @staticmethod
    def _lock_target_connection(
        cursor: Any,
        owner_id: str,
        connection_id: str,
        expected_database: str,
        expected_revision: int,
    ) -> None:
        cursor.execute(
            """
            SELECT revision, database_name
            FROM metadata.postgres_connections
            WHERE owner_id = %s AND id = %s
            FOR UPDATE
            """,
            (owner_id, connection_id),
        )
        current = cursor.fetchone()
        current_revision = int(current["revision"]) if current is not None else None
        current_database = current["database_name"] if current is not None else None
        if (
            current_revision != expected_revision
            or current_database != expected_database
        ):
            raise WorkspaceImportTargetChangedError(
                expected_revision=expected_revision,
                current_revision=current_revision,
                expected_database=expected_database,
                current_database=current_database,
            )

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
                if request.column_orders is not None:
                    cursor.execute(
                        """
                        SELECT count(*) AS entry_count
                        FROM schemii.workspace_table_column_orders
                        WHERE owner_id = %s AND workspace_id <> %s
                        """,
                        (owner_id, workspace_id),
                    )
                    other_order_entries = int(cursor.fetchone()["entry_count"])
                    requested_order_entries = sum(
                        len(order.columns) for order in request.column_orders
                    )
                    if (
                        other_order_entries + requested_order_entries
                        > self._max_column_display_order_entries_per_owner
                    ):
                        raise WorkspaceLimitError(
                            "column display order entry",
                            self._max_column_display_order_entries_per_owner,
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
                if request.column_orders is not None:
                    cursor.execute(
                        """
                        DELETE FROM schemii.workspace_table_column_orders
                        WHERE owner_id = %s AND workspace_id = %s
                        """,
                        (owner_id, workspace_id),
                    )
                    order_rows = [
                        (
                            owner_id,
                            workspace_id,
                            order.name,
                            column_name,
                            ordinal,
                        )
                        for order in request.column_orders
                        for ordinal, column_name in enumerate(order.columns)
                    ]
                    if order_rows:
                        cursor.executemany(
                            """
                            INSERT INTO schemii.workspace_table_column_orders (
                                owner_id, workspace_id, table_name,
                                column_name, ordinal
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            order_rows,
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
                    self._workspace_column_orders(cursor, owner_id, workspace_id),
                    self._workspace_import_summary(cursor, owner_id, workspace_id),
                )

    def delete(self, owner_id: str, workspace_id: str, expected_revision: int) -> None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                current = self._lock_lifecycle_mutation(
                    cursor,
                    owner_id,
                    workspace_id,
                    operation="deleted",
                    expected_revision=expected_revision,
                )
                cursor.execute(
                    """
                    DELETE FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    """,
                    (owner_id, workspace_id),
                )

    @staticmethod
    def _lock_lifecycle_mutation(
        cursor: Any,
        owner_id: str,
        workspace_id: str,
        *,
        operation: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Serialize lifecycle changes with claims and reject durable active work."""

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
            SELECT EXISTS (
                SELECT 1
                FROM schemii.migration_executions AS execution
                LEFT JOIN schemii.migration_syncs AS sync
                  ON sync.execution_id = execution.id
                WHERE execution.owner_id = %s
                  AND execution.workspace_id = %s
                  AND (
                      execution.status IN (
                          'reserved', 'applying', 'uncertain',
                          'reconciliation_required'
                      )
                      OR (
                          execution.status = 'succeeded'
                          AND execution.commit_outcome = 'committed'
                          AND sync.status IN ('pending', 'failed')
                      )
                  )
            ) AS active
            """,
            (owner_id, workspace_id),
        )
        if bool(cursor.fetchone()["active"]):
            raise WorkspaceMutationBlockedError(operation)
        return current

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

    @staticmethod
    def guard_connection_mutation(
        cursor: Any,
        owner_id: str,
        connection_id: str,
        operation: Literal["update", "delete"],
    ) -> None:
        """Join a connection mutation transaction to the execution lifecycle lock."""

        cursor.execute(
            """
            SELECT workspace.id
            FROM schemii.workspace_targets AS target
            JOIN schemii.workspaces AS workspace
              ON workspace.owner_id = target.owner_id
             AND workspace.id = target.workspace_id
            WHERE target.owner_id = %s AND target.connection_id = %s
            ORDER BY workspace.id
            FOR UPDATE OF workspace
            """,
            (owner_id, connection_id),
        )
        workspace_count = len(cursor.fetchall())
        if operation == "delete" and workspace_count:
            raise ConnectionInUseError(
                {PostgresWorkspaceRepository.dependency_name: workspace_count}
            )
        cursor.execute(
            """
            SELECT count(DISTINCT execution.workspace_id) AS active_count
            FROM schemii.migration_executions AS execution
            JOIN schemii.workspace_targets AS target
              ON target.owner_id = execution.owner_id
             AND target.workspace_id = execution.workspace_id
            LEFT JOIN schemii.migration_syncs AS sync
              ON sync.execution_id = execution.id
            WHERE target.owner_id = %s AND target.connection_id = %s
              AND (
                  execution.status IN (
                      'reserved', 'applying', 'uncertain',
                      'reconciliation_required'
                  )
                  OR (
                      execution.status = 'succeeded'
                      AND execution.commit_outcome = 'committed'
                      AND sync.status IN ('pending', 'failed')
                  )
              )
            """,
            (owner_id, connection_id),
        )
        active_count = int(cursor.fetchone()["active_count"])
        if active_count:
            raise ConnectionInUseError(
                {PostgresWorkspaceRepository.dependency_name: active_count}
            )

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

    def _owner_column_orders(
        self,
        cursor: Any,
        owner_id: str,
    ) -> dict[str, list[TableColumnDisplayOrder]]:
        cursor.execute(
            """
            SELECT workspace_id, table_name, column_name, ordinal
            FROM schemii.workspace_table_column_orders
            WHERE owner_id = %s
            ORDER BY workspace_id, table_name, ordinal
            """,
            (owner_id,),
        )
        grouped: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in cursor.fetchall():
            grouped[row["workspace_id"]][row["table_name"]].append(
                row["column_name"]
            )
        return {
            workspace_id: [
                TableColumnDisplayOrder(name=table_name, columns=columns)
                for table_name, columns in tables.items()
            ]
            for workspace_id, tables in grouped.items()
        }

    def _workspace_column_orders(
        self,
        cursor: Any,
        owner_id: str,
        workspace_id: str,
    ) -> list[TableColumnDisplayOrder]:
        cursor.execute(
            """
            SELECT table_name, column_name, ordinal
            FROM schemii.workspace_table_column_orders
            WHERE owner_id = %s AND workspace_id = %s
            ORDER BY table_name, ordinal
            """,
            (owner_id, workspace_id),
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in cursor.fetchall():
            grouped[row["table_name"]].append(row["column_name"])
        return [
            TableColumnDisplayOrder(name=table_name, columns=columns)
            for table_name, columns in grouped.items()
        ]

    def _owner_import_summaries(
        self,
        cursor: Any,
        owner_id: str,
    ) -> dict[str, WorkspaceImportSummary]:
        cursor.execute(
            """
            SELECT workspace_id, catalog_fingerprint, catalog_captured_at,
                   complete, imported_objects, issues
            FROM schemii.workspace_design_imports
            WHERE owner_id = %s
            ORDER BY workspace_id
            """,
            (owner_id,),
        )
        return {
            row["workspace_id"]: self._import_summary(row)
            for row in cursor.fetchall()
        }

    def _workspace_import_summary(
        self,
        cursor: Any,
        owner_id: str,
        workspace_id: str,
    ) -> WorkspaceImportSummary | None:
        cursor.execute(
            """
            SELECT catalog_fingerprint, catalog_captured_at,
                   complete, imported_objects, issues
            FROM schemii.workspace_design_imports
            WHERE owner_id = %s AND workspace_id = %s
            """,
            (owner_id, workspace_id),
        )
        row = cursor.fetchone()
        return self._import_summary(row) if row is not None else None

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
        column_orders: list[TableColumnDisplayOrder],
        import_summary: WorkspaceImportSummary | None,
    ) -> SchemiiWorkspace:
        return SchemiiWorkspace(
            id=row["id"],
            revision=row["revision"],
            name=row["name"],
            connection_id=row["connection_id"],
            database=row["database_name"],
            namespace=row["namespace"],
            tables=positions,
            column_orders=column_orders,
            import_summary=import_summary,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _import_summary(row: dict[str, Any]) -> WorkspaceImportSummary:
        imported_objects = row["imported_objects"]
        issues = row["issues"]
        if isinstance(imported_objects, str):
            imported_objects = json.loads(imported_objects)
        if isinstance(issues, str):
            issues = json.loads(issues)
        return WorkspaceImportSummary(
            catalog_fingerprint=row["catalog_fingerprint"],
            catalog_captured_at=row["catalog_captured_at"],
            complete=row["complete"],
            imported_objects=imported_objects,
            issues=[WorkspaceImportIssue.model_validate(issue) for issue in issues],
        )
