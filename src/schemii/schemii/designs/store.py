"""Validated owner-scoped desired-design persistence boundary."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from typing import Any, Protocol, runtime_checkable

from schemii.common.errors import MetadataStorageUnavailableError

from .models import (
    SchemiiDesign,
    SchemiiDesignContent,
    SchemiiDesignLayout,
    SchemiiDesignLayoutContent,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)


EMPTY_DESIGN_CONTENT = SchemiiDesignContent()


class DesignRepositoryError(RuntimeError):
    """Base error for desired-design persistence."""


class DesignConflictError(DesignRepositoryError):
    """Semantic desired state changed after it was read."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("The saved schema design changed in another request")


class DesignWorkspaceNotFoundError(DesignRepositoryError):
    """The owner-scoped workspace for this design no longer exists."""


class DesignLayoutConflictError(DesignRepositoryError):
    """Visual state or its semantic design changed after it was read."""

    def __init__(self, current_layout_revision: int, current_design_revision: int) -> None:
        self.current_layout_revision = current_layout_revision
        self.current_design_revision = current_design_revision
        super().__init__("The saved schema layout changed in another request")


class DesignValidationError(DesignRepositoryError):
    """Desired state contains semantically inconsistent references."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


class DesignStorageUnavailableError(
    DesignRepositoryError,
    MetadataStorageUnavailableError,
):
    """Durable desired-design metadata cannot currently be used."""


def canonical_content(content: SchemiiDesignContent) -> str:
    """Return the one stable representation used for storage fingerprints."""

    return json.dumps(
        content.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def design_fingerprint(content: SchemiiDesignContent) -> str:
    return hashlib.sha256(canonical_content(content).encode("utf-8")).hexdigest()


def _valid_identifier(value: str, *, category: str) -> None:
    if "\x00" in value or len(value.encode("utf-8")) > 63:
        raise DesignValidationError(
            f"{category} must be a valid PostgreSQL identifier",
            details={"category": category, "name": value},
        )


def _unique(values: list[str], *, category: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise DesignValidationError(
            f"{category} must be unique",
            details={"category": category, "values": duplicates},
        )


def _safe_fragment(value: str, *, category: str) -> None:
    if (
        "\x00" in value
        or ";" in value
        or "--" in value
        or "/*" in value
        or "*/" in value
    ):
        raise DesignValidationError(
            f"{category} contains unsupported SQL statement boundaries",
            details={"category": category},
        )


def validate_design_content(content: SchemiiDesignContent) -> None:
    """Validate cross-object invariants Pydantic cannot express locally."""

    all_ids: list[str] = []
    relation_names = [table.name for table in content.tables] + [
        view.name for view in content.views
    ]
    _unique(relation_names, category="table and view names")

    table_by_id = {table.id: table for table in content.tables}
    for table in content.tables:
        _valid_identifier(table.name, category="table name")
        all_ids.append(table.id)
        _unique([column.name for column in table.columns], category=f"columns in {table.name}")
        column_ids = {column.id for column in table.columns}
        for column in table.columns:
            _valid_identifier(column.name, category="column name")
            _safe_fragment(column.data_type, category=f"data type for {table.name}.{column.name}")
            value_generators = sum(
                value is not None
                for value in (
                    column.default_expression,
                    column.identity,
                    column.generated_expression,
                )
            )
            if value_generators > 1:
                raise DesignValidationError(
                    "Columns may have only one default, identity, or generated value behavior",
                    details={"table": table.name, "column": column.name},
                )
            if column.identity is not None and column.nullable:
                raise DesignValidationError(
                    "Identity columns must be non-nullable",
                    details={"table": table.name, "column": column.name},
                )
            if column.default_expression is not None:
                if not column.default_expression.strip():
                    raise DesignValidationError(
                        "Default expressions cannot be empty",
                        details={"table": table.name, "column": column.name},
                    )
                _safe_fragment(
                    column.default_expression,
                    category=f"default for {table.name}.{column.name}",
                )
            if column.generated_expression is not None:
                if not column.generated_expression.strip():
                    raise DesignValidationError(
                        "Generated expressions cannot be empty",
                        details={"table": table.name, "column": column.name},
                    )
                _safe_fragment(
                    column.generated_expression,
                    category=f"generated expression for {table.name}.{column.name}",
                )
                if (
                    len(column.generated_source_column_ids)
                    != len(set(column.generated_source_column_ids))
                    or not set(column.generated_source_column_ids) <= column_ids
                    or column.id in column.generated_source_column_ids
                ):
                    raise DesignValidationError(
                        "Generated columns may reference unique sibling columns only",
                        details={"table": table.name, "column": column.name},
                    )
            elif column.generated_source_column_ids:
                raise DesignValidationError(
                    "Generated column dependencies require a generated expression",
                    details={"table": table.name, "column": column.name},
                )
            all_ids.append(column.id)
        _unique(
            [constraint.name for constraint in [*table.keys, *table.checks]],
            category=f"constraints in {table.name}",
        )
        if sum(key.kind == "primary" for key in table.keys) > 1:
            raise DesignValidationError(
                "A table may have only one primary key",
                details={"table": table.name},
            )
        for key in table.keys:
            _valid_identifier(key.name, category="key constraint name")
            if len(key.column_ids) != len(set(key.column_ids)) or not set(key.column_ids) <= column_ids:
                raise DesignValidationError(
                    "Key constraints must reference unique columns on their own table",
                    details={"table": table.name, "constraint": key.name},
                )
            all_ids.append(key.id)
        for check in table.checks:
            _valid_identifier(check.name, category="check constraint name")
            if not check.expression.strip():
                raise DesignValidationError(
                    "Check constraint expressions cannot be empty",
                    details={"table": table.name, "constraint": check.name},
                )
            _safe_fragment(check.expression, category=f"check constraint {check.name}")
            if (
                len(check.column_ids) != len(set(check.column_ids))
                or not set(check.column_ids) <= column_ids
            ):
                raise DesignValidationError(
                    "Check constraints may reference unique columns on their own table only",
                    details={"table": table.name, "constraint": check.name},
                )
            all_ids.append(check.id)
        _unique([index.name for index in table.indexes], category=f"indexes in {table.name}")
        for index in table.indexes:
            _valid_identifier(index.name, category="index name")
            _valid_identifier(index.method, category="index method")
            if not index.column_ids and not index.expression:
                raise DesignValidationError(
                    "An index requires columns or an expression",
                    details={"table": table.name, "index": index.name},
                )
            if len(index.column_ids) != len(set(index.column_ids)) or not set(index.column_ids) <= column_ids:
                raise DesignValidationError(
                    "Indexes may reference unique columns on their own table only",
                    details={"table": table.name, "index": index.name},
                )
            if index.expression is not None:
                if not index.expression.strip():
                    raise DesignValidationError(
                        "Index expressions cannot be empty",
                        details={"table": table.name, "index": index.name},
                    )
                _safe_fragment(index.expression, category=f"index expression {index.name}")
                if (
                    len(index.expression_source_column_ids)
                    != len(set(index.expression_source_column_ids))
                    or not set(index.expression_source_column_ids) <= column_ids
                ):
                    raise DesignValidationError(
                        "Index expressions may reference unique columns on their own table only",
                        details={"table": table.name, "index": index.name},
                    )
            elif index.expression_source_column_ids:
                raise DesignValidationError(
                    "Index expression dependencies require an index expression",
                    details={"table": table.name, "index": index.name},
                )
            if index.predicate is not None:
                if not index.predicate.strip():
                    raise DesignValidationError(
                        "Index predicates cannot be empty",
                        details={"table": table.name, "index": index.name},
                    )
                _safe_fragment(index.predicate, category=f"index predicate {index.name}")
                if (
                    len(index.predicate_column_ids)
                    != len(set(index.predicate_column_ids))
                    or not set(index.predicate_column_ids) <= column_ids
                ):
                    raise DesignValidationError(
                        "Index predicates may reference unique columns on their own table only",
                        details={"table": table.name, "index": index.name},
                    )
            elif index.predicate_column_ids:
                raise DesignValidationError(
                    "Index predicate dependencies require an index predicate",
                    details={"table": table.name, "index": index.name},
                )
            all_ids.append(index.id)

    _unique([relationship.name for relationship in content.relationships], category="relationship names")
    for relationship in content.relationships:
        source = table_by_id.get(relationship.source_table_id)
        target = table_by_id.get(relationship.target_table_id)
        if source is None or target is None:
            raise DesignValidationError(
                "Relationships must reference tables in this design",
                details={"relationship": relationship.name},
            )
        source_ids = {column.id for column in source.columns}
        target_ids = {column.id for column in target.columns}
        if (
            len(relationship.source_column_ids) != len(relationship.target_column_ids)
            or len(relationship.source_column_ids) != len(set(relationship.source_column_ids))
            or len(relationship.target_column_ids) != len(set(relationship.target_column_ids))
            or not set(relationship.source_column_ids) <= source_ids
            or not set(relationship.target_column_ids) <= target_ids
        ):
            raise DesignValidationError(
                "Relationship columns must be unique, paired, and owned by their referenced tables",
                details={"relationship": relationship.name},
            )
        target_keys = {
            tuple(key.column_ids)
            for key in target.keys
            if key.kind in {"primary", "unique"}
        }
        if tuple(relationship.target_column_ids) not in target_keys:
            raise DesignValidationError(
                "Relationship targets must match a primary or unique key",
                details={"relationship": relationship.name, "targetTable": target.name},
            )
        if relationship.initially_deferred and not relationship.deferrable:
            raise DesignValidationError(
                "An initially deferred relationship must be deferrable",
                details={"relationship": relationship.name},
            )
        _valid_identifier(relationship.name, category="relationship name")
        all_ids.append(relationship.id)

    for routine in content.functions:
        _valid_identifier(routine.name, category="routine name")
        _valid_identifier(routine.language, category="routine language")
        all_ids.append(routine.id)
    for view in content.views:
        _valid_identifier(view.name, category="view name")
        all_ids.append(view.id)
    _unique(all_ids, category="design object IDs")


def design_object_ids(content: SchemiiDesignContent) -> dict[str, str]:
    ids = {table.id: "tables" for table in content.tables}
    ids.update({view.id: "views" for view in content.views})
    return ids


@runtime_checkable
class DesignRepository(Protocol):
    def get(self, owner_id: str, workspace_id: str) -> SchemiiDesign: ...

    def replace(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignReplace,
    ) -> SchemiiDesign: ...

    def get_layout(self, owner_id: str, workspace_id: str) -> SchemiiDesignLayout: ...

    def replace_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignLayoutReplace,
    ) -> SchemiiDesignLayout: ...


class InMemoryDesignRepository:
    """Thread-safe desired-design adapter for isolated application tests."""

    def __init__(self) -> None:
        self._designs: dict[tuple[str, str], SchemiiDesign] = {}
        self._layouts: dict[tuple[str, str], SchemiiDesignLayout] = {}
        self._lock = threading.RLock()

    def get(self, owner_id: str, workspace_id: str) -> SchemiiDesign:
        with self._lock:
            return self._design(owner_id, workspace_id).model_copy(deep=True)

    def replace(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignReplace,
    ) -> SchemiiDesign:
        validate_design_content(request.content)
        with self._lock:
            current = self._design(owner_id, workspace_id)
            if current.revision != request.expected_design_revision:
                raise DesignConflictError(current.revision)
            revised = SchemiiDesign(
                workspace_id=workspace_id,
                revision=current.revision + 1,
                content=request.content.model_copy(deep=True),
                fingerprint=design_fingerprint(request.content),
            )
            self._designs[(owner_id, workspace_id)] = revised
            self._advance_layout(owner_id, workspace_id, revised)
            return revised.model_copy(deep=True)

    def get_layout(self, owner_id: str, workspace_id: str) -> SchemiiDesignLayout:
        with self._lock:
            design = self._design(owner_id, workspace_id)
            return self._layout(owner_id, workspace_id, design.revision).model_copy(deep=True)

    def replace_layout(
        self,
        owner_id: str,
        workspace_id: str,
        request: SchemiiDesignLayoutReplace,
    ) -> SchemiiDesignLayout:
        with self._lock:
            design = self._design(owner_id, workspace_id)
            current = self._layout(owner_id, workspace_id, design.revision)
            if (
                current.revision != request.expected_layout_revision
                or design.revision != request.expected_design_revision
            ):
                raise DesignLayoutConflictError(current.revision, design.revision)
            allowed = design_object_ids(design.content)
            for position in request.content.objects:
                if allowed.get(position.object_id) != position.layer:
                    raise DesignValidationError(
                        "Layout positions must reference a design object on its correct layer",
                        details={"objectId": position.object_id, "layer": position.layer},
                    )
            revised = SchemiiDesignLayout(
                workspace_id=workspace_id,
                revision=current.revision + 1,
                design_revision=design.revision,
                content=request.content.model_copy(deep=True),
            )
            self._layouts[(owner_id, workspace_id)] = revised
            return revised.model_copy(deep=True)

    def _design(self, owner_id: str, workspace_id: str) -> SchemiiDesign:
        return self._designs.get((owner_id, workspace_id)) or SchemiiDesign(
            workspace_id=workspace_id,
            revision=0,
            content=EMPTY_DESIGN_CONTENT.model_copy(deep=True),
            fingerprint=design_fingerprint(EMPTY_DESIGN_CONTENT),
        )

    def _layout(
        self,
        owner_id: str,
        workspace_id: str,
        design_revision: int,
    ) -> SchemiiDesignLayout:
        return self._layouts.get((owner_id, workspace_id)) or SchemiiDesignLayout(
            workspace_id=workspace_id,
            revision=0,
            design_revision=design_revision,
            content=SchemiiDesignLayoutContent(),
        )

    def _advance_layout(
        self,
        owner_id: str,
        workspace_id: str,
        design: SchemiiDesign,
    ) -> None:
        current = self._layout(owner_id, workspace_id, design.revision - 1)
        allowed = design_object_ids(design.content)
        objects = [
            position
            for position in current.content.objects
            if allowed.get(position.object_id) == position.layer
        ]
        self._layouts[(owner_id, workspace_id)] = SchemiiDesignLayout(
            workspace_id=workspace_id,
            revision=current.revision + 1,
            design_revision=design.revision,
            content=SchemiiDesignLayoutContent(objects=objects),
        )
