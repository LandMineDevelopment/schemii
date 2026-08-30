from __future__ import annotations

import copy
import re
from typing import Any

from .ai_validation import confirmation, exact, identifier, postgres_name, revision, sql, text


def normalize_schemer_action(action: Any, access: str) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    action_type = action.get("type")
    if action_type == "read_query":
        exact(action, {"type", "dashboardId", "expectedRevision", "profileId", "database", "namespace", "sql", "purpose", "readOnly", "requiresConfirmation"})
        if access != "data" or action.get("readOnly") is not True or action.get("requiresConfirmation") is not True:
            raise ValueError("query action is not authorized")
        return {"type": action_type, "dashboardId": identifier(action.get("dashboardId")), "expectedRevision": revision(action.get("expectedRevision")), "profileId": identifier(action.get("profileId")), "database": postgres_name(action.get("database")), "namespace": postgres_name(action.get("namespace")), "sql": sql(action.get("sql")), "purpose": text(action.get("purpose"), 500), "readOnly": True, "requiresConfirmation": True}
    if action_type == "dashboard_open":
        exact(action, {"type", "dashboardId", "expectedRevision", "title", "requiresConfirmation"})
        confirmation(action)
        return {"type": action_type, "dashboardId": identifier(action.get("dashboardId")), "expectedRevision": revision(action.get("expectedRevision")), "title": text(action.get("title"), 128), "requiresConfirmation": True}
    if action_type == "dashboard_create":
        exact(action, {"type", "title", "requiresConfirmation"}); confirmation(action)
        return {"type": action_type, "title": text(action.get("title"), 128), "requiresConfirmation": True}
    if action_type == "widget_create":
        if access not in {"dashboard", "data"}:
            raise ValueError("widget mutation requires dashboard access")
        base = {"type", "dashboardId", "expectedRevision", "title", "requiresConfirmation"}
        complete = base | {"source", "query", "visualizationMode"}
        fields = frozenset(action)
        if fields not in {frozenset(base), frozenset(complete)}:
            raise ValueError("action fields are invalid")
        confirmation(action)
        result = {"type": action_type, "dashboardId": identifier(action.get("dashboardId")), "expectedRevision": revision(action.get("expectedRevision")), "title": text(action.get("title"), 128), "requiresConfirmation": True}
        if fields == frozenset(complete):
            source = action.get("source")
            if not isinstance(source, dict) or set(source) != {"profileId", "database", "namespace", "relation", "kind", "fingerprint"}:
                raise ValueError("source is invalid")
            if source.get("kind") not in {"table", "partitioned_table", "view", "materialized_view", "foreign_table"} or not isinstance(source.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", source["fingerprint"]):
                raise ValueError("source is invalid")
            result["source"] = {"profileId": identifier(source["profileId"]), "database": postgres_name(source["database"]), "namespace": postgres_name(source["namespace"]), "relation": postgres_name(source["relation"]), "kind": source["kind"], "fingerprint": source["fingerprint"]}
            if not isinstance(action.get("query"), dict):
                raise ValueError("query is invalid")
            result["query"] = copy.deepcopy(action["query"])
            if action.get("visualizationMode") not in {"table", "kpi", "bar", "line", "donut"}:
                raise ValueError("visualization mode is invalid")
            result["visualizationMode"] = action["visualizationMode"]
        return result
    if action_type in {"widget_rename", "widget_duplicate", "widget_delete"}:
        if access not in {"dashboard", "data"}:
            raise ValueError("widget mutation requires dashboard access")
        exact(action, {"type", "dashboardId", "expectedRevision", "widgetId", "currentTitle", "requiresConfirmation"} | ({"destructive"} if action_type == "widget_delete" else {"title"}))
        confirmation(action)
        if action_type == "widget_delete" and action.get("destructive") is not True:
            raise ValueError("destructive confirmation is invalid")
        result = {"type": action_type, "dashboardId": identifier(action.get("dashboardId")), "expectedRevision": revision(action.get("expectedRevision")), "widgetId": identifier(action.get("widgetId")), "currentTitle": text(action.get("currentTitle"), 128), "requiresConfirmation": True}
        if action_type == "widget_delete":
            result["destructive"] = True
        else:
            result["title"] = text(action.get("title"), 128)
        return result
    raise ValueError("unsupported action")
