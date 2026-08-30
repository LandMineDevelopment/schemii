import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore
from schemii.schema_store import SchemaStore
from schemii.schemer_server import make_handler as make_schemer_handler
from schemii.server import make_handler as make_schemii_handler
from schemii.postgres_http import (
    POSTGRES_CONSOLE_WRITE_CAPABILITY, POSTGRES_RELATION_QUERY_CAPABILITY,
)
from tests.http_test_support import FakePostgresService, RunningHttpServer
from tests.fake_metadata_authority import FakeSchemiiAuthority


class PostgresHttpContractTests(unittest.TestCase):
    def test_application_route_policy_matrix_is_explicit_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schemii = make_schemii_handler(ROOT / "src/schemii/web", FakePostgresService(), SchemaStore(root / "schemas"), "token", server_id="schemii-policy", ai_authority=FakeSchemiiAuthority())
            schemer = make_schemer_handler(ROOT / "src/schemii/schemer_web", FakePostgresService(), DashboardStore(root / "dashboards"), "token", server_id="schemer-policy", ai_authority=FakeSchemiiAuthority())
            matrix = {
                "schemii": (schemii.postgres_route_policy, True, False),
                "schemer": (schemer.postgres_route_policy, True, True),
            }
            for application, (policy, console_write, relation_query) in matrix.items():
                with self.subTest(application=application):
                    self.assertEqual(policy.application, application)
                    self.assertEqual(POSTGRES_CONSOLE_WRITE_CAPABILITY in policy.capabilities, console_write)
                    self.assertEqual(POSTGRES_RELATION_QUERY_CAPABILITY in policy.capabilities, relation_query)
                    self.assertEqual(policy.relation_query_guard is not None, application == "schemer")
                    self.assertEqual(policy.saved_widget_query is not None, application == "schemer")
                    self.assertEqual(policy.read_sql.require_profile_fingerprint, application == "schemer")

    def test_shared_profile_and_catalog_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factories = {
                "schemii": lambda service: make_schemii_handler(
                    ROOT / "src/schemii/web", service, SchemaStore(root / "schemas"),
                    "session-token", server_id="schemii-contract", ai_authority=FakeSchemiiAuthority(),
                ),
                "schemer": lambda service: make_schemer_handler(
                    ROOT / "src/schemii/schemer_web", service, DashboardStore(root / "dashboards"),
                    "session-token", server_id="schemer-contract", ai_authority=FakeSchemiiAuthority(),
                ),
            }
            for name, factory in factories.items():
                with self.subTest(application=name):
                    service = FakePostgresService(
                        profiles=[{"id": "shared", "name": "Shared", "dbname": "demo"}],
                        namespaces=["public"],
                        relations=[{"name": "orders", "kind": "table"}],
                        test_result={"ok": True, "database": "demo"},
                    )
                    running = RunningHttpServer(factory(service))
                    try:
                        self.assertEqual(running.request("/api/postgres/profiles")[0], 403)
                        status, body, _ = running.request("/api/postgres/profiles", authorized=True)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["profiles"][0]["id"], "shared")
                        self.assertEqual(running.request("/api/postgres/profiles/shared/namespaces?database=demo", authorized=True)[0], 200)
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/relations?database=demo&namespace=public", authorized=True,
                        )[0], 200)
                        status, body, _ = running.request(
                            "/api/postgres/profiles/shared/relation?database=demo&namespace=public&relation=orders", authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["definition"], {"status": "unavailable", "reason": "not_supported"})
                        self.assertNotIn("password", body.decode())
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/test", "POST", {}, authorized=True,
                        )[0], 200)
                        execution_id = str(uuid4())
                        console_id = str(uuid4())
                        request = {
                            "executionId": execution_id, "consoleId": console_id, "database": "demo",
                            "namespace": "public", "sql": "SELECT 1", "mode": "managed_read",
                            "settingsRevision": 1, "profileFingerprint": service.profile_context_fingerprint("shared"),
                        }
                        status, body, _ = running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", request, authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertFalse(json.loads(body)["committed"])
                        call = service.calls[-1]
                        self.assertEqual(call[:3], ("execute_console", "shared", request))
                        self.assertNotEqual(call[3], "session-token")
                        self.assertEqual(call[4], f"{name}-contract")
                        self.assertTrue(call[5].allow_write)
                        self.assertTrue(call[5].human_write_intent)
                        result_id = "opaque_result_resource_123"
                        result_path = (
                            f"/api/postgres/profiles/shared/console/executions/{execution_id}/results/{result_id}"
                            f"?consoleId={console_id}&database=demo&namespace=public&statementIndex=0&resultIndex=0"
                        )
                        status, body, _ = running.request(result_path + "&cursor=opaque_cursor", authorized=True)
                        self.assertEqual(status, 200)
                        page = json.loads(body)
                        self.assertEqual((page["executionId"], page["resultId"], page["returnedRows"]),
                                         (execution_id, result_id, 1))
                        self.assertEqual(service.calls[-1][0], "console_result_page")
                        status, body, _ = running.request(result_path, "DELETE", authorized=True)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["closureEvents"], ["closed"])
                        self.assertEqual(service.calls[-1][0], "close_console_result")
                        self.assertEqual(running.request(result_path + "&cursor=opaque_cursor")[0], 403)
                        self.assertEqual(running.request(
                            f"/api/postgres/profiles/shared/console/executions/{execution_id}",
                            "DELETE", authorized=True,
                        )[0], 200)
                        status, body, _ = running.request(
                            f"/api/postgres/profiles/shared/console/executions/{execution_id}?consoleId={console_id}&database=demo&namespace=public",
                            authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["outcome"], "rolled_back")
                        invalid = dict(request)
                        invalid["extra"] = True
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", invalid, authorized=True,
                        )[0], 400)
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", [], authorized=True,
                        )[0], 400)
                        status, body, _ = running.request("/api/postgres/console/settings", authorized=True)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["writeIntent"], "disabled")
                        updated = {"expectedRevision": 1, "writeIntent": "enabled", "defaultMode": "explicit",
                                   "statementLimit": 10, "rowPageSize": 50}
                        status, body, _ = running.request(
                            "/api/postgres/console/settings", "PUT", updated, authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["revision"], 2)
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/write-grants", "POST", {}, authorized=True,
                        )[0], 410)
                        transaction_id = str(uuid4())
                        create = {"transactionId": transaction_id, "consoleId": console_id, "database": "demo",
                                  "namespace": "public", "settingsRevision": 2,
                                  "profileFingerprint": service.profile_context_fingerprint("shared")}
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/transactions", "POST", create, authorized=True,
                        )[0], 201)
                        explicit_execution = str(uuid4())
                        self.assertEqual(running.request(
                            f"/api/postgres/profiles/shared/console/transactions/{transaction_id}/executions", "POST",
                            {"executionId": explicit_execution, "sql": "SAVEPOINT one; RELEASE SAVEPOINT one"}, authorized=True,
                        )[0], 200)
                        self.assertEqual(running.request(
                            f"/api/postgres/profiles/shared/console/transactions/{transaction_id}/commit", "POST",
                            {"executionId": str(uuid4())}, authorized=True,
                        )[0], 200)
                    finally:
                        running.close()

    def test_recognized_policy_route_is_not_reported_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handler = make_schemii_handler(
                ROOT / "src/schemii/web", FakePostgresService(), SchemaStore(root / "schemas"),
                "session-token", server_id="route-policy", ai_authority=FakeSchemiiAuthority(),
            )
            running = RunningHttpServer(handler)
            try:
                status, body, _ = running.request(
                    "/api/postgres/profiles/shared/relation/query", "POST", {}, authorized=True,
                )
                payload = json.loads(body)["error"]
                self.assertEqual((status, payload["code"]), (403, "capability_unavailable"))
                self.assertEqual(payload["details"]["application"], "schemii")
                self.assertEqual(payload["details"]["requiredCapability"], "relation_query")
                self.assertIn("Schemer", payload["details"]["safeAlternative"])
                self.assertNotIn("AI settings", body.decode())

                status, body, _ = running.request(
                    "/api/postgres/profiles/shared/not-a-route", "POST", {}, authorized=True,
                )
                self.assertEqual(status, 404)
                error = json.loads(body).get("error")
                self.assertFalse(isinstance(error, dict) and error.get("code") == "capability_unavailable")
            finally:
                running.close()


if __name__ == "__main__":
    unittest.main()
