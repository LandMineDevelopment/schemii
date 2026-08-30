import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import MAX_DASHBOARD_BYTES, DashboardStore, DashboardStoreError, mercury_dashboard_record, migrate_dashboard_record
from schemii.schemer_ai_executor import SchemerAiExecutor
from schemii.schemer_examples import build_mercury_dashboard
from tests.capability_test_support import capabilities_for_formatted_type


SOURCE = {
    "profileId": "schemii_example_postgres",
    "database": "schemii",
    "namespace": "bookstore",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
}
SOURCE_COLUMNS = [
    {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1},
    {"name": "ordered_at", "type": "timestamp with time zone", "nullable": False, "ordinal": 2},
]
SOURCE_V2 = {
    **SOURCE,
    "snapshotVersion": 2,
    "columns": [
        {**column, "capabilities": capabilities_for_formatted_type(column["type"])}
        for column in SOURCE_COLUMNS
    ],
}
QUERY = {
    "version": 2,
    "dimensions": [{"id": "dimension_date", "label": "Order date", "column": "ordered_at"}],
    "measures": [{"id": "measure_orders", "label": "Orders", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}],
    "filters": [],
    "sort": [
        {"targetKind": "measure", "targetId": "measure_orders", "direction": "desc", "nulls": "last"},
        {"targetKind": "dimension", "targetId": "dimension_date", "direction": "asc", "nulls": "last"},
    ],
    "limit": 100,
}
TABLE = {
    "version": 1,
    "columns": [
        {"targetId": "dimension_date", "width": 180, "hidden": False, "pinned": True, "label": "Order date"},
        {"targetId": "measure_orders", "width": 120, "hidden": False, "pinned": False, "label": "Orders"},
    ],
    "pageSize": 25,
}
VISUALIZATION = {
    "version": 1,
    "mode": "bar",
    "selections": {
        "kpi": {"measureIds": ["measure_orders"]},
        "bar": {"dimensionId": "dimension_date", "measureIds": ["measure_orders"]},
        "line": {"dimensionId": "dimension_date", "measureIds": ["measure_orders"]},
        "donut": {"dimensionId": "dimension_date", "measureId": "measure_orders"},
    },
}
DETAIL = {
    "version": 1,
    "columns": [
        {"sourceColumn": "id", "label": "Order ID", "width": 120, "hidden": False, "searchable": False, "numberFormat": {"style": "integer"}},
        {"sourceColumn": "ordered_at", "label": "Ordered at", "width": 240, "hidden": False, "searchable": False, "numberFormat": {"style": "auto"}},
    ],
    "defaultSort": {"sourceColumn": "ordered_at", "direction": "desc", "nulls": "last"},
    "rowIdentifier": "id",
    "pageSize": 25,
}
MERCURY_COLUMNS = [
    {"name": name, "type": column_type, "nullable": nullable, "ordinal": index + 1}
    for index, (name, column_type, nullable) in enumerate([
        ("order_id", "bigint", False), ("customer_id", "bigint", False),
        ("customer_name", "character varying(160)", False), ("status", "character varying(20)", False),
        ("ordered_at", "timestamp with time zone", False), ("shipped_at", "timestamp with time zone", True),
        ("order_date", "date", False), ("item_count", "bigint", True), ("order_total", "numeric(14,2)", True),
    ])
]
MERCURY_DESCRIPTOR = {
    "profileId": "schemii_example_postgres", "database": "schemii", "namespace": "bookstore",
    "relation": "order_summary", "kind": "view", "fingerprint": "b" * 64, "columns": MERCURY_COLUMNS,
}


