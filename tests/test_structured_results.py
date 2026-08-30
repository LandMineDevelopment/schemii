import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_common import PostgresServiceError
from schemii.postgres_service import PostgresService
from schemii.result_limits import json_utf8_size
from schemii.structured_results import StructuredResultRegistry, csv_export
from tests.test_postgres_service import Connection


def _catalog_columns():
    return [
        {"column_name": "id", "data_type": "integer", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int4"},
        {"column_name": "status", "data_type": "text", "nullable": True, "ordinal": 2, "type_category": "S", "type_name": "text"},
    ]


class TrackingConnectionFactory:
    def __init__(self, responses):
        self.responses = responses
        self.connections = []

    def __call__(self, **_kwargs):
        connection = Connection(self.responses)
        self.connections.append(connection)
        return connection

    def statements_matching(self, marker):
        return [
            (sql, parameters)
            for connection in self.connections
            for sql, parameters in connection.executed
            if marker in sql
        ]


class StructuredResultServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.profile_id = "analytics"
        self.owner = {
            "applicationId": "schemer", "serverId": "server-a", "sessionBinding": "session-a",
            "dashboardId": "dashboard-a", "dashboardRevision": 7, "widgetId": "widget-a",
            "authorityDigest": "a" * 64,
        }
        self.query = {
            "version": 2,
            "dimensions": [{"id": "status_dimension", "label": "Status", "column": "status"}],
            "measures": [{
                "id": "row_count", "label": "Rows", "column": None, "aggregation": "count_rows",
                "distinct": False, "nullBehavior": "preserve",
                "numberFormat": {"style": "integer"},
            }],
            "filters": [], "sort": [], "limit": 500,
        }

    def tearDown(self):
        service = getattr(self, "service", None)
        if service is not None:
            service.close()
        self.tempdir.cleanup()

    def _service(self, responses):
        responses = {
            "SELECT current_database() AS database": [{"database": "analytics"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": _catalog_columns(),
            **responses,
        }
        self.factory = TrackingConnectionFactory(responses)
        self.service = PostgresService(self.tempdir.name, connect_factory=self.factory, application_name="schemer")
        self.service.save_profile(self.profile_id, {
            "name": "Analytics", "host": "localhost", "port": 5432,
            "dbname": "analytics", "user": "reporter", "password": "secret",
            "sslmode": "prefer", "timeout": 10,
        })
        descriptor = self.service.inspect_relation(self.profile_id, "analytics", "public", "events", "table")
        return {
            key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint", "snapshotVersion")
        } | {
            "columns": [
                {key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")}
                for column in descriptor["columns"]
            ]
        }

    def test_aggregate_pages_and_exports_retained_rows_without_rerunning_query(self):
        aggregate_rows = [(f"status-{index:03d}-" + "x" * 3000, index) for index in range(400)]
        source = self._service({
            'AS "__schemer_d0"': {"rows": aggregate_rows, "columns": ["__schemer_d0", "__schemer_m0"]},
        })

        first = self.service.execute_widget_query(
            self.profile_id, source, self.query, result_owner=self.owner,
        )
        resource = first["resultResource"]
        self.assertLess(len(first["rows"]), 400)
        self.assertEqual(400, first["rowCount"])
        self.assertFalse(first["truncated"])
        self.assertTrue(resource["page"]["hasNext"])
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_d0"')))

        second = self.service.structured_result_page(
            self.profile_id, resource["id"], resource["binding"], resource["page"]["nextCursor"],
            "session-a", "server-a",
        )
        self.assertGreater(second["resultResource"]["page"]["offset"], 0)
        self.assertEqual(400, second["rowCount"])
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_d0"')))

        target = self.service.admission_target(self.profile_id)
        observed = []
        registry_page = self.service._structured_results.page

        def admitted_page(entry, cursor):
            observed.append(self.service.execution_metrics()["targets"][target]["active"])
            return registry_page(entry, cursor)

        with patch.object(self.service._structured_results, "page", side_effect=admitted_page):
            self.service.structured_result_page(
                self.profile_id, resource["id"], resource["binding"],
                second["resultResource"]["page"]["previousCursor"], "session-a", "server-a",
            )
        registry_export = self.service._structured_results.export

        def admitted_export(entry, format_name):
            observed.append(self.service.execution_metrics()["targets"][target]["active"])
            return registry_export(entry, format_name)

        with patch.object(self.service._structured_results, "export", side_effect=admitted_export):
            content_type, _filename, content, _headers = self.service.export_structured_result(
                self.profile_id, resource["id"], resource["binding"], "json", "session-a", "server-a",
            )
        self.assertEqual(observed, [1, 1])
        exported = json.loads(content)
        self.assertEqual("application/json; charset=utf-8", content_type)
        self.assertEqual(400, len(exported["rows"]))
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_d0"')))

    def test_complete_one_page_aggregate_does_not_consume_retained_capacity(self):
        source = self._service({
            'AS "__schemer_d0"': {"rows": [("active", 3)], "columns": ["__schemer_d0", "__schemer_m0"]},
        })

        for _ in range(20):
            result = self.service.execute_widget_query(
                self.profile_id, source, self.query, result_owner=self.owner,
            )
            self.assertEqual(result["rows"], [["active", 3]])
            self.assertFalse(result["truncated"])
            self.assertNotIn("resultResource", result)
        self.assertEqual(self.service.structured_result_metrics()["active"], 0)

    def test_detail_pages_share_one_snapshot_and_release_it_after_exhaustion(self):
        detail_rows = [(index,) for index in range(1, 6)]
        source = self._service({
            'AS "__schemer_count"': {"rows": [(5,)], "columns": ["__schemer_count"]},
            'AS "__schemer_c0"': {"rows": detail_rows, "columns": ["__schemer_c0"]},
        })
        selection = {"dimensions": [{"targetId": "status_dimension", "value": "active"}], "measureId": "row_count"}
        detail = {
            "version": 1,
            "columns": [{
                "id": "event_id", "label": "Event ID", "column": "id", "searchable": False,
                "numberFormat": {"style": "integer"},
            }],
            "rowIdentifier": "id",
        }

        first = self.service.execute_relation_detail(
            self.profile_id, source, self.query, selection, detail, 0, 2, None, [], result_owner=self.owner,
        )
        resource = first["resultResource"]
        second = self.service.structured_result_page(
            self.profile_id, resource["id"], resource["binding"], resource["page"]["nextCursor"],
            "session-a", "server-a",
        )
        content_type, _filename, content, _headers = self.service.export_structured_result(
            self.profile_id, resource["id"], resource["binding"], "csv", "session-a", "server-a",
        )
        third = self.service.structured_result_page(
            self.profile_id, resource["id"], resource["binding"], second["resultResource"]["page"]["nextCursor"],
            "session-a", "server-a",
        )
        previous = self.service.structured_result_page(
            self.profile_id, resource["id"], resource["binding"], second["resultResource"]["page"]["previousCursor"],
            "session-a", "server-a",
        )

        self.assertEqual([[1], [2], [3], [4], [5]], first["rows"] + second["rows"] + third["rows"])
        self.assertEqual("text/csv; charset=utf-8", content_type)
        self.assertEqual(6, len(content.decode("utf-8").splitlines()))
        self.assertEqual(first["rows"], previous["rows"])
        self.assertEqual("released", third["resultResource"]["snapshotState"])
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_count"')))
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_c0"')))
        self.assertEqual(0, self.service.structured_result_metrics()["activeSnapshots"])

    def test_owner_and_source_changes_fail_closed_without_replay(self):
        source = self._service({'AS "__schemer_d0"': {"rows": [("active", 1), ("pending", 2)], "columns": ["__schemer_d0", "__schemer_m0"]}})
        result = self.service.execute_widget_query(
            self.profile_id, source, {**self.query, "limit": 1}, result_owner=self.owner,
        )
        resource = result["resultResource"]

        with self.assertRaises(PostgresServiceError) as wrong_session:
            self.service.structured_result_page(
                self.profile_id, resource["id"], resource["binding"], None, "session-b", "server-a",
            )
        self.assertEqual((404, "result_not_found"), (wrong_session.exception.status, wrong_session.exception.code))

        changed_columns = _catalog_columns()
        changed_columns[1] = {**changed_columns[1], "nullable": False}
        self.factory.responses["a.attname AS column_name"] = changed_columns
        with self.assertRaises(PostgresServiceError) as changed_source:
            self.service.export_structured_result(
                self.profile_id, resource["id"], resource["binding"], "csv", "session-a", "server-a",
            )
        self.assertEqual((409, "result_source_changed"), (changed_source.exception.status, changed_source.exception.code))
        self.assertEqual(1, len(self.factory.statements_matching('AS "__schemer_d0"')))


class StructuredResultRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.registry = StructuredResultRegistry(clock=lambda: self.now[0], ttl_seconds=5)
        self.owner = {"applicationId": "schemer", "serverId": "server-a", "sessionBinding": "session-a"}
        self.template = {
            "columns": [{"id": "value", "label": "Value"}], "rows": [], "rowCount": 2,
            "limit": 2, "truncated": False, "semanticTruncated": False,
            "limitEvents": [], "sql": "SELECT value", "parameters": [],
        }

    def tearDown(self):
        self.registry.close()

    def _aggregate(self):
        return self.registry.add(
            owner=self.owner, kind="aggregate", columns=self.template["columns"], page_size=1,
            template=dict(self.template), rows=[["one"], ["two"]], retained_bytes=20,
        )

    def test_expiry_cancel_restart_and_capacity_are_terminal(self):
        entry = self._aggregate()
        token = entry["bindingToken"]
        result_id = entry["resultId"]
        self.now[0] += 6
        with self.assertRaises(PostgresServiceError) as expired:
            self.registry.require(result_id, self.owner, token)
        self.assertEqual((410, "result_expired"), (expired.exception.status, expired.exception.code))

        active = self._aggregate()
        first = self.registry.first_page(active)
        closed = self.registry.cancel(active)
        self.assertEqual("cancelled", closed["state"])
        with self.assertRaises(PostgresServiceError) as cancelled:
            self.registry.require(active["resultId"], self.owner, active["bindingToken"])
        self.assertEqual((410, "result_cancelled"), (cancelled.exception.status, cancelled.exception.code))
        with self.assertRaises(PostgresServiceError) as stale_page:
            self.registry.page(active, first["resultResource"]["page"]["nextCursor"])
        self.assertEqual((410, "result_cancelled"), (stale_page.exception.status, stale_page.exception.code))

        restarted = StructuredResultRegistry(clock=lambda: self.now[0])
        self.addCleanup(restarted.close)
        with self.assertRaises(PostgresServiceError) as restart_error:
            restarted.require(result_id, self.owner, token)
        self.assertEqual((410, "result_restarted"), (restart_error.exception.status, restart_error.exception.code))

        self.registry.maximum_active = 0
        with self.assertRaises(PostgresServiceError) as capacity:
            self._aggregate()
        self.assertEqual((429, "structured_result_capacity_exhausted"), (capacity.exception.status, capacity.exception.code))

    def test_csv_export_is_utf8_rfc4180_compatible(self):
        content = csv_export(
            [{"label": "Name"}, {"label": "Details"}, {"label": "Enabled"}],
            [["Jos\u00e9, Jr.", {"line": "one\ntwo"}, True], ["=1+1", 'a "quote"', False]],
        ).decode("utf-8")
        self.assertEqual(
            'Name,Details,Enabled\r\n"Jos\u00e9, Jr.","{""line"":""one\\ntwo""}",true\r\n\'=1+1,"a ""quote""",false\r\n',
            content,
        )

    def test_detail_retention_capacity_is_terminal_and_releases_snapshot(self):
        class Cursor:
            def __init__(self):
                self.rows = [("one",), ("two",), ("three",)]
                self.offset = 0
                self.closed = False

            def fetchmany(self, size):
                rows = self.rows[self.offset:self.offset + size]
                self.offset += len(rows)
                return rows

            def close(self):
                self.closed = True

        cursor = Cursor()
        cleaned = []
        template = {
            "columns": [{"id": "value", "label": "Value"}], "rows": [], "matchingRowCount": 3,
            "initialOffset": 0, "nextOffset": 0, "limit": 1, "hasMore": True, "truncated": False,
            "limitEvents": [], "sql": "SELECT value", "parameters": [],
        }
        entry = self.registry.add(
            owner=self.owner, kind="detail", columns=template["columns"], page_size=1, template=template,
            cursor=cursor, cleanup=lambda: cleaned.append(True),
            normalize_row=lambda raw, _index: ([raw[0]], []), maximum_rows=2,
        )

        first = self.registry.first_page(entry)
        second = self.registry.page(entry, first["resultResource"]["page"]["nextCursor"])

        self.assertEqual([["one"], ["two"]], first["rows"] + second["rows"])
        self.assertFalse(second["hasMore"])
        self.assertEqual("released", second["resultResource"]["snapshotState"])
        self.assertTrue(second["resultResource"]["limits"]["terminalTruncation"])
        self.assertEqual("row_capacity", second["resultResource"]["limits"]["terminalReason"])
        self.assertTrue(cursor.closed)
        self.assertEqual([True], cleaned)

    def test_snapshot_capacity_rejection_closes_the_rejected_snapshot(self):
        class Cursor:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        self.registry.maximum_snapshots = 1
        template = {
            "columns": [{"id": "value", "label": "Value"}], "rows": [], "matchingRowCount": 0,
            "initialOffset": 0, "nextOffset": 0, "limit": 1, "hasMore": False, "truncated": False,
            "limitEvents": [], "sql": "SELECT value", "parameters": [],
        }
        accepted = Cursor()
        rejected = Cursor()
        self.registry.add(
            owner=self.owner, kind="detail", columns=template["columns"], page_size=1,
            template=dict(template), cursor=accepted, cleanup=lambda: None,
            normalize_row=lambda raw, _index: (list(raw), []), maximum_rows=2,
        )
        with self.assertRaises(PostgresServiceError) as capacity:
            self.registry.add(
                owner=self.owner, kind="detail", columns=template["columns"], page_size=1,
                template=dict(template), cursor=rejected, cleanup=lambda: None,
                normalize_row=lambda raw, _index: (list(raw), []), maximum_rows=2,
            )
        self.assertEqual((429, "structured_result_snapshot_capacity_exhausted"), (capacity.exception.status, capacity.exception.code))
        self.assertFalse(accepted.closed)
        self.assertTrue(rejected.closed)

    def test_one_hundred_aggregate_spools_use_inactive_lru_without_consuming_detail_headroom(self):
        self.registry.ttl_seconds = 1000
        aggregates = []
        for index in range(100):
            self.now[0] += 1
            template = {**self.template, "semanticTruncated": True}
            aggregates.append(self.registry.add(
                owner=self.owner, kind="aggregate", columns=template["columns"], page_size=1,
                template=template, rows=[[f"row-{index}"]], retained_bytes=20,
            ))

        class Cursor:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        cursor = Cursor()
        detail_template = {
            "columns": [{"id": "value", "label": "Value"}], "rows": [], "matchingRowCount": 0,
            "initialOffset": 0, "nextOffset": 0, "limit": 1, "hasMore": False, "truncated": False,
            "limitEvents": [], "sql": "SELECT value", "parameters": [],
        }
        detail = self.registry.add(
            owner=self.owner, kind="detail", columns=detail_template["columns"], page_size=1,
            template=detail_template, cursor=cursor, cleanup=lambda: None,
            normalize_row=lambda raw, _index: (list(raw), []), maximum_rows=2,
        )
        self.assertFalse(cursor.closed)

        self.now[0] += 1
        self.registry.require(aggregates[0]["resultId"], self.owner, aggregates[0]["bindingToken"])
        aggregates[1]["operationLock"].acquire()
        try:
            replacement = self._aggregate()
        finally:
            aggregates[1]["operationLock"].release()

        with self.assertRaises(PostgresServiceError) as expired:
            self.registry.require(aggregates[2]["resultId"], self.owner, aggregates[2]["bindingToken"])
        self.assertEqual((expired.exception.status, expired.exception.code), (410, "result_expired"))
        self.assertIs(
            self.registry.require(aggregates[0]["resultId"], self.owner, aggregates[0]["bindingToken"]),
            aggregates[0],
        )
        self.assertIs(
            self.registry.require(aggregates[1]["resultId"], self.owner, aggregates[1]["bindingToken"]),
            aggregates[1],
        )
        self.assertIs(self.registry.require(detail["resultId"], self.owner, detail["bindingToken"]), detail)
        self.assertIs(self.registry.require(replacement["resultId"], self.owner, replacement["bindingToken"]), replacement)
        metrics = self.registry.metrics()
        self.assertEqual(metrics["aggregateResults"]["active"], 100)
        self.assertEqual(metrics["detailResults"]["active"], 1)
        self.assertEqual(metrics["activeSnapshots"], 1)
        self.assertEqual(metrics["aggregateEvicted"], 1)

    def test_aggregate_byte_lru_is_separate_from_detail_bytes(self):
        self.registry.maximum_aggregate_bytes = 40
        first = self._aggregate()
        self.now[0] += 1
        second = self._aggregate()
        self.now[0] += 1
        third = self._aggregate()

        with self.assertRaises(PostgresServiceError) as expired:
            self.registry.require(first["resultId"], self.owner, first["bindingToken"])
        self.assertEqual(expired.exception.code, "result_expired")
        self.assertIs(self.registry.require(second["resultId"], self.owner, second["bindingToken"]), second)
        self.assertIs(self.registry.require(third["resultId"], self.owner, third["bindingToken"]), third)
        self.assertEqual(self.registry.metrics()["aggregateResults"]["retainedBytes"], 40)

    def test_page_limit_fits_the_exact_envelope_and_metadata_overflow_is_terminal(self):
        entry = self.registry.add(
            owner=self.owner, kind="aggregate", columns=self.template["columns"], page_size=2,
            template=dict(self.template), rows=[["x" * 500], ["y" * 500]], retained_bytes=1000,
        )
        with patch("schemii.structured_results.MAX_STRUCTURED_PAGE_BYTES", 1300):
            page = self.registry.first_page(entry)
        self.assertLessEqual(json_utf8_size(page), 1300)
        self.assertEqual(len(page["rows"]), 1)

        oversized = self._aggregate()
        with patch("schemii.structured_results.MAX_STRUCTURED_PAGE_BYTES", 10):
            with self.assertRaises(PostgresServiceError) as caught:
                self.registry.first_page(oversized)
        self.assertEqual(caught.exception.code, "structured_result_page_metadata_too_large")
        with self.assertRaises(PostgresServiceError) as terminal:
            self.registry.require(oversized["resultId"], self.owner, oversized["bindingToken"])
        self.assertEqual(terminal.exception.code, "result_transport_error")

    def test_post_normalization_and_export_failures_terminalize_but_preflight_errors_do_not(self):
        class Cursor:
            def __init__(self):
                self.closed = False

            def fetchmany(self, _size):
                return [("value",)]

            def close(self):
                self.closed = True

        cursor = Cursor()
        detail_template = {
            "columns": [{"id": "value", "label": "Value"}], "rows": [], "matchingRowCount": 1,
            "initialOffset": 0, "nextOffset": 0, "limit": 1, "hasMore": True, "truncated": False,
            "limitEvents": [], "sql": "SELECT value", "parameters": [],
        }
        failing = self.registry.add(
            owner=self.owner, kind="detail", columns=detail_template["columns"], page_size=1,
            template=detail_template, cursor=cursor, cleanup=lambda: None,
            normalize_row=lambda *_: (_ for _ in ()).throw(RuntimeError("normalize")), maximum_rows=2,
        )
        with self.assertRaisesRegex(RuntimeError, "normalize"):
            self.registry.first_page(failing)
        self.assertTrue(cursor.closed)
        with self.assertRaises(PostgresServiceError) as terminal:
            self.registry.require(failing["resultId"], self.owner, failing["bindingToken"])
        self.assertEqual(terminal.exception.code, "result_transport_error")

        retained = self._aggregate()
        with self.assertRaises(PostgresServiceError) as ownership:
            self.registry.require(retained["resultId"], self.owner, "wrong")
        self.assertEqual(ownership.exception.code, "result_not_found")
        with self.assertRaises(PostgresServiceError) as cursor_error:
            self.registry.page(retained, "stale")
        self.assertEqual(cursor_error.exception.code, "result_cursor_stale")
        self.assertIs(self.registry.require(retained["resultId"], self.owner, retained["bindingToken"]), retained)

        first = self.registry.first_page(retained)
        lock = retained["operationLock"]
        lock.acquire()
        try:
            with self.assertRaises(PostgresServiceError) as busy:
                self.registry.page(retained, first["resultResource"]["page"]["nextCursor"])
        finally:
            lock.release()
        self.assertEqual(busy.exception.code, "result_busy")
        self.assertTrue(self.registry.page(retained, first["resultResource"]["page"]["nextCursor"])["rows"])

        exported = self._aggregate()
        with patch("schemii.structured_results.MAX_EXPORT_BYTES", 1):
            with self.assertRaises(PostgresServiceError) as export_error:
                self.registry.export(exported, "json")
        self.assertEqual(export_error.exception.code, "structured_result_export_too_large")
        with self.assertRaises(PostgresServiceError) as export_terminal:
            self.registry.require(exported["resultId"], self.owner, exported["bindingToken"])
        self.assertEqual(export_terminal.exception.code, "result_export_error")


if __name__ == "__main__":
    unittest.main()
