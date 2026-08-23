import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_http import PostgresHttpMixin
from schemii.dashboard_store import DashboardStore
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
        capability = "data" if action.get("type") == "read_query" else "metadata" if action.get("type") in {"dashboard_create", "dashboard_open"} else "dashboard"
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
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, authorized=False):
        return self.http.request(path, method, payload, authorized=authorized)

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
        reconciled = json.loads(self.request(path + "/reconcile", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(reconciled["id"], operation["id"])

    def test_ai_dashboard_create_uses_deterministic_server_identity(self):
        self.authority.put_chat("ses_1", "dashboard_mercury", "ses_1", access_level="metadata")
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
        self.authority.put_chat("ses_1", "dashboard_mercury", "ses_1", access_level="metadata")
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

    def test_configured_widget_builder_sanitizes_catalog_columns(self):
        from schemii.schemer_server import _configured_ai_widget
        query = {"version": 2, "dimensions": [], "measures": [{"id": "measure_orders", "label": "Orders", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}], "filters": [], "sort": [], "limit": 100}
        action = {"title": "Orders", "source": {"profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders", "kind": "table", "fingerprint": "a" * 64}, "query": query, "visualizationMode": "kpi"}
        widget = _configured_ai_widget(self.service, action, "operation_widget", 1)
        self.assertEqual(set(widget["configuration"]["source"]["columns"][0]), {"name", "type", "nullable", "ordinal", "capabilities"})
        record = self.dashboard_store.get("dashboard_mercury")
        saved = self.dashboard_store.apply_ai_mutation(record["id"], "operation_widget", record["revision"], {"type": "widget_create", "title": "Orders"}, widget)
        self.assertEqual(saved["kind"], "dashboard_saved")

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
        self.assertEqual(readiness["components"]["postgresExecution"]["classes"]["read"]["capacity"], 8)
        self.assertEqual(readiness["components"]["postgresExecution"]["targets"], {})
        status, body, _ = self.request("/api/dashboards/summary", authorized=True)
        self.assertEqual(status, 200)
        summary = json.loads(body)["summaries"][0]
        self.assertEqual(summary["id"], "dashboard_mercury")
        self.assertNotIn("widgets", summary)

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
        series_source = {**preview_source, "columns": [
            {"name": "ordered_on", "type": "date", "nullable": False, "ordinal": 1},
            {"name": "amount", "type": "numeric", "nullable": True, "ordinal": 2},
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
        status, body, _ = self.request(f"/api/dashboards/{record['id']}", "PUT", record, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], record["revision"] + 1)
        self.assertEqual(json.loads(body)["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")
        self.assertEqual(self.request(f"/api/dashboards/{record['id']}", "PUT", record, True)[0], 409)

        status, body, _ = self.request("/api/dashboards", "POST", {"title": "New dashboard"}, True)
        self.assertEqual(status, 201)
        created = json.loads(body)
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(self.request(f"/api/dashboards/{created['id']}", "DELETE", {"expectedRevision": created["revision"]}, authorized=True)[0], 200)

    def test_mercury_reset_uses_live_view_and_preserves_layout(self):
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
        record["dashboard"]["widgets"][0]["layout"]["desktop"]["x"] = 1
        record = self.dashboard_store.save(record["id"], record)
        path = "/api/examples/mercury/reset"
        self.assertEqual(self.request(path, "POST", {"expectedRevision": record["revision"]})[0], 403)
        status, body, _ = self.request(path, "POST", {"expectedRevision": record["revision"]}, True)
        self.assertEqual(status, 200)
        restored = json.loads(body)
        self.assertEqual(restored["dashboard"]["widgets"][0]["layout"]["desktop"]["x"], 1)
        self.assertEqual({widget["kind"] for widget in restored["dashboard"]["widgets"]}, {"aggregate_report"})
        self.assertTrue(all(widget["configuration"]["source"]["snapshotVersion"] == 2 for widget in restored["dashboard"]["widgets"][:6]))
        self.assertIn(("inspect_relation", MERCURY_PROFILE_ID, "schemii", "bookstore", "order_summary", "view", None), self.service.calls)
        self.assertEqual(self.request(path, "POST", {"expectedRevision": record["revision"]}, True)[0], 409)


if __name__ == "__main__":
    unittest.main()
