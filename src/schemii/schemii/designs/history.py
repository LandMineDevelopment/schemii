"""Server-derived semantic summaries for durable design history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import DesignChangeItem, DesignChangeSummary, SchemiiDesignContent


@dataclass(frozen=True, slots=True)
class _HistoryObject:
    kind: str
    name: str
    value: Any


def _objects(content: SchemiiDesignContent) -> dict[str, _HistoryObject]:
    objects: dict[str, _HistoryObject] = {}
    for design_type in content.types:
        objects[design_type.id] = _HistoryObject(
            "type", design_type.name, design_type.model_dump(mode="json")
        )
    for table in content.tables:
        objects[table.id] = _HistoryObject(
            "table",
            table.name,
            {
                "name": table.name,
                "columnOrder": [column.id for column in table.columns],
                "keyOrder": [key.id for key in table.keys],
                "checkOrder": [check.id for check in table.checks],
                "indexOrder": [index.id for index in table.indexes],
            },
        )
        for column in table.columns:
            objects[column.id] = _HistoryObject(
                "column", f"{table.name}.{column.name}", column.model_dump(mode="json")
            )
        for key in table.keys:
            objects[key.id] = _HistoryObject(
                "key", key.name, key.model_dump(mode="json")
            )
        for check in table.checks:
            objects[check.id] = _HistoryObject(
                "check", check.name, check.model_dump(mode="json")
            )
        for index in table.indexes:
            objects[index.id] = _HistoryObject(
                "index", index.name, index.model_dump(mode="json")
            )
    for relationship in content.relationships:
        objects[relationship.id] = _HistoryObject(
            "relationship", relationship.name, relationship.model_dump(mode="json")
        )
    for routine in content.functions:
        objects[routine.id] = _HistoryObject(
            "routine",
            f"{routine.name}({routine.identity_arguments})",
            routine.model_dump(mode="json"),
        )
    for view in content.views:
        objects[view.id] = _HistoryObject(
            "view", view.name, view.model_dump(mode="json")
        )
    for trigger in content.triggers:
        objects[trigger.id] = _HistoryObject(
            "trigger", trigger.name, trigger.model_dump(mode="json")
        )
    return objects


def design_change_summary(
    before: SchemiiDesignContent,
    after: SchemiiDesignContent,
) -> DesignChangeSummary:
    """Describe a complete state transition using only stable source objects."""

    old = _objects(before)
    new = _objects(after)
    changes: list[DesignChangeItem] = []
    for object_id in sorted(old.keys() - new.keys(), key=lambda value: (old[value].kind, old[value].name.casefold())):
        item = old[object_id]
        changes.append(DesignChangeItem(operation="removed", kind=item.kind, name=item.name))
    for object_id in sorted(new.keys() - old.keys(), key=lambda value: (new[value].kind, new[value].name.casefold())):
        item = new[object_id]
        changes.append(DesignChangeItem(operation="added", kind=item.kind, name=item.name))
    for object_id in sorted(old.keys() & new.keys(), key=lambda value: (new[value].kind, new[value].name.casefold())):
        previous = old[object_id]
        current = new[object_id]
        if previous.value == current.value and previous.name == current.name:
            continue
        if previous.name != current.name:
            changes.append(
                DesignChangeItem(
                    operation="renamed",
                    kind=current.kind,
                    name=current.name,
                    previous_name=previous.name,
                )
            )
        else:
            changes.append(DesignChangeItem(operation="changed", kind=current.kind, name=current.name))

    if not changes:
        return DesignChangeSummary(title="No schema changes", change_count=0, changes=[])
    first = changes[0]
    verbs = {
        "added": "Create",
        "removed": "Delete",
        "changed": "Update",
        "renamed": "Rename",
    }
    if len(changes) == 1:
        if first.operation == "renamed":
            title = f"Rename {first.kind} {first.previous_name} to {first.name}"
        else:
            title = f"{verbs[first.operation]} {first.kind} {first.name}"
    else:
        title = f"{verbs[first.operation]} {first.kind} {first.name} and {len(changes) - 1} related change(s)"
    return DesignChangeSummary(title=title, change_count=len(changes), changes=changes)
