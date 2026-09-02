"""Source-derived deletion consequences for desired-schema objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

from schemii.common.postgres.query_analysis import (
    QueryDefinitionError,
    analyze_query_definition,
)

from .models import (
    DesignDeletionImpact,
    DesignDeletionImpactNode,
    SchemiiDesignContent,
)


class DesignObjectNotFoundError(LookupError):
    """The requested stable object ID is absent from the current design."""


@dataclass(frozen=True, slots=True)
class _Object:
    object_id: str
    kind: str
    name: str
    consequence: str
    severity: str
    context: str | None = None
    table_id: str | None = None
    table_name: str | None = None


@dataclass(slots=True)
class _Inventory:
    objects: dict[str, _Object]
    dependents: dict[str, set[str]]

    def add(self, item: _Object) -> None:
        self.objects[item.object_id] = item
        self.dependents.setdefault(item.object_id, set())

    def link(self, target_id: str, dependent_id: str) -> None:
        if target_id != dependent_id:
            self.dependents.setdefault(target_id, set()).add(dependent_id)


def _routine_name(value: str) -> str:
    return value.rsplit(".", 1)[-1].strip().strip('"')


def _mentions_type(value: str | None, type_name: str) -> bool:
    if not value:
        return False
    unquoted = re.sub(r'"((?:[^"]|"")+)"', lambda match: match.group(1).replace('""', '"'), value)
    return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(type_name)}(?![A-Za-z0-9_$])", unquoted, re.IGNORECASE) is not None


def _query_relations(content: SchemiiDesignContent) -> list[dict[str, object]]:
    relations: list[dict[str, object]] = [
        {
            "namespace": "desired",
            "name": table.name,
            "kind": "table",
            "columns": [
                {"name": column.name, "data_type": column.data_type}
                for column in table.columns
            ],
        }
        for table in content.tables
    ]
    relations.extend(
        {
            "namespace": "desired",
            "name": view.name,
            "kind": view.kind,
            "columns": [],
        }
        for view in content.views
    )
    return relations


def _inventory(content: SchemiiDesignContent) -> _Inventory:
    inventory = _Inventory(objects={}, dependents={})
    tables_by_id = {table.id: table for table in content.tables}
    relations_by_name: dict[str, str] = {}

    for design_type in content.types:
        inventory.add(_Object(
            design_type.id,
            "type",
            design_type.name,
            f"Applying this change removes the {design_type.kind} type contract.",
            "integrity",
            design_type.kind,
        ))

    for table in content.tables:
        relations_by_name[table.name] = table.id
        inventory.add(_Object(
            table.id,
            "table",
            table.name,
            f"Applying this change drops the table and permanently deletes data stored in its {len(table.columns)} column(s).",
            "data_loss",
            f"{len(table.columns)} columns",
            table.id,
            table.name,
        ))
        for column in table.columns:
            inventory.add(_Object(
                column.id,
                "column",
                column.name,
                f"Applying this change permanently deletes the data stored in {table.name}.{column.name}.",
                "data_loss",
                table.name,
                table.id,
                table.name,
            ))
            if len(table.columns) == 1:
                inventory.link(column.id, table.id)
        for key in table.keys:
            label = "Primary key" if key.kind == "primary" else "Unique key"
            inventory.add(_Object(
                key.id,
                "key",
                key.name,
                f"Applying this change removes {label.lower()} enforcement from {table.name}.",
                "integrity",
                f"{table.name} · {label}",
                table.id,
                table.name,
            ))
            inventory.link(table.id, key.id)
            for column_id in key.column_ids:
                inventory.link(column_id, key.id)
        for check in table.checks:
            inventory.add(_Object(
                check.id,
                "check",
                check.name,
                f"Applying this change stops PostgreSQL from enforcing this check on {table.name}.",
                "integrity",
                table.name,
                table.id,
                table.name,
            ))
            inventory.link(table.id, check.id)
            for column_id in check.column_ids:
                inventory.link(column_id, check.id)
        for index in table.indexes:
            inventory.add(_Object(
                index.id,
                "index",
                index.name,
                (
                    f"Applying this change removes uniqueness enforcement and its access path from {table.name}."
                    if index.unique
                    else f"Applying this change removes a query access path from {table.name}; affected queries may become slower."
                ),
                "integrity" if index.unique else "performance",
                table.name,
                table.id,
                table.name,
            ))
            inventory.link(table.id, index.id)
            for column_id in {
                *index.column_ids,
                *index.expression_source_column_ids,
                *index.predicate_column_ids,
            }:
                inventory.link(column_id, index.id)
        for generated in table.columns:
            for source_id in generated.generated_source_column_ids:
                inventory.link(source_id, generated.id)

    for relationship in content.relationships:
        source = tables_by_id[relationship.source_table_id]
        target = tables_by_id[relationship.target_table_id]
        inventory.add(_Object(
            relationship.id,
            "relationship",
            relationship.name,
            f"Applying this change stops PostgreSQL from enforcing the reference from {source.name} to {target.name}.",
            "integrity",
            f"{source.name} → {target.name}",
            source.id,
            source.name,
        ))
        inventory.link(source.id, relationship.id)
        for column_id in relationship.source_column_ids:
            inventory.link(column_id, relationship.id)
        for column_id in relationship.target_column_ids:
            inventory.link(column_id, relationship.id)
        target_key = next(
            (
                key
                for key in target.keys
                if key.column_ids == relationship.target_column_ids
                and key.kind in {"primary", "unique"}
            ),
            None,
        )
        inventory.link(target_key.id if target_key else target.id, relationship.id)

    for routine in content.functions:
        inventory.add(_Object(
            routine.id,
            "routine",
            f"{routine.name}({routine.identity_arguments})",
            f"Applying this change removes the callable {routine.kind} interface.",
            "behavior",
            routine.language,
        ))

    for view in content.views:
        relations_by_name[view.name] = view.id
        materialized = view.kind == "materialized_view"
        inventory.add(_Object(
            view.id,
            "view",
            view.name,
            (
                "Applying this change drops the materialized view and permanently deletes its stored result data."
                if materialized
                else "Applying this change removes the view interface from PostgreSQL."
            ),
            "data_loss" if materialized else "behavior",
            "Materialized view" if materialized else "View",
        ))

    for trigger in content.triggers:
        target_id = relations_by_name.get(trigger.relation_name)
        target = tables_by_id.get(target_id) if target_id else None
        inventory.add(_Object(
            trigger.id,
            "trigger",
            trigger.name,
            f"Applying this change stops the automatic behavior on {trigger.relation_name}.",
            "behavior",
            trigger.relation_name,
            target.id if target else None,
            target.name if target else None,
        ))
        if target_id:
            inventory.link(target_id, trigger.id)
        routine = next(
            (item for item in content.functions if item.name == _routine_name(trigger.function_name)),
            None,
        )
        if routine:
            inventory.link(routine.id, trigger.id)
        if target:
            column_ids = {
                column.id
                for column in target.columns
                if column.name in {*trigger.update_columns, *trigger.referenced_columns}
            }
            for column_id in column_ids:
                inventory.link(column_id, trigger.id)

    relation_ids = {
        **{table.name: table.id for table in content.tables},
        **{view.name: view.id for view in content.views},
    }
    column_ids = {
        (table.name, column.name): column.id
        for table in content.tables
        for column in table.columns
    }
    relations = _query_relations(content)
    for view in content.views:
        try:
            analysis = analyze_query_definition(
                view.definition,
                relations,
                current_namespace="desired",
            )
        except QueryDefinitionError:
            continue
        for source in analysis.get("sources", []):
            relation_id = relation_ids.get(str(source.get("name", "")))
            if relation_id:
                inventory.link(relation_id, view.id)
            for column in source.get("columns", []):
                column_id = column_ids.get((str(source.get("name", "")), str(column.get("name", ""))))
                if column_id:
                    inventory.link(column_id, view.id)

    for design_type in content.types:
        for table in content.tables:
            for column in table.columns:
                if _mentions_type(column.data_type, design_type.name):
                    inventory.link(design_type.id, column.id)
        for dependent_type in content.types:
            if dependent_type.id != design_type.id and _mentions_type(dependent_type.base_type, design_type.name):
                inventory.link(design_type.id, dependent_type.id)
        for routine in content.functions:
            if _mentions_type(routine.arguments, design_type.name) or _mentions_type(routine.return_type, design_type.name):
                inventory.link(design_type.id, routine.id)

    return inventory


def _node(
    inventory: _Inventory,
    object_id: str,
    trail: frozenset[str] = frozenset(),
    depth: int = 0,
) -> DesignDeletionImpactNode:
    item = inventory.objects[object_id]
    next_trail = {*trail, object_id}
    children = [
        _node(inventory, dependent_id, frozenset(next_trail), depth + 1)
        for dependent_id in sorted(
            (inventory.dependents.get(object_id, set()) - next_trail) if depth < 32 else set(),
            key=lambda value: (
                inventory.objects[value].kind,
                inventory.objects[value].name.casefold(),
            ),
        )
    ]
    return DesignDeletionImpactNode(
        object_id=item.object_id,
        kind=item.kind,
        name=item.name,
        context=item.context,
        consequence=item.consequence,
        severity=item.severity,
        table_id=item.table_id,
        table_name=item.table_name,
        children=children,
    )


def design_deletion_impact(
    content: SchemiiDesignContent,
    design_revision: int,
    object_id: str,
) -> DesignDeletionImpact:
    """Return the recursive current-design objects that block one deletion."""

    inventory = _inventory(content)
    if object_id not in inventory.objects:
        raise DesignObjectNotFoundError(object_id)
    target = _node(inventory, object_id)
    return DesignDeletionImpact(
        design_revision=design_revision,
        target=target.model_copy(update={"children": []}),
        blocked=bool(target.children),
        dependents=target.children,
    )


def validate_design_transition(
    before: SchemiiDesignContent,
    after: SchemiiDesignContent,
) -> None:
    """Reject a removal that leaves any source-derived dependent behind."""

    old = _inventory(before)
    new = _inventory(after)
    removed = set(old.objects) - set(new.objects)
    surviving = set(new.objects)
    for object_id in sorted(removed):
        blockers = old.dependents.get(object_id, set()) & surviving
        if not blockers:
            continue
        target = old.objects[object_id]
        names = ", ".join(
            f"{old.objects[item].kind} “{old.objects[item].name}”"
            for item in sorted(blockers, key=lambda value: old.objects[value].name.casefold())
        )
        raise ValueError(
            f"Delete or update {names} before deleting {target.kind} “{target.name}”."
        )
