from __future__ import annotations

import copy
import re
from typing import Any

from .ai_validation import (
    confirmation as _confirmation,
    exact as _exact,
    exact_optional as _exact_optional,
    identifier as _id,
    insert_rows as _insert_rows,
    normalized_list as _list,
    postgres_name as _name,
    sql as _sql,
    text as _text,
)


SCHEMA_ACTIONS = {
    "create_project", "populate_schema", "add_table", "rename_table", "add_column", "update_column",
    "delete_element", "add_relationship", "migration_preview",
}
SCHEMII_ACTION_CAPABILITIES = {
    "create_project": "schema", "populate_schema": "schema", "add_table": "schema",
    "rename_table": "schema", "add_column": "schema", "update_column": "schema",
    "delete_element": "schema", "add_relationship": "schema", "schema_batch": "schema",
    "migration_preview": "schema", "migration_apply": "schema", "data_read": "structured",
    "insert_rows_preview": "write", "create_view_preview": "write", "postgres_write_apply": "write",
    "schema_read_query": "rawread", "raw_write": "rawwrite",
}


def schemii_action_capability(action: Any) -> str | None:
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise ValueError("action type is invalid")
    action_type = action["type"]
    if action_type in {"connection_setup", "open_project", "open_connection"}:
        return None
    capability = SCHEMII_ACTION_CAPABILITIES.get(action_type)
    if capability is None:
        raise ValueError("unsupported action")
    if action_type == "schema_batch":
        actions = action.get("actions")
        if not isinstance(actions, list) or not actions or any(schemii_action_capability(item) != "schema" for item in actions):
            raise ValueError("schema batch capability is invalid")
    return capability


def schemii_action_approval_floor(action: Any) -> str | None:
    action_type = action.get("type") if isinstance(action, dict) else None
    if action_type in {"connection_setup", "open_project", "open_connection", "schema_read_query", "raw_write", "delete_element"}:
        return "every_action"
    if action_type == "schema_batch" and any(item.get("type") == "delete_element" for item in action.get("actions", []) if isinstance(item, dict)):
        return "every_action"
    if action_type == "migration_preview" and action.get("destructivePolicy") == "allow-preview":
        return "every_action"
    if action_type == "migration_apply" and action.get("destructive") is True:
        return "every_action"
    return None


def _has_permission(access: str, permission: str) -> bool:
    if access in {"data", "schema-data", "schema-read-write"} and permission in {"rawread", "write"}:
        return True
    return permission in access.split("-")


