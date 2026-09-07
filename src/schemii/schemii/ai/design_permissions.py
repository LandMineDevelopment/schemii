"""Derive design authority from actual effects, independent of tool syntax.

A whole-table replacement has exactly the same permissions as individual edits.
Inputs are validated design contents (or their snake-case ``model_dump`` values),
not untrusted model arguments. This boundary deliberately does not import tools.
"""

from collections.abc import Mapping
from typing import Any


TOP_LEVEL_COLLECTIONS = ("types", "tables", "relationships", "functions", "views", "triggers")
TABLE_COLLECTIONS = ("columns", "keys", "checks", "indexes")


def _mapping(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python", by_alias=False)
    if not isinstance(value, Mapping):
        raise ValueError("Design permission comparison requires design objects.")
    return value


def _objects(content: Mapping[str, Any], collection: str) -> dict[str, Mapping[str, Any]]:
    result = {}
    for value in content.get(collection, []):
        item = _mapping(value)
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError("Design permission comparison requires unique stable object IDs.")
        result[identifier] = item
    return result


def design_permission_ids(before: Any, after: Any) -> list[str]:
    """Return sorted ``collection.create/update/delete`` effects of a design edit.

    Table-owned objects are compared within their parent: moving an object to a
    different table requires delete and create, not merely update. Table creation
    and deletion include every child effect. Ordering unrelated collections is
    immaterial; changing the relative order of surviving columns is a column
    update (the visual design order), never an implicit live database reorder.
    """
    previous, desired = _mapping(before), _mapping(after)
    for content in (previous, desired):
        if set(content) - set(TOP_LEVEL_COLLECTIONS):
            raise ValueError("Unclassified design fields cannot be authorized.")
    permissions: set[str] = set()

    def compare(collection: str, old_parent: Mapping[str, Any], new_parent: Mapping[str, Any]) -> None:
        old, new = _objects(old_parent, collection), _objects(new_parent, collection)
        if old.keys() - new.keys():
            permissions.add(f"{collection}.delete")
        if new.keys() - old.keys():
            permissions.add(f"{collection}.create")
        common = old.keys() & new.keys()
        if collection == "columns":
            if [key for key in old if key in common] != [key for key in new if key in common]:
                permissions.add("columns.update")
        for identifier in common:
            old_item, new_item = old[identifier], new[identifier]
            if collection == "tables":
                old_item = {key: value for key, value in old_item.items() if key not in TABLE_COLLECTIONS}
                new_item = {key: value for key, value in new_item.items() if key not in TABLE_COLLECTIONS}
            if old_item != new_item:
                permissions.add(f"{collection}.update")
        if collection == "tables":
            for identifier in old.keys() | new.keys():
                for child in TABLE_COLLECTIONS:
                    compare(child, old.get(identifier, {}), new.get(identifier, {}))

    for collection in TOP_LEVEL_COLLECTIONS:
        compare(collection, previous, desired)
    return sorted(permissions)
