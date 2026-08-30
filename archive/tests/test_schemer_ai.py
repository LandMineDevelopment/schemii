import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import mercury_dashboard_record
from schemii.schemer_ai import dashboard_context, validated_query_result


class SchemerAiTests(unittest.TestCase):
    def test_context_is_bounded_redacted_and_omits_layout(self):
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][0]["configuration"] = {
            "source": {
                "profileId": "shared", "database": "demo", "namespace": "public", "relation": "orders",
                "kind": "table", "fingerprint": "a" * 64,
            },
            "query": {"filters": [{"conditions": [{"column": "email", "operator": "eq", "values": ["secret@example.com"]}]}]},
        }
        profiles = [{"id": "shared", "name": "Analytics", "dbname": "demo", "host": "private", "user": "owner", "password": "secret"}]

        metadata = json.loads(dashboard_context(record, "metadata", [record], profiles))
        dashboard = json.loads(dashboard_context(record, "dashboard", [record], profiles))

        self.assertNotIn("widgets", metadata["activeDashboard"])
        self.assertEqual(dashboard["application"], "schemer")
        self.assertEqual(dashboard["activeDashboard"]["widgets"][0]["widgetId"], "widget_revenue")
        self.assertNotIn("layout", dashboard["activeDashboard"]["widgets"][0])
        serialized = json.dumps(dashboard).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("owner", serialized)
        self.assertNotIn("secret@example.com", serialized)
        self.assertIn('"valuesRedacted": true', json.dumps(dashboard))
        self.assertLessEqual(len(dashboard_context(record, "dashboard", [record] * 100, profiles).encode()), 64 * 1024)

    def test_metadata_context_enforces_byte_limit_with_escaped_titles(self):
        records = []
        for index in range(50):
            item = mercury_dashboard_record()
            item["id"] = f"dashboard_{index}"
            item["dashboard"]["title"] = "\U0001f4ca" * 128
            records.append(item)
        context = dashboard_context(records[0], "metadata", records, [])
        self.assertLessEqual(len(context.encode()), 64 * 1024)
        self.assertTrue(json.loads(context)["truncated"])

    def test_data_context_accepts_only_exact_bounded_query_results(self):
        record = mercury_dashboard_record()
        target = {"profileId": "shared", "database": "demo", "namespace": "public"}
        result = {
            "profileId": "shared", "database": "demo", "namespace": "public", "columns": [{"name": "count"}], "rows": [[3]],
            "rowCount": 1, "truncated": False, "maxRows": 100, "maxColumns": 50, "maxResultBytes": 256 * 1024,
        }
        validated = validated_query_result(result, target)
        context = json.loads(dashboard_context(record, "data", [record], [], target, validated))
        self.assertEqual(context["analyticTarget"], target)
        self.assertEqual(context["queryResult"]["rows"], [[3]])
        self.assertIn("widgets", context["activeDashboard"])
        self.assertNotIn("queryResult", json.loads(dashboard_context(record, "metadata", [record], [], target, result)))
        with self.assertRaises(ValueError):
            validated_query_result({**result, "database": "other"}, target)
        with self.assertRaises(ValueError):
            validated_query_result({**result, "rows": [[float("nan")]]}, target)

    def test_catalog_context_exposes_only_bounded_verified_sources(self):
        record = mercury_dashboard_record()
        source = {
            "profileId": "shared", "database": "demo", "namespace": "public", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
            "columns": [{"name": "customer_id", "type": "bigint", "nullable": False, "ordinal": 1, "suggestions": ["dimension"]}],
        }
        dashboard = json.loads(dashboard_context(record, "dashboard", [record], [], catalog_sources=[source]))
        self.assertEqual(dashboard["catalogContext"]["sources"], [source])
        self.assertFalse(dashboard["catalogContext"]["complete"])
        self.assertNotIn("availableConnections", dashboard)
        self.assertNotIn("catalogContext", json.loads(dashboard_context(record, "metadata", [record], [], catalog_sources=[source])))
        self.assertLessEqual(len(json.dumps(dashboard).encode()), 64 * 1024)


if __name__ == "__main__":
    unittest.main()
