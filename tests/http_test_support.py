import json
import hashlib
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


class QuietHandlerMixin:
    def log_message(self, format, *args):
        pass


class RunningHttpServer:
    def __init__(self, handler, token="session-token"):
        quiet_handler = type(f"Quiet{handler.__name__}", (QuietHandlerMixin, handler), {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.token = token

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", payload=None, content_type="application/json", authorized=False, headers=None, timeout=5):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if authorized:
            request.add_header("X-Schemii-Token", self.token)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            try:
                return error.code, error.read(), error.headers
            finally:
                error.close()


class FakePostgresService:
    def __init__(self, *, profiles=None, namespaces=None, relations=None, descriptor=None, preview_rows=None, test_result=None):
        self.calls = []
        self.profiles = list(profiles or [])
        self.namespaces = list(namespaces or ["public"])
        self.relations = list(relations or [])
        self.descriptor = descriptor
        self.preview_rows = list(preview_rows or [])
        self.test_result = dict(test_result or {"ok": True})
        self.view_layout_token = "0" * 64
        self.view_expected_absent = False
        self.view_operation = "upsert"
        self.view_expectation = {"kind": "view", "fingerprint": "a" * 64}
        self.view_saved_id = "view_summary"
        self.ai_write_results = {}
        self.console_settings_value = {
            "application": "test", "revision": 1, "writeIntent": "disabled", "defaultMode": "managed_read",
            "statementLimit": 100, "rowPageSize": 100, "inheritance": "none",
            "maxima": {"statementLimit": 100, "rowPageSize": 500},
        }

    def list_profiles(self):
        return self.profiles

    def target_readiness(self):
        return {"required": False, "status": "available", "configured": len(self.profiles), "profiles": {}}

    def execution_metrics(self):
        return {
            "status": "available",
            "global": {"active": 0, "capacity": 12},
            "targetCapacity": 4,
            "target": {"active": 0, "capacityPerTarget": 4, "tracked": 0},
            "classes": {
                name: {"active": 0, "capacity": capacity, "admitted": 0, "rejected": 0,
                       "completed": 0, "failed": 0, "waitMs": 0.0, "runMs": 0.0}
                for name, capacity in {"catalog": 8, "read": 8, "console": 4, "write": 1}.items()
            },
            "targets": {},
        }

    def profile_context_fingerprint(self, profile_id):
        profile = next(item for item in self.profiles if item["id"] == profile_id)
        encoded = json.dumps([profile_id, profile.get("host"), profile.get("port"), profile.get("dbname"), profile.get("user"), profile.get("sslmode")], separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def save_profile(self, profile_id, body):
        self.calls.append(("save_profile", profile_id, body))
        saved = {"id": profile_id or "pg_created", **{key: value for key, value in body.items() if key != "password"}}
        self.profiles = [saved]
        return saved

    def delete_profile(self, profile_id, expected_fingerprint=None):
        self.calls.append(("delete_profile", profile_id, expected_fingerprint))
        return {"deleted": profile_id}

    def list_history(self, profile_id, limit):
        self.calls.append(("list_history", profile_id, limit))
        return [{"id": "history_one"}]

    def preview_table_data(self, profile_id, namespace, table, offset, limit):
        self.calls.append(("preview_table_data", profile_id, namespace, table, offset, limit))
        return {"columns": [], "rows": [], "offset": offset, "nextOffset": offset, "hasMore": False}

    def execute_read_only_sql(self, profile_id, namespace, statement, **policy):
        self.calls.append(("execute_read_only_sql", profile_id, namespace, statement, policy))
        return {"columns": [{"name": "answer"}], "rows": [[1]], "rowCount": 1, "truncated": False}

    def cancel_read_only_sql(self, operation_id):
        self.calls.append(("cancel_read_only_sql", operation_id))
        return {"requested": True}

    def release_read_only_sql(self, operation_id):
        self.calls.append(("release_read_only_sql", operation_id))

    def execute_console(self, profile_id, body, binding, server_id, policy=None):
        self.calls.append(("execute_console", profile_id, body, binding, server_id, policy))
        return {
            "executionId": body["executionId"],
            "target": {"profileId": profile_id, "database": body["database"], "namespace": body["namespace"]},
            "mode": "read", "committed": False, "statements": [], "limits": {},
        }

    def console_settings(self):
        self.calls.append(("console_settings",))
        return dict(self.console_settings_value)

    def update_console_settings(self, expected_revision, settings):
        self.calls.append(("update_console_settings", expected_revision, settings))
        self.console_settings_value = {**self.console_settings_value, **settings, "revision": expected_revision + 1}
        return dict(self.console_settings_value)

    def cancel_console(self, profile_id, execution_id, binding, server_id):
        self.calls.append(("cancel_console", profile_id, execution_id, binding, server_id))
        return {"requested": True}

    def console_execution_status(self, profile_id, execution_id, console_id, database, namespace, binding, server_id):
        self.calls.append(("console_execution_status", profile_id, execution_id, console_id, database, namespace, binding, server_id))
        return {"executionId": execution_id, "mode": "managed_read", "state": "succeeded", "outcome": "rolled_back",
                "completedStatementIndexes": [0], "errorCode": None, "postgresEvidence": None, "reconciliationEvidence": None}

    def console_result_page(self, profile_id, execution_id, result_id, console_id, database, namespace,
                            statement_index, result_index, cursor, binding, server_id):
        self.calls.append(("console_result_page", profile_id, execution_id, result_id, console_id, database,
                           namespace, statement_index, result_index, cursor, binding, server_id))
        return {"resultId": result_id, "executionId": execution_id, "statementIndex": int(statement_index),
                "resultIndex": int(result_index), "columns": [{"name": "answer"}], "rows": [[2]],
                "pageSize": 100, "returnedRows": 1, "hasMore": False, "nextCursor": None,
                "snapshotRetention": "server_spool", "transactionRetention": False, "expiresAt": None,
                "resourceState": "closed", "closureEvents": ["exhausted"], "truncationEvents": []}

    def close_console_result(self, profile_id, execution_id, result_id, console_id, database, namespace,
                             statement_index, result_index, binding, server_id):
        self.calls.append(("close_console_result", profile_id, execution_id, result_id, console_id, database,
                           namespace, statement_index, result_index, binding, server_id))
        return {"resultId": result_id, "executionId": execution_id, "statementIndex": int(statement_index),
                "resultIndex": int(result_index), "closed": True, "closureEvents": ["closed"]}

    def create_console_transaction(self, profile_id, body, binding, server_id, policy=None):
        self.calls.append(("create_console_transaction", profile_id, body, binding, server_id, policy))
        return {"transactionId": body["transactionId"], "consoleId": body["consoleId"], "state": "in_transaction"}

    def console_transaction_status(self, profile_id, transaction_id, binding, server_id):
        self.calls.append(("console_transaction_status", profile_id, transaction_id, binding, server_id))
        return {"transactionId": transaction_id, "state": "in_transaction"}

    def execute_console_transaction(self, profile_id, transaction_id, body, binding, server_id):
        self.calls.append(("execute_console_transaction", profile_id, transaction_id, body, binding, server_id))
        return {"executionId": body["executionId"], "transactionId": transaction_id, "transactionState": "in_transaction", "statements": []}

    def finish_console_transaction(self, profile_id, transaction_id, body, binding, server_id, action):
        self.calls.append(("finish_console_transaction", profile_id, transaction_id, body, binding, server_id, action))
        return {"executionId": body["executionId"], "transactionId": transaction_id, "state": "closed",
                "outcome": "committed" if action == "commit" else "rolled_back"}

    def create_console_write_grant(self, profile_id, body, binding, server_id):
        self.calls.append(("create_console_write_grant", profile_id, body, binding, server_id))
        return {"writeGrantId": str(uuid4())}

    def revoke_console_write_grant(self, profile_id, grant_id, binding, server_id):
        self.calls.append(("revoke_console_write_grant", profile_id, grant_id, binding, server_id))
        return {"revoked": True}

    def list_namespace_page(self, profile_id, database, scope="user", page_size=None, cursor=None):
        self.calls.append(("list_namespace_page", profile_id, database, scope, page_size, cursor))
        entries = [{"name": name, "classification": "user", "system": False} for name in self.namespaces]
        return {
            "profileId": profile_id, "profileFingerprint": "a" * 64, "database": database, "scope": scope,
            "catalogFingerprint": "b" * 64, "entries": entries, "namespaces": self.namespaces,
            "page": {"pageSize": int(page_size or 100), "returned": len(entries), "hasMore": False, "nextCursor": None},
        }

    def namespace_exists(self, profile_id, database, namespace):
        self.calls.append(("namespace_exists", profile_id, database, namespace))
        return namespace in self.namespaces

    def list_relations(self, profile_id, database, namespace, **options):
        self.calls.append(("list_relations", profile_id, database, namespace, options))
        entries = [{"profileId": profile_id, "database": database, "namespace": namespace, "relation": item["name"], **item} for item in self.relations]
        return {
            "profileId": profile_id, "profileFingerprint": "a" * 64, "database": database, "namespace": namespace,
            "filter": {"kind": options.get("kind"), "search": options.get("search", "")}, "catalogFingerprint": "b" * 64,
            "entries": entries, "relations": self.relations,
            "page": {"pageSize": int(options.get("page_size") or 100), "returned": len(entries), "hasMore": False, "nextCursor": None},
        }

    def inspect_relation(self, profile_id, database, namespace, relation, expected_kind=None, expected_fingerprint=None):
        self.calls.append(("inspect_relation", profile_id, database, namespace, relation, expected_kind, expected_fingerprint))
        return self.descriptor or {
            "profileId": profile_id, "database": database, "namespace": namespace, "relation": relation,
            "kind": "table", "columns": [], "fingerprint": "a" * 64,
            "definition": {"status": "unavailable", "reason": "not_supported"},
        }

    def list_relation_lineage(self, profile_id, database, namespace, relation, direction, **options):
        self.calls.append(("list_relation_lineage", profile_id, database, namespace, relation, direction, options))
        return {
            "status": "available", "profileId": profile_id, "database": database, "namespace": namespace,
            "relation": relation, "kind": options["expected_kind"], "relationFingerprint": options["expected_fingerprint"],
            "direction": direction, "catalogFingerprint": "c" * 64, "items": [], "truncated": False,
            "page": {"pageSize": int(options.get("page_size") or 100), "returned": 0, "hasMore": False, "nextCursor": None},
        }

    def preview_relation_rows(self, profile_id, source, offset, limit):
        self.calls.append(("preview_relation_rows", profile_id, source, offset, limit))
        return {
            **source, "columns": [], "rows": self.preview_rows, "offset": offset,
            "nextOffset": offset + len(self.preview_rows), "hasMore": False, "stableOrder": False,
        }

    def verify_relation_source(self, profile_id, source):
        self.calls.append(("verify_relation_source", profile_id, source))
        return {"status": "verified", "matches": True, **source, "missingColumns": [], "addedColumns": [], "changedColumns": []}

    def execute_widget_query(self, profile_id, source, query):
        self.calls.append(("execute_widget_query", profile_id, source, query))
        return {"columns": [{"label": "Rows"}], "rows": [[1]], "sql": "SELECT count(*)", "parameters": []}

    def execute_temporal_series(self, profile_id, source, query, action, refresh_generation, series=None, window_start=None):
        self.calls.append(("execute_temporal_series", profile_id, source, query, action, refresh_generation, series, window_start))
        return {"seriesVersion": 1, "action": action, "refreshGeneration": refresh_generation}

    def execute_relation_detail(self, profile_id, source, query, selection, detail, offset, limit, sort, searches):
        self.calls.append(("execute_relation_detail", profile_id, source, query, selection, detail, offset, limit, sort, searches))
        return {"columns": [], "rows": [], "matchingRowCount": 0, "offset": offset, "limit": limit, "hasMore": False}

    def catalog_status(self, profile_id, namespace):
        self.calls.append(("catalog_status", profile_id, namespace))
        return {"profileId": profile_id, "namespace": namespace, "fingerprint": "live"}

    def test_profile(self, profile_id):
        self.calls.append(("test_profile", profile_id))
        return self.test_result

    def introspect(self, profile_id, namespace):
        self.calls.append(("introspect", profile_id, namespace))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}

    def preview(self, profile_id, namespace, schema, allow_destructive, *, persist=True):
        self.calls.append(("preview", profile_id, namespace, schema, allow_destructive, persist))
        return {"id": "plan_one" if persist else None, "previewOnly": not persist, "steps": [], "warnings": [], "blockingDifferences": [], "complete": True, "applyCapable": True}

    def preview_ai_migration(self, operation_id, profile_id, database, namespace, schema, allow_destructive, schema_binding, operation_timeout_ms=None):
        self.calls.append(("preview_ai_migration", operation_id, profile_id, database, namespace, schema, allow_destructive, schema_binding, operation_timeout_ms))
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_one", "reviewDigest": "c" * 64, "destructive": False, "steps": [], "warnings": [], "blockingDifferences": [], "complete": True, "applyCapable": True, "liveFingerprint": "live"}

    def apply_ai_migration(self, operation_id, plan_id, profile_id, database, namespace, expected_destructive, confirm_destructive, review_digest, operation_timeout_ms=None):
        self.calls.append(("apply_ai_migration", operation_id, plan_id, profile_id, database, namespace, expected_destructive, confirm_destructive, review_digest, operation_timeout_ms))
        return {"kind": "migration_applied", "operationId": operation_id, "planId": plan_id, "refreshedSchema": {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}}

    def reconcile_ai_migration(self, plan_id, profile_id):
        self.calls.append(("reconcile_ai_migration", plan_id, profile_id))
        return {"kind": "migration_applied", "planId": plan_id}

    def update_ai_migration_result(self, plan_id, result):
        self.calls.append(("update_ai_migration_result", plan_id, result))
        return result

    def preview_ai_insert_rows(self, operation_id, profile_id, database, namespace, relation, rows, schema_binding, operation_timeout_ms=None):
        self.calls.append(("preview_ai_insert_rows", operation_id, profile_id, database, namespace, relation, rows, schema_binding, operation_timeout_ms))
        effects = [{"kind": "constraints", "certainty": "certain", "summary": "PostgreSQL evaluates constraints", "objectCount": 0, "details": [], "complete": True, "truncated": False, "digest": "c" * 64}]
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_insert", "planDigest": "a" * 64, "effectsDigest": "d" * 64, "effects": effects, "kind": "insert_rows", "rowCount": len(rows), "submittedRowCount": len(rows), "columns": list(rows[0]), "rows": rows, "steps": [], "warnings": []}

    def preview_ai_create_view(self, operation_id, profile_id, database, namespace, relation, definition, schema_binding, operation_timeout_ms=None):
        self.calls.append(("preview_ai_create_view", operation_id, profile_id, database, namespace, relation, definition, schema_binding, operation_timeout_ms))
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_view", "planDigest": "b" * 64, "kind": "create_view", "steps": [{"action": "create", "objectType": "view", "name": relation, "sql": definition + ";", "destructive": False}], "warnings": []}

    def apply_ai_postgres_write(self, operation_id, plan_id, profile_id, database, namespace, relation, expected_kind, expected_review_digest, operation_timeout_ms=None):
        self.calls.append(("apply_ai_postgres_write", operation_id, plan_id, profile_id, database, namespace, relation, expected_kind, expected_review_digest, operation_timeout_ms))
        if expected_kind == "create_view" and hasattr(self, "_test_migrations"):
            self.view_expected_absent = True
            self.view_expectation = {"absent": True}
            self.view_saved_id = None
            result = self._test_migrations.apply("plan_view", expected_review_digest, False)
            return {**result, "kind": "view_created"}
        target = {"profileId": profile_id, "database": database, "namespace": namespace, "relation": relation}
        if expected_kind == "insert_rows":
            result = {"kind": "rows_inserted", "operationId": operation_id, "planId": plan_id, "target": target, "submittedRowCount": 2, "commandRowCount": 2, "insertedRowCount": 2, "secondaryWritesCounted": False, "effectsDigest": "d" * 64}
            self.ai_write_results[plan_id] = result
            return result
        result = {
            "kind": "view_created", "operationId": operation_id, "planId": plan_id, "target": target,
            "schemaBinding": {"schemaId": "schema_one", "revision": 1, "layoutToken": self.view_layout_token},
            "descriptor": {**target, "kind": "view", "fingerprint": "b" * 64},
            "desiredDefinition": f'CREATE VIEW "{namespace}"."{relation}" AS SELECT 1', "queryDefinition": "SELECT 1",
        }
        self.ai_write_results[plan_id] = result
        return result

    def reconcile_ai_postgres_write(self, plan_id, profile_id):
        self.calls.append(("reconcile_ai_postgres_write", plan_id, profile_id))
        return self.ai_write_results.get(plan_id, {"kind": "rows_inserted", "planId": plan_id, "submittedRowCount": 2, "commandRowCount": 2, "insertedRowCount": 2, "secondaryWritesCounted": False, "effectsDigest": "d" * 64})

    def update_ai_postgres_write_result(self, plan_id, result):
        self.calls.append(("update_ai_postgres_write_result", plan_id, result))
        self.ai_write_results[plan_id] = result
        return result

    def apply(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply", profile_id, plan_id, confirm_destructive))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}

    def preview_view_mutation(self, profile_id, database, namespace, relation, operation, expectation, desired, allow_destructive, schema_binding, *, operation_timeout_ms=None, **kwargs):
        call = ("preview_view_mutation", profile_id, database, namespace, relation, operation, expectation, desired, allow_destructive, schema_binding)
        self.calls.append(call if operation_timeout_ms is None else (*call, operation_timeout_ms))
        return {"id": "plan_view", "operation": operation, "destructive": operation == "delete", "steps": [], "warnings": []}

    def apply_view_mutation(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply_view_mutation", profile_id, plan_id, confirm_destructive))
        common = {
            "applied": True, "planId": plan_id,
            "operation": self.view_operation,
            "schemaBinding": {"schemaId": "schema_one", "expectedSchemaRevision": 1, "layoutToken": self.view_layout_token, "savedViewId": self.view_saved_id},
            "expectedAbsent": self.view_expected_absent,
        }
        if self.view_operation == "delete":
            return {**common, "deleted": {
                "profileId": profile_id, "database": "demo", "namespace": "public", "relation": "summary", "kind": self.view_expectation["kind"],
            }}
        return {**common,
            "descriptor": {
                "profileId": profile_id, "database": "demo", "namespace": "public", "relation": "summary",
                "kind": "view", "fingerprint": "b" * 64,
            },
            "desiredDefinition": 'CREATE OR REPLACE VIEW "public"."summary" AS SELECT 2',
            "queryDefinition": "SELECT 2",
        }

    def view_mutation_binding(self, profile_id, plan_id):
        self.calls.append(("view_mutation_binding", profile_id, plan_id))
        return {
            "schemaBinding": {"schemaId": "schema_one", "expectedSchemaRevision": 1, "layoutToken": self.view_layout_token, "savedViewId": self.view_saved_id},
            "database": "demo", "namespace": "public", "relation": "summary",
            "operation": self.view_operation, "expectation": self.view_expectation,
        }