class DashboardStoreTests(unittest.TestCase):
    def test_representative_version_one_dashboards_upgrade_to_current_without_losing_intent(self):
        empty = {
            "id": "dashboard_empty", "version": 1, "revision": 3, "updatedAt": None,
            "dashboard": {
                "title": "Empty", "archived": False, "widgets": [], "slicers": [],
                "viewport": {"desktop": {"x": 12, "y": 24}, "mobile": {"x": 3, "y": 6}},
            },
        }
        configured = {
            "id": "dashboard_configured", "version": 1, "revision": 8, "updatedAt": "2026-01-02T03:04:05Z",
            "dashboard": {
                "title": "Configured", "archived": True, "slicers": [],
                "viewport": {"desktop": {"x": 80, "y": 90}, "mobile": {"x": 4, "y": 5}},
                "widgets": [{
                    "id": "widget_one", "kind": "placeholder", "title": "Preserve me",
                    "layout": {"desktop": {"x": 2, "y": 4, "w": 3, "h": 2}, "mobile": {"order": 7, "h": 2}},
                    "configuration": {},
                }],
            },
            "aiOperationReceipts": {"operation_one": {"kind": "dashboard_saved", "revision": 8}},
        }

        migrated_empty = migrate_dashboard_record(empty, "dashboard_empty")
        migrated_configured = migrate_dashboard_record(configured, "dashboard_configured")

        self.assertEqual((migrated_empty["version"], migrated_empty["revision"]), (3, 3))
        self.assertEqual(migrated_empty["dashboard"]["viewport"], {"desktop": {"y": 24}, "mobile": {"y": 6}})
        self.assertNotIn("layout", migrated_configured["dashboard"]["widgets"][0])
        self.assertEqual(migrated_configured["dashboard"]["widgets"][0]["configuration"], {})
        self.assertEqual(migrated_configured["aiOperationReceipts"], configured["aiOperationReceipts"])
        self.assertNotIn("updatedAt", migrated_empty)

    def test_version_one_null_timestamp_reads_in_memory_without_rewriting(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dashboards"
            path.mkdir()
            legacy = {
                "id": "dashboard_empty", "version": 1, "revision": 3, "updatedAt": None,
                "dashboard": {
                    "title": "Empty", "archived": False, "widgets": [], "slicers": [],
                    "viewport": {"desktop": {"x": 0, "y": 0}, "mobile": {"x": 0, "y": 0}},
                },
            }
            record_path = path / "dashboard_empty.json"
            record_path.write_text(json.dumps(legacy), encoding="utf-8")
            before = record_path.read_bytes()

            store = DashboardStore(path, read_only=True)
            migrated = store.get("dashboard_empty")

            self.assertEqual((migrated["version"], migrated["revision"]), (3, 3))
            self.assertNotIn("updatedAt", migrated)
            self.assertEqual(record_path.read_bytes(), before)

    def test_read_only_store_does_not_create_paths_or_allow_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing"
            store = DashboardStore(path, read_only=True)
            self.assertEqual(store.list(), [])
            self.assertFalse(path.exists())
            with self.assertRaises(DashboardStoreError) as caught:
                store.create("Blocked")
            self.assertEqual(caught.exception.payload["error"]["code"], "dashboard_store_read_only")

    def test_health_integrity_work_is_bounded_and_non_overlapping(self):
        for index in range(20):
            record = mercury_dashboard_record()
            record["id"] = f"dashboard_{index}"
            record["revision"] = 1
            (self.root / f"dashboard_{index}.json").write_text(json.dumps(record), encoding="utf-8")
        store = DashboardStore(self.root)
        original_read = store._read
        calls = []

        def counted(path):
            calls.append(path)
            return original_read(path)

        store._read = counted
        self.assertEqual(store.health()["recordCount"], 20)
        self.assertLessEqual(len(calls), 8)

        store._health_lock.acquire()
        try:
            calls.clear()
            self.assertEqual(store.health()["recordCount"], 20)
            self.assertEqual(calls, [])
        finally:
            store._health_lock.release()

    def test_health_scan_preserves_concurrent_record_updates(self):
        for index in range(20):
            record = mercury_dashboard_record()
            record["id"] = f"dashboard_{index}"
            record["revision"] = 1
            (self.root / f"dashboard_{index}.json").write_text(json.dumps(record), encoding="utf-8")
        store = DashboardStore(self.root)
        store.health()
        active_scan = store._health_scan

        created = store.create("Created during scan")
        store.delete("dashboard_0", 1)

        self.assertIn(created["id"], active_scan["recordIds"])
        self.assertNotIn("dashboard_0", active_scan["recordIds"])
        while store._health_scan is active_scan:
            store.health()
        self.assertEqual(store.health()["recordCount"], 20)

    def test_health_scan_treats_only_enumerated_then_deleted_record_as_absent(self):
        for index in range(20):
            record = mercury_dashboard_record()
            record["id"] = f"dashboard_{index}"
            record["revision"] = 1
            (self.root / f"dashboard_{index}.json").write_text(json.dumps(record), encoding="utf-8")
        store = DashboardStore(self.root)
        target = self.root / "dashboard_0.json"

        class Entry:
            def __init__(self, path):
                self.path = str(path)
                self.name = path.name

            def is_file(self, *, follow_symlinks):
                return Path(self.path).is_file()

        entries = [Entry(target)] + [
            Entry(self.root / f"dashboard_{index}.json") for index in range(1, 20)
        ]
        store._directory_entries = lambda path: iter(entries) if Path(path) == self.root else iter(())
        scan_reached_read = threading.Event()
        allow_read = threading.Event()
        target_removed = threading.Event()
        health_errors = []
        delete_errors = []
        original_read = store._read

        def interleaved_read(path):
            if path == target and threading.current_thread().name == "bounded-health-scan":
                scan_reached_read.set()
                self.assertTrue(allow_read.wait(2))
            return original_read(path)

        store._read = interleaved_read
        real_remove = __import__("schemii.dashboard_store", fromlist=["remove_file"]).remove_file

        def tracked_remove(path):
            real_remove(path)
            if Path(path) == target:
                target_removed.set()

        def scan_health():
            try:
                store.health()
            except Exception as exc:
                health_errors.append(exc)

        def delete_target():
            try:
                store.delete("dashboard_0", 1)
            except Exception as exc:
                delete_errors.append(exc)

        health_thread = threading.Thread(target=scan_health, name="bounded-health-scan")
        with patch("schemii.dashboard_store.remove_file", side_effect=tracked_remove):
            health_thread.start()
            self.assertTrue(scan_reached_read.wait(2))
            delete_thread = threading.Thread(target=delete_target)
            delete_thread.start()
            self.assertTrue(target_removed.wait(2))
            allow_read.set()
            health_thread.join(2)
            delete_thread.join(2)

        self.assertFalse(health_thread.is_alive())
        self.assertFalse(delete_thread.is_alive())
        self.assertEqual(health_errors, [])
        self.assertEqual(delete_errors, [])
        active_scan = store._health_scan
        while store._health_scan is active_scan:
            store.health()
        self.assertEqual(store.health()["recordCount"], 19)
        self.assertIsNone(store._health_error)

    def test_read_only_get_and_revision_guard_do_not_create_file_locks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dashboards"
            path.mkdir()
            record = mercury_dashboard_record()
            record["revision"] = 1
            (path / "dashboard_mercury.json").write_text(json.dumps(record), encoding="utf-8")
            store = DashboardStore(path, read_only=True)

            self.assertEqual(store.get("dashboard_mercury")["revision"], 1)
            with store.guard_revision("dashboard_mercury", 1) as guarded:
                self.assertEqual(guarded["id"], "dashboard_mercury")
            self.assertFalse((path / ".locks").exists())

            with self.assertRaises(DashboardStoreError) as caught:
                store.save("dashboard_mercury", record)
            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(caught.exception.payload["error"]["code"], "dashboard_store_read_only")

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "dashboards"
        self.store = DashboardStore(self.root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_version_one_read_migrates_geometry_in_memory_without_rewriting_user_data(self):
        legacy = mercury_dashboard_record()
        legacy["version"] = 1
        legacy["revision"] = 7
        legacy["updatedAt"] = "2026-08-23T12:00:00Z"
        legacy["aiOperationReceipts"] = {"operation_legacy": {"kind": "dashboard_saved", "revision": 7}}
        legacy_layouts = (
            (0, 0, 4, 3), (4, 0, 4, 3), (8, 0, 4, 3),
            (0, 3, 8, 6), (8, 3, 4, 6), (0, 9, 12, 5),
        )
        for order, (widget, (x, y, width, height)) in enumerate(zip(legacy["dashboard"]["widgets"], legacy_layouts)):
            widget["layout"] = {
                "desktop": {"x": x, "y": y, "w": width, "h": height},
                "mobile": {"order": order, "h": height},
            }
        legacy["dashboard"]["viewport"] = {"desktop": {"x": 0, "y": 0}, "mobile": {"x": 0, "y": 0}}
        legacy["dashboard"]["widgets"][0]["configuration"] = {"source": SOURCE}
        path = self.root / "dashboard_mercury.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before = path.read_bytes()

        migrated = self.store.get("dashboard_mercury")

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((migrated["version"], migrated["revision"]), (3, 7))
        self.assertTrue(all("layout" not in widget for widget in migrated["dashboard"]["widgets"]))
        self.assertEqual(migrated["dashboard"]["widgets"][0]["configuration"], {"source": SOURCE})
        self.assertEqual(migrated["aiOperationReceipts"], legacy["aiOperationReceipts"])
        self.assertEqual(migrated["dashboard"]["viewport"], {"desktop": {"y": 0}, "mobile": {"y": 0}})

    def test_version_two_read_uses_mobile_order_with_stable_ties_and_drops_geometry(self):
        legacy = mercury_dashboard_record()
        legacy["version"] = 2
        legacy["revision"] = 7
        orders = [4, 1, 1, 5, 3, 2]
        expected_ids = [
            widget["id"]
            for _, widget in sorted(zip(orders, legacy["dashboard"]["widgets"]), key=lambda item: item[0])
        ]
        for index, (widget, order) in enumerate(zip(legacy["dashboard"]["widgets"], orders)):
            widget["layout"] = {
                "desktop": {"x": index * 10, "y": index * 20, "width": 420, "height": 260},
                "mobile": {"order": order},
            }
        legacy["dashboard"]["viewport"] = {"desktop": {"x": 45, "y": 90}, "mobile": {"x": 12, "y": 24}}
        path = self.root / "dashboard_mercury.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before = path.read_bytes()

        migrated = self.store.get("dashboard_mercury")

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(migrated["version"], 3)
        self.assertEqual([widget["id"] for widget in migrated["dashboard"]["widgets"]], expected_ids)
        self.assertTrue(all("layout" not in widget for widget in migrated["dashboard"]["widgets"]))
        self.assertEqual(migrated["dashboard"]["viewport"], {"desktop": {"y": 90}, "mobile": {"y": 24}})

    def test_version_three_rejects_persisted_widget_geometry(self):
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][0]["layout"] = {
            "desktop": {"x": 0, "y": 0, "width": 420, "height": 260}, "mobile": {"order": 0},
        }
        with self.assertRaises(DashboardStoreError) as caught:
            self.store.save(record["id"], record)
        self.assertEqual(caught.exception.payload["error"]["code"], "invalid_dashboard")

    def test_example_initializes_once_and_deletion_is_respected(self):
        self.store.initialize_once()
        records = self.store.list()
        self.assertEqual([record["id"] for record in records], ["dashboard_mercury"])
        self.assertEqual(len(records[0]["dashboard"]["widgets"]), 6)
        self.store.delete("dashboard_mercury", records[0]["revision"])
        self.store.initialize_once()
        self.assertEqual(self.store.list(), [])

    def test_delete_rejects_stale_revision(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        with self.assertRaises(DashboardStoreError) as error:
            self.store.delete("dashboard_mercury", current["revision"] + 1)
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_changed")
        self.assertEqual(self.store.get("dashboard_mercury")["revision"], current["revision"])

    def test_live_mercury_template_has_six_executable_widgets(self):
        record = build_mercury_dashboard(MERCURY_DESCRIPTOR)
        widgets = record["dashboard"]["widgets"]
        self.assertEqual([widget["kind"] for widget in widgets], ["aggregate_report"] * 6)
        self.assertEqual({widget["configuration"]["source"]["relation"] for widget in widgets}, {"order_summary"})
        self.assertEqual([widget["configuration"]["visualization"]["mode"] for widget in widgets], ["kpi", "kpi", "kpi", "line", "donut", "table"])
        self.assertEqual(widgets[-1]["configuration"]["query"]["limit"], 10)

    def test_live_mercury_template_preserves_catalog_capability_snapshot(self):
        descriptor = {
            **MERCURY_DESCRIPTOR,
            "snapshotVersion": 2,
            "columns": [
                {**column, "capabilities": capabilities_for_formatted_type(column["type"])}
                for column in MERCURY_COLUMNS
            ],
        }
        record = build_mercury_dashboard(descriptor)
        for widget in record["dashboard"]["widgets"]:
            source = widget["configuration"]["source"]
            self.assertEqual(source["snapshotVersion"], 2)
            self.assertTrue(all("capabilities" in column for column in source["columns"]))

    def test_mercury_reset_preserves_order_viewport_and_custom_widgets(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        current["dashboard"]["viewport"]["desktop"] = {"y": 73}
        current["dashboard"]["widgets"].append({
            "id": "widget_custom", "kind": "aggregate_report", "title": "Custom",
            "configuration": {"source": SOURCE_V2, "query": QUERY},
        })
        current["dashboard"]["slicers"] = [{
            "id": "slicer_custom", "kind": "date_range", "title": "Custom dates",
            "range": {"start": "2026-01-01", "endExclusive": "2026-02-01"},
            "bindings": [{"widgetId": "widget_custom", "sourceColumn": "ordered_at"}],
        }]
        current = self.store.save(current["id"], current)
        restored = self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        self.assertEqual(restored["dashboard"]["viewport"]["desktop"], {"y": 73})
        self.assertEqual(restored["dashboard"]["widgets"][-1]["id"], "widget_custom")
        self.assertEqual(restored["dashboard"]["slicers"], current["dashboard"]["slicers"])
        self.assertTrue(all(widget["kind"] == "aggregate_report" for widget in restored["dashboard"]["widgets"][:6]))
        with self.assertRaises(DashboardStoreError) as error:
            self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_changed")

    def test_mercury_reset_appends_missing_widgets_by_array_order(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        current["dashboard"]["widgets"] = [widget for widget in current["dashboard"]["widgets"] if widget["id"] != "widget_revenue"]
        current["dashboard"]["widgets"].append({
            "id": "widget_custom", "kind": "placeholder", "title": "Custom",
            "configuration": {},
        })
        current = self.store.save(current["id"], current)
        restored = self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        widgets = restored["dashboard"]["widgets"]
        self.assertEqual(widgets[-1]["id"], "widget_revenue")
        self.assertTrue(all("layout" not in widget for widget in widgets))

    def test_legacy_mercury_upgrade_preserves_order_but_not_configured_widgets(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        configured = current["dashboard"]["widgets"][1]
        configured["kind"] = "aggregate_report"
        configured["configuration"] = build_mercury_dashboard(MERCURY_DESCRIPTOR)["dashboard"]["widgets"][1]["configuration"]
        configured["title"] = "My configured orders"
        current["dashboard"]["widgets"][2]["title"] = "My renamed preview"
        current = self.store.save(current["id"], current)
        upgraded = self.store.upgrade_mercury_example(build_mercury_dashboard(MERCURY_DESCRIPTOR))
        self.assertEqual([widget["id"] for widget in upgraded["dashboard"]["widgets"]], [widget["id"] for widget in current["dashboard"]["widgets"]])
        self.assertEqual(upgraded["dashboard"]["widgets"][0]["kind"], "aggregate_report")
        self.assertEqual(upgraded["dashboard"]["widgets"][1]["title"], "My configured orders")
        self.assertEqual(upgraded["dashboard"]["widgets"][2]["kind"], "placeholder")
        self.assertEqual(upgraded["dashboard"]["widgets"][2]["title"], "My renamed preview")
        self.assertEqual(self.store.upgrade_mercury_example(build_mercury_dashboard(MERCURY_DESCRIPTOR))["revision"], upgraded["revision"])

    def test_create_duplicate_and_permissions(self):
        self.store.initialize_once()
        created = self.store.create("Operations")
        duplicate = self.store.create("Mercury copy", "dashboard_mercury")
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(len(duplicate["dashboard"]["widgets"]), 6)
        self.assertNotEqual(duplicate["id"], "dashboard_mercury")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.root / f"{created['id']}.json").stat().st_mode), 0o600)

    def test_ai_mutations_are_idempotent_and_preserve_unrelated_state(self):
        self.store.initialize_once()
        before = self.store.get("dashboard_mercury")
        viewport = json.loads(json.dumps(before["dashboard"]["viewport"]))
        unrelated = json.loads(json.dumps(before["dashboard"]["widgets"][1:]))
        action = {"type": "widget_rename", "dashboardId": "dashboard_mercury", "expectedRevision": 1, "widgetId": before["dashboard"]["widgets"][0]["id"], "currentTitle": before["dashboard"]["widgets"][0]["title"], "title": "Renamed", "requiresConfirmation": True}
        first = self.store.apply_ai_mutation("dashboard_mercury", "operation_one", 1, action)
        duplicate = DashboardStore(self.root).apply_ai_mutation("dashboard_mercury", "operation_one", 1, action)
        current = self.store.get("dashboard_mercury")
        self.assertEqual(first, duplicate)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["dashboard"]["viewport"], viewport)
        self.assertEqual(current["dashboard"]["widgets"][1:], unrelated)

    def test_ai_duplicate_uses_deterministic_id_and_appends_without_layout(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); source = current["dashboard"]["widgets"][0]
        action = {"type": "widget_duplicate", "dashboardId": current["id"], "expectedRevision": current["revision"], "widgetId": source["id"], "currentTitle": source["title"], "title": "Copy", "requiresConfirmation": True}
        result = self.store.apply_ai_mutation(current["id"], "operation_duplicate", current["revision"], action)
        saved = self.store.get(current["id"]); duplicate = next(item for item in saved["dashboard"]["widgets"] if item["id"] == result["widgetId"])
        self.assertEqual(duplicate["configuration"], source["configuration"])
        self.assertEqual(saved["dashboard"]["widgets"][-1]["id"], duplicate["id"])
        self.assertNotIn("layout", duplicate)

    def test_bound_widget_delete_and_source_change_require_explicit_binding_removal(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        widget = current["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": SOURCE_V2, "query": QUERY}
        current["dashboard"]["slicers"] = [{
            "id": "slicer_orders", "kind": "date_range", "title": "Order dates",
            "range": {"start": "2026-01-01", "endExclusive": "2026-02-01"},
            "bindings": [{"widgetId": widget["id"], "sourceColumn": "ordered_at"}],
        }]
        current = self.store.save(current["id"], current)

        replaced = json.loads(json.dumps(current))
        replaced["dashboard"]["widgets"][0]["configuration"]["source"]["relation"] = "other_orders"
        replaced["dashboard"]["widgets"][0]["configuration"]["source"]["fingerprint"] = "b" * 64
        with self.assertRaises(DashboardStoreError) as affected:
            self.store.save(replaced["id"], replaced, "reject")
        self.assertEqual(affected.exception.payload["error"]["code"], "slicer_binding_affected")
        replaced = self.store.save(replaced["id"], replaced, "remove")
        self.assertEqual(replaced["dashboard"]["slicers"], [])

        replacement_slicer = json.loads(json.dumps(current["dashboard"]["slicers"]))
        replaced["dashboard"]["slicers"] = replacement_slicer
        replaced["dashboard"]["widgets"][0]["configuration"]["source"] = SOURCE_V2
        replaced = self.store.save(replaced["id"], replaced)
        deleted = json.loads(json.dumps(replaced))
        deleted["dashboard"]["widgets"] = deleted["dashboard"]["widgets"][1:]
        with self.assertRaises(DashboardStoreError) as affected:
            self.store.save(deleted["id"], deleted, "reject")
        self.assertEqual(affected.exception.payload["error"]["code"], "slicer_binding_affected")
        saved = self.store.save(deleted["id"], deleted, "remove")
        self.assertEqual(saved["dashboard"]["slicers"], [])

    def test_ai_delete_rejects_bound_widget_without_mutating_dashboard(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        widget = current["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": SOURCE_V2, "query": QUERY}
        current["dashboard"]["slicers"] = [{
            "id": "slicer_orders", "kind": "date_range", "title": "Order dates",
            "range": {"start": "2026-01-01", "endExclusive": "2026-02-01"},
            "bindings": [{"widgetId": widget["id"], "sourceColumn": "ordered_at"}],
        }]
        current = self.store.save(current["id"], current)
        action = {
            "type": "widget_delete", "widgetId": widget["id"], "currentTitle": widget["title"],
        }
        with self.assertRaises(DashboardStoreError) as affected:
            self.store.apply_ai_mutation(current["id"], "operation_delete_bound", current["revision"], action)
        self.assertEqual(affected.exception.payload["error"]["code"], "slicer_binding_affected")
        self.assertEqual(self.store.get(current["id"])["revision"], current["revision"])

    def test_ai_mutations_serialize_across_store_instances(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); widget = current["dashboard"]["widgets"][0]
        action = {"type": "widget_rename", "dashboardId": current["id"], "expectedRevision": 1, "widgetId": widget["id"], "currentTitle": widget["title"], "title": "First", "requiresConfirmation": True}
        outcomes = []
        errors = []
        def mutate(store, operation):
            try: outcomes.append(store.apply_ai_mutation(current["id"], operation, 1, action))
            except DashboardStoreError as error: errors.append(error.payload["error"]["code"])
        threads = [threading.Thread(target=mutate, args=(store, operation)) for store, operation in ((self.store, "operation_one"), (DashboardStore(self.root), "operation_two"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(errors, ["dashboard_changed"])
        self.assertEqual(self.store.get(current["id"])["revision"], 2)

    def test_revision_snapshot_and_unrelated_dashboard_locks_do_not_serialize_work(self):
        self.store.initialize_once()
        other = self.store.create("Other")
        mercury = self.store.get("dashboard_mercury")
        snapshot_save_finished = threading.Event()
        unrelated_guard_entered = threading.Event()

        def save_snapshot_target():
            current = self.store.get(mercury["id"])
            current["dashboard"]["title"] = "Changed during query"
            self.store.save(current["id"], current)
            snapshot_save_finished.set()

        with self.store.guard_revision(mercury["id"], mercury["revision"]):
            thread = threading.Thread(target=save_snapshot_target)
            thread.start()
            self.assertTrue(snapshot_save_finished.wait(1), "revision snapshots must not hold the dashboard lock")
            thread.join()

        def guard_other():
            with self.store._guard(other["id"]):
                unrelated_guard_entered.set()

        with self.store._guard(mercury["id"]):
            thread = threading.Thread(target=guard_other)
            thread.start()
            self.assertTrue(unrelated_guard_entered.wait(1), "different dashboards must have independent locks")
            thread.join()

    def test_receipt_rollover_and_deletion_preserve_reconciliation_evidence(self):
        store = DashboardStore(self.root, max_ai_receipts=2)
        store.initialize_once()
        expected = store.get("dashboard_mercury")
        receipts = {}
        for index in range(3):
            widget = expected["dashboard"]["widgets"][0]
            action = {
                "type": "widget_rename", "widgetId": widget["id"], "currentTitle": widget["title"],
                "title": f"Renamed {index}",
            }
            operation_id = f"operation_{index}"
            receipts[operation_id] = store.apply_ai_mutation(expected["id"], operation_id, expected["revision"], action)
            expected = store.get(expected["id"])

        restarted = DashboardStore(self.root, max_ai_receipts=2)
        evidence = restarted.operation_receipt_evidence(expected["id"], "operation_0")
        self.assertEqual(evidence, {"receipt": receipts["operation_0"], "source": "archive", "archiveComplete": True})
        self.assertEqual(restarted.apply_ai_mutation(expected["id"], "operation_0", 1, {"type": "widget_delete"}), receipts["operation_0"])
        restarted.delete(expected["id"], expected["revision"])
        self.assertEqual(restarted.operation_receipt_evidence(expected["id"], "operation_2")["receipt"], receipts["operation_2"])
        self.assertEqual(restarted.health()["receiptArchiveCount"], 3)

        class Authority:
            @staticmethod
            def resolve_operation(operation_id, chat_id, state, **outcome):
                return {"id": operation_id, "state": state, **outcome}

        reconciled = SchemerAiExecutor(
            object(), restarted, Authority(), catalog_sources=lambda *_: [], configured_widget=lambda *_: {},
        ).reconcile(
            {"id": "chat", "dashboardId": expected["id"]}, {"id": "operation_0", "state": "uncertain"},
            {"action": {"type": "widget_rename"}},
        )
        self.assertEqual(reconciled["operation"]["state"], "succeeded")
        self.assertEqual(reconciled["operation"]["result"], receipts["operation_0"])

        restarted._receipt_path("operation_0").write_text("not-json", encoding="utf-8")
        with self.assertRaises(DashboardStoreError) as unhealthy:
            restarted.health()
        self.assertEqual(unhealthy.exception.payload["error"]["code"], "dashboard_receipt_archive_error")

    def test_receipt_archive_is_durable_before_rollover_write(self):
        store = DashboardStore(self.root, max_ai_receipts=1)
        store.initialize_once()
        current = store.get("dashboard_mercury")
        widget = current["dashboard"]["widgets"][0]
        first = store.apply_ai_mutation(current["id"], "operation_first", current["revision"], {
            "type": "widget_rename", "widgetId": widget["id"], "currentTitle": widget["title"], "title": "First",
        })
        current = store.get(current["id"])
        real_write = __import__("schemii.dashboard_store", fromlist=["write_json"]).write_json

        def fail_dashboard_write(path, payload, **options):
            if Path(path).parent == self.root:
                raise OSError("dashboard fsync failed")
            return real_write(path, payload, **options)

        with patch("schemii.dashboard_store.write_json", side_effect=fail_dashboard_write):
            with self.assertRaises(DashboardStoreError):
                store.apply_ai_mutation(current["id"], "operation_second", current["revision"], {
                    "type": "widget_rename", "widgetId": widget["id"], "currentTitle": "First", "title": "Second",
                })
        self.assertEqual(store.operation_receipt_evidence(current["id"], "operation_first")["receipt"], first)
        self.assertEqual(store.get(current["id"])["revision"], current["revision"])

    def test_reconciliation_never_treats_untracked_legacy_absence_as_not_applied(self):
        self.store.initialize_once()

        class Authority:
            def __init__(self):
                self.resolutions = []

            def resolve_operation(self, *args, **kwargs):
                self.resolutions.append((args, kwargs))
                return {"id": args[0], "state": args[2], **kwargs}

        authority = Authority()
        executor = SchemerAiExecutor(
            object(), self.store, authority, catalog_sources=lambda *_: [], configured_widget=lambda *_: {},
        )
        current = {"id": "legacy_operation", "state": "uncertain"}
        legacy = executor.reconcile(
            {"id": "chat", "dashboardId": "dashboard_mercury"}, current,
            {"action": {"type": "widget_rename"}},
        )
        self.assertEqual(legacy["operation"], current)
        self.assertEqual(legacy["reconciliation"]["status"], "insufficient_evidence")
        self.assertEqual(authority.resolutions, [])

        self.store._track_operation("dashboard_mercury", "tracked_not_applied")
        tracked = executor.reconcile(
            {"id": "chat", "dashboardId": "dashboard_mercury"}, {"id": "tracked_not_applied", "state": "uncertain"},
            {"action": {"type": "widget_delete"}},
        )
        self.assertEqual(tracked["operation"]["state"], "failed")
        self.assertTrue(tracked["operation"]["error"]["receiptEvidence"]["archiveComplete"])

    def test_dashboard_pages_reject_tampering_and_stale_cursors_without_parsing_cached_summaries(self):
        self.store.create("Zulu")
        self.store.create("Alpha")
        first = self.store.list_page(summaries=True, page_size="1")
        self.assertTrue(first["page"]["hasMore"])
        cursor = first["page"]["nextCursor"]
        with self.assertRaises(DashboardStoreError) as malformed:
            self.store.list_page(summaries=True, page_size="1", cursor=("A" if cursor[0] != "A" else "B") + cursor[1:])
        self.assertEqual(malformed.exception.payload["error"]["code"], "invalid_dashboard_cursor")

        self.store.create("Changed")
        with self.assertRaises(DashboardStoreError) as stale:
            self.store.list_page(summaries=True, page_size="1", cursor=cursor)
        self.assertEqual(stale.exception.payload["error"]["code"], "dashboard_cursor_stale")

        with patch.object(self.store, "_read", side_effect=AssertionError("valid summary caches should avoid record parsing")):
            self.assertTrue(self.store.list_page(summaries=True, page_size=2)["items"])

    def test_oversized_existing_record_is_readable_but_not_overwritten(self):
        self.store.initialize_once()
        path = self.root / "dashboard_mercury.json"
        existing = self.store.get("dashboard_mercury")
        existing["aiOperationReceipts"] = {"legacy": {"blob": "x" * MAX_DASHBOARD_BYTES}}
        path.write_text(json.dumps(existing), encoding="utf-8")
        before = path.read_bytes()
        loaded = self.store.get("dashboard_mercury")
        loaded["dashboard"]["title"] = "Must not replace"
        with self.assertRaises(DashboardStoreError) as oversized:
            self.store.save(loaded["id"], loaded)
        self.assertEqual((oversized.exception.status, oversized.exception.payload["error"]["code"]), (413, "dashboard_too_large"))
        self.assertEqual(path.read_bytes(), before)

    def test_delete_uses_directory_synced_atomic_json_removal(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        synced = []
        with patch("schemii.atomic_json._sync_directory", side_effect=lambda directory: synced.append(directory)):
            self.store.delete(current["id"], current["revision"])
        self.assertIn(self.root, synced)
        self.assertFalse((self.root / "dashboard_mercury.json").exists())

    def test_client_save_cannot_remove_or_forge_ai_receipts(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); widget = current["dashboard"]["widgets"][0]
        action = {"type": "widget_rename", "dashboardId": current["id"], "expectedRevision": 1, "widgetId": widget["id"], "currentTitle": widget["title"], "title": "Renamed", "requiresConfirmation": True}
        receipt = self.store.apply_ai_mutation(current["id"], "operation_receipt", 1, action)
        saved = self.store.get(current["id"]); saved["aiOperationReceipts"] = {"forged": {"kind": "fake"}}
        self.store.save(saved["id"], saved)
        self.assertEqual(self.store.operation_receipt(current["id"], "operation_receipt"), receipt)
        self.assertIsNone(self.store.operation_receipt(current["id"], "forged"))

    def test_ai_placeholder_appends_without_layout(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury")
        action = {"type": "widget_create", "dashboardId": current["id"], "expectedRevision": current["revision"], "title": "New", "requiresConfirmation": True}
        result = self.store.apply_ai_mutation(current["id"], "operation_create", current["revision"], action)
        widget = next(item for item in self.store.get(current["id"])["dashboard"]["widgets"] if item["id"] == result["widgetId"])
        self.assertEqual(self.store.get(current["id"])["dashboard"]["widgets"][-1]["id"], widget["id"])
        self.assertNotIn("layout", widget)

    def test_stale_revision_is_rejected_without_changing_order(self):
        self.store.initialize_once()
        first = self.store.get("dashboard_mercury")
        stale = json.loads(json.dumps(first))
        first["dashboard"]["widgets"][0], first["dashboard"]["widgets"][1] = first["dashboard"]["widgets"][1], first["dashboard"]["widgets"][0]
        saved = self.store.save(first["id"], first)
        self.assertEqual(saved["dashboard"]["widgets"][0]["id"], "widget_orders")
        with self.assertRaises(DashboardStoreError) as error:
            self.store.save(stale["id"], stale)
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_conflict")
        self.assertEqual(self.store.get(first["id"])["dashboard"]["widgets"][0]["id"], "widget_orders")

    def test_revision_guard_rejects_stale_operations(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        with self.store.guard_revision(record["id"], record["revision"]):
            self.assertEqual(self.store.get(record["id"])["revision"], record["revision"])
        with self.assertRaises(DashboardStoreError) as error:
            with self.store.guard_revision(record["id"], record["revision"] + 1):
                pass
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_changed")

    def test_invalid_records_and_duplicate_widget_ids_are_rejected(self):
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][1]["id"] = record["dashboard"]["widgets"][0]["id"]
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)
        with self.assertRaises(DashboardStoreError):
            self.store.create("  invalid  ")

    def test_single_widget_source_persists_and_duplicates_independently(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        source = {**SOURCE, "columns": SOURCE_COLUMNS}
        record["dashboard"]["widgets"][0]["configuration"] = {"source": source}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["source"], source)
        duplicate = self.store.create("Sourced copy", record["id"])
        duplicate_source = duplicate["dashboard"]["widgets"][0]["configuration"]["source"]
        duplicate_source["relation"] = "customers"
        self.assertEqual(self.store.get(record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")

    def test_widget_source_rejects_multiple_sources_joins_sql_and_malformed_identity(self):
        invalid_configurations = [
            {"sources": [SOURCE]},
            {"source": SOURCE, "columns": [{"relation": "customers", "column": "id"}]},
            {"source": {**SOURCE, "join": {"relation": "customers"}}},
            {"source": {**SOURCE, "columnReference": "customers.id"}},
            {"source": {**SOURCE, "sql": "SELECT * FROM orders"}},
            {"source": [SOURCE]},
            {"source": {**SOURCE, "kind": "sequence"}},
            {"source": {**SOURCE, "fingerprint": "short"}},
            {"source": {key: value for key, value in SOURCE.items() if key != "namespace"}},
            {"source": {**SOURCE, "columns": [{**SOURCE_COLUMNS[0], "suggestions": ["identifier"]}]}},
            {"source": {**SOURCE, "columns": [SOURCE_COLUMNS[0], SOURCE_COLUMNS[0]]}},
        ]
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                record = mercury_dashboard_record()
                record["dashboard"]["widgets"][0]["configuration"] = configuration
                with self.assertRaises(DashboardStoreError) as error:
                    self.store.save(record["id"], record)
                self.assertEqual(error.exception.payload["error"]["code"], "invalid_dashboard")

    def test_versioned_widget_query_round_trips_and_requires_snapshot(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["query"], QUERY)
        for configuration in (
            {"source": SOURCE, "query": QUERY},
            {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": {**QUERY, "version": 3}},
            {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": {**QUERY, "measures": []}},
            {"query": QUERY},
        ):
            invalid = mercury_dashboard_record()
            invalid["dashboard"]["widgets"][0]["configuration"] = configuration
            with self.assertRaises(DashboardStoreError):
                self.store.save(invalid["id"], invalid)

    def test_aggregate_report_table_configuration_round_trips(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": TABLE}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["table"], TABLE)

    def test_aggregate_report_visualization_round_trips_without_changing_query_or_table(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": TABLE, "visualization": VISUALIZATION}
        saved = self.store.save(record["id"], record)
        configuration = saved["dashboard"]["widgets"][0]["configuration"]
        self.assertEqual(configuration["visualization"], VISUALIZATION)
        self.assertEqual(configuration["query"], QUERY)
        self.assertEqual(configuration["table"], TABLE)
        configuration["visualization"]["selections"]["bar"]["dimensionId"] = None
        configuration["visualization"]["selections"]["line"]["dimensionId"] = None
        configuration["visualization"]["selections"]["donut"]["dimensionId"] = None
        saved_again = self.store.save(saved["id"], saved)
        self.assertIsNone(saved_again["dashboard"]["widgets"][0]["configuration"]["visualization"]["selections"]["bar"]["dimensionId"])

    def test_aggregate_report_detail_round_trips_without_changing_other_configuration(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        source = {**SOURCE, "columns": SOURCE_COLUMNS}
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": source,
            "query": QUERY,
            "table": TABLE,
            "visualization": VISUALIZATION,
            "detail": DETAIL,
        }
        saved = self.store.save(record["id"], record)
        configuration = saved["dashboard"]["widgets"][0]["configuration"]
        self.assertEqual(configuration["detail"], DETAIL)
        self.assertEqual(configuration["source"], source)
        self.assertEqual(configuration["query"], QUERY)
        self.assertEqual(configuration["table"], TABLE)
        self.assertEqual(configuration["visualization"], VISUALIZATION)

    def test_aggregate_report_detail_accepts_nullable_options_and_boundaries(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        detail = {
            **DETAIL,
            "columns": [
                {"sourceColumn": "id", "label": "I", "width": 64, "hidden": True, "searchable": False, "numberFormat": {"style": "integer"}},
                {"sourceColumn": "ordered_at", "label": "O" * 128, "width": 1024, "hidden": False, "searchable": False, "numberFormat": {"style": "auto"}},
            ],
            "defaultSort": None,
            "rowIdentifier": None,
            "pageSize": 100,
        }
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["detail"], detail)

    def test_aggregate_report_rejects_invalid_detail_shapes_and_references(self):
        invalid_details = [
            {**DETAIL, "version": 2},
            {**DETAIL, "version": True},
            {**DETAIL, "pageSize": 20},
            {**DETAIL, "pageSize": True},
            {**DETAIL, "pageSize": []},
            {**DETAIL, "columns": []},
            {**DETAIL, "columns": [DETAIL["columns"][0], DETAIL["columns"][0]]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "sourceColumn": "missing"}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "width": 63}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "width": 1025}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "hidden": 0}]},
            {**DETAIL, "columns": [{**column, "hidden": True} for column in DETAIL["columns"]]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "searchable": "yes"}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "label": ""}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "label": "x" * 129}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "extra": False}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "numberFormat": {"style": "currency", "currency": "usd", "fractionDigits": 2}}]},
            {**DETAIL, "columns": [{key: value for key, value in DETAIL["columns"][0].items() if key != "numberFormat"}]},
            {**DETAIL, "defaultSort": {"sourceColumn": "missing", "direction": "asc", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": [], "direction": "asc", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "up", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": [], "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "auto"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": []}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "first", "extra": True}},
            {**DETAIL, "rowIdentifier": "missing"},
            {**DETAIL, "rowIdentifier": False},
            {**DETAIL, "extra": None},
        ]
        for detail in invalid_details:
            with self.subTest(detail=detail):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
                with self.assertRaises(DashboardStoreError) as error:
                    self.store.save(record["id"], record)
                self.assertEqual(error.exception.payload["error"]["code"], "invalid_dashboard")
        extra_source_columns = [
            {"name": f"column_{index}", "type": "text", "nullable": True, "ordinal": index + 3}
            for index in range(63)
        ]
        too_many_columns = [
            {"sourceColumn": column["name"], "label": column["name"], "width": 160, "hidden": False, "searchable": True, "numberFormat": {"style": "auto"}}
            for column in extra_source_columns
        ]
        record = mercury_dashboard_record()
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": {**SOURCE, "columns": SOURCE_COLUMNS + extra_source_columns},
            "query": QUERY,
            "detail": {**DETAIL, "columns": DETAIL["columns"] + too_many_columns},
        }
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)

    def test_detail_references_can_use_snapshot_columns_and_non_aggregate_widgets_reject_detail(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        detail = {**DETAIL, "columns": [{**DETAIL["columns"][0], "searchable": True}], "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "last"}}
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["detail"], detail)
        for kind in ("preview", "placeholder"):
            with self.subTest(kind=kind):
                record = mercury_dashboard_record()
                record["dashboard"]["widgets"][0]["kind"] = kind
                record["dashboard"]["widgets"][0]["configuration"] = {
                    "source": {**SOURCE, "columns": SOURCE_COLUMNS},
                    "query": QUERY,
                    "detail": DETAIL,
                }
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)

    def test_aggregate_report_rejects_invalid_visualization_references_and_shapes(self):
        invalid_visualizations = [
            {**VISUALIZATION, "version": 2},
            {**VISUALIZATION, "mode": "scatter"},
            {**VISUALIZATION, "mode": []},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "bar": {"dimensionId": "missing", "measureIds": ["measure_orders"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "bar": {"dimensionId": [], "measureIds": ["measure_orders"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "line": {"dimensionId": "dimension_date", "measureIds": []}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "line": {"dimensionId": "dimension_date", "measureIds": [[]]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "kpi": {"measureIds": ["missing"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "donut": {"dimensionId": "dimension_date", "measureId": None}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "extra": {}}},
        ]
        for visualization in invalid_visualizations:
            with self.subTest(visualization=visualization):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "visualization": visualization}
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)

    def test_aggregate_report_rejects_invalid_presentation_without_breaking_existing_widgets(self):
        invalid_tables = [
            {**TABLE, "pageSize": 500},
            {**TABLE, "columns": TABLE["columns"][:-1]},
            {**TABLE, "columns": [TABLE["columns"][0], TABLE["columns"][0]]},
            {**TABLE, "columns": [TABLE["columns"][1], TABLE["columns"][0]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "width": 63}, TABLE["columns"][1]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "hidden": "no"}, TABLE["columns"][1]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "targetId": []}, TABLE["columns"][1]]},
        ]
        for table in invalid_tables:
            with self.subTest(table=table):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": table}
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][0]["kind"] = "aggregate_report"
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)

    def test_malformed_file_is_not_listed_or_overwritten(self):
        malformed = self.root / "broken.json"
        malformed.write_text("not json", encoding="utf-8")
        with self.assertRaises(DashboardStoreError) as listed:
            self.store.list()
        self.assertEqual(listed.exception.payload["error"]["code"], "dashboard_record_malformed")
        with self.assertRaises(DashboardStoreError):
            self.store.get("broken")
        with self.assertRaises(DashboardStoreError):
            self.store.health()
        self.assertEqual(malformed.read_text(encoding="utf-8"), "not json")


if __name__ == "__main__":
    unittest.main()
