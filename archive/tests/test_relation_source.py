import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.relation_source import RelationSourceValidationError, normalize_relation_source
from tests.capability_test_support import column


SOURCE = {
    "profileId": "local",
    "database": "demo",
    "namespace": "public",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
}


class RelationSourceTests(unittest.TestCase):
    def test_normalizes_exact_identity_and_optional_columns(self):
        source = {
            **SOURCE,
            "columns": [{"name": "id", "type": "x" * 512, "nullable": False, "ordinal": 1}],
        }
        self.assertEqual(normalize_relation_source(source, expected_profile_id="local"), source)

    def test_versions_catalog_capability_snapshots_without_rewriting_legacy(self):
        legacy = {**SOURCE, "columns": [{"name": "id", "type": "bigint", "nullable": False, "ordinal": 1}]}
        self.assertEqual(normalize_relation_source(legacy), legacy)
        current = {**SOURCE, "snapshotVersion": 2, "columns": [column("id", "bigint", False, 1, oid=20, name="int8", category="N", pattern=False)]}
        self.assertEqual(normalize_relation_source(current), current)
        tampered = {**current, "columns": [{**current["columns"][0], "capabilities": {**current["columns"][0]["capabilities"], "sortable": False}}]}
        with self.assertRaisesRegex(RelationSourceValidationError, "fingerprint"):
            normalize_relation_source(tampered)

    def test_rejects_type_drift_and_profile_mismatch(self):
        for column_type in (" x", "x ", "x" * 513):
            with self.subTest(column_type=len(column_type)), self.assertRaises(RelationSourceValidationError):
                normalize_relation_source({
                    **SOURCE,
                    "columns": [{"name": "id", "type": column_type, "nullable": False, "ordinal": 1}],
                })
        with self.assertRaises(RelationSourceValidationError):
            normalize_relation_source(SOURCE, expected_profile_id="other")

    def test_accepts_foreign_and_partitioned_tables_as_read_sources(self):
        for kind in ("foreign_table", "partitioned_table"):
            with self.subTest(kind=kind):
                self.assertEqual(normalize_relation_source({**SOURCE, "kind": kind})["kind"], kind)

    def test_enforces_postgresql_identifier_bytes_and_ordered_unique_columns(self):
        self.assertEqual(normalize_relation_source({**SOURCE, "relation": "é" * 31})["relation"], "é" * 31)
        invalid_sources = (
            {**SOURCE, "relation": "é" * 32},
            {**SOURCE, "columns": [
                {"name": "a", "type": "text", "nullable": True, "ordinal": 2},
                {"name": "b", "type": "text", "nullable": True, "ordinal": 1},
            ]},
            {**SOURCE, "columns": [
                {"name": "a", "type": "text", "nullable": True, "ordinal": 1},
                {"name": "a", "type": "text", "nullable": True, "ordinal": 2},
            ]},
        )
        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaises(RelationSourceValidationError):
                normalize_relation_source(source)


if __name__ == "__main__":
    unittest.main()
