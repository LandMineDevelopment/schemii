import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.query_type_capabilities import catalog_capabilities, normalize_capabilities
from schemii.widget_query import QueryValidationError, compile_query, normalize_query
from schemii.postgres_service import quote_identifier
from tests.capability_test_support import capabilities, column


def catalog_column(**values):
    return {
        "declared_type_oid": 91001, "base_type_oid": 1700, "base_type_namespace": "pg_catalog",
        "declared_type_namespace": "billing", "declared_type_name": "amount_domain", "declared_type_kind": "d", "declared_type_category": "N",
        "base_type_name": "numeric", "base_type_kind": "b", "base_type_category": "N",
        "type_catalog_version": "domain:4:base:9", "collation_identity": None,
        "array_identity": None, "range_identity": None, **values,
    }


def operator(logical, oid, name, *, namespace="pg_catalog", input_oid=1700, version="1"):
    return {
        "logical_name": logical, "operator_oid": oid, "operator_namespace": namespace,
        "operator_name": name, "input_type_oid": input_oid, "result_type_oid": 16,
        "catalog_version": version,
    }


def aggregate(logical, oid, name, *, namespace="pg_catalog", input_oid=1700, result_oid=1700, version="1", sortable=True, zeroable=True):
    return {
        "logical_name": logical, "aggregate_oid": oid, "aggregate_namespace": namespace,
        "aggregate_name": name, "input_type_oid": input_oid, "result_type_oid": result_oid,
        "catalog_version": version, "output_sortable": sortable, "output_zeroable": zeroable,
    }


class QueryTypeCapabilityTests(unittest.TestCase):
    def test_recursive_domain_projects_base_catalog_operations_and_mutation_identity(self):
        operators = [
            operator("eq", 1, "="), operator("neq", 2, "<>"), operator("lt", 3, "<"),
            operator("lte", 4, "<="), operator("gt", 5, ">"), operator("gte", 6, ">="),
        ]
        aggregates = [
            aggregate("count", 10, "count", result_oid=20), aggregate("sum", 11, "sum"),
            aggregate("average", 12, "avg"), aggregate("minimum", 13, "min"), aggregate("maximum", 14, "max"),
        ]
        projected = catalog_capabilities(catalog_column(), operators, aggregates)
        self.assertEqual((projected["declaredTypeOid"], projected["baseTypeOid"]), (91001, 1700))
        self.assertTrue(projected["groupable"] and projected["sortable"] and projected["numeric"])
        self.assertEqual([item["name"] for item in projected["aggregates"]], ["count", "sum", "average", "minimum", "maximum"])
        changed = catalog_capabilities(catalog_column(type_catalog_version="domain:5:base:9"), operators, aggregates)
        self.assertNotEqual(projected["capabilityFingerprint"], changed["capabilityFingerprint"])

    def test_enum_custom_btree_equality_only_and_no_equality_are_not_overpromised(self):
        enum_column = catalog_column(base_type_oid=92000, base_type_namespace="app_types", base_type_name="mood", base_type_kind="e", base_type_category="E", declared_type_oid=92000)
        custom_ops = [operator("eq", 21, "===", namespace="app_ops", input_oid=92000), operator("neq", 22, "!==", namespace="app_ops", input_oid=92000)]
        equality_only = catalog_capabilities(enum_column, custom_ops, [aggregate("count", 23, "count", input_oid=92000, result_oid=20, zeroable=False)])
        self.assertTrue(equality_only["groupable"])
        self.assertFalse(equality_only["sortable"])
        self.assertEqual([item["name"] for item in equality_only["filterOperators"]], ["eq", "neq", "in", "not_in", "is_null", "is_not_null"])
        no_equality = catalog_capabilities({**enum_column, "base_type_name": "opaque"}, [], [])
        self.assertFalse(no_equality["groupable"])
        self.assertEqual([item["name"] for item in no_equality["filterOperators"]], ["is_null", "is_not_null"])

    def test_custom_aggregate_and_operator_compile_schema_qualified(self):
        custom = column(
            "amount", "billing.amount_domain", True, 1, oid=93000, namespace="billing", name="amount_base",
            category="N", pattern=False, numeric=True, aggregates=("count", "sum"),
        )
        capability = custom["capabilities"]
        for item in capability["filterOperators"]:
            if "operator" in item:
                item["operator"].update({"namespace": "billing_ops", "name": "===" if item["name"] in {"eq", "in", "not_in"} else item["operator"]["name"]})
            if "operators" in item:
                for identity in item["operators"].values():
                    identity["namespace"] = "billing_ops"
        for item in capability["aggregates"]:
            item["aggregate"]["namespace"] = "billing_stats"
        identity = {key: value for key, value in capability.items() if key != "capabilityFingerprint"}
        from schemii.postgres_common import canonical_fingerprint
        capability["capabilityFingerprint"] = canonical_fingerprint(identity)
        normalize_capabilities(capability)
        query = {
            "version": 2, "dimensions": [],
            "measures": [{"id": "m", "label": "Total", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "auto"}}],
            "filters": [{"id": "g", "conditions": [{"id": "f", "column": "amount", "operator": "eq", "values": [5]}]}],
            "sort": [], "limit": 10,
        }
        normalized = normalize_query(query, [custom])
        sql = compile_query({"namespace": "billing", "relation": "invoice"}, normalized, quote_identifier, [custom])["sql"]
        self.assertIn('"billing_stats"."sum"(("amount"::"billing"."amount_base"))', sql)
        self.assertIn('OPERATOR("billing_ops".===) %s::"billing"."amount_base"', sql)
        self.assertNotIn("5", sql)

    def test_timestamp_domain_is_temporal_and_legacy_snapshot_requires_reselection(self):
        timestamp_domain = capabilities(1114, name="timestamp", category="D", pattern=False, temporal="timestamp")
        self.assertEqual(timestamp_domain["temporal"], "timestamp")
        legacy = [{"name": "created_at", "type": "timestamp without time zone", "nullable": False, "ordinal": 1}]
        query = {"version": 2, "dimensions": [], "measures": [{"id": "m", "label": "Rows", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}], "filters": [], "sort": [], "limit": 10}
        with self.assertRaisesRegex(QueryValidationError, "reselect"):
            normalize_query(query, legacy)
        self.assertEqual(normalize_query(query, legacy, allow_legacy_snapshot=True)["version"], 2)

    def test_legacy_snapshot_keeps_postgresql_count_compatibility(self):
        legacy = [{"name": "payload", "type": "json", "nullable": True, "ordinal": 1}]
        query = {
            "version": 2,
            "dimensions": [],
            "measures": [{
                "id": "measure_count", "label": "Count", "column": "payload", "aggregation": "count",
                "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"},
            }],
            "filters": [], "sort": [], "limit": 100,
        }
        self.assertEqual(normalize_query(query, legacy, allow_legacy_snapshot=True)["measures"][0]["aggregation"], "count")


if __name__ == "__main__":
    unittest.main()
