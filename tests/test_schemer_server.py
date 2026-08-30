import json
import sys
import tempfile
import unittest
import urllib.parse
from http.client import HTTPConnection
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_http import PostgresHttpMixin
from schemii.dashboard_store import MAX_DASHBOARD_BYTES, DashboardStore
from schemii.legacy_source_upgrade import (
    MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH,
    MAX_LEGACY_SOURCE_UPGRADE_REQUEST_BODY_BYTES,
)
from schemii.schemer_examples import MERCURY_PROFILE_ID
from schemii.schemer_server import _ai_catalog_sources, make_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer
from tests.fake_metadata_authority import FakeAiMaintenance, FakeSchemiiAuthority
from tests.test_server import FakeAIService
from tests.capability_test_support import capabilities_for_formatted_type, column


class FakeSchemerAuthority(FakeSchemiiAuthority):
    settings_application = "schemer"

    def __init__(self):
        super().__init__()
        self.chats.clear()
        self.put_chat("ses_1", "dashboard_mercury", "ses_1", access_level="dashboard")

    def put_chat(self, chat_id, dashboard_id, external_id, target=None, access_level="dashboard"):
        capabilities = {
            "metadata": ["metadata"],
            "dashboard": ["metadata", "dashboard"],
            "data": ["metadata", "dashboard", "data"],
        }[access_level]
        self.chats[chat_id] = {
            "id": chat_id, "dashboardId": dashboard_id, "externalSessionId": external_id,
            "title": "Dashboard chat", "contextTitle": "Dashboard chat", "conversationTitle": None,
            "target": target or {}, "accessLevel": access_level,
            "capabilities": capabilities, "policyRevision": 1, "state": "active",
        }
        return self.get_chat(chat_id)

    def provision_chat(self, dashboard_id):
        chat_id = str(__import__("uuid").uuid4())
        self.chats[chat_id] = {"id": chat_id, "dashboardId": dashboard_id, "state": "provisioning"}
        return {"chatId": chat_id}

    def bind_external_session(self, chat_id, external_id, title):
        self.chats[chat_id].update({"externalSessionId": external_id, "title": title, "contextTitle": title, "conversationTitle": None})

    def activate_chat(self, chat_id, target, access_level):
        current = self.chats[chat_id]
        return self.put_chat(chat_id, current["dashboardId"], current["externalSessionId"], target, access_level)

    def list_chats(self, dashboard_id=None):
        return [self.get_chat(key) for key, value in self.chats.items() if value.get("state") == "active" and (dashboard_id is None or value["dashboardId"] == dashboard_id)]

    @staticmethod
    def policy_binding(chat, action):
        capability = "data" if action.get("type") == "read_query" else "metadata" if action.get("type") == "dashboard_open" else "dashboard"
        return {"capability": capability, "configuredMode": "every_action", "effectiveMode": "every_action", "policyRevision": chat["policyRevision"], "origin": "model"}


class SchemerServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService(
            profiles=[{
                "id": "shared", "name": "Shared", "host": "postgres", "port": 5432,
                "dbname": "schemii", "user": "schemii", "sslmode": "disable", "timeout": 10,
            }],
            namespaces=["bookstore", "public"],
            relations=[{"name": "orders", "kind": "table"}],
            descriptor={
                "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
                "kind": "table", "snapshotVersion": 2, "columns": [{**column("id", "bigint", False, 1, oid=20, category="N", name="int8", numeric=True, pattern=False, aggregates=("count", "sum", "average", "minimum", "maximum")), "suggestions": ["dimension", "identifier"]}],
                "fingerprint": "a" * 64,
                "definition": {"status": "unavailable", "reason": "not_supported"},
            },
            preview_rows=[{"id": 1}],
            test_result={"ok": True, "database": "schemii"},
        )
        self.dashboard_store = DashboardStore(Path(self.temporary_directory.name) / "dashboards")
        self.dashboard_store.initialize_once()
        self.ai_service = FakeAIService()
        self.authority = FakeSchemerAuthority()
        self.ai_maintenance = FakeAiMaintenance(self.authority)
        handler = make_handler(
            ROOT / "src" / "schemii" / "schemer_web",
            self.service,
            self.dashboard_store,
            "session-token",
            server_id="schemer-server",
            ai_authority=self.authority,
            ai_service=self.ai_service,
            ai_maintenance=self.ai_maintenance,
        )
        self.assertTrue(issubclass(handler, PostgresHttpMixin))
        self.http = RunningHttpServer(handler)

    def tearDown(self):
        self.http.close()
        self.dashboard_store.close()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, authorized=False, headers=None):
        return self.http.request(path, method, payload, authorized=authorized, headers=headers)

    def test_ai_settings_route_is_schemer_scoped(self):
        status, body, _ = self.request("/api/ai/settings", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["application"], "schemer")

        status, body, _ = self.request("/api/ai/settings", "PUT", {"expectedRevision": 9, "policy": {}}, authorized=True)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "policy_changed")

    def test_ai_widget_rename_executes_server_side_and_reconciles(self):
        record = self.dashboard_store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{"type": "widget_rename", "dashboardId": record["id"], "expectedRevision": record["revision"], "widgetId": widget["id"], "currentTitle": widget["title"], "title": "Renamed", "requiresConfirmation": True}]}
        message = {"text": "Rename it", "model": {}}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}"
        execute = {"confirmation": {"accepted": True, "mode": "every_action"}}
        operation = json.loads(self.request(path + "/execute", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["result"]["kind"], "dashboard_saved")
        self.assertEqual(self.dashboard_store.get(record["id"])["dashboard"]["widgets"][0]["title"], "Renamed")
        repeated = json.loads(self.request(path + "/execute", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual((repeated["id"], repeated["state"]), (operation["id"], "succeeded"))
        reconciled = json.loads(self.request(path + "/reconcile", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(reconciled["id"], operation["id"])

    def test_ai_dashboard_create_uses_deterministic_server_identity(self):
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{"type": "dashboard_create", "title": "Operations", "requiresConfirmation": True}]}
        message = {"text": "Create it", "model": {}}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        execute = {"confirmation": {"accepted": True, "mode": "every_action"}}
        operation = json.loads(self.request(path, "POST", execute, authorized=True)[1])["operation"]
        self.assertTrue(operation["result"]["dashboardId"].startswith("dashboard_"))
        self.assertEqual(self.dashboard_store.get(operation["result"]["dashboardId"])["dashboard"]["title"], "Operations")

    def test_ai_analytic_query_cancellation_uses_shared_operation_boundary(self):
        proposal = self.authority.create_proposal(
            "ses_1", {"type": "read_query", "sql": "SELECT pg_sleep(30)"},
            {"policyRevision": 1}, {}, {},
        )
        operation, _ = self.authority.authorize_and_claim(proposal["id"], "ses_1", 1, None)

        status, body, _ = self.request(
            f"/api/ai/sessions/ses_1/proposals/{proposal['id']}/execution", "DELETE", authorized=True,
        )

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["cancellation"]["requested"])
        self.assertEqual(payload["operation"]["id"], operation["id"])
        self.assertIn(("cancel_read_only_sql", operation["id"]), self.service.calls)

    def test_ai_lease_loss_after_dashboard_write_is_uncertain_and_not_replayed(self):
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{"type": "dashboard_create", "title": "Lease Lost", "requiresConfirmation": True}]}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", {"text": "Create it", "model": {}}, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        execute = {"confirmation": {"accepted": True, "mode": "every_action"}}
        self.ai_maintenance.lost = True
        status, body, _ = self.request(path, "POST", execute, authorized=True)
        operation = json.loads(body)["error"]["details"]["operation"]
        self.assertEqual((status, operation["state"]), (409, "uncertain"))
        dashboards = [item for item in self.dashboard_store.list() if item["dashboard"]["title"] == "Lease Lost"]
        self.assertEqual(len(dashboards), 1)
        repeated = json.loads(self.request(path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(repeated["state"], "uncertain")
        self.assertEqual(len([item for item in self.dashboard_store.list() if item["dashboard"]["title"] == "Lease Lost"]), 1)

    def test_ai_dashboard_binding_preflight_rejects_before_claim(self):
        record = self.dashboard_store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "widget_rename", "dashboardId": record["id"], "expectedRevision": record["revision"],
            "widgetId": widget["id"], "currentTitle": widget["title"], "title": "Stale rename",
            "requiresConfirmation": True,
        }]}
        _, body, _ = self.request(
            "/api/ai/sessions/ses_1/messages", "POST", {"text": "Rename it", "model": {}}, authorized=True,
        )
        proposal = json.loads(body)["proposals"][0]
        changed = self.dashboard_store.get(record["id"])
        changed["dashboard"]["title"] = "Changed before execution"
        self.dashboard_store.save(changed["id"], changed)
        operations_before = set(self.authority.operations)

        status, response, _ = self.request(
            f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST",
            {"confirmation": {"accepted": True, "mode": "every_action"}}, authorized=True,
        )

        self.assertEqual((status, json.loads(response)["error"]["code"]), (409, "dashboard_changed"))
        self.assertEqual(set(self.authority.operations), operations_before)

    def test_configured_widget_builder_sanitizes_catalog_columns(self):
        from schemii.schemer_server import _configured_ai_widget
        query = {"version": 2, "dimensions": [], "measures": [{"id": "measure_orders", "label": "Orders", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}], "filters": [], "sort": [], "limit": 100}
        action = {"title": "Orders", "source": {"profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders", "kind": "table", "fingerprint": "a" * 64}, "query": query, "visualizationMode": "kpi"}
        widget = _configured_ai_widget(self.service, action, "operation_widget", 1)
        self.assertEqual(set(widget["configuration"]["source"]["columns"][0]), {"name", "type", "nullable", "ordinal", "capabilities"})
        record = self.dashboard_store.get("dashboard_mercury")
        saved = self.dashboard_store.apply_ai_mutation(record["id"], "operation_widget", record["revision"], {"type": "widget_create", "title": "Orders"}, widget)
        self.assertEqual(saved["kind"], "dashboard_saved")
        self.assertIn((
            "execute_widget_query", "shared", widget["configuration"]["source"], query,
            {"operation_timeout_ms": None, "operation_id": "operation_widget"},
        ), self.service.calls)

    def test_ai_query_rows_are_ephemeral_but_result_reference_and_evidence_are_durable(self):
        record = self.dashboard_store.get("dashboard_mercury")
        target = {"profileId": "shared", "database": "schemii", "namespace": "bookstore"}
        self.authority.put_chat(
            "ses_1", record["id"], "ses_1",
            {**target, "profileFingerprint": self.service.profile_context_fingerprint("shared")}, "data",
        )
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "read_query", "dashboardId": record["id"], "expectedRevision": record["revision"],
            **target, "sql": "SELECT 1 AS answer", "purpose": "Return one answer",
            "readOnly": True, "requiresConfirmation": True,
        }]}
        _, body, _ = self.request(
            "/api/ai/sessions/ses_1/messages", "POST", {"text": "Run it", "model": {}}, authorized=True,
        )
        proposal = json.loads(body)["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        status, body, _ = self.request(
            path, "POST", {"confirmation": {"accepted": True, "mode": "every_action"}}, authorized=True,
        )

        operation = json.loads(body)["operation"]
        durable = self.authority.operations[operation["id"]]["result"]
        self.assertEqual(status, 200)
        self.assertEqual(operation["result"]["display"]["rows"], [[1]])
        self.assertNotIn("display", durable)
        self.assertEqual(durable["evidence"], {"rowCount": 1, "columnCount": 1, "truncated": False})
        self.assertIn(durable["resultRef"], self.authority.results)
        self.assertEqual(self.authority.results[durable["resultRef"]]["payload"]["rows"], [[1]])

    def test_static_shared_assets_and_session(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Schemer", body)
        self.assertEqual(self.request("/shared/theme.css")[0], 200)
        self.assertEqual(self.request("/shared/postgres-client.js")[0], 200)
        self.assertEqual(self.request("/shared/ui-components.js")[0], 200)
        self.assertEqual(self.request("/shared/../server.py")[0], 404)
        status, body, _ = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"token": "session-token", "serverId": "schemer-server"})
        status, body, _ = self.request("/api/readiness")
        self.assertEqual(status, 200)
        readiness = json.loads(body)
        self.assertEqual(readiness["metadata"]["ok"], True)
        self.assertEqual(readiness["components"]["dashboardStore"]["status"], "available")
        self.assertEqual(readiness["components"]["dashboardStore"]["recordCount"], 1)
        self.assertEqual(readiness["components"]["postgresExecution"]["classes"]["read"]["capacity"], 8)
        self.assertEqual(readiness["components"]["postgresExecution"]["targets"], {})
        self.assertEqual(readiness["components"]["httpAccess"]["mode"], "loopback-only")
        status, body, _ = self.request("/api/dashboards/summary", authorized=True)
        self.assertEqual(status, 200)
        summary = json.loads(body)["summaries"][0]
        self.assertEqual(summary["id"], "dashboard_mercury")
        self.assertNotIn("widgets", summary)

    def test_dashboard_corruption_is_visible_in_list_and_structured_readiness(self):
        broken = self.dashboard_store.dashboard_dir / "broken.json"
        broken.write_text("not-json", encoding="utf-8")
        try:
            status, body, _ = self.request("/api/dashboards/summary", authorized=True)
            self.assertEqual(status, 500)
            self.assertEqual(json.loads(body)["error"]["code"], "dashboard_record_malformed")

            status, body, _ = self.request("/api/readiness")
            report = json.loads(body)
            self.assertEqual(status, 503)
            self.assertFalse(report["ready"])
            self.assertEqual(report["components"]["dashboardStore"]["status"], "unavailable")
            self.assertEqual(report["components"]["dashboardStore"]["error"]["code"], "dashboard_record_malformed")
            self.assertIn("metadata", report["components"])
        finally:
            broken.unlink(missing_ok=True)

    def test_dashboard_body_limit_returns_413(self):
        connection = HTTPConnection("127.0.0.1", self.http.server.server_port, timeout=5)
        connection.putrequest("POST", "/api/dashboards")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_DASHBOARD_BYTES + 1))
        connection.putheader("X-Schemii-Token", "session-token")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        connection.close()
        self.assertEqual(response.status, 413)
        error = json.loads(body)["error"]
        self.assertEqual(error["code"], "request_too_large")
        self.assertEqual(error["details"]["limit"], MAX_DASHBOARD_BYTES)

    def test_dashboard_summary_api_is_cursor_paged_and_rejects_tampering_and_staleness(self):
        self.dashboard_store.create("Alpha")
        status, body, _ = self.request("/api/dashboards/summary?pageSize=1", authorized=True)
        first = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["summaries"]), 1)
        self.assertTrue(first["page"]["hasMore"])
        cursor = first["page"]["nextCursor"]

        tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
        status, body, _ = self.request(
            "/api/dashboards/summary?" + urllib.parse.urlencode({"pageSize": 1, "cursor": tampered}), authorized=True,
        )
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, "invalid_dashboard_cursor"))

        self.dashboard_store.create("Changed")
        status, body, _ = self.request(
            "/api/dashboards/summary?" + urllib.parse.urlencode({"pageSize": 1, "cursor": cursor}), authorized=True,
        )
        self.assertEqual((status, json.loads(body)["error"]["code"]), (409, "dashboard_cursor_stale"))

    def test_unparameterized_dashboard_lists_remain_complete_while_explicit_pages_are_bounded(self):
        for index in range(55):
            self.dashboard_store.create(f"Dashboard {index:02d}")

        status, body, _ = self.request("/api/dashboards", authorized=True)
        complete = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(complete["dashboards"]), 56)
        self.assertNotIn("page", complete)

        status, body, _ = self.request("/api/dashboards?pageSize=10", authorized=True)
        paged = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(paged["dashboards"]), 10)
        self.assertTrue(paged["page"]["hasMore"])

    def test_revision_bound_sql_uses_lock_free_dashboard_snapshot(self):
        revision = self.dashboard_store.get("dashboard_mercury")["revision"]
        original = self.service.execute_read_only_sql

        def execute_while_dashboard_changes(*args, **kwargs):
            finished = __import__("threading").Event()

            def save():
                current = self.dashboard_store.get("dashboard_mercury")
                current["dashboard"]["title"] = "Changed while PostgreSQL runs"
                self.dashboard_store.save(current["id"], current)
                finished.set()

            thread = __import__("threading").Thread(target=save)
            thread.start()
            if not finished.wait(1):
                raise AssertionError("PostgreSQL ran while the dashboard lock was held")
            thread.join()
            return original(*args, **kwargs)

        self.service.execute_read_only_sql = execute_while_dashboard_changes
        payload = {
            "database": "schemii", "namespace": "bookstore", "sql": "SELECT 1", "profileFingerprint": "confirmed-profile",
            "dashboardId": "dashboard_mercury", "expectedRevision": revision,
        }
        status, _, _ = self.request("/api/postgres/profiles/shared/sql", "POST", payload, True)
        self.assertEqual(status, 200)
        self.assertEqual(self.dashboard_store.get("dashboard_mercury")["revision"], revision + 1)

    def test_shared_connection_routes_match_schemii_contract(self):
        self.assertEqual(self.request("/api/postgres/profiles")[0], 403)
        status, body, _ = self.request("/api/postgres/profiles", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["profiles"][0]["id"], "shared")
        self.assertEqual(self.request("/api/postgres/profiles/shared/namespaces?database=schemii", authorized=True)[0], 200)
        relation_path = "/api/postgres/profiles/shared/relations?database=schemii&namespace=bookstore"
        status, body, _ = self.request(relation_path, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["relations"][0], {"name": "orders", "kind": "table"})
        inspect_path = "/api/postgres/profiles/shared/relation?database=schemii&namespace=bookstore&relation=orders"
        status, body, _ = self.request(inspect_path, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["columns"][0]["type"], "bigint")
        self.assertEqual(json.loads(body)["columns"][0]["suggestions"], ["dimension", "identifier"])
        self.assertEqual(json.loads(body)["definition"], {"status": "unavailable", "reason": "not_supported"})
        self.assertNotIn("password", body.decode())
        self.assertEqual(self.request("/api/postgres/profiles/shared/test", "POST", {}, True)[0], 200)
        self.assertIn(("list_namespace_page", "shared", "schemii", "user", None, None), self.service.calls)
        self.assertIn(("list_relations", "shared", "schemii", "bookstore", {}), self.service.calls)
        self.assertIn(("inspect_relation", "shared", "schemii", "bookstore", "orders", None, None), self.service.calls)
        preview_source = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }
        preview_path = "/api/postgres/profiles/shared/relation/preview"
        status, body, _ = self.request(preview_path, "POST", {"source": preview_source, "limit": 20}, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["rows"], [{"id": 1}])
        self.assertIn(("preview_relation_rows", "shared", preview_source, 0, 20), self.service.calls)
        verify_path = "/api/postgres/profiles/shared/relation/verify"
        status, body, _ = self.request(verify_path, "POST", {"source": preview_source}, True)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["matches"])
        self.assertIn(("verify_relation_source", "shared", preview_source), self.service.calls)
        query = {"version": 1, "measures": []}
        query_path = "/api/postgres/profiles/shared/relation/query"
        self.assertEqual(self.request(query_path, "POST", {"source": preview_source, "query": query})[0], 403)
        status, body, _ = self.request(query_path, "POST", {"source": preview_source, "query": query}, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sql"], "SELECT count(*)")
        self.assertIn(("execute_widget_query", "shared", preview_source, query), self.service.calls)
        revision = self.dashboard_store.get("dashboard_mercury")["revision"]
        guarded_query = {"source": preview_source, "query": query, "dashboardId": "dashboard_mercury", "expectedRevision": revision}
        self.assertEqual(self.request(query_path, "POST", guarded_query, True)[0], 200)
        self.assertEqual(self.request(query_path, "POST", {**guarded_query, "expectedRevision": revision + 1}, True)[0], 409)
        self.assertEqual(self.request(query_path, "POST", {"source": preview_source, "query": query, "sql": "SELECT 1"}, True)[0], 400)
        series_path = "/api/postgres/profiles/shared/relation/temporal-series"
        manifest = {"source": preview_source, "query": query, "action": "manifest", "refreshGeneration": "refresh-one"}
        self.assertEqual(self.request(series_path, "POST", manifest)[0], 403)
        self.assertEqual(self.request(series_path, "POST", manifest, True)[0], 400)
        mercury = self.dashboard_store.get("dashboard_mercury")
        trend = next(item for item in mercury["dashboard"]["widgets"] if item["id"] == "widget_trend")
        series_source = {**preview_source, "snapshotVersion": 2, "columns": [
            {"name": "ordered_on", "type": "date", "nullable": False, "ordinal": 1, "capabilities": capabilities_for_formatted_type("date")},
            {"name": "amount", "type": "numeric", "nullable": True, "ordinal": 2, "capabilities": capabilities_for_formatted_type("numeric")},
        ]}
        series_query = {
            "version": 2,
            "dimensions": [{"id": "dimension_date", "label": "Date", "column": "ordered_on"}],
            "measures": [{"id": "measure_amount", "label": "Amount", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "decimal", "fractionDigits": 2}}],
            "filters": [], "sort": [], "limit": 500,
        }
        trend["kind"] = "aggregate_report"
        trend["configuration"] = {
            "source": series_source, "query": series_query,
            "visualization": {"version": 1, "mode": "line", "selections": {
                "kpi": {"measureIds": ["measure_amount"]},
                "bar": {"dimensionId": "dimension_date", "measureIds": ["measure_amount"]},
                "line": {"dimensionId": "dimension_date", "measureIds": ["measure_amount"]},
                "donut": {"dimensionId": "dimension_date", "measureId": "measure_amount"},
            }},
            "detail": {"version": 1, "columns": [{"sourceColumn": "ordered_on", "label": "Date", "width": 160, "hidden": False, "searchable": True, "numberFormat": {"style": "auto"}}], "defaultSort": None, "rowIdentifier": None, "pageSize": 25},
        }
        mercury = self.dashboard_store.save("dashboard_mercury", mercury)
        revision = mercury["revision"]
        saved_query_path = "/api/postgres/profiles/shared/saved-widgets/aggregate"
        saved_binding = {"dashboardId": "dashboard_mercury", "expectedRevision": revision, "widgetId": "widget_trend"}
        self.assertEqual(self.request(saved_query_path, "POST", saved_binding, True)[0], 200)
        self.assertEqual(self.request(saved_query_path, "POST", {**saved_binding, "source": preview_source}, True)[0], 400)
        self.assertEqual(self.request(saved_query_path, "POST", {**saved_binding, "expectedRevision": revision + 1}, True)[0], 409)
        guarded_manifest = {**manifest, "source": series_source, "query": series_query, "dashboardId": "dashboard_mercury", "expectedRevision": revision, "widgetId": "widget_trend"}
        self.assertEqual(self.request(series_path, "POST", guarded_manifest, True)[0], 200)
        self.assertIn(("execute_temporal_series", "shared", guarded_manifest["source"], guarded_manifest["query"], "manifest", "refresh-one", None, None), self.service.calls)
        series = {
            "key": "a" * 64, "dimensionId": "dimension_date", "sourceType": "date", "interpretation": "utc",
            "bucketSeconds": 86400, "windowBucketCount": 48, "pointLimit": 500,
            "alignedStart": "2026-01-01T00:00:00Z", "alignedEndExclusive": "2026-03-01T00:00:00Z",
        }
        window = {**guarded_manifest, "action": "window", "series": series, "windowStart": "2026-01-01T00:00:00Z"}
        self.assertEqual(self.request(series_path, "POST", window, True)[0], 200)
        self.assertIn(("execute_temporal_series", "shared", guarded_manifest["source"], guarded_manifest["query"], "window", "refresh-one", series, "2026-01-01T00:00:00Z"), self.service.calls)
        self.assertEqual(self.request(series_path, "POST", {**window, "expectedRevision": revision + 1}, True)[0], 409)
        self.assertEqual(self.request(series_path, "POST", {**guarded_manifest, "query": query}, True)[0], 409)
        self.assertEqual(self.request(series_path, "POST", {**guarded_manifest, "widgetId": "widget_orders"}, True)[0], 409)
        self.assertEqual(self.request(series_path, "POST", {**manifest, "windowStart": "2026-01-01T00:00:00Z"}, True)[0], 400)
        detail_request = {
            "source": preview_source, "query": query, "selection": {"dimensions": []},
            "detail": {"version": 1, "columns": [], "rowIdentifier": None},
            "offset": 0, "limit": 20, "sort": None, "searches": [],
        }
        detail_path = "/api/postgres/profiles/shared/relation/detail"
        self.assertEqual(self.request(detail_path, "POST", detail_request)[0], 403)
        status, body, _ = self.request(detail_path, "POST", detail_request, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["matchingRowCount"], 0)
        self.assertEqual(json.loads(body)["nextOffset"], 0)
        self.assertIn((
            "execute_relation_detail", "shared", preview_source, query, detail_request["selection"],
            detail_request["detail"], 0, 20, None, [],
        ), self.service.calls)
        revision = self.dashboard_store.get("dashboard_mercury")["revision"]
        guarded_detail = {**detail_request, "dashboardId": "dashboard_mercury", "expectedRevision": revision}
        self.assertEqual(self.request(detail_path, "POST", guarded_detail, True)[0], 200)
        self.assertEqual(self.request(detail_path, "POST", {**guarded_detail, "expectedRevision": revision + 1}, True)[0], 409)
        self.assertEqual(self.request(detail_path, "POST", {**detail_request, "extra": True}, True)[0], 400)
        legacy_request = {key: value for key, value in detail_request.items() if key != "searches"}
        legacy_request["search"] = "old global search"
        self.assertEqual(self.request(detail_path, "POST", legacy_request, True)[0], 400)
        saved_detail_path = "/api/postgres/profiles/shared/saved-widgets/detail"
        saved_detail = {"dashboardId": "dashboard_mercury", "expectedRevision": revision, "widgetId": "widget_trend", "selection": {"dimensions": []}, "offset": 0, "limit": 20, "sort": None, "searches": []}
        self.assertEqual(self.request(saved_detail_path, "POST", saved_detail, True)[0], 200)
        self.assertEqual(self.request(saved_detail_path, "POST", {**saved_detail, "query": query}, True)[0], 400)
        self.assertIn(("test_profile", "shared"), self.service.calls)

    def test_dashboard_slicers_apply_to_preview_saved_detail_and_temporal_execution(self):
        source = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore",
            "relation": "orders", "kind": "table", "fingerprint": "a" * 64,
            "snapshotVersion": 2,
            "columns": [
                {**column("ordered_on", "date", False, 1, oid=1082, category="D", name="date", temporal="date", pattern=False)},
                {**column("amount", "numeric", True, 2, oid=1700, category="N", name="numeric", numeric=True, pattern=False, aggregates=("count", "sum", "average", "minimum", "maximum"))},
            ],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_date", "label": "Date", "column": "ordered_on"}],
            "measures": [{
                "id": "measure_amount", "label": "Amount", "column": "amount", "aggregation": "sum",
                "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "decimal", "fractionDigits": 2},
            }],
            "filters": [], "sort": [], "limit": 100,
        }
        dashboard = self.dashboard_store.get("dashboard_mercury")
        widget = next(item for item in dashboard["dashboard"]["widgets"] if item["id"] == "widget_trend")
        widget["kind"] = "preview"
        widget["configuration"] = {"source": source}
        dashboard = self.dashboard_store.save(dashboard["id"], dashboard)

        preview_path = "/api/postgres/profiles/shared/dashboard-widgets/preview"
        preview_request = {
            "dashboardId": dashboard["id"], "expectedRevision": dashboard["revision"],
            "widgetId": widget["id"], "query": query,
        }
        status, body, _ = self.request(preview_path, "POST", preview_request, True)
        self.assertEqual(status, 200)
        first_preview = json.loads(body)
        self.assertEqual(first_preview["effectiveQuery"], query)
        self.assertEqual(first_preview["slicerLineage"], [])
        resource = first_preview["resultResource"]
        page_path = f"/api/postgres/profiles/shared/structured-results/{resource['id']}?cursor=first"
        self.assertEqual(self.request(
            page_path, authorized=True, headers={"X-Schemer-Result-Binding": resource["binding"]},
        )[0], 200)

        dashboard = self.dashboard_store.get(dashboard["id"])
        widget = next(item for item in dashboard["dashboard"]["widgets"] if item["id"] == "widget_trend")
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": source, "query": query,
            "visualization": {"version": 1, "mode": "line", "selections": {
                "kpi": {"measureIds": ["measure_amount"]},
                "bar": {"dimensionId": "dimension_date", "measureIds": ["measure_amount"]},
                "line": {"dimensionId": "dimension_date", "measureIds": ["measure_amount"]},
                "donut": {"dimensionId": "dimension_date", "measureId": "measure_amount"},
            }},
            "detail": {
                "version": 1,
                "columns": [{
                    "sourceColumn": "ordered_on", "label": "Date", "width": 160,
                    "hidden": False, "searchable": True, "numberFormat": {"style": "auto"},
                }],
                "defaultSort": None, "rowIdentifier": None, "pageSize": 25,
            },
        }
        dashboard["dashboard"]["slicers"] = [{
            "id": "slicer_dates", "kind": "date_range", "title": "Order dates",
            "range": {"start": "2026-01-01", "endExclusive": "2026-02-01"},
            "bindings": [{"widgetId": widget["id"], "sourceColumn": "ordered_on"}],
        }]
        dashboard = self.dashboard_store.save(dashboard["id"], dashboard)
        binding = {
            "dashboardId": dashboard["id"], "expectedRevision": dashboard["revision"], "widgetId": widget["id"],
        }

        for path, request in (
            ("/api/postgres/profiles/shared/saved-widgets/aggregate", binding),
            (preview_path, {**binding, "query": {**query, "limit": 25}}),
        ):
            with self.subTest(path=path):
                status, body, _ = self.request(path, "POST", request, True)
                result = json.loads(body)
                self.assertEqual(status, 200)
                self.assertEqual([item["operator"] for item in result["effectiveQuery"]["filters"][0]["conditions"]], ["gte", "lt"])
                self.assertEqual(result["slicerLineage"][0]["slicerId"], "slicer_dates")

        detail_request = {
            **binding, "selection": {"dimensions": []}, "offset": 0, "limit": 25,
            "sort": None, "searches": [],
        }
        status, body, _ = self.request(
            "/api/postgres/profiles/shared/saved-widgets/detail", "POST", detail_request, True,
        )
        detail_result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(detail_result["slicerLineage"][0]["sourceColumn"], "ordered_on")
        self.assertEqual(len(detail_result["effectiveQuery"]["filters"][0]["conditions"]), 2)

        temporal_request = {
            **binding, "source": source, "query": query,
            "action": "manifest", "refreshGeneration": "slicer-refresh",
        }
        status, body, _ = self.request(
            "/api/postgres/profiles/shared/relation/temporal-series", "POST", temporal_request, True,
        )
        temporal_result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(temporal_result["slicerLineage"][0]["slicerId"], "slicer_dates")
        self.assertEqual(len(temporal_result["effectiveQuery"]["filters"][0]["conditions"]), 2)

        self.assertEqual(self.request(
            "/api/postgres/profiles/shared/saved-widgets/aggregate", "POST",
            {**binding, "expectedRevision": dashboard["revision"] + 1}, True,
        )[0], 409)

    def test_schema_design_routes_report_the_application_policy_limitation(self):
        routes = (
            ("/api/postgres/profiles/shared/fingerprint?namespace=bookstore", "GET"),
            ("/api/postgres/profiles/shared/data?namespace=bookstore&table=orders", "GET"),
            ("/api/postgres/profiles/shared/introspect", "POST"),
        )
        for path, method in routes:
            with self.subTest(path=path):
                status, body, _ = self.request(path, method, {}, True)
                error = json.loads(body)["error"]
                self.assertEqual((status, error["code"]), (403, "capability_unavailable"))
                self.assertEqual(error["details"]["application"], "schemer")
                self.assertEqual(error["details"]["requiredCapability"], "schema")
                self.assertIn("Schemii", error["details"]["safeAlternative"])

    def test_structured_result_routes_bind_session_revision_export_and_cancellation(self):
        self.assertEqual(self.request("/")[0], 200)
        session_status, session_body, _ = self.request("/api/session")
        self.assertEqual(session_status, 200)
        self.assertEqual(json.loads(session_body)["serverId"], "schemer-server")
        readiness_status, readiness_body, _ = self.request("/api/readiness")
        self.assertEqual(readiness_status, 200)
        structured = json.loads(readiness_body)["components"]["structuredResults"]
        self.assertEqual(structured["capacity"], 108)
        self.assertEqual(structured["aggregateResults"]["capacity"], 100)
        self.assertEqual(structured["detailResults"]["capacity"], 8)
        source = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }
        query_path = "/api/postgres/profiles/shared/relation/query"
        query = {"version": 1, "measures": []}
        status, body, _ = self.request(query_path, "POST", {"source": source, "query": query}, True)
        self.assertEqual(status, 200)
        contextless = json.loads(body)["resultResource"]

        def result_path(resource, *, suffix="", extra="cursor=first"):
            query = f"?{extra}" if extra else ""
            return f"/api/postgres/profiles/shared/structured-results/{resource['id']}{suffix}{query}"

        def result_headers(resource, binding=None):
            return {"X-Schemer-Result-Binding": binding if binding is not None else resource["binding"]}

        self.assertEqual(self.request(result_path(contextless))[0], 403)
        self.assertEqual(self.request(result_path(contextless), authorized=True)[0], 400)
        self.assertEqual(self.request(result_path(contextless), authorized=True, headers=result_headers(contextless))[0], 200)
        self.assertEqual(self.request(result_path(contextless), authorized=True, headers=result_headers(contextless, "wrong"))[0], 404)
        self.assertEqual(self.request(
            result_path(contextless) + f"&binding={urllib.parse.quote(contextless['binding'], safe='')}",
            authorized=True, headers=result_headers(contextless),
        )[0], 400)
        export_path = result_path(contextless, suffix="/export", extra="format=json")
        status, export_body, headers = self.request(export_path, authorized=True, headers=result_headers(contextless))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertIn("result.json", headers["Content-Disposition"])
        self.assertIn("rows", json.loads(export_body))

        revision = self.dashboard_store.get("dashboard_mercury")["revision"]
        guarded_payload = {
            "source": source, "query": query,
            "dashboardId": "dashboard_mercury", "expectedRevision": revision,
        }
        status, body, _ = self.request(query_path, "POST", guarded_payload, True)
        self.assertEqual(status, 200)
        guarded = json.loads(body)["resultResource"]
        self.assertEqual(self.request(result_path(guarded), authorized=True, headers=result_headers(guarded))[0], 200)

        dashboard = self.dashboard_store.get("dashboard_mercury")
        dashboard["dashboard"]["title"] = "Revision changed after query execution"
        self.dashboard_store.save("dashboard_mercury", dashboard)
        status, stale_body, _ = self.request(result_path(guarded), authorized=True, headers=result_headers(guarded))
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(stale_body)["error"]["code"], "dashboard_changed")
        self.assertEqual(self.request(result_path(contextless), authorized=True, headers=result_headers(contextless))[0], 200)

        self.assertEqual(self.request(
            result_path(contextless, extra=""), "DELETE", authorized=True, headers=result_headers(contextless),
        )[0], 200)
        status, closed_body, _ = self.request(
            result_path(contextless), authorized=True, headers=result_headers(contextless),
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(closed_body)["error"]["code"], "result_cancelled")

    def test_read_sql_route_is_strict_and_uses_schemer_policy(self):
        path = "/api/postgres/profiles/shared/sql"
        self.assertEqual(self.request(path, "POST", {"namespace": "bookstore", "sql": "SELECT 1"}, True)[0], 400)
        payload = {
            "database": "schemii", "namespace": "bookstore", "sql": "SELECT 1", "profileFingerprint": "confirmed-profile",
            "dashboardId": "dashboard_mercury", "expectedRevision": self.dashboard_store.get("dashboard_mercury")["revision"],
        }
        self.assertEqual(self.request(path, "POST", payload, True)[0], 200)
        self.assertEqual(self.service.calls[-1], (
            "execute_read_only_sql", "shared", "bookstore", "SELECT 1", {
                "database": "schemii", "expected_profile_fingerprint": "confirmed-profile", "allow_explain": False, "max_rows": 100,
                "max_columns": 50, "max_result_bytes": 256 * 1024,
            },
        ))
        self.assertEqual(self.request(path, "POST", {**payload, "unknown": True}, True)[0], 400)

    def test_ai_routes_use_schemer_context_and_local_session(self):
        self.assertEqual(self.request("/api/ai/status")[0], 403)
        status, body, _ = self.request("/api/ai/status", authorized=True)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["enabled"])
        status, body, _ = self.request("/api/ai/sessions", "POST", {
            "dashboardId": "dashboard_mercury", "accessLevel": "dashboard", "model": {"providerId": "openai", "modelId": "gpt"},
        }, True)
        self.assertEqual(status, 201)
        chat_id = json.loads(body)["id"]
        self.assertNotEqual(chat_id, "ses_1")
        message = {
            "text": "Rename a widget", "model": {"providerId": "openai", "modelId": "gpt"},
        }
        status, body, _ = self.request(f"/api/ai/sessions/{chat_id}/messages", "POST", message, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["text"], "Proposed.")
        prompt_call = next(call for call in self.ai_service.calls if call[0] == "prompt")
        self.assertIn("Schemer context (untrusted JSON):", prompt_call[2])
        context_text = prompt_call[2].split("\n\nUser request:\n", 1)[0]
        self.assertIn('"application":"schemer"', context_text)
        self.assertIn('"widgetId":"widget_revenue"', context_text)
        self.assertNotIn("password", context_text.lower())
        self.assertNotIn('"host"', context_text.lower())
        self.assertNotIn("SCHEMII_ACTION", prompt_call[4])
        self.assertIn("schemer_*", prompt_call[4])
        self.assertEqual(self.request(f"/api/ai/sessions/{chat_id}/messages", "POST", {**message, "schemaId": "schema_one"}, True)[0], 400)

    def test_ai_catalog_sources_are_hydrated_from_postgres(self):
        record = self.dashboard_store.get("dashboard_mercury")
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }}
        sources = _ai_catalog_sources(self.service, record, None)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["relation"], "orders")
        self.assertEqual(sources[0]["columns"][0]["name"], "id")
        self.assertNotIn("definition", sources[0])

    def test_ai_data_mode_requires_target_and_bounds_follow_up_results(self):
        path = "/api/ai/sessions/ses_1/messages"
        base = {
            "text": "Count orders", "model": {"providerId": "openai", "modelId": "gpt"},
        }
        target = {"profileId": "shared", "database": "schemii", "namespace": "bookstore"}
        self.authority.put_chat("ses_1", "dashboard_mercury", "ses_1", {**target, "profileFingerprint": self.service.profile_context_fingerprint("shared")}, "data")
        status, _, _ = self.request(path, "POST", base, True)
        self.assertEqual(status, 200)
        prompt_call = next(call for call in reversed(self.ai_service.calls) if call[0] == "prompt")
        self.assertTrue(prompt_call[-1])
        self.assertIn('"analyticTarget":{"profileId":"shared","database":"schemii","namespace":"bookstore"}', prompt_call[2])

        query_result = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "columns": [{"name": "count"}], "rows": [[1]],
            "rowCount": 1, "truncated": False, "maxRows": 100, "maxColumns": 50, "maxResultBytes": 256 * 1024,
        }
        self.assertEqual(self.request(path, "POST", {**base, "queryResult": query_result}, True)[0], 400)

    def test_ai_data_history_is_scoped_to_the_exact_target(self):
        fingerprint = self.service.profile_context_fingerprint("shared")
        target = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore",
            "profileFingerprint": fingerprint,
        }
        self.authority.put_chat("ses_1", "dashboard_mercury", "ses_1", target, "data")

        correct = urllib.parse.urlencode({
            "dashboardId": "dashboard_mercury", "accessLevel": "data",
            "profileId": "shared", "database": "schemii", "namespace": "bookstore",
        })
        wrong = urllib.parse.urlencode({
            "dashboardId": "dashboard_mercury", "accessLevel": "data",
            "profileId": "shared", "database": "schemii", "namespace": "public",
        })

        status, body, _ = self.request(f"/api/ai/sessions?{correct}", authorized=True)
        self.assertEqual(status, 200)
        session = json.loads(body)["sessions"][0]
        self.assertEqual(session["id"], "ses_1")
        self.assertEqual(session["title"], "Add events")
        self.assertEqual(session["contextTitle"], "Dashboard chat")
        self.assertEqual(self.request(f"/api/ai/sessions?{wrong}", authorized=True)[0], 200)
        self.assertEqual(json.loads(self.request(f"/api/ai/sessions?{wrong}", authorized=True)[1])["sessions"], [])
        self.assertEqual(self.request(f"/api/ai/sessions/ses_1/messages?{wrong}", authorized=True)[0], 409)
        self.assertEqual(self.request(f"/api/ai/sessions/ses_1/messages?{correct}", authorized=True)[0], 200)

    def test_ai_chat_title_can_be_renamed(self):
        path = "/api/ai/sessions/ses_1/title"
        status, body, _ = self.request(path, "PUT", {"title": "Revenue dashboard revisions"}, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["title"], "Revenue dashboard revisions")
        self.assertEqual(self.authority.get_chat("ses_1")["conversationTitle"], "Revenue dashboard revisions")

    def test_profile_writes_use_shared_router_and_redact_password(self):
        profile = {
            "name": "Analytics", "host": "postgres", "port": 5432, "dbname": "schemii",
            "user": "reader", "password": "secret", "sslmode": "disable", "timeout": 10,
        }
        status, body, _ = self.request("/api/postgres/profiles", "POST", profile, True)
        self.assertEqual(status, 201)
        self.assertNotIn("password", json.loads(body))
        self.assertEqual(self.service.calls[-1], ("save_profile", None, profile))

    def test_dashboard_routes_require_session_and_reject_stale_updates(self):
        self.assertEqual(self.request("/api/dashboards")[0], 403)
        status, body, _ = self.request("/api/dashboards", authorized=True)
        self.assertEqual(status, 200)
        record = json.loads(body)["dashboards"][0]
        record["dashboard"]["title"] = "Updated dashboard"
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore",
            "relation": "orders", "kind": "table", "fingerprint": "a" * 64,
        }}
        save_request = {"record": record, "bindingAction": "reject"}
        status, body, _ = self.request(f"/api/dashboards/{record['id']}", "PUT", save_request, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], record["revision"] + 1)
        self.assertEqual(json.loads(body)["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")
        self.assertEqual(self.request(f"/api/dashboards/{record['id']}", "PUT", save_request, True)[0], 409)

        current = self.dashboard_store.get(record["id"])
        current["dashboard"]["title"] = "Legacy raw save"
        status, body, _ = self.request(f"/api/dashboards/{record['id']}", "PUT", current, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["dashboard"]["title"], "Legacy raw save")

        status, body, _ = self.request("/api/dashboards", "POST", {"title": "New dashboard"}, True)
        self.assertEqual(status, 201)
        created = json.loads(body)
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(self.request(f"/api/dashboards/{created['id']}", "DELETE", {"expectedRevision": created["revision"]}, authorized=True)[0], 200)

    def test_legacy_source_upgrade_routes_are_authorized_read_only_then_atomic(self):
        self.assertEqual(self.request("/")[0], 200)
        session_status, session_body, _ = self.request("/api/session")
        self.assertEqual(session_status, 200)
        self.assertEqual(json.loads(session_body)["token"], "session-token")
        record = self.dashboard_store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        legacy_columns = [
            {key: column_value[key] for key in ("name", "type", "nullable", "ordinal")}
            for column_value in self.service.descriptor["columns"]
        ]
        widget["kind"] = "placeholder"
        widget["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "c" * 64, "columns": legacy_columns,
        }}
        record = self.dashboard_store.save(record["id"], record)
        self.service.descriptor["legacyFingerprint"] = "c" * 64
        request = {
            "dashboardId": record["id"], "expectedRevision": record["revision"], "widgetIds": [widget["id"]],
        }
        preview_path = "/api/dashboards/legacy-sources/preview"
        apply_path = "/api/dashboards/legacy-sources/apply"

        self.assertEqual(self.request(preview_path, "POST", request)[0], 403)
        before = self.dashboard_store.get(record["id"])
        status, body, _ = self.request(preview_path, "POST", request, True)
        self.assertEqual(status, 200)
        preview = json.loads(body)
        self.assertEqual(preview["compatibleWidgetIds"], [widget["id"]])
        self.assertEqual(preview["incompatibleWidgetIds"], [])
        self.assertEqual(self.dashboard_store.get(record["id"]), before)

        unconfirmed = {**request, "digest": preview["digest"], "confirmed": False}
        status, body, _ = self.request(apply_path, "POST", unconfirmed, True)
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, "confirmation_required"))

        status, body, _ = self.request(apply_path, "POST", {**unconfirmed, "confirmed": True}, True)
        self.assertEqual(status, 200)
        applied = json.loads(body)
        self.assertEqual(applied["revision"], record["revision"] + 1)
        self.assertEqual(applied["upgradedWidgetIds"], [widget["id"]])
        self.assertEqual(applied["postWriteVerification"], {
            "status": "current", "changedWidgetIds": [], "unavailableWidgetIds": [],
        })
        saved_source = self.dashboard_store.get(record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]
        self.assertEqual(saved_source["snapshotVersion"], 2)
        self.assertEqual(saved_source["fingerprint"], "a" * 64)
        self.assertEqual(self.request(apply_path, "POST", {**unconfirmed, "confirmed": True}, True)[0], 409)

    def test_legacy_source_upgrade_accepts_the_maximum_widget_binding_and_bounded_digest(self):
        record = self.dashboard_store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        legacy_columns = [
            {key: column_value[key] for key in ("name", "type", "nullable", "ordinal")}
            for column_value in self.service.descriptor["columns"]
        ]
        widget["kind"] = "placeholder"
        widget["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "c" * 64, "columns": legacy_columns,
        }}
        record = self.dashboard_store.save(record["id"], record)
        self.service.descriptor["legacyFingerprint"] = "c" * 64
        widget_ids = [widget["id"]] + [
            f"widget_{index:03}_" + "x" * 117
            for index in range(99)
        ]
        request = {
            "dashboardId": record["id"], "expectedRevision": record["revision"], "widgetIds": widget_ids,
        }
        preview_path = "/api/dashboards/legacy-sources/preview"
        apply_path = "/api/dashboards/legacy-sources/apply"

        status, body, _ = self.request(preview_path, "POST", request, True)
        preview = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(preview["maximumDigestLength"], MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH)
        self.assertGreater(len(preview["digest"]), 8192)
        over_bound = {
            **request, "digest": "x" * (MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH + 1), "confirmed": True,
        }
        status, body, _ = self.request(apply_path, "POST", over_bound, True)
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, "legacy_source_digest_invalid"))

        status, body, _ = self.request(
            apply_path, "POST", {**request, "digest": preview["digest"], "confirmed": True}, True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["upgradedWidgetIds"], [widget["id"]])

    def test_legacy_source_route_body_limit_accepts_the_boundary_and_rejects_one_byte_over(self):
        parsed = urllib.parse.urlparse(self.http.base_url)

        def raw_request(size):
            connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            try:
                connection.request(
                    "POST", "/api/dashboards/legacy-sources/apply", body=b"x" * size,
                    headers={"Content-Type": "application/json", "X-Schemii-Token": "session-token"},
                )
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        at_boundary = raw_request(MAX_LEGACY_SOURCE_UPGRADE_REQUEST_BODY_BYTES)
        over_boundary = raw_request(MAX_LEGACY_SOURCE_UPGRADE_REQUEST_BODY_BYTES + 1)
        self.assertEqual((at_boundary[0], at_boundary[1]["error"]["code"]), (400, "invalid_request"))
        self.assertEqual((over_boundary[0], over_boundary[1]["error"]["code"]), (413, "request_too_large"))
        self.assertEqual(over_boundary[1]["error"]["details"]["limit"], MAX_LEGACY_SOURCE_UPGRADE_REQUEST_BODY_BYTES)

    def test_mercury_reset_uses_live_view_and_preserves_order_and_viewport(self):
        columns = [
            {"name": name, "type": column_type, "nullable": nullable, "ordinal": index + 1}
            for index, (name, column_type, nullable) in enumerate([
                ("order_id", "bigint", False), ("customer_id", "bigint", False),
                ("customer_name", "character varying(160)", False), ("status", "character varying(20)", False),
                ("ordered_at", "timestamp with time zone", False), ("shipped_at", "timestamp with time zone", True),
                ("order_date", "date", False), ("item_count", "bigint", True), ("order_total", "numeric(14,2)", True),
            ])
        ]
        self.service.profiles = [{
            "id": MERCURY_PROFILE_ID, "name": "Mercury", "host": "postgres", "port": 5432,
            "dbname": "schemii", "user": "schemii", "sslmode": "disable", "timeout": 10,
        }]
        self.service.descriptor = {
            "profileId": MERCURY_PROFILE_ID, "database": "schemii", "namespace": "bookstore",
            "relation": "order_summary", "kind": "view", "fingerprint": "c" * 64, "snapshotVersion": 2,
            "columns": [{**item, "capabilities": capabilities_for_formatted_type(item["type"])} for item in columns],
            "definition": {"status": "available", "sql": "SELECT ..."},
        }
        record = self.dashboard_store.get("dashboard_mercury")
        first = record["dashboard"]["widgets"].pop(0)
        record["dashboard"]["widgets"].append(first)
        record["dashboard"]["viewport"]["desktop"]["y"] = 73
        preserved_order = [widget["id"] for widget in record["dashboard"]["widgets"]]
        record = self.dashboard_store.save(record["id"], record)
        path = "/api/examples/mercury/reset"
        reset = {"expectedRevision": record["revision"], "bindingAction": "reject"}
        self.assertEqual(self.request(path, "POST", reset)[0], 403)
        status, body, _ = self.request(path, "POST", reset, True)
        self.assertEqual(status, 200)
        restored = json.loads(body)
        self.assertEqual([widget["id"] for widget in restored["dashboard"]["widgets"]], preserved_order)
        self.assertEqual(restored["dashboard"]["viewport"]["desktop"], {"y": 73})
        self.assertTrue(all("layout" not in widget for widget in restored["dashboard"]["widgets"]))
        self.assertEqual({widget["kind"] for widget in restored["dashboard"]["widgets"]}, {"aggregate_report"})
        self.assertTrue(all(widget["configuration"]["source"]["snapshotVersion"] == 2 for widget in restored["dashboard"]["widgets"][:6]))
        self.assertIn(("inspect_relation", MERCURY_PROFILE_ID, "schemii", "bookstore", "order_summary", "view", None), self.service.calls)
        legacy_reset = {"expectedRevision": restored["revision"]}
        self.assertEqual(self.request(path, "POST", legacy_reset, True)[0], 200)
        self.assertEqual(self.request(path, "POST", reset, True)[0], 409)


if __name__ == "__main__":
    unittest.main()
