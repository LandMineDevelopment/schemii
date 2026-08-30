import copy
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore, DashboardStoreError
from schemii.legacy_source_upgrade import (
    MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH,
    LegacySourceUpgrade,
)
from tests.capability_test_support import capabilities_for_formatted_type


LEGACY_COLUMNS = [
    {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1},
]
LEGACY_SOURCE = {
    "profileId": "shared",
    "database": "demo",
    "namespace": "public",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
    "columns": LEGACY_COLUMNS,
}
CURRENT_COLUMNS = [
    {**LEGACY_COLUMNS[0], "capabilities": capabilities_for_formatted_type("bigint")},
]
QUERY = {
    "version": 2,
    "dimensions": [],
    "measures": [{
        "id": "measure_rows", "label": "Rows", "column": None, "aggregation": "count_rows",
        "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"},
    }],
    "filters": [],
    "sort": [],
    "limit": 100,
}
TABLE = {
    "version": 1,
    "columns": [{"targetId": "measure_rows", "width": 120, "hidden": False, "pinned": False, "label": "Rows"}],
    "pageSize": 25,
}
VISUALIZATION = {
    "version": 1,
    "mode": "kpi",
    "selections": {
        "kpi": {"measureIds": ["measure_rows"]},
        "bar": {"dimensionId": None, "measureIds": ["measure_rows"]},
        "line": {"dimensionId": None, "measureIds": ["measure_rows"]},
        "donut": {"dimensionId": None, "measureId": "measure_rows"},
    },
}
DETAIL = {
    "version": 1,
    "columns": [{
        "sourceColumn": "id", "label": "ID", "width": 120, "hidden": False,
        "searchable": False, "numberFormat": {"style": "integer"},
    }],
    "defaultSort": None,
    "rowIdentifier": "id",
    "pageSize": 25,
}


def descriptor(relation="orders", *, database="demo", kind="table", legacy_fingerprint="a" * 64, fingerprint="b" * 64):
    return {
        "profileId": "shared",
        "database": database,
        "namespace": "public",
        "relation": relation,
        "kind": kind,
        "fingerprint": fingerprint,
        "legacyFingerprint": legacy_fingerprint,
        "snapshotVersion": 2,
        "columns": copy.deepcopy(CURRENT_COLUMNS),
    }


class CatalogService:
    def __init__(self, descriptors):
        self.descriptors = descriptors
        self.calls = []
        self.on_inspect = None
        self.profile_fingerprint = "9" * 64
        self.guard_active = False
        self.guard_calls = []
        self.guard_failure_at = None

    def profile_context_fingerprint(self, profile_id):
        if profile_id != "shared":
            raise KeyError(profile_id)
        return self.profile_fingerprint

    def inspect_relation(self, profile_id, database, namespace, relation, expected_kind=None, expected_fingerprint=None):
        self.calls.append((profile_id, database, namespace, relation, expected_kind, expected_fingerprint))
        if self.on_inspect is not None:
            self.on_inspect(len(self.calls))
        return copy.deepcopy(self.descriptors[relation])

    def verify_relation_source(self, profile_id, source):
        descriptor_value = self.descriptors[source["relation"]]
        matches = descriptor_value["kind"] == source["kind"] and descriptor_value["fingerprint"] == source["fingerprint"]
        return {
            "status": "verified" if matches else "changed",
            "matches": matches,
            "expectedFingerprint": source["fingerprint"],
            "currentFingerprint": descriptor_value["fingerprint"],
        }

    def verify_relation_sources(self, profile_id, sources):
        return {"results": [self.verify_relation_source(profile_id, source) for source in sources]}

    @contextmanager
    def verified_relation_catalog_snapshots(self, targets):
        fields = ("profileId", "database", "namespace", "relation")
        if not isinstance(targets, list) or any(not isinstance(target, dict) or set(target) != set(fields) for target in targets):
            raise AssertionError("fake received invalid verified relation targets")
        ordered = [dict(zip(fields, identity)) for identity in sorted({tuple(target[field] for field in fields) for target in targets})]
        self.guard_calls.append(copy.deepcopy(ordered))
        self.guard_active = True
        try:
            snapshots = []
            for index, target in enumerate(ordered, 1):
                descriptor_value = self.inspect_relation(
                    target["profileId"], target["database"], target["namespace"], target["relation"],
                )
                if self.guard_failure_at == index:
                    raise RuntimeError("guarded catalog inspection failed")
                snapshots.append({
                    **target,
                    "profileFingerprint": self.profile_context_fingerprint(target["profileId"]),
                    "descriptor": descriptor_value,
                })
            yield snapshots
        finally:
            self.guard_active = False


class LegacySourceUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = DashboardStore(Path(self.temporary_directory.name) / "dashboards")
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": copy.deepcopy(LEGACY_SOURCE),
            "query": copy.deepcopy(QUERY),
            "table": copy.deepcopy(TABLE),
            "visualization": copy.deepcopy(VISUALIZATION),
            "detail": copy.deepcopy(DETAIL),
        }
        record["dashboard"]["viewport"] = {"desktop": {"y": 81}, "mobile": {"y": 13}}
        self.record = self.store.save(record["id"], record)
        self.now = [1_000]
        self.service = CatalogService({"orders": descriptor()})
        self.upgrade = LegacySourceUpgrade(
            self.service, self.store, secret=b"x" * 32, ttl_seconds=30, clock=lambda: self.now[0],
        )

    def tearDown(self):
        self.store.close()
        self.temporary_directory.cleanup()

    def request(self, widget_ids=None):
        return {
            "dashboardId": self.record["id"],
            "expectedRevision": self.record["revision"],
            "widgetIds": widget_ids or ["widget_revenue"],
        }

    def test_preview_is_read_only_and_apply_upgrades_exact_source_once(self):
        before = self.store.get(self.record["id"])

        preview = self.upgrade.preview(self.request())

        self.assertEqual(preview["compatibleWidgetIds"], ["widget_revenue"])
        self.assertEqual(preview["incompatibleWidgetIds"], [])
        self.assertEqual(preview["deferredWidgetIds"], [])
        self.assertEqual(preview["maximumUniqueProfileDatabases"], 4)
        self.assertEqual(preview["maximumDigestLength"], MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH)
        self.assertEqual(preview["results"][0]["profileFingerprint"], "9" * 64)
        self.assertEqual(preview["results"][0]["savedLegacyFingerprint"], preview["results"][0]["currentLegacyFingerprint"])
        self.assertEqual(self.store.get(self.record["id"]), before)
        self.assertEqual(self.service.calls[-1], ("shared", "demo", "public", "orders", None, None))

        applied = self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})
        saved = self.store.get(self.record["id"])

        self.assertEqual(applied["revision"], before["revision"] + 1)
        self.assertEqual(applied["upgradedWidgetIds"], ["widget_revenue"])
        self.assertEqual(applied["postWriteVerification"], {
            "status": "current", "changedWidgetIds": [], "unavailableWidgetIds": [],
        })
        widget = saved["dashboard"]["widgets"][0]
        self.assertEqual(widget["configuration"]["source"]["snapshotVersion"], 2)
        self.assertEqual(widget["configuration"]["source"]["fingerprint"], "b" * 64)
        expected = copy.deepcopy(before)
        expected["revision"] = saved["revision"]
        expected["updatedAt"] = saved["updatedAt"]
        expected["dashboard"]["widgets"][0]["configuration"]["source"] = {
            **{key: self.service.descriptors["orders"][key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": copy.deepcopy(CURRENT_COLUMNS),
        }
        self.assertEqual(saved, expected)
        self.assertEqual(self.service.guard_calls, [[{
            "profileId": "shared", "database": "demo", "namespace": "public", "relation": "orders",
        }]])

    def test_preview_and_apply_chunk_more_than_four_profile_database_pairs_deterministically(self):
        record = self.store.get(self.record["id"])
        widget_ids = [record["dashboard"]["widgets"][0]["id"]]
        for index in range(1, 5):
            relation = f"orders_{index}"
            database = f"demo_{index}"
            widget = copy.deepcopy(record["dashboard"]["widgets"][0])
            widget["id"] = f"widget_batch_{index}"
            widget["title"] = f"Batch {index}"
            widget["configuration"]["source"] = {
                **copy.deepcopy(LEGACY_SOURCE), "database": database, "relation": relation,
            }
            record["dashboard"]["widgets"].append(widget)
            widget_ids.append(widget["id"])
            self.service.descriptors[relation] = descriptor(relation, database=database)
        self.record = self.store.save(record["id"], record)

        preview = self.upgrade.preview(self.request(widget_ids))

        self.assertEqual(preview["widgetIds"], widget_ids[:4])
        self.assertEqual(preview["compatibleWidgetIds"], widget_ids[:4])
        self.assertEqual(preview["deferredWidgetIds"], widget_ids[4:])
        self.assertEqual(preview["maximumUniqueProfileDatabases"], 4)
        applied = self.upgrade.apply({
            "dashboardId": self.record["id"], "expectedRevision": self.record["revision"],
            "widgetIds": preview["widgetIds"], "digest": preview["digest"], "confirmed": True,
        })
        saved = self.store.get(self.record["id"])
        sources = {
            widget["id"]: widget["configuration"]["source"]
            for widget in saved["dashboard"]["widgets"] if widget["id"] in widget_ids
        }
        self.assertEqual(applied["upgradedWidgetIds"], widget_ids[:4])
        self.assertTrue(all(sources[widget_id].get("snapshotVersion") == 2 for widget_id in widget_ids[:4]))
        self.assertNotIn("snapshotVersion", sources[widget_ids[4]])

        next_preview = self.upgrade.preview({
            "dashboardId": saved["id"], "expectedRevision": saved["revision"], "widgetIds": widget_ids[4:],
        })
        self.assertEqual(next_preview["widgetIds"], widget_ids[4:])
        self.assertEqual(next_preview["deferredWidgetIds"], [])

    def test_maximum_widget_id_digest_is_accepted_and_one_byte_over_the_bound_is_rejected(self):
        maximum_ids = ["widget_revenue"] + [
            f"widget_{index:03}_" + "x" * 117
            for index in range(99)
        ]

        preview = self.upgrade.preview(self.request(maximum_ids))

        self.assertEqual(len(preview["widgetIds"]), 100)
        self.assertGreater(len(preview["digest"]), 8192)
        self.assertLessEqual(len(preview["digest"]), MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH)
        with self.assertRaises(DashboardStoreError) as over_bound:
            self.upgrade.apply({
                **self.request(maximum_ids),
                "digest": "x" * (MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH + 1),
                "confirmed": True,
            })
        self.assertEqual(over_bound.exception.payload["error"]["code"], "legacy_source_digest_invalid")

        applied = self.upgrade.apply({
            **self.request(maximum_ids), "digest": preview["digest"], "confirmed": True,
        })
        self.assertEqual(applied["upgradedWidgetIds"], ["widget_revenue"])

    def test_post_write_verification_reports_a_subsequent_change_without_rewrite_or_replay(self):
        preview = self.upgrade.preview(self.request())
        original_upgrade = self.store.upgrade_legacy_sources
        writes = []

        def upgrade_then_change(*args):
            record = original_upgrade(*args)
            writes.append(record["revision"])
            self.service.descriptors["orders"]["fingerprint"] = "f" * 64
            return record

        self.store.upgrade_legacy_sources = upgrade_then_change
        applied = self.upgrade.apply({
            **self.request(), "digest": preview["digest"], "confirmed": True,
        })

        self.assertEqual(writes, [self.record["revision"] + 1])
        self.assertEqual(applied["postWriteVerification"], {
            "status": "changed", "changedWidgetIds": ["widget_revenue"], "unavailableWidgetIds": [],
        })
        saved = self.store.get(self.record["id"])
        self.assertEqual(saved["revision"], self.record["revision"] + 1)
        self.assertEqual(
            saved["dashboard"]["widgets"][0]["configuration"]["source"]["fingerprint"],
            "b" * 64,
        )

    def test_post_write_verification_failure_reports_unavailable_after_the_single_write(self):
        preview = self.upgrade.preview(self.request())

        def unavailable(*_args):
            raise RuntimeError("verification unavailable")

        self.service.verify_relation_sources = unavailable

        applied = self.upgrade.apply({
            **self.request(), "digest": preview["digest"], "confirmed": True,
        })

        self.assertEqual(applied["revision"], self.record["revision"] + 1)
        self.assertEqual(applied["postWriteVerification"], {
            "status": "unavailable", "changedWidgetIds": [], "unavailableWidgetIds": ["widget_revenue"],
        })
        self.assertEqual(self.store.get(self.record["id"])["revision"], self.record["revision"] + 1)

    def test_partitioned_table_preserves_historical_table_identity_but_saves_current_kind(self):
        self.service.descriptors["orders"] = descriptor(kind="partitioned_table")

        preview = self.upgrade.preview(self.request())
        self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})

        source = self.store.get(self.record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]
        self.assertEqual(source["kind"], "partitioned_table")

    def test_partial_apply_preserves_incompatible_widgets_and_commits_once(self):
        record = self.store.get(self.record["id"])
        second = copy.deepcopy(record["dashboard"]["widgets"][0])
        second["id"] = "widget_incompatible"
        second["title"] = "Incompatible"
        second["configuration"]["source"] = {
            **copy.deepcopy(LEGACY_SOURCE), "relation": "changed_orders", "fingerprint": "c" * 64,
        }
        record["dashboard"]["widgets"].append(second)
        self.record = self.store.save(record["id"], record)
        self.service.descriptors["changed_orders"] = descriptor(
            "changed_orders", legacy_fingerprint="d" * 64, fingerprint="e" * 64,
        )
        requested = ["widget_revenue", "widget_incompatible"]

        preview = self.upgrade.preview(self.request(requested))
        applied = self.upgrade.apply({**self.request(requested), "digest": preview["digest"], "confirmed": True})
        saved = self.store.get(self.record["id"])

        self.assertEqual(preview["compatibleWidgetIds"], ["widget_revenue"])
        self.assertEqual(preview["incompatibleWidgetIds"], ["widget_incompatible"])
        self.assertEqual(applied["revision"], self.record["revision"] + 1)
        self.assertEqual(applied["incompatibleWidgetIds"], ["widget_incompatible"])
        by_id = {widget["id"]: widget for widget in saved["dashboard"]["widgets"]}
        self.assertEqual(by_id["widget_revenue"]["configuration"]["source"]["snapshotVersion"], 2)
        self.assertNotIn("snapshotVersion", by_id["widget_incompatible"]["configuration"]["source"])

    def test_apply_rejects_tampering_expiry_catalog_changes_and_stale_revisions(self):
        preview = self.upgrade.preview(self.request())
        apply_request = {**self.request(), "digest": preview["digest"], "confirmed": True}

        tampered = {**apply_request, "digest": ("A" if preview["digest"][0] != "A" else "B") + preview["digest"][1:]}
        with self.assertRaises(DashboardStoreError) as invalid:
            self.upgrade.apply(tampered)
        self.assertEqual(invalid.exception.payload["error"]["code"], "legacy_source_digest_invalid")

        self.now[0] = 1_030
        with self.assertRaises(DashboardStoreError) as expired:
            self.upgrade.apply(apply_request)
        self.assertEqual(expired.exception.payload["error"]["code"], "legacy_source_digest_expired")

        self.now[0] = 1_000
        preview = self.upgrade.preview(self.request())
        self.service.descriptors["orders"]["fingerprint"] = "f" * 64
        with self.assertRaises(DashboardStoreError) as changed:
            self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})
        self.assertEqual(changed.exception.payload["error"]["code"], "legacy_source_upgrade_changed")

        self.service.descriptors["orders"]["fingerprint"] = "b" * 64
        preview = self.upgrade.preview(self.request())
        changed_record = self.store.get(self.record["id"])
        changed_record["dashboard"]["title"] = "Changed"
        self.store.save(changed_record["id"], changed_record)
        with self.assertRaises(DashboardStoreError) as stale:
            self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})
        self.assertEqual(stale.exception.payload["error"]["code"], "dashboard_changed")

    def test_review_binds_profile_context_and_rejects_clock_rollback(self):
        preview = self.upgrade.preview(self.request())
        apply_request = {**self.request(), "digest": preview["digest"], "confirmed": True}

        self.service.profile_fingerprint = "8" * 64
        with self.assertRaises(DashboardStoreError) as profile_changed:
            self.upgrade.apply(apply_request)
        self.assertEqual(profile_changed.exception.payload["error"]["code"], "legacy_source_upgrade_changed")

        self.service.profile_fingerprint = "9" * 64
        preview = self.upgrade.preview(self.request())
        self.now[0] = 999
        with self.assertRaises(DashboardStoreError) as clock_changed:
            self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})
        self.assertEqual(clock_changed.exception.payload["error"]["code"], "legacy_source_digest_expired")

    def test_preview_rejects_changed_target_and_profile_changed_during_inspection(self):
        self.service.descriptors["orders"]["namespace"] = "other"
        changed_target = self.upgrade.preview(self.request())
        self.assertEqual(changed_target["results"][0]["error"]["code"], "legacy_source_changed")

        self.service.descriptors["orders"] = descriptor()
        self.service.on_inspect = lambda _count: setattr(self.service, "profile_fingerprint", "8" * 64)
        changed_profile = self.upgrade.preview(self.request())
        self.assertEqual(changed_profile["results"][0]["error"]["code"], "profile_changed")

    def test_store_rejects_unreviewed_replacement_fields(self):
        replacement_source = {
            **{key: LEGACY_SOURCE[key] for key in ("profileId", "database", "namespace", "relation", "kind")},
            "fingerprint": "b" * 64,
            "snapshotVersion": 2,
            "columns": copy.deepcopy(CURRENT_COLUMNS),
        }
        with self.assertRaises(DashboardStoreError) as caught:
            self.store.upgrade_legacy_sources(self.record["id"], self.record["revision"], {
                "widget_revenue": {
                    "expectedSource": copy.deepcopy(LEGACY_SOURCE),
                    "source": replacement_source,
                    "unreviewed": True,
                },
            })
        self.assertEqual(caught.exception.payload["error"]["code"], "validation_error")

        changed_target = copy.deepcopy(replacement_source)
        changed_target["relation"] = "other_orders"
        with self.assertRaises(DashboardStoreError) as changed:
            self.store.upgrade_legacy_sources(self.record["id"], self.record["revision"], {
                "widget_revenue": {"expectedSource": copy.deepcopy(LEGACY_SOURCE), "source": changed_target},
            })
        self.assertEqual(changed.exception.payload["error"]["code"], "validation_error")

    def test_apply_requires_explicit_confirmation(self):
        preview = self.upgrade.preview(self.request())
        with self.assertRaises(DashboardStoreError) as caught:
            self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": False})
        self.assertEqual(caught.exception.payload["error"]["code"], "confirmation_required")

    def test_apply_rejects_dashboard_change_during_catalog_reinspection(self):
        preview = self.upgrade.preview(self.request())

        def mutate_dashboard(call_count):
            if call_count != 2:
                return
            changed = self.store.get(self.record["id"])
            changed["dashboard"]["title"] = "Changed during inspection"
            self.store.save(changed["id"], changed)

        self.service.on_inspect = mutate_dashboard
        with self.assertRaises(DashboardStoreError) as caught:
            self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})

        self.assertEqual(caught.exception.payload["error"]["code"], "dashboard_changed")
        source = self.store.get(self.record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]
        self.assertNotIn("snapshotVersion", source)

    def test_apply_performs_final_validation_and_dashboard_write_inside_catalog_guard(self):
        preview = self.upgrade.preview(self.request())
        original_upgrade = self.store.upgrade_legacy_sources
        observed = []

        def guarded_upgrade(*args):
            observed.append(self.service.guard_active)
            return original_upgrade(*args)

        self.store.upgrade_legacy_sources = guarded_upgrade
        self.upgrade.apply({**self.request(), "digest": preview["digest"], "confirmed": True})

        self.assertEqual(observed, [True])
        self.assertFalse(self.service.guard_active)

    def test_guard_setup_error_after_one_descriptor_leaves_all_widgets_unmodified(self):
        record = self.store.get(self.record["id"])
        second = copy.deepcopy(record["dashboard"]["widgets"][0])
        second["id"] = "widget_orders_archive"
        second["title"] = "Orders archive"
        second["configuration"]["source"] = {
            **copy.deepcopy(LEGACY_SOURCE), "relation": "orders_archive",
        }
        record["dashboard"]["widgets"].append(second)
        self.record = self.store.save(record["id"], record)
        self.service.descriptors["orders_archive"] = descriptor("orders_archive")
        widget_ids = ["widget_revenue", "widget_orders_archive"]
        preview = self.upgrade.preview(self.request(widget_ids))
        before = self.store.get(self.record["id"])
        self.service.guard_failure_at = 2

        with self.assertRaisesRegex(RuntimeError, "guarded catalog inspection failed"):
            self.upgrade.apply({
                **self.request(widget_ids), "digest": preview["digest"], "confirmed": True,
            })

        self.assertEqual(self.store.get(self.record["id"]), before)
        self.assertFalse(self.service.guard_active)


if __name__ == "__main__":
    unittest.main()
