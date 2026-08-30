from __future__ import annotations

from typing import Any

from .ai_http import bounded_ai_query_result
from .ai_tool_contracts import contract_for_action
from .dashboard_store import DashboardStoreError
from .opencode_service import OpenCodeServiceError
from .postgres_common import PostgresServiceError


class SchemerAiExecutor:
    """Executes and reconciles Schemer proposals outside the HTTP adapter."""

    MUTATIONS = {"dashboard_create", "widget_create", "widget_rename", "widget_duplicate", "widget_delete"}

    def __init__(self, service, dashboard_store, authority, *, catalog_sources, configured_widget):
        self.service = service
        self.dashboard_store = dashboard_store
        self.authority = authority
        self.catalog_sources = catalog_sources
        self.configured_widget = configured_widget

    def reconcile(self, chat: dict[str, Any], current: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        if proposal["action"].get("type") not in self.MUTATIONS:
            return {"operation": current}
        evidence = self.dashboard_store.operation_receipt_evidence(chat["dashboardId"], current["id"])
        receipt = evidence["receipt"]
        if receipt is None and evidence["archiveComplete"]:
            operation = self.authority.resolve_operation(current["id"], chat["id"], "failed", error={
                "code": "operation_not_applied",
                "message": "The complete dashboard receipt history proves that the mutation was not applied; request a fresh proposal",
                "receiptEvidence": {"archiveComplete": evidence["archiveComplete"]},
            })
        elif receipt is not None:
            operation = self.authority.resolve_operation(current["id"], chat["id"], "succeeded", result=receipt)
        else:
            return {
                "operation": current,
                "reconciliation": {
                    "status": "insufficient_evidence",
                    "message": "Legacy dashboard receipt history is incomplete; the operation remains uncertain and will not be replayed",
                },
            }
        return {"operation": operation}

    def execute(self, action, operation_id, *, chat, record, profile, schema_concurrency, authorization_target, policy_binding=None):
        action_type = action.get("type")
        bounds = (policy_binding or {}).get("snapshot", {}).get("bounds", {})
        versioned_policy = (policy_binding or {}).get("snapshot", {}).get("version") == 2
        rows_disclosed = bounds.get("rowsDisclosed") or 100
        contract = contract_for_action("schemer", action)
        if contract is None or contract.executor_adapter != action_type:
            raise OpenCodeServiceError(409, "action_temporarily_unavailable", "This action has no Schemer executor contract")
        if action_type == "read_query":
            if profile is None or any(action.get(key) != authorization_target[key] for key in ("profileId", "database", "namespace")):
                raise PostgresServiceError(409, "action_target_changed", "Query target no longer matches the proposal")
            result = self.service.execute_read_only_sql(profile["id"], authorization_target["namespace"], action.get("sql"), database=profile["dbname"], expected_profile_fingerprint=self.service.profile_context_fingerprint(profile["id"]), allow_explain=False, max_rows=min(100, rows_disclosed), max_columns=50, max_result_bytes=256 * 1024, operation_timeout_ms=bounds.get("operationTimeoutMs"), operation_id=operation_id)
            if versioned_policy:
                self.authority.consume_bound(operation_id, "rowsDisclosed", len(result.get("rows", [])), {"kind": "sql_result"})
            reference = self.authority.create_result(chat["id"], {"operationId": operation_id, "resource": chat["dashboardId"], "target": authorization_target, "revision": record["revision"], "access": "data", "policyBinding": policy_binding}, bounded_ai_query_result(result, max_rows=min(100, rows_disclosed), max_columns=50, max_bytes=48 * 1024))
            return {"kind": "sql_result", "display": result, "resultRef": reference["id"], "schemaConcurrency": schema_concurrency, "authorizationTarget": authorization_target}
        if action_type == "dashboard_open":
            target = self.dashboard_store.get(action.get("dashboardId"))
            if target["revision"] != action.get("expectedRevision") or target["dashboard"]["title"] != action.get("title"):
                raise DashboardStoreError(409, "dashboard_changed", "Target dashboard changed; request a fresh proposal")
            return {"kind": "client_command", "command": {"type": "open_dashboard", "dashboardId": target["id"], "revision": target["revision"]}}
        if action_type == "dashboard_create":
            return self.dashboard_store.create_ai(operation_id, action["title"])
        if action_type in {"widget_create", "widget_rename", "widget_duplicate", "widget_delete"}:
            if action["dashboardId"] != chat["dashboardId"] or action["expectedRevision"] != schema_concurrency["revision"]:
                raise DashboardStoreError(409, "dashboard_changed", "Dashboard binding no longer matches the proposal")
            prepared = None
            if action_type == "widget_create" and "source" in action:
                if chat["accessLevel"] == "data" and any(action["source"].get(key) != authorization_target.get(key) for key in ("profileId", "database", "namespace")):
                    raise PostgresServiceError(409, "action_target_changed", "Widget source no longer matches the confirmed data target")
                allowed = self.catalog_sources(self.service, record, authorization_target if chat["accessLevel"] == "data" else None)
                fields = ("profileId", "database", "namespace", "relation", "kind", "fingerprint")
                identity = tuple(action["source"].get(key) for key in fields)
                if identity not in {tuple(item.get(key) for key in fields) for item in allowed}:
                    raise PostgresServiceError(409, "action_target_changed", "Widget source is outside the bounded catalog context issued for this proposal")
                with self.dashboard_store.guard_revision(chat["dashboardId"], schema_concurrency["revision"]) as guarded:
                    widget_count = len(guarded["dashboard"]["widgets"])
                prepared = self.configured_widget(
                    self.service, action, operation_id, widget_count,
                    operation_timeout_ms=bounds.get("operationTimeoutMs"),
                )
            return self.dashboard_store.apply_ai_mutation(chat["dashboardId"], operation_id, schema_concurrency["revision"], action, prepared)
        raise OpenCodeServiceError(409, "action_temporarily_unavailable", "This action is unavailable until its server execution adapter is installed")

    @staticmethod
    def durable_result(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("kind") != "sql_result":
            return result
        display = result.get("display") if isinstance(result.get("display"), dict) else {}
        return {
            key: value for key, value in result.items() if key != "display"
        } | {
            "evidence": {
                "rowCount": display.get("rowCount", len(display.get("rows", []))),
                "columnCount": len(display.get("columns", [])),
                "truncated": bool(display.get("truncated")),
            },
        }
