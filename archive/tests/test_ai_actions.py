import sys
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schemii_ai_actions import normalize_schemii_action
from schemii.schemer_ai_actions import normalize_schemer_action


class AiActionTests(unittest.TestCase):
    def test_schemii_query_is_canonical_and_allows_multiline_sql(self):
        action = {
            "action": "schema_read_query", "profileId": "local", "namespace": "public",
            "sql": "SELECT 1\nFROM demo", "purpose": "Inspect demo", "readOnly": True, "requiresApproval": True,
        }
        normalized = normalize_schemii_action(action, "rawread")
        self.assertEqual(normalized["type"], "schema_read_query")
        self.assertTrue(normalized["requiresConfirmation"])
        self.assertEqual(normalized["sql"], action["sql"])
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "unknown": True}, "rawread")
        with self.assertRaises(ValueError):
            normalize_schemii_action(action, "schema")
        self.assertEqual(normalize_schemii_action(action, "schema-read-write")["type"], "schema_read_query")

    def test_schemii_client_commands_retain_review_metadata(self):
        project = normalize_schemii_action({
            "type": "open_project", "schemaId": "schema_one", "projectName": "Demo", "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(project["requiresConfirmation"])
        connection = normalize_schemii_action({
            "type": "connection_setup", "name": "Demo", "host": "127.0.0.1", "port": 5432,
            "database": "demo", "user": "reader", "sslmode": "prefer",
            "requiresPasswordEntry": True, "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(connection["requiresPasswordEntry"])
        self.assertNotIn("password", connection)

    def test_schemii_mutations_are_exact_and_canonical(self):
        action = {
            "type": "add_table", "name": "events", "purpose": "Store events",
            "columns": [{"name": "id", "type": "uuid", "primary": True}],
            "requiresConfirmation": True,
        }
        normalized = normalize_schemii_action(action, "schema")
        self.assertEqual(normalized["columns"][0]["name"], "id")
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "unknown": True}, "schema")
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "columns": [{"name": "id", "type": "uuid", "extra": True}]}, "schema")
        self.assertEqual(normalize_schemii_action(action, "schema-read-write")["type"], "add_table")
        with self.assertRaises(ValueError):
            normalize_schemii_action(action, "metadata")

        deletion = normalize_schemii_action({
            "type": "delete_element", "elementType": "column", "tableId": "table_one", "columnId": "col_one",
            "reason": "No longer used", "destructive": True, "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(deletion["destructive"])

    def test_schemii_connection_and_preview_actions_are_exact(self):
        connection = normalize_schemii_action({
            "type": "open_connection", "profileId": "local", "name": "Local", "database": "demo",
            "namespace": "public", "requiresConfirmation": True,
        }, "schema")
        self.assertEqual(connection["namespace"], "public")
        preview = normalize_schemii_action({
            "type": "migration_preview", "profileId": "local", "namespace": "public", "destructivePolicy": "reject",
            "purpose": "Review changes", "readOnly": True, "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(preview["readOnly"])
        with self.assertRaises(ValueError):
            normalize_schemii_action({**preview, "readOnly": False}, "schema")
        insert = normalize_schemii_action({
            "type": "insert_rows_preview", "profileId": "local", "namespace": "public", "relation": "events",
            "rows": [{"name": "launch", "priority": 2}, {"name": "review", "priority": 3}],
            "purpose": "Add initial events", "readOnly": True, "requiresConfirmation": True,
        }, "write")
        self.assertEqual(len(insert["rows"]), 2)
        self.assertEqual(len(normalize_schemii_action(insert, "schema-read-write")["rows"]), 2)
        view = normalize_schemii_action({
            "type": "create_view_preview", "profileId": "local", "namespace": "public", "relation": "active_events",
            "definition": 'CREATE VIEW "public"."active_events" AS SELECT 1', "purpose": "Create active events",
            "readOnly": True, "requiresConfirmation": True,
        }, "write")
        self.assertEqual(view["relation"], "active_events")
        for invalid in (
            {**insert, "rows": [{"name": "one"}, {"name": "two", "priority": 2}]},
            {**insert, "rows": [{"value": math.inf}]},
            {**view, "definition": 'CREATE OR REPLACE VIEW "public"."active_events" AS SELECT 1'},
        ):
            with self.assertRaises(ValueError):
                normalize_schemii_action(invalid, "write")
        with self.assertRaises(ValueError):
            normalize_schemii_action(insert, "schema")
        with self.assertRaises(ValueError):
            normalize_schemii_action({
                "type": "migration_apply", "profileId": "local", "database": "demo", "namespace": "public",
                "planId": "ai_plan_one", "destructive": False, "requiresConfirmation": True,
            }, "schema")
        with self.assertRaises(ValueError):
            normalize_schemii_action({
                "type": "postgres_write_apply", "writeKind": "insert_rows", "profileId": "local",
                "database": "demo", "namespace": "public", "relation": "events", "planId": "ai_plan_one",
                "requiresConfirmation": True,
            }, "data")

    def test_structured_read_and_raw_write_are_separately_authorized(self):
        structured = {
            "type": "data_read", "profileId": "local", "namespace": "public", "relation": "events",
            "offset": 0, "limit": 25, "purpose": "Inspect events", "readOnly": True, "requiresConfirmation": True,
        }
        self.assertEqual(normalize_schemii_action(structured, "structured")["relation"], "events")
        for server_owned in (
            {"database": "demo"}, {"profileFingerprint": "f" * 64},
            {"source": {"kind": "view", "fingerprint": "a" * 64, "columns": []}},
        ):
            with self.subTest(server_owned=server_owned), self.assertRaises(ValueError):
                normalize_schemii_action({**structured, **server_owned}, "structured")
        with self.assertRaises(ValueError):
            normalize_schemii_action(structured, "rawread")
        raw_write = {
            "type": "raw_write", "profileId": "local", "namespace": "public",
            "sql": "UPDATE events SET active = true;\nDELETE FROM events WHERE expired;",
            "purpose": "Refresh events", "requiresConfirmation": True,
        }
        self.assertEqual(normalize_schemii_action(raw_write, "rawwrite")["sql"], raw_write["sql"])
        with self.assertRaises(ValueError):
            normalize_schemii_action(raw_write, "write")

    def test_schemer_actions_are_exact_and_revision_bound(self):
        action = {
            "type": "dashboard_open", "dashboardId": "dashboard_one", "expectedRevision": 3,
            "title": "Demo", "requiresConfirmation": True,
        }
        self.assertEqual(normalize_schemer_action(action, "dashboard")["expectedRevision"], 3)
        with self.assertRaises(ValueError):
            normalize_schemer_action({**action, "expectedRevision": True}, "dashboard")

    def test_schemer_widget_mutations_are_exact(self):
        rename = normalize_schemer_action({"type": "widget_rename", "dashboardId": "dashboard_one", "expectedRevision": 3, "widgetId": "widget_one", "currentTitle": "Old", "title": "New", "requiresConfirmation": True}, "dashboard")
        self.assertEqual(rename["title"], "New")
        with self.assertRaises(ValueError): normalize_schemer_action({**rename, "extra": True}, "dashboard")
        created = normalize_schemer_action({"type": "dashboard_create", "title": "New dashboard", "requiresConfirmation": True}, "metadata")
        self.assertEqual(created["type"], "dashboard_create")


if __name__ == "__main__":
    unittest.main()
