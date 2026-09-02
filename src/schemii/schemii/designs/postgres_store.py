"""Durable PostgreSQL adapter for database-independent desired designs."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .models import (
    SchemiiDesign,
    SchemiiDesignContent,
    SchemiiDesignLayout,
    SchemiiDesignLayoutContent,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)
from .store import (
    DesignConflictError,
    DesignLayoutConflictError,
    DesignRepositoryError,
    DesignStorageUnavailableError,
    DesignValidationError,
    DesignWorkspaceNotFoundError,
    authored_content_document,
    design_fingerprint,
    design_object_ids,
    validate_design_content,
)


class PostgresDesignRepository:
    """Persist one canonical design and independent layout per workspace."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

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
    ) -> SchemiiDesign:
        validate_design_content(request.content)
        serialized = json.dumps(
            authored_content_document(request.content),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        fingerprint = design_fingerprint(request.content)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._ensure_rows(cursor, owner_id, workspace_id)
                cursor.execute(
                    """
                    SELECT revision
                    FROM schemii.workspace_designs
                    WHERE owner_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                current_revision = int(cursor.fetchone()["revision"])
                if current_revision != request.expected_design_revision:
                    raise DesignConflictError(current_revision)
                next_revision = current_revision + 1
                cursor.execute(
                    """
                    UPDATE schemii.workspace_designs
                    SET revision = %s,
                        content = %s::jsonb,
                        fingerprint = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (
                        next_revision,
                        serialized,
                        fingerprint,
                        owner_id,
                        workspace_id,
                    ),
                )
                cursor.execute(
                    """
                    SELECT revision, objects
                    FROM schemii.workspace_design_layouts
                    WHERE owner_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                layout_row = cursor.fetchone()
                current_layout = SchemiiDesignLayoutContent(
                    objects=self._json(layout_row["objects"]),
                )
                allowed = design_object_ids(request.content)
                retained = [
                    position
                    for position in current_layout.objects
                    if allowed.get(position.object_id) == position.layer
                ]
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_layouts
                    SET revision = revision + 1,
                        design_revision = %s,
                        objects = %s::jsonb,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (
                        next_revision,
                        json.dumps(
                            [position.model_dump(mode="json") for position in retained],
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        owner_id,
                        workspace_id,
                    ),
                )
                return SchemiiDesign(
                    workspace_id=workspace_id,
                    revision=next_revision,
                    content=request.content.model_copy(deep=True),
                    fingerprint=fingerprint,
                )

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
                cursor.execute(
                    """
                    SELECT revision, content
                    FROM schemii.workspace_designs
                    WHERE owner_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                design_row = cursor.fetchone()
                design_revision = int(design_row["revision"])
                design_content = SchemiiDesignContent.model_validate(
                    self._json(design_row["content"])
                )
                cursor.execute(
                    """
                    SELECT revision
                    FROM schemii.workspace_design_layouts
                    WHERE owner_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                current_layout_revision = int(cursor.fetchone()["revision"])
                if (
                    current_layout_revision != request.expected_layout_revision
                    or design_revision != request.expected_design_revision
                ):
                    raise DesignLayoutConflictError(
                        current_layout_revision,
                        design_revision,
                    )
                allowed = design_object_ids(design_content)
                for position in request.content.objects:
                    if allowed.get(position.object_id) != position.layer:
                        raise DesignValidationError(
                            "Layout positions must reference a design object on its correct layer",
                            details={
                                "objectId": position.object_id,
                                "layer": position.layer,
                            },
                        )
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_layouts
                    SET revision = revision + 1,
                        design_revision = %s,
                        objects = %s::jsonb,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    RETURNING revision, design_revision, objects
                    """,
                    (
                        design_revision,
                        json.dumps(
                            request.content.model_dump(mode="json")["objects"],
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        owner_id,
                        workspace_id,
                    ),
                )
                return self._layout(workspace_id, cursor.fetchone())

    def _ensure_rows(self, cursor: Any, owner_id: str, workspace_id: str) -> None:
        cursor.execute(
            """
            INSERT INTO schemii.workspace_designs (workspace_id, owner_id)
            SELECT id, owner_id
            FROM schemii.workspaces
            WHERE owner_id = %s AND id = %s
            ON CONFLICT (workspace_id) DO NOTHING
            """,
            (owner_id, workspace_id),
        )
        cursor.execute(
            """
            INSERT INTO schemii.workspace_design_layouts (workspace_id, owner_id)
            SELECT id, owner_id
            FROM schemii.workspaces
            WHERE owner_id = %s AND id = %s
            ON CONFLICT (workspace_id) DO NOTHING
            """,
            (owner_id, workspace_id),
        )
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM schemii.workspaces
                WHERE owner_id = %s AND id = %s
            ) AS workspace_exists
            """,
            (owner_id, workspace_id),
        )
        if not cursor.fetchone()["workspace_exists"]:
            raise DesignWorkspaceNotFoundError("Schemii workspace was not found")

    def _select_design(self, cursor: Any, owner_id: str, workspace_id: str) -> SchemiiDesign:
        cursor.execute(
            """
            SELECT revision, content, fingerprint
            FROM schemii.workspace_designs
            WHERE owner_id = %s AND workspace_id = %s
            """,
            (owner_id, workspace_id),
        )
        row = cursor.fetchone()
        content = SchemiiDesignContent.model_validate(self._json(row["content"]))
        return SchemiiDesign(
            workspace_id=workspace_id,
            revision=row["revision"],
            content=content,
            fingerprint=design_fingerprint(content),
        )

    @staticmethod
    def _layout(workspace_id: str, row: dict[str, Any]) -> SchemiiDesignLayout:
        return SchemiiDesignLayout(
            workspace_id=workspace_id,
            revision=row["revision"],
            design_revision=row["design_revision"],
            content=SchemiiDesignLayoutContent(
                objects=PostgresDesignRepository._json(row["objects"]),
            ),
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