def normalize_schemii_action(action: Any, access: str) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    action_type = action.get("type") or action.get("action")
    if action_type == "schema_read_query":
        field = "action" if "action" in action else "type"
        approval = "requiresApproval" if "requiresApproval" in action else "requiresConfirmation"
        _exact(action, {field, "profileId", "namespace", "sql", "purpose", "readOnly", approval})
        if not _has_permission(access, "rawread") or action.get("readOnly") is not True or action.get(approval) is not True:
            raise ValueError("query action is not authorized")
        return {
            "type": action_type, "profileId": _id(action.get("profileId")),
            "namespace": _name(action.get("namespace")), "sql": _sql(action.get("sql")),
            "purpose": _text(action.get("purpose"), 500), "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "data_read":
        _exact(action, {"type", "profileId", "namespace", "relation", "offset", "limit", "purpose", "readOnly", "requiresConfirmation"})
        if not _has_permission(access, "structured") or action.get("readOnly") is not True:
            raise ValueError("structured data read is not authorized")
        _confirmation(action)
        offset, limit = action.get("offset"), action.get("limit")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10_000_000:
            raise ValueError("offset is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit is invalid")
        return {"type": action_type, "profileId": _id(action.get("profileId")), "namespace": _name(action.get("namespace")), "relation": _name(action.get("relation")), "offset": offset, "limit": limit, "purpose": _text(action.get("purpose"), 500), "readOnly": True, "requiresConfirmation": True}
    if action_type == "raw_write":
        _exact(action, {"type", "profileId", "namespace", "sql", "purpose", "requiresConfirmation"})
        if not _has_permission(access, "rawwrite"):
            raise ValueError("raw write is not authorized")
        _confirmation(action)
        return {"type": action_type, "profileId": _id(action.get("profileId")), "namespace": _name(action.get("namespace")), "sql": _sql(action.get("sql"), maximum=100_000), "purpose": _text(action.get("purpose"), 500), "requiresConfirmation": True}
    if action_type == "open_project":
        _exact(action, {"type", "schemaId", "projectName", "requiresConfirmation"})
        if action.get("requiresConfirmation") is not True:
            raise ValueError("confirmation is required")
        return {"type": action_type, "schemaId": _id(action.get("schemaId")), "projectName": _text(action.get("projectName"), 256), "requiresConfirmation": True}
    if action_type == "connection_setup":
        _exact(action, {"type", "name", "host", "port", "database", "user", "sslmode", "requiresPasswordEntry", "requiresConfirmation"})
        if action.get("requiresPasswordEntry") is not True or action.get("requiresConfirmation") is not True:
            raise ValueError("connection confirmation is required")
        port = action.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port is invalid")
        sslmode = action.get("sslmode")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("sslmode is invalid")
        return {
            "type": action_type, "name": _text(action.get("name"), 100), "host": _text(action.get("host"), 253),
            "port": port, "database": _name(action.get("database")), "user": _text(action.get("user"), 63), "sslmode": sslmode,
            "requiresPasswordEntry": True, "requiresConfirmation": True,
        }
    if action_type == "create_project":
        if not _has_permission(access, "schema"):
            raise ValueError("project creation is not authorized")
        _exact(action, {"type", "projectName", "requiresConfirmation"})
        _confirmation(action)
        return {"type": action_type, "projectName": _text(action.get("projectName"), 256), "requiresConfirmation": True}
    if action_type == "open_connection":
        _exact(action, {"type", "profileId", "name", "database", "namespace", "requiresConfirmation"})
        _confirmation(action)
        return {
            "type": action_type, "profileId": _id(action.get("profileId")), "name": _text(action.get("name"), 256),
            "database": _name(action.get("database")), "namespace": _name(action.get("namespace")), "requiresConfirmation": True,
        }
    if action_type == "migration_preview":
        if not _has_permission(access, "schema"):
            raise ValueError("schema preview is not authorized")
        _exact(action, {"type", "profileId", "namespace", "destructivePolicy", "purpose", "readOnly", "requiresConfirmation"})
        _confirmation(action)
        if action.get("readOnly") is not True or action.get("destructivePolicy") not in {"reject", "allow-preview"}:
            raise ValueError("migration preview policy is invalid")
        return {
            "type": action_type, "profileId": _id(action.get("profileId")), "namespace": _name(action.get("namespace")),
            "destructivePolicy": action["destructivePolicy"], "purpose": _text(action.get("purpose"), 500),
            "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "insert_rows_preview":
        _exact(action, {"type", "profileId", "namespace", "relation", "rows", "purpose", "readOnly", "requiresConfirmation"})
        if not _has_permission(access, "write") or action.get("readOnly") is not True:
            raise ValueError("row insertion preview is not authorized")
        _confirmation(action)
        rows = _insert_rows(action.get("rows"))
        return {
            "type": action_type, "profileId": _id(action.get("profileId")),
            "namespace": _name(action.get("namespace")), "relation": _name(action.get("relation")),
            "rows": rows, "purpose": _text(action.get("purpose"), 500),
            "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "create_view_preview":
        _exact(action, {"type", "profileId", "namespace", "relation", "definition", "purpose", "readOnly", "requiresConfirmation"})
        if not _has_permission(access, "write") or action.get("readOnly") is not True:
            raise ValueError("view creation preview is not authorized")
        _confirmation(action)
        definition = _sql(action.get("definition"))
        if not re.match(r"^CREATE\s+VIEW\b", definition, re.I):
            raise ValueError("only CREATE VIEW is supported")
        return {
            "type": action_type, "profileId": _id(action.get("profileId")),
            "namespace": _name(action.get("namespace")), "relation": _name(action.get("relation")),
            "definition": definition, "purpose": _text(action.get("purpose"), 500),
            "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "populate_schema":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact(action, {"type", "purpose", "tables", "relationships", "requiresConfirmation"})
        _confirmation(action)
        tables = _list(action.get("tables"), 1, 20, lambda item: _table_definition(item, require_purpose=True))
        relationships = _list(action.get("relationships"), 0, 50, _relationship_definition)
        _unique_names(tables, "table")
        return {"type": action_type, "purpose": _text(action.get("purpose"), 500), "tables": tables, "relationships": relationships, "requiresConfirmation": True}
    if action_type == "add_table":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact_optional(action, {"type", "name", "purpose", "columns", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        result = {"type": action_type, **_table_definition({key: action[key] for key in ("name", "purpose", "columns")}, require_purpose=True), "requiresConfirmation": True}
        return _optional_target(action, result)
    if action_type == "rename_table":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact_optional(action, {"type", "tableId", "newName", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        return _optional_target(action, {"type": action_type, "tableId": _id(action.get("tableId")), "newName": _name(action.get("newName")), "requiresConfirmation": True})
    if action_type == "add_column":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact_optional(action, {"type", "tableId", "name", "columnType", "nullable", "requiresConfirmation"}, {"profileId", "namespace", "default"})
        _confirmation(action)
        if not isinstance(action.get("nullable"), bool):
            raise ValueError("nullable is invalid")
        result = {"type": action_type, "tableId": _id(action.get("tableId")), "name": _name(action.get("name")), "columnType": _column_type(action.get("columnType")), "nullable": action["nullable"], "requiresConfirmation": True}
        if "default" in action:
            result["default"] = _default(action["default"], nullable=False)
        return _optional_target(action, result)
    if action_type == "update_column":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact_optional(action, {"type", "tableId", "columnId", "changes", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        changes = action.get("changes")
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "type", "nullable", "default"}:
            raise ValueError("column changes are invalid")
        normalized = {}
        if "name" in changes: normalized["name"] = _name(changes["name"])
        if "type" in changes: normalized["type"] = _column_type(changes["type"])
        if "nullable" in changes:
            if not isinstance(changes["nullable"], bool): raise ValueError("nullable is invalid")
            normalized["nullable"] = changes["nullable"]
        if "default" in changes: normalized["default"] = _default(changes["default"], nullable=True)
        return _optional_target(action, {"type": action_type, "tableId": _id(action.get("tableId")), "columnId": _id(action.get("columnId")), "changes": normalized, "requiresConfirmation": True})
    if action_type == "delete_element":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        _exact_optional(action, {"type", "elementType", "tableId", "reason", "destructive", "requiresConfirmation"}, {"profileId", "namespace", "columnId", "impact"})
        _confirmation(action)
        if action.get("destructive") is not True or action.get("elementType") not in {"table", "column"}:
            raise ValueError("destructive element type is invalid")
        if (action["elementType"] == "column") != ("columnId" in action):
            raise ValueError("column identity is invalid")
        result = {"type": action_type, "elementType": action["elementType"], "tableId": _id(action.get("tableId")), "reason": _text(action.get("reason"), 500), "destructive": True, "requiresConfirmation": True}
        if "columnId" in action: result["columnId"] = _id(action["columnId"])
        if "impact" in action:
            if not isinstance(action["impact"], list): raise ValueError("impact is invalid")
            result["impact"] = copy.deepcopy(action["impact"])
        return _optional_target(action, result)
    if action_type == "add_relationship":
        if not _has_permission(access, "schema"):
            raise ValueError("schema mutation is not authorized")
        required = {"type", "fromTableName", "fromColumnName", "toTableName", "toColumnName", "onDelete", "onUpdate", "requiresConfirmation"}
        optional = {"profileId", "namespace", "fromTableId", "fromColumnId", "toTableId", "toColumnId", "constraintName"}
        _exact_optional(action, required, optional)
        _confirmation(action)
        return _optional_target(action, {"type": action_type, **_relationship_definition({key: value for key, value in action.items() if key not in {"type", "profileId", "namespace", "requiresConfirmation"}}), "requiresConfirmation": True})
    raise ValueError("unsupported action")


def _optional_target(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if ("profileId" in action) != ("namespace" in action):
        raise ValueError("optional target is incomplete")
    if "profileId" in action:
        result.update({"profileId": _id(action["profileId"]), "namespace": _name(action["namespace"])})
    return result


def _table_definition(value: Any, *, require_purpose: bool) -> dict[str, Any]:
    fields = {"name", "columns"} | ({"purpose"} if require_purpose else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("table definition is invalid")
    columns = _list(value.get("columns"), 1, 50, _column_definition)
    _unique_names(columns, "column")
    result = {"name": _name(value.get("name")), "columns": columns}
    if require_purpose: result["purpose"] = _text(value.get("purpose"), 500)
    return result


def _column_definition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not {"name", "type"} <= set(value) or set(value) - {"name", "type", "primary", "nullable", "unique", "default"}:
        raise ValueError("column definition is invalid")
    result = {"name": _name(value.get("name")), "type": _column_type(value.get("type"))}
    for field in ("primary", "nullable", "unique"):
        if field in value:
            if not isinstance(value[field], bool): raise ValueError(f"{field} is invalid")
            result[field] = value[field]
    if "default" in value: result["default"] = _default(value["default"], nullable=False)
    return result


def _relationship_definition(value: Any) -> dict[str, Any]:
    required = {"fromTableName", "fromColumnName", "toTableName", "toColumnName", "onDelete", "onUpdate"}
    optional = {"fromTableId", "fromColumnId", "toTableId", "toColumnId", "constraintName"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError("relationship definition is invalid")
    result = {key: _name(value[key]) for key in ("fromTableName", "fromColumnName", "toTableName", "toColumnName")}
    for key in ("fromTableId", "fromColumnId", "toTableId", "toColumnId"):
        if key in value: result[key] = _id(value[key])
    if "constraintName" in value: result["constraintName"] = _name(value["constraintName"])
    for key in ("onDelete", "onUpdate"):
        if value[key] not in {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}: raise ValueError("relationship action is invalid")
        result[key] = value[key]
    return result


def _unique_names(items: list[dict[str, Any]], kind: str) -> None:
    names = [item["name"].casefold() for item in items]
    if len(names) != len(set(names)): raise ValueError(f"{kind} names are duplicated")


def _column_type(value: Any) -> str:
    return _text(value, 128)


def _default(value: Any, *, nullable: bool) -> str | None:
    if nullable and value is None: return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1000 or "\x00" in value: raise ValueError("column default is invalid")
    return value
