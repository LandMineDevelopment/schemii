"""Durable PostgreSQL adapter for database-independent desired designs."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator

from .deletion_impact import validate_design_transition
from .history_retention import history_target_index, prune_postgres_history
from .models import (
    DesignHistoryBaseline,
    DesignHistoryState,
    DesignObjectPosition,
    SchemiiDesign,
    SchemiiDesignContent,
    SchemiiDesignLayout,
    SchemiiDesignLayoutContent,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)
from .store import (
    DesignConflictError,
    DesignHistoryBoundaryError,
    DesignLayoutConflictError,
    DesignMutationBlockedError,
    DesignRepositoryError,
    DesignStorageUnavailableError,
    DesignValidationError,
    DesignWorkspaceNotFoundError,
    _history_state,
    authored_content_document,
    design_fingerprint,
    design_object_ids,
    validate_design_content,
)


@dataclass(frozen=True, slots=True)
class _PostgresHistoryEntry:
    id: int
    parent_id: int | None
    source_design_revision: int
    operation_kind: str
    operation_group_id: str | None
    content: SchemiiDesignContent
    created_at: datetime


class PostgresDesignRepository:
    """Persist canonical designs, layout, and durable semantic history."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def initialize(
        self,
        owner_id: str,
        workspace_id: str,
        content: SchemiiDesignContent,
        layout: SchemiiDesignLayoutContent,
    ) -> tuple[SchemiiDesign, SchemiiDesignLayout]:
        """Replace a pristine root so an import is the workspace starting point."""

        validate_design_content(content)
        self._validate_layout(content, layout, initial=True)
        serialized = self._content_json(content)
        fingerprint = design_fingerprint(content)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                current = self._locked_design_row(cursor, owner_id, workspace_id)
                revision = int(current["revision"])
                if revision != 0:
                    raise DesignConflictError(revision)
                self._ensure_history(cursor, owner_id, workspace_id, current)
                cursor.execute(
                    """
                    UPDATE schemii.workspace_designs
                    SET revision = 1, content = %s::jsonb, fingerprint = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (serialized, fingerprint, owner_id, workspace_id),
                )
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_history_entries AS entry
                    SET source_design_revision = 1, content = %s::jsonb,
                        fingerprint = %s, created_at = clock_timestamp()
                    FROM schemii.workspace_design_history_state AS state
                    WHERE state.owner_id = %s AND state.workspace_id = %s
                      AND entry.id = state.cursor_id AND entry.parent_id IS NULL
                    """,
                    (serialized, fingerprint, owner_id, workspace_id),
                )
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_layouts
                    SET revision = 1, design_revision = 1, objects = %s::jsonb,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    RETURNING revision, design_revision, objects
                    """,
                    (self._layout_json(layout), owner_id, workspace_id),
                )
                layout_row = cursor.fetchone()
                self._remember_positions(cursor, owner_id, workspace_id, layout.objects)
                design = SchemiiDesign(
                    workspace_id=workspace_id,
                    revision=1,
                    content=content.model_copy(deep=True),
                    fingerprint=fingerprint,
                )
                return design, self._layout(workspace_id, layout_row)

    def get(self, owner_id: str, workspace_id: str) -> SchemiiDesign:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                return self._select_design(cursor, owner_id, workspace_id)

    def replace(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignReplace,
        *,
        operation_kind: str = "edit",
        expected_baseline_id: str | None = None,
    ) -> SchemiiDesign:
        validate_design_content(request.content)
        serialized = self._content_json(request.content)
        fingerprint = design_fingerprint(request.content)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                current_row = self._locked_design_row(cursor, owner_id, workspace_id)
                current_revision = int(current_row["revision"])
                if current_revision != request.expected_design_revision:
                    raise DesignConflictError(current_revision)
                self._guard_mutation(cursor, owner_id, workspace_id, operation_kind)
                current_content = SchemiiDesignContent.model_validate(
                    self._json(current_row["content"])
                )
                if (
                    operation_kind == "edit"
                    and design_fingerprint(current_content) == fingerprint
                ):
                    return SchemiiDesign(
                        workspace_id=workspace_id,
                        revision=current_revision,
                        content=current_content,
                        fingerprint=fingerprint,
                    )
                if expected_baseline_id is not None:
                    self._check_baseline(cursor, owner_id, workspace_id, expected_baseline_id)
                try:
                    validate_design_transition(current_content, request.content)
                except ValueError as error:
                    raise DesignValidationError(
                        str(error), details={"reason": "dependent_object"}
                    ) from error
                self._ensure_history(cursor, owner_id, workspace_id, current_row)
                next_revision = current_revision + 1
                cursor.execute(
                    """
                    UPDATE schemii.workspace_designs
                    SET revision = %s, content = %s::jsonb, fingerprint = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (next_revision, serialized, fingerprint, owner_id, workspace_id),
                )
                cursor.execute(
                    """
                    SELECT cursor_id FROM schemii.workspace_design_history_state
                    WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                parent_id = int(cursor.fetchone()["cursor_id"])
                cursor.execute(
                    """
                    INSERT INTO schemii.workspace_design_history_entries (
                        workspace_id, owner_id, parent_id, source_design_revision,
                        operation_kind, operation_group_id, content, fingerprint
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        workspace_id, owner_id, parent_id, next_revision,
                        operation_kind, request.history_group_id, serialized, fingerprint,
                    ),
                )
                entry_id = int(cursor.fetchone()["id"])
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_history_state
                    SET cursor_id = %s, active_tip_id = %s, updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (entry_id, entry_id, owner_id, workspace_id),
                )
                prune_postgres_history(cursor, owner_id, workspace_id)
                design = SchemiiDesign(
                    workspace_id=workspace_id,
                    revision=next_revision,
                    content=request.content.model_copy(deep=True),
                    fingerprint=fingerprint,
                )
                self._advance_layout(cursor, owner_id, workspace_id, design)
                return design

    def history_state(
        self,
        owner_id: str,
        workspace_id: str,
        baseline: DesignHistoryBaseline,
    ) -> DesignHistoryState:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                design = self._select_design(cursor, owner_id, workspace_id)
                self._ensure_history(
                    cursor,
                    owner_id,
                    workspace_id,
                    {
                        "revision": design.revision,
                        "content": authored_content_document(design.content),
                        "fingerprint": design.fingerprint,
                    },
                )
                entries, cursor_index = self._active_chain(cursor, owner_id, workspace_id)
                return _history_state(design, entries, cursor_index, baseline)

    def undo(
        self, owner_id: str, workspace_id: str, expected_design_revision: int
    ) -> SchemiiDesign:
        return self._move_history(owner_id, workspace_id, expected_design_revision, "undo")

    def redo(
        self, owner_id: str, workspace_id: str, expected_design_revision: int
    ) -> SchemiiDesign:
        return self._move_history(owner_id, workspace_id, expected_design_revision, "redo")

    def initial_content(
        self, owner_id: str, workspace_id: str
    ) -> tuple[int, SchemiiDesignContent]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                row = self._locked_design_row(cursor, owner_id, workspace_id)
                self._ensure_history(cursor, owner_id, workspace_id, row)
                entries, _ = self._active_chain(cursor, owner_id, workspace_id)
                root = entries[0]
                return root.source_design_revision, root.content.model_copy(deep=True)

    def get_layout(self, owner_id: str, workspace_id: str) -> SchemiiDesignLayout:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                cursor.execute(
                    """
                    SELECT revision, design_revision, objects
                    FROM schemii.workspace_design_layouts
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (owner_id, workspace_id),
                )
                return self._layout(workspace_id, cursor.fetchone())

    def replace_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignLayoutReplace,
    ) -> SchemiiDesignLayout:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                design_row = self._locked_design_row(cursor, owner_id, workspace_id)
                design_revision = int(design_row["revision"])
                content = SchemiiDesignContent.model_validate(self._json(design_row["content"]))
                cursor.execute(
                    """
                    SELECT revision FROM schemii.workspace_design_layouts
                    WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                current_revision = int(cursor.fetchone()["revision"])
                if (
                    current_revision != request.expected_layout_revision
                    or design_revision != request.expected_design_revision
                ):
                    raise DesignLayoutConflictError(current_revision, design_revision)
                self._validate_layout(content, request.content)
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_layouts
                    SET revision = revision + 1, design_revision = %s,
                        objects = %s::jsonb, updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    RETURNING revision, design_revision, objects
                    """,
                    (
                        design_revision, self._layout_json(request.content),
                        owner_id, workspace_id,
                    ),
                )
                row = cursor.fetchone()
                self._remember_positions(cursor, owner_id, workspace_id, request.content.objects)
                return self._layout(workspace_id, row)

    def _move_history(
        self,
        owner_id: str,
        workspace_id: str,
        expected_design_revision: int,
        action: str,
    ) -> SchemiiDesign:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                current_row = self._locked_design_row(cursor, owner_id, workspace_id)
                current_revision = int(current_row["revision"])
                if current_revision != expected_design_revision:
                    raise DesignConflictError(current_revision)
                self._guard_mutation(cursor, owner_id, workspace_id, "edit")
                self._ensure_history(cursor, owner_id, workspace_id, current_row)
                entries, cursor_index = self._active_chain(cursor, owner_id, workspace_id)
                target_index = history_target_index(entries, cursor_index, action)
                if target_index is None:
                    raise DesignHistoryBoundaryError(action)
                target = entries[target_index]
                validate_design_content(target.content)
                next_revision = current_revision + 1
                fingerprint = design_fingerprint(target.content)
                cursor.execute(
                    """
                    UPDATE schemii.workspace_designs
                    SET revision = %s, content = %s::jsonb, fingerprint = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (
                        next_revision, self._content_json(target.content), fingerprint,
                        owner_id, workspace_id,
                    ),
                )
                from_id = entries[cursor_index].id
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_history_state
                    SET cursor_id = %s, updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (target.id, owner_id, workspace_id),
                )
                cursor.execute(
                    """
                    INSERT INTO schemii.workspace_design_history_transitions (
                        workspace_id, owner_id, action, from_entry_id, to_entry_id,
                        design_revision
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (workspace_id, owner_id, action, from_id, target.id, next_revision),
                )
                prune_postgres_history(cursor, owner_id, workspace_id)
                design = SchemiiDesign(
                    workspace_id=workspace_id,
                    revision=next_revision,
                    content=target.content.model_copy(deep=True),
                    fingerprint=fingerprint,
                )
                self._advance_layout(cursor, owner_id, workspace_id, design)
                return design

    def _active_chain(
        self, cursor: Any, owner_id: str, workspace_id: str
    ) -> tuple[list[_PostgresHistoryEntry], int]:
        cursor.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT entry.*
                FROM schemii.workspace_design_history_entries AS entry
                JOIN schemii.workspace_design_history_state AS state
                  ON state.active_tip_id = entry.id
                WHERE state.owner_id = %s AND state.workspace_id = %s
                UNION ALL
                SELECT parent.*
                FROM schemii.workspace_design_history_entries AS parent
                JOIN chain ON chain.parent_id = parent.id
            )
            SELECT chain.*, state.cursor_id
            FROM chain
            CROSS JOIN schemii.workspace_design_history_state AS state
            WHERE state.owner_id = %s AND state.workspace_id = %s
            """,
            (owner_id, workspace_id, owner_id, workspace_id),
        )
        rows = cursor.fetchall()
        by_id = {int(row["id"]): self._history_entry(row) for row in rows}
        parent_ids = {entry.parent_id for entry in by_id.values() if entry.parent_id is not None}
        tip = next(entry for entry in by_id.values() if entry.id not in parent_ids)
        entries: list[_PostgresHistoryEntry] = []
        current: _PostgresHistoryEntry | None = tip
        while current is not None:
            entries.append(current)
            current = by_id.get(current.parent_id) if current.parent_id is not None else None
        entries.reverse()
        cursor_id = int(rows[0]["cursor_id"])
        return entries, next(index for index, entry in enumerate(entries) if entry.id == cursor_id)

    def _ensure_history(
        self, cursor: Any, owner_id: str, workspace_id: str, design_row: dict[str, Any]
    ) -> None:
        cursor.execute(
            """
            SELECT cursor_id FROM schemii.workspace_design_history_state
            WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
            """,
            (owner_id, workspace_id),
        )
        if cursor.fetchone() is not None:
            return
        content = SchemiiDesignContent.model_validate(self._json(design_row["content"]))
        fingerprint = design_fingerprint(content)
        cursor.execute(
            """
            INSERT INTO schemii.workspace_design_history_entries (
                workspace_id, owner_id, parent_id, source_design_revision,
                operation_kind, content, fingerprint
            ) VALUES (%s, %s, NULL, %s, 'initial', %s::jsonb, %s)
            RETURNING id
            """,
            (
                workspace_id, owner_id, int(design_row["revision"]),
                self._content_json(content), fingerprint,
            ),
        )
        entry_id = int(cursor.fetchone()["id"])
        cursor.execute(
            """
            INSERT INTO schemii.workspace_design_history_state (
                workspace_id, owner_id, cursor_id, active_tip_id
            ) VALUES (%s, %s, %s, %s)
            """,
            (workspace_id, owner_id, entry_id, entry_id),
        )

    def _advance_layout(
        self, cursor: Any, owner_id: str, workspace_id: str, design: SchemiiDesign
    ) -> None:
        cursor.execute(
            """
            SELECT revision, objects FROM schemii.workspace_design_layouts
            WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
            """,
            (owner_id, workspace_id),
        )
        row = cursor.fetchone()
        current = SchemiiDesignLayoutContent(objects=self._json(row["objects"]))
        self._remember_positions(cursor, owner_id, workspace_id, current.objects)
        allowed = design_object_ids(design.content)
        positions = {
            item.object_id: item
            for item in current.objects
            if allowed.get(item.object_id) == item.layer
        }
        cursor.execute(
            """
            SELECT object_id, layer, x, y
            FROM schemii.workspace_design_position_memory
            WHERE owner_id = %s AND workspace_id = %s
            ORDER BY updated_at, object_id
            """,
            (owner_id, workspace_id),
        )
        for memory in cursor.fetchall():
            object_id = memory["object_id"]
            if object_id not in positions and allowed.get(object_id) == memory["layer"]:
                positions[object_id] = DesignObjectPosition.model_validate(memory)
        content = SchemiiDesignLayoutContent(objects=list(positions.values()))
        cursor.execute(
            """
            UPDATE schemii.workspace_design_layouts
            SET revision = %s, design_revision = %s, objects = %s::jsonb,
                updated_at = clock_timestamp()
            WHERE owner_id = %s AND workspace_id = %s
            """,
            (
                int(row["revision"]) + 1, design.revision, self._layout_json(content),
                owner_id, workspace_id,
            ),
        )

    @staticmethod
    def _remember_positions(
        cursor: Any,
        owner_id: str,
        workspace_id: str,
        positions: list[DesignObjectPosition],
    ) -> None:
        for position in positions:
            cursor.execute(
                """
                INSERT INTO schemii.workspace_design_position_memory (
                    workspace_id, owner_id, object_id, layer, x, y
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, workspace_id, object_id) DO UPDATE
                SET layer = EXCLUDED.layer, x = EXCLUDED.x, y = EXCLUDED.y,
                    updated_at = clock_timestamp()
                """,
                (
                    workspace_id, owner_id, position.object_id, position.layer,
                    position.x, position.y,
                ),
            )

    @staticmethod
    def _guard_mutation(
        cursor: Any, owner_id: str, workspace_id: str, operation_kind: str
    ) -> None:
        if operation_kind == "checkpoint":
            return
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM schemii.migration_executions
                WHERE owner_id = %s AND workspace_id = %s
                  AND status IN ('reserved', 'applying', 'uncertain', 'reconciliation_required')
            ) AS active
            """,
            (owner_id, workspace_id),
        )
        if cursor.fetchone()["active"]:
            raise DesignMutationBlockedError()

    @staticmethod
    def _check_baseline(
        cursor: Any, owner_id: str, workspace_id: str, expected_id: str
    ) -> None:
        cursor.execute(
            """
            SELECT baseline_id FROM schemii.workspace_schema_baseline_heads
            WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
            """,
            (owner_id, workspace_id),
        )
        row = cursor.fetchone()
        current = row["baseline_id"] if row else None
        if current != expected_id:
            raise DesignValidationError(
                "The PostgreSQL synchronization baseline changed after review",
                details={"reason": "baseline_changed", "currentBaselineId": current},
            )

    @staticmethod
    def _validate_layout(
        content: SchemiiDesignContent,
        layout: SchemiiDesignLayoutContent,
        *,
        initial: bool = False,
    ) -> None:
        allowed = design_object_ids(content)
        for position in layout.objects:
            if allowed.get(position.object_id) != position.layer:
                prefix = "Initial layout" if initial else "Layout"
                raise DesignValidationError(
                    f"{prefix} positions must reference a design object on its correct layer",
                    details={"objectId": position.object_id, "layer": position.layer},
                )

    def _ensure_rows(self, cursor: Any, owner_id: str, workspace_id: str) -> None:
        cursor.execute(
            """
            INSERT INTO schemii.workspace_designs (workspace_id, owner_id)
            SELECT id, owner_id FROM schemii.workspaces
            WHERE owner_id = %s AND id = %s
            ON CONFLICT (workspace_id) DO NOTHING
            """,
            (owner_id, workspace_id),
        )
        cursor.execute(
            """
            INSERT INTO schemii.workspace_design_layouts (workspace_id, owner_id)
            SELECT id, owner_id FROM schemii.workspaces
            WHERE owner_id = %s AND id = %s
            ON CONFLICT (workspace_id) DO NOTHING
            """,
            (owner_id, workspace_id),
        )
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM schemii.workspaces WHERE owner_id = %s AND id = %s
            ) AS workspace_exists
            """,
            (owner_id, workspace_id),
        )
        if not cursor.fetchone()["workspace_exists"]:
            raise DesignWorkspaceNotFoundError("Schemii workspace was not found")

    @staticmethod
    def _locked_design_row(cursor: Any, owner_id: str, workspace_id: str) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT revision, content, fingerprint FROM schemii.workspace_designs
            WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
            """,
            (owner_id, workspace_id),
        )
        return cursor.fetchone()

    def _select_design(self, cursor: Any, owner_id: str, workspace_id: str) -> SchemiiDesign:
        cursor.execute(
            """
            SELECT revision, content, fingerprint FROM schemii.workspace_designs
            WHERE owner_id = %s AND workspace_id = %s
            """,
            (owner_id, workspace_id),
        )
        row = cursor.fetchone()
        content = SchemiiDesignContent.model_validate(self._json(row["content"]))
        return SchemiiDesign(
            workspace_id=workspace_id,
            revision=int(row["revision"]),
            content=content,
            fingerprint=design_fingerprint(content),
        )

    @staticmethod
    def _history_entry(row: dict[str, Any]) -> _PostgresHistoryEntry:
        return _PostgresHistoryEntry(
            id=int(row["id"]),
            parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
            source_design_revision=int(row["source_design_revision"]),
            operation_kind=row["operation_kind"],
            operation_group_id=row["operation_group_id"],
            content=SchemiiDesignContent.model_validate(PostgresDesignRepository._json(row["content"])),
            created_at=row["created_at"],
        )

    @staticmethod
    def _layout(workspace_id: str, row: dict[str, Any]) -> SchemiiDesignLayout:
        return SchemiiDesignLayout(
            workspace_id=workspace_id,
            revision=int(row["revision"]),
            design_revision=int(row["design_revision"]),
            content=SchemiiDesignLayoutContent(
                objects=PostgresDesignRepository._json(row["objects"])
            ),
        )

    @staticmethod
    def _content_json(content: SchemiiDesignContent) -> str:
        return json.dumps(
            authored_content_document(content),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _layout_json(content: SchemiiDesignLayoutContent) -> str:
        return json.dumps(
            content.model_dump(mode="json")["objects"],
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise DesignStorageUnavailableError(
                "Saved Schemii designs are temporarily unavailable"
            ) from error
        try:
            yield connection
            connection.commit()
        except DesignRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise DesignStorageUnavailableError(
                "Saved Schemii designs are temporarily unavailable"
            ) from error
        finally:
            connection.close()
