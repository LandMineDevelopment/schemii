from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .schema_store import SchemaStoreError


COLORS = ["#f4b942", "#65a9ff", "#9b82f4", "#59c894", "#ef7c8e", "#e58d4c"]
REFERENTIAL_ACTIONS = {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}
TYPE_ALIASES = {
    "serial": "integer", "int": "integer", "int4": "integer", "bigserial": "bigint", "int8": "bigint",
    "smallserial": "smallint", "int2": "smallint", "bool": "boolean", "varchar": "character varying",
}


def apply_schema_actions(record: dict[str, Any], actions: list[dict[str, Any]], operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(actions, list) or not actions:
        raise SchemaStoreError(400, "validation_error", "Schema actions are invalid")
    changed, impact = [], []
    current = record
    for index, action in enumerate(actions):
        current, receipt = apply_schema_action(current, action, operation_id if len(actions) == 1 else f"{operation_id}:{index}")
        changed.extend(receipt["changed"])
        impact.extend(receipt["impact"])
    return current, {"actionType": actions[0]["type"] if len(actions) == 1 else "schema_batch", "changed": changed, "impact": impact}


def apply_schema_action(record: dict[str, Any], action: dict[str, Any], operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = record["schema"]
    action_type = action["type"]
    changed: list[dict[str, str]] = []
    impact: list[dict[str, str]] = []
    if action_type == "populate_schema":
        new_tables = [_new_table(item, operation_id, f"table:{index}", len(schema["tables"]) + index) for index, item in enumerate(action["tables"])]
        _place_tables(schema, new_tables)
        schema["tables"].extend(new_tables)
        changed.extend({"kind": "table", "id": table["id"]} for table in new_tables)
        for index, item in enumerate(action["relationships"]):
            relationship = _new_relationship(schema, item, operation_id, f"relationship:{index}")
            schema["relationships"].append(relationship)
            changed.append({"kind": "relationship", "id": relationship["id"]})
    elif action_type == "add_table":
        table = _new_table(action, operation_id, "table", len(schema["tables"]))
        _place_tables(schema, [table])
        schema["tables"].append(table)
        changed.append({"kind": "table", "id": table["id"]})
    elif action_type == "rename_table":
        table = _table(schema, action["tableId"])
        old_name = table["name"]
        table["name"] = action["newName"]
        _rename_table_owned_objects(schema, table, old_name, action["newName"])
        changed.append({"kind": "table", "id": table["id"]})
    elif action_type == "add_column":
        table = _table(schema, action["tableId"])
        column = _new_column({**action, "type": action["columnType"]}, operation_id, "column")
        table["columns"].append(column)
        _sync_primary_key(table, operation_id)
        changed.append({"kind": "column", "id": column["id"]})
    elif action_type == "update_column":
        table = _table(schema, action["tableId"])
        column = _column(table, action["columnId"])
        changes = action["changes"]
        if "name" in changes and changes["name"] != column["name"]:
            _require_safe_column_rename(schema, table, column)
            _rename_relationship_column(schema, table, column, changes["name"])
        column.update(changes)
        if column.get("primary"):
            column.update({"nullable": False, "unique": True})
        _sync_primary_key(table, operation_id)
        changed.append({"kind": "column", "id": column["id"]})
    elif action_type == "delete_element":
        table = _table(schema, action["tableId"])
        if action["elementType"] == "table":
            related = [item for item in schema["relationships"] if item.get("fromTableId") == table["id"] or item.get("toTableId") == table["id"]]
            impact.extend({"kind": "relationship", "id": item.get("id", "")} for item in related)
            impact.append({"kind": "table", "id": table["id"]})
            schema["relationships"] = [item for item in schema["relationships"] if item not in related]
            schema["tables"] = [item for item in schema["tables"] if item["id"] != table["id"]]
            _remove_table_layout(schema, table["id"])
        else:
            column = _column(table, action["columnId"])
            dependencies = _column_dependencies(schema, table, column)
            impact.extend(dependencies)
            object_ids = {item["id"] for item in dependencies if item["kind"] in {"check", "index", "trigger"}}
            for key in ("checks", "indexes", "triggers"):
                table[key] = [item for item in table.get(key, []) if item.get("id") not in object_ids]
            table["uniqueConstraints"] = [item for item in table.get("uniqueConstraints", []) if column["id"] not in item.get("columnIds", [])]
            schema["relationships"] = [item for item in schema["relationships"] if not _relationship_has_column(item, column["id"])]
            table["columns"] = [item for item in table["columns"] if item["id"] != column["id"]]
            _sync_primary_key(table, operation_id)
    elif action_type == "add_relationship":
        relationship = _new_relationship(schema, action, operation_id, "relationship")
        schema["relationships"].append(relationship)
        changed.append({"kind": "relationship", "id": relationship["id"]})
    else:  # pragma: no cover - normalization owns the action registry.
        raise SchemaStoreError(400, "invalid_ai_action", "Schema action is unsupported")
    _validate_schema(schema)
    return record, {"actionType": action_type, "changed": changed, "impact": impact}


def destructive_impact(record: dict[str, Any], action: dict[str, Any]) -> list[dict[str, str]]:
    if action.get("type") != "delete_element":
        return []
    schema = record["schema"]
    table = _table(schema, action["tableId"])
    if action["elementType"] == "table":
        related = [item for item in schema["relationships"] if item.get("fromTableId") == table["id"] or item.get("toTableId") == table["id"]]
        return [{"kind": "relationship", "id": item.get("id", "")} for item in related] + [{"kind": "table", "id": table["id"]}]
    return _column_dependencies(schema, table, _column(table, action["columnId"]))


def deterministic_id(prefix: str, operation_id: str, path: str) -> str:
    digest = hashlib.sha256(f"{operation_id}:{path}".encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _new_table(item: dict[str, Any], operation_id: str, path: str, color_index: int) -> dict[str, Any]:
    columns = item.get("columns") or [{"name": "id", "type": "uuid", "primary": True, "nullable": False, "unique": True, "default": ""}]
    table = {
        "id": deterministic_id("table", operation_id, path), "name": item["name"], "color": COLORS[color_index % len(COLORS)],
        "columns": [_new_column(column, operation_id, f"{path}:column:{index}") for index, column in enumerate(columns)],
        "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
    }
    _sync_primary_key(table, operation_id)
    return table


def _new_column(item: dict[str, Any], operation_id: str, path: str) -> dict[str, Any]:
    primary = item.get("primary") is True
    return {
        "id": deterministic_id("col", operation_id, path), "name": item["name"], "type": item["type"],
        "primary": primary, "nullable": False if primary else item.get("nullable", True),
        "unique": primary or item.get("unique") is True, "default": item.get("default") or "",
    }


def _place_tables(schema: dict[str, Any], tables: list[dict[str, Any]]) -> None:
    existing = schema["tables"]
    layout_objects = _table_layout_objects(schema)
    coordinates = [
        layout_objects.get(item.get("id"), item) if layout_objects is not None else item
        for item in existing
    ]
    max_x = max((item.get("x", 0) for item in coordinates if isinstance(item, dict) and isinstance(item.get("x"), (int, float))), default=-325)
    min_y = min((item.get("y", 0) for item in coordinates if isinstance(item, dict) and isinstance(item.get("y"), (int, float))), default=0)
    columns = max(1, math.ceil(math.sqrt(len(tables))))
    for index, table in enumerate(tables):
        table["x"] = max_x + 325 + (index % columns) * 325
        table["y"] = min_y + (index // columns) * 235
        if layout_objects is not None:
            layout_objects[table["id"]] = {"x": table["x"], "y": table["y"], "color": table["color"]}


def _table_layout_objects(schema: dict[str, Any]) -> dict[str, Any] | None:
    layout = schema.get("layout")
    if not isinstance(layout, dict):
        return None
    layers = layout.get("layers")
    if isinstance(layers, dict):
        tables = layers.get("tables")
        return tables.get("objects") if isinstance(tables, dict) and isinstance(tables.get("objects"), dict) else None
    return layout.get("tables") if isinstance(layout.get("tables"), dict) else None


def _remove_table_layout(schema: dict[str, Any], table_id: str) -> None:
    objects = _table_layout_objects(schema)
    if objects is not None:
        objects.pop(table_id, None)


def _table(schema: dict[str, Any], table_id: str) -> dict[str, Any]:
    matches = [item for item in schema["tables"] if item.get("id") == table_id]
    if len(matches) != 1:
        raise SchemaStoreError(409, "schema_element_changed", "Target table no longer has one stable identity")
    return matches[0]


def _column(table: dict[str, Any], column_id: str) -> dict[str, Any]:
    matches = [item for item in table["columns"] if item.get("id") == column_id]
    if len(matches) != 1:
        raise SchemaStoreError(409, "schema_element_changed", "Target column no longer has one stable identity")
    return matches[0]


def _new_relationship(schema: dict[str, Any], item: dict[str, Any], operation_id: str, path: str) -> dict[str, Any]:
    from_table = _relationship_table(schema, item, "from")
    to_table = _relationship_table(schema, item, "to")
    from_column = _relationship_column(from_table, item, "from")
    to_column = _relationship_column(to_table, item, "to")
    return {
        "id": deterministic_id("rel", operation_id, path), "fromTableId": from_table["id"], "fromColumnId": from_column["id"],
        "toTableId": to_table["id"], "toColumnId": to_column["id"],
        "constraintName": item.get("constraintName") or f"{from_table['name']}_{from_column['name']}_fkey",
        "targetNamespace": to_table.get("namespace") or schema.get("postgres", {}).get("namespace"),
        "targetTableName": to_table["name"], "targetColumnNames": [to_column["name"]],
        "onUpdate": item.get("onUpdate", "NO ACTION"), "onDelete": item.get("onDelete", "NO ACTION"),
        "deferrable": False, "initiallyDeferred": False, "matchType": "SIMPLE", "validated": True,
    }


def _relationship_table(schema: dict[str, Any], item: dict[str, Any], prefix: str) -> dict[str, Any]:
    table_id, name = item.get(f"{prefix}TableId"), item.get(f"{prefix}TableName")
    matches = [table for table in schema["tables"] if table.get("id") == table_id] if table_id else [table for table in schema["tables"] if table.get("name", "").casefold() == str(name).casefold()]
    if len(matches) != 1 or (name is not None and matches[0].get("name") != name):
        raise SchemaStoreError(409, "schema_element_changed", f"{prefix.title()} relationship table changed")
    return matches[0]


def _relationship_column(table: dict[str, Any], item: dict[str, Any], prefix: str) -> dict[str, Any]:
    column_id, name = item.get(f"{prefix}ColumnId"), item.get(f"{prefix}ColumnName")
    matches = [column for column in table["columns"] if column.get("id") == column_id] if column_id else [column for column in table["columns"] if column.get("name", "").casefold() == str(name).casefold()]
    if len(matches) != 1 or (name is not None and matches[0].get("name") != name):
        raise SchemaStoreError(409, "schema_element_changed", f"{prefix.title()} relationship column changed")
    return matches[0]


def _sync_primary_key(table: dict[str, Any], operation_id: str) -> None:
    ids = [column["id"] for column in table["columns"] if column.get("primary")]
    if not ids:
        table["primaryKey"] = None
        return
    current = table.get("primaryKey") if isinstance(table.get("primaryKey"), dict) else {}
    table["primaryKey"] = {**current, "id": current.get("id") or deterministic_id("pk", operation_id, f"pk:{table['id']}"), "name": f"{table['name']}_pkey", "columnIds": ids}


def _rename_table_owned_objects(schema: dict[str, Any], table: dict[str, Any], old: str, new: str) -> None:
    primary = table.get("primaryKey")
    if isinstance(primary, dict):
        primary["name"] = f"{new}_pkey"
    for key in ("uniqueConstraints", "checks", "indexes", "triggers"):
        for item in table.get(key, []):
            name = item.get("name")
            if item.get("definition") and (_identifier_in_text(old, item["definition"]) or (isinstance(name, str) and name.startswith(f"{old}_") and _identifier_in_text(name, item["definition"]))):
                raise SchemaStoreError(409, "unsupported_schema_dependency", "Table rename has stored SQL dependencies that cannot be rewritten safely", dependencies=[{"kind": key.rstrip("s"), "id": item.get("id", "")}])
            if isinstance(name, str) and name.startswith(f"{old}_"):
                item["name"] = f"{new}{name[len(old):]}"
    for relationship in schema["relationships"]:
        if relationship.get("definition") and _identifier_in_text(old, relationship["definition"]):
            raise SchemaStoreError(409, "unsupported_schema_dependency", "Table rename has a stored relationship definition that cannot be rewritten safely", dependencies=[{"kind": "relationship", "id": relationship.get("id", "")}])
        if relationship.get("fromTableId") == table["id"]:
            for key in ("name", "constraintName"):
                value = relationship.get(key)
                if isinstance(value, str) and value.startswith(f"{old}_"):
                    relationship[key] = f"{new}{value[len(old):]}"
        if relationship.get("toTableId") == table["id"] or relationship.get("targetTableName") == old:
            relationship["targetTableName"] = new
    postgres = table.get("postgres")
    if isinstance(postgres, dict) and postgres.get("parentTable") == old:
        postgres["parentTable"] = new


def _require_safe_column_rename(schema: dict[str, Any], table: dict[str, Any], column: dict[str, Any]) -> None:
    ambiguous = []
    for kind, key in (("check", "checks"), ("index", "indexes"), ("trigger", "triggers")):
        for item in table.get(key, []):
            if column["id"] in item.get("columnIds", []) or _identifier_in_text(column["name"], item.get("definition")):
                ambiguous.append({"kind": kind, "id": item.get("id", "")})
    if ambiguous:
        raise SchemaStoreError(409, "unsupported_schema_dependency", "Column rename has stored SQL dependencies that cannot be rewritten safely", dependencies=ambiguous)


def _rename_relationship_column(schema: dict[str, Any], table: dict[str, Any], column: dict[str, Any], new_name: str) -> None:
    for item in schema["relationships"]:
        to_ids = item.get("toColumnIds") or [item.get("toColumnId")]
        if item.get("toTableId") == table["id"] and column["id"] in to_ids:
            item["targetColumnNames"] = [new_name if value == column["name"] else value for value in item.get("targetColumnNames", [])]


def _column_dependencies(schema: dict[str, Any], table: dict[str, Any], column: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for kind, key in (("check", "checks"), ("index", "indexes"), ("trigger", "triggers")):
        for item in table.get(key, []):
            if column["id"] in item.get("columnIds", []) or _identifier_in_text(column["name"], item.get("definition")) or _identifier_in_object_name(column["name"], item.get("name")):
                result.append({"kind": kind, "id": item.get("id", "")})
    result.extend({"kind": "unique_constraint", "id": item.get("id", "")} for item in table.get("uniqueConstraints", []) if column["id"] in item.get("columnIds", []))
    result.extend({"kind": "relationship", "id": item.get("id", "")} for item in schema["relationships"] if _relationship_has_column(item, column["id"]))
    result.append({"kind": "column", "id": column["id"]})
    return result


def _relationship_has_column(item: dict[str, Any], column_id: str) -> bool:
    return column_id in (item.get("fromColumnIds") or [item.get("fromColumnId")]) or column_id in (item.get("toColumnIds") or [item.get("toColumnId")])


def _identifier_in_text(name: str, text: Any) -> bool:
    if not isinstance(text, str):
        return False
    return f'"{name}"' in text or re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text, re.IGNORECASE) is not None


def _identifier_in_object_name(name: str, value: Any) -> bool:
    return isinstance(value, str) and re.search(rf"(?:^|_){re.escape(name)}(?:_|$)", value) is not None


def _validate_schema(schema: dict[str, Any]) -> None:
    table_ids, table_names, column_ids = set(), set(), set()
    for table in schema["tables"]:
        if table.get("id") in table_ids or table.get("name", "").casefold() in table_names:
            raise SchemaStoreError(409, "schema_name_conflict", "Schema contains duplicate table identity or name")
        table_ids.add(table.get("id")); table_names.add(table.get("name", "").casefold())
        names = set()
        for column in table.get("columns", []):
            if column.get("id") in column_ids or column.get("name", "").casefold() in names:
                raise SchemaStoreError(409, "schema_name_conflict", "Schema contains duplicate column identity or name")
            column_ids.add(column.get("id")); names.add(column.get("name", "").casefold())
    relationship_keys = set()
    for item in schema["relationships"]:
        from_table = _table(schema, item.get("fromTableId"))
        to_matches = [table for table in schema["tables"] if table.get("id") == item.get("toTableId")]
        if not to_matches:
            continue
        to_table = to_matches[0]
        from_ids = item.get("fromColumnIds") or [item.get("fromColumnId")]
        to_ids = item.get("toColumnIds") or [item.get("toColumnId")]
        if len(from_ids) != len(to_ids) or not from_ids:
            raise SchemaStoreError(409, "relationship_changed", "Relationship column identity is invalid")
        from_columns = [_column(from_table, column_id) for column_id in from_ids]
        to_columns = [_column(to_table, column_id) for column_id in to_ids]
        for from_column, to_column in zip(from_columns, to_columns):
            if TYPE_ALIASES.get(from_column["type"].strip().casefold(), from_column["type"].strip().casefold()) != TYPE_ALIASES.get(to_column["type"].strip().casefold(), to_column["type"].strip().casefold()):
                raise SchemaStoreError(409, "relationship_type_mismatch", "Relationship columns must have matching types")
            if item.get("onDelete") == "SET NULL" and not from_column.get("nullable"):
                raise SchemaStoreError(409, "relationship_action_invalid", "SET NULL requires nullable source columns")
        target_key = all(column.get("primary") for column in to_columns) or (len(to_columns) == 1 and to_columns[0].get("unique")) or any(constraint.get("columnIds") == to_ids for constraint in to_table.get("uniqueConstraints", []))
        if not target_key:
            raise SchemaStoreError(409, "relationship_target_invalid", "Relationship target must be primary or unique")
        key = (from_table["id"], tuple(from_ids), to_table["id"], tuple(to_ids))
        if key in relationship_keys:
            raise SchemaStoreError(409, "relationship_conflict", "Schema contains a duplicate relationship")
        relationship_keys.add(key)
