import sys
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schemer_ai_executor import SchemerAiExecutor
from schemii.schemii_ai_executor import SchemiiAiExecutor
from schemii.postgres_common import PostgresServiceError


class StoreDouble:
    def __init__(self, record):
        self.record = record

    def get(self, resource_id):
        return self.record


class ExecutorContractTests(unittest.TestCase):
    def test_structured_read_requires_and_forwards_exact_server_source(self):
        class Service:
            def __init__(self): self.calls = []
            def preview_relation_rows(self, *args):
                self.calls.append(args)
                return {"columns": [{"name": "id"}], "rows": [{"id": 1}, {"id": 2}], "hasMore": True}

        class Authority:
            def __init__(self): self.bounds = []
            def consume_bound(self, *args): self.bounds.append(args)
            def create_result(self, *args): return {"id": "result"}

        service, authority = Service(), Authority()
        executor = SchemiiAiExecutor(service, object(), authority, mutation_types=set(), has_access=lambda *_: True, policy_binding=lambda *_args, **_kwargs: {})
        target = {"profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "f" * 64}
        columns = [{"name": "id", "type": "bigint", "nullable": False, "ordinal": 1}]
        for kind in ("table", "partitioned_table", "view", "materialized_view", "foreign_table"):
            source = {
                "profileId": "local", "database": "demo", "namespace": "public", "relation": "events",
                "kind": kind, "fingerprint": "a" * 64, "columns": columns,
            }
            action = {
                "type": "data_read", "profileId": "local", "database": "demo", "namespace": "public",
                "relation": "events", "profileFingerprint": "f" * 64, "source": source, "offset": 50, "limit": 25,
            }
            result = executor.execute(
                action, "chat", "schema", {"revision": 1}, {"id": "local", "dbname": "demo"}, target,
                {"revision": 1}, f"operation-{kind}", "structured", session_binding="binding", server_id="server",
                console_policy=None, proposal_envelope=lambda *_: {},
                policy_binding={"snapshot": {"version": 2, "bounds": {"rowsDisclosed": 2, "pagesInspected": 3}}},
            )
            self.assertEqual(result["display"]["rows"], [[1], [2]])
            self.assertEqual(service.calls[-1], ("local", source, 50, 2))
        self.assertTrue(any(item[1:3] == ("rowsDisclosed", 2) for item in authority.bounds))

        calls_before_bound_rejection = len(service.calls)
        with self.assertRaises(PostgresServiceError) as caught:
            executor.execute(
                {**action, "offset": 75}, "chat", "schema", {"revision": 1}, {"id": "local", "dbname": "demo"}, target,
                {"revision": 1}, "page-bound", "structured", session_binding="binding", server_id="server",
                console_policy=None, proposal_envelope=lambda *_: {},
                policy_binding={"snapshot": {"version": 2, "bounds": {"rowsDisclosed": 2, "pagesInspected": 3}}},
            )
        self.assertEqual(caught.exception.code, "policy_bound_exceeded")
        self.assertEqual(len(service.calls), calls_before_bound_rejection)

        tampered = {**action, "relation": "other"}
        with self.assertRaises(PostgresServiceError) as caught:
            executor.execute(
                tampered, "chat", "schema", {"revision": 1}, {"id": "local", "dbname": "demo"}, target,
                {"revision": 1}, "tampered", "structured", session_binding="binding", server_id="server",
                console_policy=None, proposal_envelope=lambda *_: {},
            )
        self.assertEqual(caught.exception.code, "action_target_changed")
        legacy = {key: value for key, value in action.items() if key not in {"source", "database", "profileFingerprint"}}
        with self.assertRaises(PostgresServiceError) as caught:
            executor.execute(
                legacy, "chat", "schema", {"revision": 1}, {"id": "local", "dbname": "demo"}, target,
                {"revision": 1}, "legacy", "structured", session_binding="binding", server_id="server",
                console_policy=None, proposal_envelope=lambda *_: {},
            )
        self.assertEqual(caught.exception.code, "relation_changed")

    def test_schemii_client_command_executes_without_http_handler_state(self):
        store = StoreDouble({"id": "schema_one", "revision": 3, "layoutToken": "a" * 64, "schema": {"projectName": "Demo"}})
        executor = SchemiiAiExecutor(object(), store, object(), mutation_types=set(), has_access=lambda *_: False, policy_binding=lambda *_args, **_kwargs: {})
        result = executor.execute(
            {"type": "open_project", "schemaId": "schema_one", "projectName": "Demo"},
            "chat", "schema_one", store.record, None, {}, {"revision": 3, "layoutToken": "a" * 64},
            "operation", "metadata", session_binding="binding", server_id="server", console_policy=None,
            proposal_envelope=lambda *_: {},
        )
        self.assertEqual(result["command"], {"type": "open_schema", "schemaId": "schema_one", "revision": 3, "layoutToken": "a" * 64})

    def test_schemer_client_command_executes_without_http_handler_state(self):
        dashboard = {"id": "dashboard_one", "revision": 4, "dashboard": {"title": "Demo"}}
        executor = SchemerAiExecutor(object(), StoreDouble(dashboard), object(), catalog_sources=lambda *_: [], configured_widget=lambda *_: {})
        result = executor.execute(
            {"type": "dashboard_open", "dashboardId": "dashboard_one", "expectedRevision": 4, "title": "Demo"},
            "operation", chat={"id": "chat", "dashboardId": "dashboard_one", "accessLevel": "metadata"},
            record=dashboard, profile=None, schema_concurrency={"revision": 4}, authorization_target={},
        )
        self.assertEqual(result["command"], {"type": "open_dashboard", "dashboardId": "dashboard_one", "revision": 4})

    def test_schemer_complete_widget_validation_receives_operation_timeout_and_identity(self):
        source = {
            "profileId": "local", "database": "demo", "namespace": "public", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }
        dashboard = {"id": "dashboard_one", "revision": 4, "dashboard": {"widgets": []}}

        class Store(StoreDouble):
            @contextmanager
            def guard_revision(self, dashboard_id, revision):
                yield self.record

            def apply_ai_mutation(self, dashboard_id, operation_id, revision, action, prepared):
                return prepared

        calls = []
        executor = SchemerAiExecutor(
            object(), Store(dashboard), object(), catalog_sources=lambda *_: [source],
            configured_widget=lambda *args, **kwargs: calls.append((args, kwargs)) or {"kind": "aggregate_report"},
        )
        action = {
            "type": "widget_create", "dashboardId": "dashboard_one", "expectedRevision": 4,
            "title": "Orders", "source": source, "query": {"version": 2}, "visualizationMode": "table",
        }
        result = executor.execute(
            action, "operation-widget", chat={"id": "chat", "dashboardId": "dashboard_one", "accessLevel": "data"},
            record=dashboard, profile=None, schema_concurrency={"revision": 4},
            authorization_target={"profileId": "local", "database": "demo", "namespace": "public"},
            policy_binding={"snapshot": {"version": 2, "bounds": {"operationTimeoutMs": 4200}}},
        )

        self.assertEqual(result, {"kind": "aggregate_report"})
        self.assertEqual(calls[0][0][2:4], ("operation-widget", 0))
        self.assertEqual(calls[0][1], {"operation_timeout_ms": 4200})

    def test_finite_rows_written_bound_rejects_arbitrary_raw_write_before_service_call(self):
        class Service:
            def execute_console(self, *args, **kwargs):
                raise AssertionError("raw SQL must not reach PostgreSQL")

        executor = SchemiiAiExecutor(Service(), object(), object(), mutation_types=set(), has_access=lambda *_: True, policy_binding=lambda *_args, **_kwargs: {})
        with self.assertRaises(PostgresServiceError) as caught:
            executor.execute(
                {"type": "raw_write", "profileId": "local", "namespace": "public", "sql": "UPDATE events SET active = true"},
                "chat", "schema", {"revision": 1}, {"id": "local", "dbname": "demo"},
                {"profileId": "local", "database": "demo", "namespace": "public"}, {"revision": 1},
                "operation", "rawwrite", session_binding="binding", server_id="server", console_policy=None,
                proposal_envelope=lambda *_: {}, policy_binding={"snapshot": {"bounds": {"rowsWritten": 10}}},
            )
        self.assertEqual(caught.exception.code, "application_limitation")
        self.assertIn("Console", caught.exception.details["guidance"])

    def test_migration_insert_and_view_adapters_receive_only_proposal_bound_timeout(self):
        class Service:
            def __init__(self): self.calls = []
            def list_profiles(self): return [{"id": "local", "dbname": "demo"}]
            def profile_context_fingerprint(self, profile_id): return "f" * 64
            def preview_ai_migration(self, *args):
                self.calls.append(("migration_preview", args[-1]))
                return {"applyCapable": False}
            def preview_ai_insert_rows(self, *args):
                self.calls.append(("insert_preview", args[-1]))
                return {"applyPlanId": "insert-plan", "planDigest": "a" * 64, "effectsDigest": "e" * 64, "rowCount": 1}
            def preview_ai_create_view(self, *args):
                self.calls.append(("view_preview", args[-1]))
                return {"applyPlanId": "view-plan", "planDigest": "b" * 64}
            def apply_ai_migration(self, *args):
                self.calls.append(("migration_apply", args[-1])); return {"state": "succeeded"}
            def apply_ai_postgres_write(self, *args):
                self.calls.append(("write_apply", args[-1])); return {"state": "succeeded"}

        class Store:
            def require_view_mutation_binding(self, *args): return None

        class Authority:
            def get_chat(self, chat_id): return {"id": chat_id, "policySnapshot": {"bounds": {"operationTimeoutMs": 3200}}}
            def create_proposal(self, *args): return {"id": "proposal", "action": args[1], "policyBinding": args[2]}
            def consume_bound(self, *args): return None

        service = Service()
        executor = SchemiiAiExecutor(service, Store(), Authority(), mutation_types=set(), has_access=lambda *_: True, policy_binding=lambda *_args, **_kwargs: {})
        target = {"profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "f" * 64}
        concurrency = {"revision": 1, "layoutToken": "c" * 64}
        record = {"revision": 1, "layoutToken": "c" * 64, "schema": {"postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"}}}
        binding = {"snapshot": {"version": 2, "bounds": {"operationTimeoutMs": 3200, "rowsWritten": None}}}
        common = dict(session_binding="binding", server_id="server", console_policy=None, proposal_envelope=lambda *_: {}, policy_binding=binding)

        actions = [
            {"type": "migration_preview", "profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "f" * 64, "destructivePolicy": "reject"},
            {"type": "insert_rows_preview", "profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "f" * 64, "relation": "events", "rows": [{"name": "x"}]},
            {"type": "create_view_preview", "profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "f" * 64, "relation": "recent", "definition": "CREATE VIEW public.recent AS SELECT 1"},
            {"type": "migration_apply", "profileId": "local", "database": "demo", "namespace": "public", "planId": "migration-plan", "destructive": False, "reviewDigest": "d" * 64},
            {"type": "postgres_write_apply", "profileId": "local", "database": "demo", "namespace": "public", "relation": "events", "planId": "insert-plan", "writeKind": "insert_rows", "reviewDigest": "a" * 64, "effectsDigest": "e" * 64, "reviewedPlan": {"effectsDigest": "e" * 64}, "rowCount": 1},
            {"type": "postgres_write_apply", "profileId": "local", "database": "demo", "namespace": "public", "relation": "recent", "planId": "view-plan", "writeKind": "create_view", "reviewDigest": "b" * 64, "rowCount": None},
        ]
        for index, action in enumerate(actions):
            executor.execute(action, "chat", "schema", record, {"id": "local", "dbname": "demo"}, target, concurrency, f"operation-{index}", "schema-write", **common)
        self.assertEqual(service.calls, [
            ("migration_preview", 3200), ("insert_preview", 3200), ("view_preview", 3200),
            ("migration_apply", 3200), ("write_apply", 3200), ("write_apply", 3200),
        ])


if __name__ == "__main__":
    unittest.main()
