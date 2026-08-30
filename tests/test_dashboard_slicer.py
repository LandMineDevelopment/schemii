import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_slicer import (
    MAX_SLICERS,
    SlicerValidationError,
    compose_dashboard_slicers,
    normalize_dashboard_slicers,
)
from tests.capability_test_support import column


SOURCE = {
    "profileId": "local",
    "database": "demo",
    "namespace": "public",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
    "snapshotVersion": 2,
    "columns": [
        column("status", "text", False, 1, oid=25),
        column("order_date", "date", False, 2, oid=1082, category="D", name="date", temporal="date", pattern=False),
        column("created_at", "timestamp", False, 3, oid=1114, category="D", name="timestamp", temporal="timestamp", pattern=False),
        column("occurred_at", "timestamp with time zone", False, 4, oid=1184, category="D", name="timestamptz", temporal="timestamp_tz", pattern=False),
    ],
}


def query():
    return {
        "version": 2,
        "dimensions": [{"id": "dimension_date", "label": "Date", "column": "order_date"}],
        "measures": [{
            "id": "measure_rows", "label": "Rows", "column": None,
            "aggregation": "count_rows", "distinct": False,
            "nullBehavior": "preserve", "numberFormat": {"style": "integer"},
        }],
        "filters": [
            {"id": "group_paid", "conditions": [{"id": "filter_paid", "column": "status", "operator": "eq", "values": ["paid"]}]},
            {"id": "group_pending", "conditions": [{"id": "filter_pending", "column": "status", "operator": "eq", "values": ["pending"]}]},
        ],
        "sort": [],
        "limit": 100,
    }


def widget():
    return {
        "id": "widget_orders",
        "kind": "aggregate_report",
        "title": "Orders",
        "configuration": {"source": SOURCE, "query": query()},
    }


def slicers():
    return [
        {
            "id": "slicer_order_date", "kind": "date_range", "title": "Order date",
            "range": {"start": "2026-01-01", "endExclusive": "2026-02-01"},
            "bindings": [{"widgetId": "widget_orders", "sourceColumn": "order_date"}],
        },
        {
            "id": "slicer_created", "kind": "date_range", "title": "Created",
            "range": {"start": "2026-03-01", "endExclusive": "2026-04-01"},
            "bindings": [{
                "widgetId": "widget_orders", "sourceColumn": "created_at",
                "sourceTimeZone": "America/New_York",
            }],
        },
    ]


class DashboardSlicerTests(unittest.TestCase):
    def test_normalizes_exact_temporal_bindings_and_timezone_semantics(self):
        normalized = normalize_dashboard_slicers(slicers(), [widget()])
        self.assertEqual(normalized, slicers())

        invalid = slicers()
        del invalid[1]["bindings"][0]["sourceTimeZone"]
        with self.assertRaises(SlicerValidationError):
            normalize_dashboard_slicers(invalid, [widget()])

        invalid = slicers()
        invalid[0]["bindings"][0]["sourceTimeZone"] = "UTC"
        with self.assertRaises(SlicerValidationError):
            normalize_dashboard_slicers(invalid, [widget()])

        invalid = slicers()
        invalid[0]["bindings"][0]["sourceColumn"] = "status"
        with self.assertRaises(SlicerValidationError):
            normalize_dashboard_slicers(invalid, [widget()])

        invalid = slicers()
        invalid[0]["range"]["endExclusive"] = invalid[0]["range"]["start"]
        with self.assertRaises(SlicerValidationError):
            normalize_dashboard_slicers(invalid, [widget()])

    def test_composes_each_slicer_into_every_or_group_deterministically(self):
        widgets = [widget()]
        normalized_slicers = normalize_dashboard_slicers(slicers(), widgets)

        first, lineage = compose_dashboard_slicers(widgets, normalized_slicers, "widget_orders", query())
        second, repeated_lineage = compose_dashboard_slicers(widgets, normalized_slicers, "widget_orders", query())

        self.assertEqual(first, second)
        self.assertEqual(lineage, repeated_lineage)
        self.assertEqual([len(group["conditions"]) for group in first["filters"]], [5, 5])
        for group, original in zip(first["filters"], query()["filters"]):
            self.assertEqual(group["conditions"][0], original["conditions"][0])
            self.assertEqual(
                [(item["column"], item["operator"], item["values"]) for item in group["conditions"][1:]],
                [
                    ("order_date", "gte", ["2026-01-01"]),
                    ("order_date", "lt", ["2026-02-01"]),
                    ("created_at", "gte", ["2026-03-01T00:00:00"]),
                    ("created_at", "lt", ["2026-04-01T00:00:00"]),
                ],
            )
        self.assertEqual(lineage[0]["range"], {
            "startInclusive": "2026-01-01", "endExclusive": "2026-02-01",
        })
        self.assertEqual(lineage[1]["sourceTimeZone"], "America/New_York")
        self.assertEqual(len(lineage[0]["conditions"]), 2)

    def test_composes_an_empty_filter_as_one_and_group(self):
        value = query()
        value["filters"] = []
        effective, lineage = compose_dashboard_slicers(
            [widget()], normalize_dashboard_slicers(slicers()[:1], [widget()]), "widget_orders", value,
        )
        self.assertEqual(len(effective["filters"]), 1)
        self.assertEqual([item["operator"] for item in effective["filters"][0]["conditions"]], ["gte", "lt"])
        self.assertEqual(lineage[0]["conditions"][0]["filterGroupId"], effective["filters"][0]["id"])

    def test_rejects_slicer_and_composed_query_limits(self):
        with self.assertRaises(SlicerValidationError):
            normalize_dashboard_slicers(slicers()[:1] * (MAX_SLICERS + 1), [widget()])

        value = query()
        value["filters"] = [{
            "id": "large_group",
            "conditions": [
                {"id": f"filter_{index}", "column": "status", "operator": "eq", "values": [str(index)]}
                for index in range(63)
            ],
        }]
        with self.assertRaises(SlicerValidationError) as limited:
            compose_dashboard_slicers(
                [widget()], normalize_dashboard_slicers(slicers()[:1], [widget()]), "widget_orders", value,
            )
        self.assertEqual(limited.exception.code, "slicer_query_limit")
        self.assertEqual(limited.exception.details["kind"], "filterConditions")

    def test_composition_does_not_mutate_saved_query_or_slicers(self):
        widgets = [widget()]
        saved_slicers = normalize_dashboard_slicers(slicers(), widgets)
        saved_query = query()
        before = json.dumps({"query": saved_query, "slicers": saved_slicers}, sort_keys=True)
        compose_dashboard_slicers(widgets, saved_slicers, "widget_orders", saved_query)
        self.assertEqual(json.dumps({"query": saved_query, "slicers": saved_slicers}, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
