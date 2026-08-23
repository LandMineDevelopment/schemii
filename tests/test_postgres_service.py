import copy
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import (
    ConflictError,
    PostgresService,
    PostgresServiceError,
    ValidationError,
    canonical_fingerprint,
    quote_identifier,
)
from schemii.postgres_safety import namespace_lock_keys
from tests.capability_test_support import capabilities_for_formatted_type


PROFILE = {
    "name": "Local", "host": "localhost", "port": 5432, "dbname": "demo",
    "user": "developer", "password": "secret", "sslmode": "prefer", "timeout": 5,
}


class Column:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.description = []
        self.offset = 0

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        self.offset = 0
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise self.connection.failure or RuntimeError("database detail that must not escape")
        if "AS namespace_exists" in sql and not any(marker in sql for marker in self.connection.responses):
            self.rows = [{"namespace_exists": True}]
            self.description = [Column("namespace_exists")]
            return
        responses = list(self.connection.responses.items())
        comment = sql.split("/*", 1)[1].split("*/", 1)[0].strip() if "/*" in sql and "*/" in sql else ""
        source_columns = self.connection.responses.get("a.attname AS column_name", [])
        if comment == "structured_query_operators" and source_columns:
            self.rows = []
            for source in source_columns:
                capability = capabilities_for_formatted_type(source["data_type"])
                for item in capability["filterOperators"]:
                    identities = []
                    if "operator" in item:
                        identities.append((item["name"] if item["name"] not in {"in", "not_in", "contains", "starts_with", "ends_with"} else "eq" if item["name"] in {"in", "not_in"} else "like", item["operator"]))
                    elif "operators" in item:
                        continue
                    for logical, identity in identities:
                        if any(row["ordinal"] == source["ordinal"] and row["logical_name"] == logical for row in self.rows):
                            continue
                        self.rows.append({"ordinal": source["ordinal"], "logical_name": logical, "operator_oid": identity["oid"], "operator_namespace": identity["namespace"], "operator_name": identity["name"], "input_type_oid": identity["inputTypeOid"], "result_type_oid": identity["resultTypeOid"], "catalog_version": identity["catalogVersion"] + self.connection.capability_version_suffix})
            self.description = [Column(name) for name in self.rows[0]] if self.rows else []
            return
        if comment == "structured_query_aggregates" and source_columns:
            self.rows = []
            for source in source_columns:
                for item in capabilities_for_formatted_type(source["data_type"])["aggregates"]:
                    identity = item["aggregate"]
                    self.rows.append({"ordinal": source["ordinal"], "logical_name": item["name"], "aggregate_oid": identity["oid"], "aggregate_namespace": identity["namespace"], "aggregate_name": identity["name"], "input_type_oid": identity["inputTypeOid"], "result_type_oid": identity["resultTypeOid"], "output_sortable": item["sortable"], "output_zeroable": item["zeroable"], "catalog_version": identity["catalogVersion"] + self.connection.capability_version_suffix})
            self.description = [Column(name) for name in self.rows[0]] if self.rows else []
            return
        exact = [(marker, response) for marker, response in responses if comment == marker or comment.startswith(marker + "_")]
        for marker, response in (exact or responses):
            if marker in sql:
                if isinstance(response, dict) and "rows" in response:
                    self.rows = response["rows"]
                    self.description = [Column(name) for name in response.get("columns", [])]
                else:
                    self.rows = [dict(row) if isinstance(row, dict) else row for row in response]
                    if comment == "structured_query_column_types":
                        for row in self.rows:
                            capability = capabilities_for_formatted_type(row["data_type"])
                            row.update({
                                "declared_type_oid": capability["declaredTypeOid"], "base_type_oid": capability["baseTypeOid"],
                                "declared_type_namespace": capability["declaredType"]["namespace"], "declared_type_name": capability["declaredType"]["name"],
                                "declared_type_kind": capability["declaredType"]["kind"], "declared_type_category": capability["declaredType"]["category"],
                                "base_type_namespace": capability["type"]["namespace"], "base_type_name": capability["type"]["name"],
                                "base_type_kind": capability["type"]["kind"], "base_type_category": capability["type"]["category"],
                                "type_catalog_version": capability["type"]["catalogVersion"] + self.connection.capability_version_suffix, "collation_oid": None,
                                "array_type_oid": None, "range_type_oid": None,
                            })
                    if "server_version_num" in sql:
                        for row in self.rows:
                            row.setdefault("server_version_num", 160000)
                    if "c.oid AS live_oid" in sql:
                        for row in self.rows:
                            row.setdefault("live_oid", 1)
                    if self.rows and isinstance(self.rows[0], dict):
                        self.description = [Column(name) for name in self.rows[0]]
                break
        else:
            self.rows = []
            self.description = []

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        rows = self.rows[self.offset:self.offset + size]
        self.offset += len(rows)
        return rows

    def close(self):
        pass


class Connection:
    def __init__(self, responses=None, fail_on=None, failure=None, capability_version_suffix=""):
        self.responses = responses or {}
        self.fail_on = fail_on
        self.failure = failure
        self.capability_version_suffix = capability_version_suffix
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def empty_schema(fingerprint="live"):
    return {
        "projectName": "demo.public", "tables": [], "relationships": [], "functions": [], "views": [],
        "postgres": {"namespace": "public", "database": "demo", "serverVersion": "16", "fingerprint": fingerprint},
    }


def migration_assessment(table_name, *, dependencies=None, blockers=None, kind="r", available=True, opaque="one"):
    if not available:
        return {"status": "unavailable", "relations": {table_name: {"status": "unavailable"}}}
    dependency_manifest = {"status": "available", "items": dependencies or [], "truncated": False}
    manifest = {
        "status": "available", "inventory": {"opaque_metadata": opaque},
        "viewDependencies": dependency_manifest, "blockers": blockers or [],
    }
    manifest["fingerprint"] = canonical_fingerprint(manifest)
    return {"status": "available", "relations": {table_name: {
        "status": "available", "catalogKind": kind, "viewDependencies": dependency_manifest,
        "reconstruction": manifest,
    }}}


class PostgresServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        self.profile = self.service.save_profile("local", PROFILE)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_profile_secret_redaction_permissions_and_blank_update(self):
        self.assertNotIn("password", self.profile)
        self.assertNotIn("password", self.service.list_profiles()[0])
        self.assertRegex(self.service.list_profiles()[0]["contextFingerprint"], r"^[0-9a-f]{64}$")
        store = Path(self.temporary_directory.name) / "postgres_profiles.json"
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(Path(self.temporary_directory.name).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
        self.service.save_profile("local", {**PROFILE, "name": "Updated", "password": ""})
        self.assertEqual(json.loads(store.read_text())["profiles"]["local"]["password"], "secret")
        store.chmod(0o644)
        PostgresService(self.temporary_directory.name)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)

    def test_profile_context_fingerprint_tracks_only_connection_identity(self):
        original = self.service.profile_context_fingerprint("local")

        self.service.save_profile("local", {**PROFILE, "name": "Renamed", "password": "changed", "timeout": 30})
        self.assertEqual(self.service.profile_context_fingerprint("local"), original)

        for field, value in (
            ("host", "127.0.0.1"),
            ("port", 5433),
            ("dbname", "other"),
            ("user", "reader"),
            ("sslmode", "require"),
        ):
            with self.subTest(field=field):
                self.service.save_profile("local", {**PROFILE, field: value})
                self.assertNotEqual(self.service.profile_context_fingerprint("local"), original)

    def test_profile_validation_identifier_quoting_and_plan_invalidation(self):
        for change in ({"port": 0}, {"timeout": True}, {"sslmode": "maybe"}, {"host": "bad host"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.service.save_profile("bad", {**PROFILE, **change})
        with self.assertRaises(ValidationError):
            self.service.save_profile("../bad", PROFILE)
        self.assertEqual(quote_identifier('Odd"Name'), '"Odd""Name"')

        self.service.introspect = lambda profile_id, namespace: empty_schema()
        plan = self.service.preview("local", "public", empty_schema(), persist=False)
        self.service.save_profile("local", {**PROFILE, "dbname": "other"})
        self.assertIsNone(plan["id"])

    def test_preview_only_plan_is_not_apply_capable(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema()
        plan = self.service.preview("local", "public", empty_schema(), persist=False)
        self.assertIsNone(plan["id"])
        self.assertTrue(plan["previewOnly"])
        self.assertFalse((Path(self.temporary_directory.name) / "ai_migration_plans").exists())

    def test_migration_plan_ttl_uses_its_own_injected_clock(self):
        service = PostgresService(
            self.temporary_directory.name, plan_ttl_seconds=41,
            temporal_manifest_ttl_seconds=13, clock=lambda: 1000.25,
        )
        service.introspect = lambda profile_id, namespace: empty_schema()

        plan = service.preview("local", "public", empty_schema(), persist=False)

        self.assertEqual(plan["expiresAt"], 1041.25)

    def test_preview_reports_actual_connected_database(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema("live") | {"postgres": {**empty_schema("live")["postgres"], "database": "other"}}
        plan = self.service.preview("local", "public", empty_schema(), persist=False)
        self.assertEqual(plan["database"], "other")

    def test_connection_uses_keyword_arguments_without_exposing_password(self):
        captured = {}
        connection = Connection({"current_database()": [{"database": "demo", "version": "PostgreSQL 16"}]})
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (captured.update(kwargs) or connection),
        )
        result = service.test_profile("local")
        self.assertTrue(result["ok"])
        self.assertEqual(captured["dbname"], "demo")
        self.assertEqual(captured["password"], "secret")
        self.assertEqual(captured["application_name"], "schemii")
        self.assertNotIn("password", result)

        captured.clear()
        service = PostgresService(
            self.temporary_directory.name, application_name="schemer",
            connect_factory=lambda **kwargs: (captured.update(kwargs) or connection),
        )
        service.test_profile("local")
        self.assertEqual(captured["application_name"], "schemer")

    def test_namespace_discovery_uses_a_read_only_transaction(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "namespace_catalog_fingerprint": [{"first_hash": "a" * 32, "second_hash": "b" * 32}],
            "namespace_catalog_page": [{"namespace": "public", "classification": "user"}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        self.assertEqual(service.list_namespaces("local"), ["public"])
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertEqual(connection.rollbacks, 1)

    def test_namespace_pages_classify_system_schemas_and_bind_cursors(self):
        fingerprint = {"first_hash": "a" * 32, "second_hash": "b" * 32}
        first = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "namespace_catalog_fingerprint": [fingerprint],
            "namespace_catalog_page": [
                {"namespace": "information_schema", "classification": "information_schema"},
                {"namespace": "pg_catalog", "classification": "pg_catalog"},
                {"namespace": "pg_temp_3", "classification": "temporary"},
            ],
        })
        second = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "namespace_catalog_fingerprint": [fingerprint],
            "namespace_catalog_page": [{"namespace": "pg_temp_3", "classification": "temporary"}],
        })
        def catalog_connection(value=fingerprint):
            return Connection(responses={
                "SELECT current_database() AS database": [{"database": "demo"}],
                "namespace_catalog_fingerprint": [value], "namespace_catalog_page": [],
            })

        connections = [first, second, catalog_connection(), catalog_connection(), catalog_connection({"first_hash": "c" * 32, "second_hash": "d" * 32})]
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connections.pop(0))

        page = service.list_namespace_page("local", "demo", scope="all", page_size="2")
        self.assertEqual([item["classification"] for item in page["entries"]], ["information_schema", "pg_catalog"])
        self.assertTrue(page["page"]["hasMore"])
        classification_sql = next(sql for sql, _ in first.executed if "namespace_catalog_page" in sql)
        for classification in ("pg_catalog", "information_schema", "temporary", "toast", "other_system", "user"):
            self.assertIn(f"'{classification}'", classification_sql)
        continued = service.list_namespace_page("local", "demo", scope="all", page_size="2", cursor=page["page"]["nextCursor"])
        self.assertEqual(continued["namespaces"], ["pg_temp_3"])
        page_sql, page_params = next(item for item in second.executed if "namespace_catalog_page" in item[0])
        self.assertIn("nspname > %s", page_sql)
        self.assertEqual(page_params, ("pg_catalog", 3))

        with self.assertRaises(PostgresServiceError) as malformed:
            service.list_namespace_page("local", "demo", cursor="not-a-cursor")
        self.assertEqual(malformed.exception.code, "invalid_catalog_cursor")
        with self.assertRaises(PostgresServiceError) as mismatch:
            service.list_namespace_page("local", "demo", scope="user", page_size="2", cursor=page["page"]["nextCursor"])
        self.assertEqual(mismatch.exception.code, "catalog_cursor_mismatch")
        with self.assertRaises(PostgresServiceError) as stale:
            service.list_namespace_page("local", "demo", scope="all", page_size="2", cursor=page["page"]["nextCursor"])
        self.assertEqual(stale.exception.code, "catalog_cursor_stale")

    def test_namespace_exact_existence_does_not_depend_on_a_catalog_page(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "AS namespace_exists": [{"namespace_exists": True}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        self.assertTrue(service.namespace_exists("local", "demo", "after_the_page"))
        self.assertFalse(any("namespace_catalog_page" in sql for sql, _ in connection.executed))

    def test_table_data_preview_is_paginated_ordered_and_json_safe(self):
        connection = Connection(responses={
            "con.contype = 'p'": [{"column_name": "id"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "uuid", "nullable": False, "ordinal": 1},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2},
                {"column_name": "created_at", "data_type": "timestamp", "nullable": False, "ordinal": 3},
            ],
            'SELECT * FROM "public"."payments"': [
                {"id": UUID(int=1), "amount": Decimal("10.25"), "created_at": datetime(2026, 7, 25, 12, 30)},
                {"id": UUID(int=2), "amount": None, "created_at": datetime(2026, 7, 25, 12, 31)},
                {"id": UUID(int=3), "amount": Decimal("3"), "created_at": datetime(2026, 7, 25, 12, 32)},
            ],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.preview_table_data("local", "public", "payments", offset=10, limit=2)
        self.assertEqual(result["primaryKey"], ["id"])
        self.assertTrue(result["stableOrder"])
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["nextOffset"], 12)
        self.assertEqual(result["rows"][0]["amount"], "10.25")
        data_query = next(item for item in connection.executed if 'SELECT * FROM "public"."payments"' in item[0])
        self.assertIn('ORDER BY "id"', data_query[0])
        self.assertEqual(data_query[1], (3, 10))
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertTrue(connection.closed)

    def test_relation_catalog_verifies_database_and_lists_supported_kinds(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "AS namespace_exists": [{"namespace_exists": True}],
            "relation_catalog_fingerprint": [{"first_hash": "a" * 32, "second_hash": "b" * 32}],
            "c.relname AS relation_name": [
                {"relation_name": "orders", "catalog_kind": "r", "relation_kind": "table"},
                {"relation_name": "order_summary", "catalog_kind": "v", "relation_kind": "view"},
                {"relation_name": "daily_sales", "catalog_kind": "m", "relation_kind": "materialized_view"},
                {"relation_name": "remote_orders", "catalog_kind": "f", "relation_kind": "foreign_table"},
            ],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.list_relations("local", "demo", "public")
        self.assertEqual(result["profileId"], "local")
        self.assertEqual(result["database"], "demo")
        self.assertEqual(result["namespace"], "public")
        self.assertEqual([item["kind"] for item in result["relations"]], ["table", "view", "materialized_view", "foreign_table"])
        catalog_query = next(sql for sql, _ in connection.executed if "c.relname AS relation_name" in sql)
        fingerprint_query = next(sql for sql, _ in connection.executed if "relation_catalog_fingerprint" in sql)
        self.assertIn("c.relkind IN ('r', 'p', 'v', 'm', 'f')", catalog_query)
        self.assertIn("c.relkind::text ||", fingerprint_query)
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_relation_catalog_rejects_unverified_database(self):
        connection = Connection(responses={"SELECT current_database() AS database": [{"database": "other"}]})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.list_relations("local", "demo", "public")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any("c.relname AS relation_name" in sql for sql, _ in connection.executed))
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_relation_catalog_keyset_continues_with_filters(self):
        fingerprint = {"first_hash": "a" * 32, "second_hash": "b" * 32}
        common = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "AS namespace_exists": [{"namespace_exists": True}],
            "relation_catalog_fingerprint": [fingerprint],
        }
        first = Connection(responses={**common, "relation_catalog_page": [
            {"relation_name": "remote_001", "catalog_kind": "f", "relation_kind": "foreign_table"},
            {"relation_name": "remote_002", "catalog_kind": "f", "relation_kind": "foreign_table"},
        ]})
        second = Connection(responses={**common, "relation_catalog_page": [
            {"relation_name": "remote_002", "catalog_kind": "f", "relation_kind": "foreign_table"},
        ]})
        connections = [first, second]
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connections.pop(0))

        page = service.list_relations("local", "demo", "public", kind="foreign_table", search="remote", page_size="1")
        continued = service.list_relations(
            "local", "demo", "public", kind="foreign_table", search="remote", page_size="1",
            cursor=page["page"]["nextCursor"],
        )

        self.assertEqual(page["entries"][0]["relation"], "remote_001")
        self.assertEqual(continued["entries"][0]["relation"], "remote_002")
        sql, params = next(item for item in second.executed if "relation_catalog_page" in item[0])
        self.assertIn("(c.relname, c.relkind) > (%s, %s)", sql)
        self.assertEqual(params, ("public", "f", "remote", "remote_001", "f", 2))

    def test_full_introspection_uses_one_read_only_snapshot_and_reports_missing_namespace(self):
        connection = Connection()
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        service._introspect_connection = lambda *args: empty_schema()
        self.assertEqual(service.introspect("local", "public")["postgres"]["fingerprint"], "live")
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

        missing = Connection(responses={
            "current_setting('server_version')": [{
                "database": "demo", "server_version": "16", "server_version_num": "160000", "timezone": "UTC",
            }],
            "AS namespace_exists": [{"namespace_exists": False}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: missing)
        with self.assertRaises(PostgresServiceError) as error:
            service.introspect("local", "missing")
        self.assertEqual((error.exception.status, error.exception.code), (404, "namespace_not_found"))
        self.assertFalse(any("c.oid AS table_oid" in sql for sql, _ in missing.executed))

    def test_full_introspection_projects_collections_above_result_limit_without_partial_fingerprint(self):
        table_rows = [
            {"table_oid": number, "table_name": f"table_{number:04}", "relation_kind": "r", "is_partition": False}
            for number in range(1001)
        ]
        connection = Connection(responses={
            "current_setting('server_version')": [{
                "database": "demo", "server_version": "16", "server_version_num": "160000", "timezone": "UTC",
            }],
            "c.oid AS table_oid": table_rows,
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        result = service.introspect("local", "public")

        self.assertEqual(len(result["tables"]), 1001)
        self.assertEqual(result["tables"][-1]["name"], "table_1000")
        self.assertEqual(result["postgres"]["fingerprint"], canonical_fingerprint(result))
        self.assertFalse(any("catalog_collection_too_large" in str(item) for item in connection.executed))
        self.assertEqual(connection.rollbacks, 1)

    def test_full_introspection_rejects_oversized_definition_instead_of_returning_partial_schema(self):
        connection = Connection(responses={
            "current_setting('server_version')": [{
                "database": "demo", "server_version": "16", "server_version_num": "160000", "timezone": "UTC",
            }],
            "p.proname AS name": [{
                "name": "oversized", "kind": "f", "identity_arguments": "", "arguments": "",
                "return_type": "integer", "language": "sql", "definition": "x" * (64 * 1024 + 1),
            }],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        with self.assertRaises(PostgresServiceError) as error:
            service.introspect("local", "public")

        self.assertEqual(error.exception.code, "catalog_definition_too_large")
        self.assertEqual(error.exception.details["limitation"], "application")
        self.assertEqual(connection.rollbacks, 1)

    def test_namespace_lock_identity_is_domain_separated_database_scoped_and_stable(self):
        first = namespace_lock_keys("demo", "public")
        self.assertEqual(first, namespace_lock_keys("demo", "public"))
        self.assertNotEqual(first, namespace_lock_keys("other", "public"))
        self.assertNotEqual(first, namespace_lock_keys("demo", "other"))
        self.assertTrue(all(-(2 ** 31) <= item < 2 ** 31 for item in first))

    def test_relation_inspection_returns_ordered_columns_and_stable_fingerprint(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, total FROM orders"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
                {"column_name": "total", "data_type": "numeric(12,2)", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
        }
        connection = Connection(responses=responses)
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        first = service.inspect_relation("local", "demo", "reporting", "order_summary")
        second = service.inspect_relation("local", "demo", "reporting", "order_summary")
        self.assertEqual(first["kind"], "view")
        self.assertEqual([{key: column[key] for key in ("name", "type", "nullable", "ordinal", "suggestions")} for column in first["columns"]], [
            {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1, "suggestions": ["dimension", "identifier"]},
            {"name": "total", "type": "numeric(12,2)", "nullable": True, "ordinal": 2, "suggestions": ["dimension", "measure"]},
        ])
        self.assertEqual(len(first["fingerprint"]), 64)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["definition"], {
            "status": "available", "format": "query", "sql": "SELECT id, total FROM orders",
        })

        changed = {**responses, "a.attname AS column_name": [
            responses["a.attname AS column_name"][0],
            {"column_name": "total", "data_type": "numeric(12,2)", "nullable": False, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
        ]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        self.assertNotEqual(first["fingerprint"], changed_service.inspect_relation("local", "demo", "reporting", "order_summary")["fingerprint"])
        changed_definition = {**responses, "c.relkind AS catalog_kind": [{
            "catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, total, tax FROM orders",
        }]}
        definition_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed_definition))
        self.assertNotEqual(first["fingerprint"], definition_service.inspect_relation("local", "demo", "reporting", "order_summary")["fingerprint"])

    def test_relation_inspection_derives_verified_output_column_provenance(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{
                "catalog_kind": "v", "relation_kind": "view",
                "view_definition": "SELECT o.id AS order_id, o.subtotal + o.tax AS gross_total FROM sales.orders o",
            }],
            "a.attname AS column_name": [
                {"column_name": "order_id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
                {"column_name": "gross_total", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            "relation_dependencies": [{"namespace": "sales", "relation_name": "orders", "relation_kind": "table"}],
            "view_provenance_source_columns": [
                {"namespace": "sales", "relation_name": "orders", "relation_kind": "table", "column_name": "id", "ordinal": 1, "data_type": "bigint"},
                {"namespace": "sales", "relation_name": "orders", "relation_kind": "table", "column_name": "subtotal", "ordinal": 2, "data_type": "numeric"},
                {"namespace": "sales", "relation_name": "orders", "relation_kind": "table", "column_name": "tax", "ordinal": 3, "data_type": "numeric"},
            ],
        }
        connection = Connection(responses=responses)
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        result = service.inspect_relation("local", "demo", "reporting", "order_summary")

        provenance = result["columnProvenance"]
        self.assertEqual(provenance["status"], "available")
        self.assertEqual([item["derivation"] for item in provenance["outputs"]], ["direct", "expression"])
        self.assertEqual(provenance["outputs"][0]["inputs"][0]["columnName"], "id")
        self.assertEqual([item["columnName"] for item in provenance["outputs"][1]["inputs"]], ["subtotal", "tax"])
        self.assertEqual(result["joinPredicates"]["status"], "available")
        self.assertEqual(result["joinPredicates"]["joins"], [])
        self.assertEqual(result["sqlStages"]["status"], "available")
        self.assertEqual(
            [stage["kind"] for stage in result["sqlStages"]["stages"]], ["query_block"],
        )
        source_query = next(sql for sql, _ in connection.executed if "view_provenance_source_columns" in sql)
        self.assertIn("WITH ORDINALITY", source_query)
        self.assertNotIn("unnest(%s::text[], %s::text[])", source_query)

    def test_foreign_table_inspection_is_a_read_source_with_advisory_select(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 160000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 42, "catalog_kind": "f", "relation_kind": "foreign_table", "view_definition": None,
                "owner_name": "fdw_owner", "current_role": "reporter", "can_select": True,
                "materialized_populated": False,
            }],
            "a.attname AS column_name": [{
                "column_name": "remote_id", "data_type": "bigint", "nullable": False, "ordinal": 1,
                "type_category": "N", "type_name": "int8",
            }],
            "relation_dependents": [{"namespace": "reports", "relation_name": "remote_orders_view", "relation_kind": "view"}],
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=responses))

        descriptor = service.inspect_relation("local", "demo", "public", "remote_orders", "foreign_table")

        self.assertEqual(descriptor["kind"], "foreign_table")
        self.assertTrue(descriptor["permissions"]["canSelect"])
        self.assertRegex(descriptor["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(descriptor["definition"], {"status": "unavailable", "reason": "not_supported"})
        self.assertEqual(descriptor["sqlStages"]["status"], "unavailable")
        self.assertEqual(descriptor["sqlStages"]["reason"], "not_supported")
        self.assertEqual(descriptor["dependencies"]["status"], "available")
        self.assertEqual(descriptor["dependencies"]["items"], [])
        self.assertFalse(descriptor["dependencies"]["page"]["hasMore"])
        self.assertEqual(descriptor["dependents"]["status"], "available")
        self.assertEqual(descriptor["dependents"]["items"][0]["namespace"], "reports")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        self.assertEqual(service.verify_relation_source("local", source)["status"], "verified")

    def test_relation_inspection_adds_advisory_capabilities_and_bounded_oid_free_lineage(self):
        dependents = [
            {"namespace": "reports", "relation_name": f"summary_{number:03}", "relation_kind": "view"}
            for number in range(501)
        ]
        responses = {
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 160000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 20, "catalog_kind": "v", "relation_kind": "view",
                "view_definition": "SELECT * FROM sales.orders", "owner_name": "reporter",
                "current_role": "reader", "can_alter": False, "can_select": True,
                "can_refresh": False, "materialized_populated": True,
            }],
            "a.attname AS column_name": [],
            "relation_dependencies": [
                {"namespace": "sales", "relation_name": "orders", "relation_kind": "table"},
                {"namespace": "shared", "relation_name": "rates", "relation_kind": "foreign_table"},
            ],
            "relation_dependents": dependents,
        }
        connection = Connection(responses=responses)
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.inspect_relation("local", "demo", "reports", "order_summary")

        self.assertEqual(result["owner"], {"status": "available", "name": "reporter"})
        self.assertEqual(result["permissions"], {
            "status": "available", "role": "reader", "advisory": True,
            "canSelect": True, "isOwner": False, "inheritsOwner": False, "canSetRole": False,
            "canAlter": False, "canRefresh": False,
        })
        self.assertEqual(result["columnProvenance"], {"status": "unavailable", "reason": "no_outputs"})
        self.assertEqual(result["materialized"], {"status": "unavailable", "reason": "not_applicable"})
        self.assertEqual(result["dependencies"]["status"], "available")
        self.assertEqual(
            [(item["namespace"], item["relation"], item["kind"]) for item in result["dependencies"]["items"]],
            [("sales", "orders", "table"), ("shared", "rates", "foreign_table")],
        )
        self.assertFalse(result["dependencies"]["page"]["hasMore"])
        self.assertEqual(len(result["dependents"]["items"]), 100)
        self.assertTrue(result["dependents"]["truncated"])
        self.assertTrue(result["dependents"]["page"]["nextCursor"])
        self.assertNotIn("liveOid", json.dumps(result["dependencies"]))
        lineage_queries = [item for item in connection.executed if "relation_depend" in item[0]]
        self.assertTrue(all(item[1][:2] == (20, 20) for item in lineage_queries))
        self.assertTrue(all("SELECT DISTINCT" in item[0] for item in lineage_queries))

        changed = {**responses, "c.relkind AS catalog_kind": [{
            **responses["c.relkind AS catalog_kind"][0], "owner_name": "other",
            "current_role": "other_reader", "can_alter": True, "can_select": False,
        }]}
        changed_service = PostgresService(
            self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed)
        )
        self.assertEqual(
            result["fingerprint"],
            changed_service.inspect_relation("local", "demo", "reports", "order_summary")["fingerprint"],
        )

    def test_materialized_relation_reports_population_refresh_capability_and_index_eligibility(self):
        for populated, has_index, expected in ((True, True, True), (True, False, False), (False, True, False)):
            with self.subTest(populated=populated, has_index=has_index):
                responses = {
                    "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 170000}],
                    "c.relkind AS catalog_kind": [{
                        "live_oid": 30, "catalog_kind": "m", "relation_kind": "materialized_view",
                        "view_definition": "SELECT 1", "owner_name": None, "current_role": "maintainer",
                        "can_alter": False, "can_select": True, "can_refresh": True,
                        "materialized_populated": populated,
                    }],
                    "a.attname AS column_name": [],
                    "concurrent_refresh_index": [{"has_refresh_index": has_index}],
                    "relation_dependencies": [],
                    "relation_dependents": [],
                }
                connection = Connection(responses=responses)
                service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
                result = service.inspect_relation("local", "demo", "reports", "daily_sales")
                self.assertEqual(result["owner"], {"status": "unavailable", "reason": "not_permitted"})
                self.assertTrue(result["permissions"]["canRefresh"])
                self.assertEqual(result["materialized"], {
                    "status": "available", "populated": populated, "concurrentRefreshEligible": expected,
                })
                relation_query = next(sql for sql, _ in connection.executed if "c.relkind AS catalog_kind" in sql)
                self.assertIn("has_table_privilege(c.oid, 'MAINTAIN')", relation_query)
                index_query = next(sql for sql, _ in connection.executed if "concurrent_refresh_index" in sql)
                for condition in (
                    "i.indisunique", "i.indisvalid", "i.indisready", "i.indimmediate",
                    "i.indpred IS NULL", "i.indexprs IS NULL",
                ):
                    self.assertIn(condition, index_query)

    def test_relation_inspection_uses_oid_bound_columns_and_rolls_back(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 160000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 77, "catalog_kind": "r", "relation_kind": "table", "view_definition": None,
            }],
            "a.attname AS column_name": [],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.inspect_relation("local", "demo", "public", "orders")
        column_query = next(item for item in connection.executed if "a.attname AS column_name" in item[0])
        operator_query = next(item for item in connection.executed if "structured_query_operators" in item[0])
        aggregate_query = next(item for item in connection.executed if "structured_query_aggregates" in item[0])
        self.assertIn("WITH RECURSIVE type_chain", column_query[0])
        self.assertIn("pg_catalog.pg_opclass", operator_query[0])
        self.assertIn("opclass.opcdefault", operator_query[0])
        self.assertIn("binary_cast.castsource = base.oid", operator_query[0])
        self.assertIn("binary_cast.casttarget = opclass.opcintype", operator_query[0])
        self.assertIn("binary_cast.castmethod = 'b'", operator_query[0])
        self.assertIn("binary_cast.castcontext = 'i'", operator_query[0])
        self.assertIn("opclass_type.typcategory = base_type.typcategory", operator_query[0])
        self.assertIn("opclass_type.typispreferred", operator_query[0])
        self.assertIn("selected.cast_identity", operator_query[0])
        self.assertIn("pg_catalog.pg_aggregate", aggregate_query[0])
        self.assertIn("a.attrelid = %s", column_query[0])
        self.assertIn("pg_catalog.pg_collation coll", column_query[0])
        self.assertIn("pg_catalog.pg_constraint con", column_query[0])
        self.assertIn("pg_catalog.pg_range rng", column_query[0])
        self.assertNotIn("pg_catalog.pg_collation collation", column_query[0])
        lineage_fingerprint_queries = [
            sql for sql, _ in connection.executed if "relation_dependencies_fingerprint" in sql or "relation_dependents_fingerprint" in sql
        ]
        self.assertTrue(lineage_fingerprint_queries)
        self.assertTrue(all("catalog_kind::text" in sql for sql in lineage_fingerprint_queries))
        self.assertEqual(column_query[1], (77, 77))
        self.assertEqual(result["dependencies"]["items"], [])
        self.assertEqual(result["dependents"]["items"], [])
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertEqual(connection.rollbacks, 1)

    def test_relation_fingerprint_does_not_include_sql_stage_analysis(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{
                "catalog_kind": "v", "relation_kind": "view",
                "view_definition": "WITH selected AS (SELECT id FROM sales.orders) SELECT id FROM selected",
            }],
            "a.attname AS column_name": [{
                "column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1,
                "type_category": "N", "type_name": "int8",
            }],
            "relation_dependencies": [{"namespace": "sales", "relation_name": "orders", "relation_kind": "table"}],
            "view_provenance_source_columns": [{
                "namespace": "sales", "relation_name": "orders", "relation_kind": "table",
                "column_name": "id", "ordinal": 1, "data_type": "bigint",
            }],
        }
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: Connection(responses=responses),
        )
        original = service.inspect_relation("local", "demo", "reports", "selected_orders")
        self.assertEqual(original["sqlStages"]["status"], "available")
        self.assertEqual(original["sqlStages"]["stages"][0]["name"], "selected")
        replacement = {
            "status": "unavailable", "reason": "analysis_failed", "version": 1,
            "orderSemantics": "syntactic_dependency", "stages": [],
            "relationFingerprint": original["fingerprint"], "fingerprint": "f" * 64,
        }

        with patch("schemii.postgres_catalog.derive_sql_stages", return_value=replacement):
            changed_analysis = service.inspect_relation("local", "demo", "reports", "selected_orders")

        self.assertEqual(original["fingerprint"], changed_analysis["fingerprint"])
        self.assertNotEqual(original["sqlStages"], changed_analysis["sqlStages"])

    def test_relation_lineage_pages_cross_namespace_table_and_foreign_dependents_and_rejects_stale_cursor(self):
        base = {
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 160000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 77, "catalog_kind": "r", "relation_kind": "table", "view_definition": None,
            }],
            "a.attname AS column_name": [],
            "relation_dependencies_fingerprint": [{"first_hash": "1" * 32, "second_hash": "2" * 32}],
            "relation_dependencies_page": [],
            "relation_dependents_fingerprint": [{"first_hash": "3" * 32, "second_hash": "4" * 32}],
            "relation_lineage_identity": [{"live_oid": 77}],
        }
        first_rows = [
            {"namespace": "analytics", "relation_name": "orders_view", "catalog_kind": "v", "relation_kind": "view"},
            {"namespace": "remote", "relation_name": "orders_fdw", "catalog_kind": "f", "relation_kind": "foreign_table"},
        ]
        first = Connection(responses={**base, "relation_dependents_page": first_rows})
        second = Connection(responses={**base, "relation_dependents_page": first_rows[1:]})
        connections = [first, second]
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connections.pop(0))
        descriptor = service.inspect_relation("local", "demo", "public", "orders")

        # Recreate connections because descriptor inspection consumed the first one.
        first = Connection(responses={**base, "relation_dependents_page": first_rows})
        second = Connection(responses={**base, "relation_dependents_page": first_rows[1:]})
        connections[:] = [first, second]
        page = service.list_relation_lineage(
            "local", "demo", "public", "orders", "dependents",
            expected_kind="table", expected_fingerprint=descriptor["fingerprint"], page_size="1",
        )
        continued = service.list_relation_lineage(
            "local", "demo", "public", "orders", "dependents",
            expected_kind="table", expected_fingerprint=descriptor["fingerprint"], page_size="1",
            cursor=page["page"]["nextCursor"],
        )
        self.assertEqual((page["items"][0]["namespace"], page["items"][0]["kind"]), ("analytics", "view"))
        self.assertEqual((continued["items"][0]["namespace"], continued["items"][0]["kind"]), ("remote", "foreign_table"))
        self.assertEqual(page["catalogFingerprint"], continued["catalogFingerprint"])
        self.assertTrue(all(connection.executed[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" for connection in (first, second)))

        stale = Connection(responses={
            **base,
            "relation_dependents_fingerprint": [{"first_hash": "5" * 32, "second_hash": "6" * 32}],
            "relation_dependents_page": first_rows[1:],
        })
        service._connect_factory = lambda **kwargs: stale
        with self.assertRaises(PostgresServiceError) as error:
            service.list_relation_lineage(
                "local", "demo", "public", "orders", "dependents",
                expected_kind="table", expected_fingerprint=descriptor["fingerprint"], page_size="1",
                cursor=page["page"]["nextCursor"],
            )
        self.assertEqual(error.exception.code, "catalog_cursor_stale")

        mismatch = Connection(responses={**base, "relation_dependents_page": []})
        service._connect_factory = lambda **kwargs: mismatch
        with self.assertRaises(PostgresServiceError) as error:
            service.list_relation_lineage(
                "local", "demo", "public", "orders", "dependencies",
                expected_kind="table", expected_fingerprint=descriptor["fingerprint"], page_size="1",
                cursor=page["page"]["nextCursor"],
            )
        self.assertEqual(error.exception.code, "catalog_cursor_mismatch")

    def test_relation_definitions_are_bounded_and_tables_do_not_claim_complete_ddl(self):
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "a.attname AS column_name": [],
        }
        table_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
        }))
        self.assertEqual(table_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "not_supported",
        })
        view_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "x" * (64 * 1024 + 1)}],
        }))
        self.assertEqual(view_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "too_large",
        })
        for catalog_kind, relation_kind in (("v", "view"), ("m", "materialized_view")):
            with self.subTest(kind=relation_kind):
                permitted_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
                    **base, "c.relkind AS catalog_kind": [{
                        "catalog_kind": catalog_kind, "relation_kind": relation_kind, "view_definition": "SELECT 1",
                    }],
                }))
                self.assertEqual(permitted_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
                    "status": "available", "format": "query", "sql": "SELECT 1",
                })
        unavailable_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": None}],
        }))
        self.assertEqual(unavailable_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "not_permitted",
        })
        untrusted = '</code><script>throw new Error("unsafe")</script>'
        untrusted_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": untrusted}],
        }))
        self.assertEqual(untrusted_service.inspect_relation("local", "demo", "public", "orders")["definition"]["sql"], untrusted)

    def test_relation_inspection_rejects_missing_relation(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.inspect_relation("local", "demo", "public", "missing")
        self.assertEqual(error.exception.code, "not_found")
        self.assertFalse(any("a.attname AS column_name" in sql for sql, _ in connection.executed))

    def test_relation_column_role_suggestions_are_advisory_and_not_fingerprinted(self):
        rows = [
            {"column_name": "customer_id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
            {"column_name": "amount", "data_type": "numeric", "nullable": False, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            {"column_name": "ordered_at", "data_type": "timestamp with time zone", "nullable": False, "ordinal": 3, "type_category": "D", "type_name": "timestamptz"},
            {"column_name": "external_key", "data_type": "uuid", "nullable": False, "ordinal": 4, "type_category": "U", "type_name": "uuid"},
            {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 5, "type_category": "S", "type_name": "text"},
            {"column_name": "metadata", "data_type": "jsonb", "nullable": True, "ordinal": 6, "type_category": "U", "type_name": "jsonb"},
        ]
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": rows,
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=base))
        result = service.inspect_relation("local", "demo", "public", "orders")
        self.assertEqual([column["suggestions"] for column in result["columns"]], [
            ["dimension", "identifier"], ["dimension", "measure"], ["dimension", "date"],
            ["dimension", "identifier"], ["dimension"], [],
        ])
        changed_policy_input = {**base, "a.attname AS column_name": [{**row, "type_category": "S"} for row in rows]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed_policy_input))
        self.assertEqual(result["fingerprint"], changed_service.inspect_relation("local", "demo", "public", "orders")["fingerprint"])

    def test_verified_relation_preview_is_read_only_bounded_and_uses_one_relation(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, amount FROM payments"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            'SELECT "id", "amount" FROM "public"."orders"': [
                {"id": 1, "amount": Decimal("10.25")},
                {"id": 2, "amount": Decimal("20")},
                {"id": 3, "amount": Decimal("30")},
            ],
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")}
        result = service.preview_relation_rows("local", source, offset=10, limit=2)
        self.assertEqual(result["rows"], [{"id": 1, "amount": "10.25"}, {"id": 2, "amount": "20"}])
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["nextOffset"], 12)
        self.assertFalse(result["stableOrder"])
        preview_connection = connections[-1]
        self.assertEqual(preview_connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertFalse(any("statement_timeout" in sql for sql, _ in preview_connection.executed))
        data_sql, parameters = next(item for item in preview_connection.executed if 'FROM "public"."orders"' in item[0])
        self.assertNotIn("*", data_sql)
        self.assertNotIn("JOIN", data_sql.upper())
        self.assertEqual(parameters, (3, 10))
        self.assertTrue(preview_connection.closed)

        stale_columns = {**source, "columns": [
            {"name": "id", "type": "integer", "nullable": False, "ordinal": 1},
            {"name": "amount", "type": "numeric", "nullable": True, "ordinal": 2},
        ]}
        with self.assertRaises(PostgresServiceError) as error:
            service.preview_relation_rows("local", stale_columns, limit=2)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('FROM "public"."orders"' in sql for sql, _ in connections[-1].executed))

    def test_verified_relation_preview_rejects_stale_or_unbounded_sources_before_select(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [{"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"}],
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        source = {
            "profileId": "local", "database": "demo", "namespace": "public", "relation": "orders",
            "kind": "table", "fingerprint": "0" * 64,
        }
        with self.assertRaises(PostgresServiceError) as error:
            service.preview_relation_rows("local", source)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('FROM "public"."orders"' in sql for sql, _ in connections[-1].executed))
        for invalid_source, limit in (({**source, "join": "customers"}, 20), (source, 51)):
            with self.subTest(source=invalid_source, limit=limit), self.assertRaises(ValidationError):
                service.preview_relation_rows("local", invalid_source, limit=limit)

    def test_relation_source_verification_reports_missing_added_and_changed_columns(self):
        base_rows = [
            {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
            {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 2, "type_category": "S", "type_name": "text"},
        ]
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": base_rows,
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=base))
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        self.assertEqual(service.verify_relation_source("local", source)["status"], "verified")

        changed = {**base, "a.attname AS column_name": [
            {"column_name": "id", "data_type": "integer", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "int4"},
            {"column_name": "created_at", "data_type": "timestamp", "nullable": False, "ordinal": 3, "type_category": "D", "type_name": "timestamp"},
        ]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        result = changed_service.verify_relation_source("local", source)
        self.assertEqual(result["status"], "changed")
        self.assertFalse(result["matches"])
        self.assertEqual(result["missingColumns"], ["status"])
        self.assertEqual(result["addedColumns"], ["created_at"])
        self.assertEqual(result["changedColumns"], [{"name": "id", "changes": ["type", "nullable", "ordinal", "capabilities"]}])

        missing = {**base, "c.relkind AS catalog_kind": []}
        missing_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=missing))
        missing_result = missing_service.verify_relation_source("local", source)
        self.assertEqual(missing_result["status"], "missing")
        self.assertEqual(missing_result["missingColumns"], ["id", "status"])

    def test_widget_query_is_verified_read_only_bounded_and_returns_provenance(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 1, "type_category": "S", "type_name": "text"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            '"status" AS "__schemer_d0"': {
                "columns": ["__schemer_d0", "__schemer_m0"],
                "rows": [("paid", Decimal("30.50")), ("pending", Decimal("12")), ("extra", Decimal("1"))],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_status", "label": "Status", "column": "status"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "currency", "currency": "USD", "fractionDigits": 2}}],
            "filters": [{"id": "filter_group_status", "conditions": [{"id": "filter_status", "column": "status", "operator": "neq", "values": ["cancelled"]}]}],
            "sort": [{"targetKind": "measure", "targetId": "measure_revenue", "direction": "desc", "nulls": "last"}],
            "limit": 2,
        }
        result = service.execute_widget_query("local", source, query)
        self.assertEqual(result["rows"], [["paid", "30.50"], ["pending", "12"]])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["queryVersion"], 2)
        self.assertEqual(result["parameters"], ["cancelled", 3])
        self.assertIsInstance(result["queryDurationMs"], int)
        self.assertTrue(result["queriedAt"].endswith("Z"))
        self.assertEqual(result["lineage"]["measures"][0]["sourceColumn"], "amount")
        self.assertEqual(result["lineage"]["filterGroups"][0]["conditions"][0]["operator"], "neq")
        self.assertEqual(result["provenance"]["profile"], {"id": "local", "label": "Local"})
        self.assertNotIn("password", json.dumps(result["provenance"]))
        self.assertEqual(result["provenance"]["relation"]["definition"]["reason"], "not_supported")
        query_connection = connections[-1]
        self.assertEqual(query_connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn(('LOCK TABLE "public"."orders" IN ACCESS SHARE MODE', ()), query_connection.executed)
        sql, parameters = next(item for item in query_connection.executed if '"status" AS "__schemer_d0"' in item[0])
        self.assertEqual(sql, result["sql"])
        self.assertNotIn("cancelled", sql)
        self.assertEqual(parameters, ("cancelled", 3))
        self.assertNotIn("JOIN", sql.upper())
        self.assertEqual(query_connection.rollbacks, 1)
        self.assertTrue(query_connection.closed)

        changed_source = {**source, "fingerprint": "0" * 64}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_widget_query("local", changed_source, query)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('"status" AS "__schemer_d0"' in sql for sql, _ in connections[-1].executed))
        duplicate_ordinals = {**source, "columns": [{**column, "ordinal": 1} for column in source["columns"]]}
        with self.assertRaises(ValidationError):
            service.execute_widget_query("local", duplicate_ordinals, query)

    def test_capability_catalog_mutation_stales_source_before_query_sql(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [{"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 1, "type_category": "S", "type_name": "text"}],
        }
        initial = Connection(responses=responses)
        changed = Connection(responses=responses, capability_version_suffix=":changed")
        connections = [initial, changed]
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connections.pop(0))
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        query = {"version": 2, "dimensions": [], "measures": [{"id": "m", "label": "Rows", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}], "filters": [], "sort": [], "limit": 10}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_widget_query("local", source, query)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('AS "__schemer_m0"' in sql for sql, _ in changed.executed))

    def test_temporal_series_manifest_and_windows_use_one_proportional_utc_domain(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "ordered_on", "data_type": "date", "nullable": False, "ordinal": 1, "type_category": "D", "type_name": "date"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            'AS "__schemer_min"': [{"__schemer_min": datetime(2026, 1, 1), "__schemer_max": datetime(2026, 1, 10), "__schemer_points": 10}],
            "pg_catalog.to_timestamp": {
                "columns": ["__schemer_t0", "__schemer_m0"],
                "rows": [
                    (datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("30.50")),
                    (datetime(2026, 1, 3, tzinfo=timezone.utc), Decimal("12.00")),
                ],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
            plan_ttl_seconds=111,
            temporal_manifest_ttl_seconds=17,
            clock=lambda: 1000.25,
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_ordered", "label": "Ordered on", "column": "ordered_on"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "currency", "currency": "USD", "fractionDigits": 2}}],
            "filters": [], "sort": [], "limit": 10,
        }
        manifest = service.execute_temporal_series("local", source, query, "manifest", "refresh-one")
        self.assertFalse(manifest["empty"])
        self.assertEqual(manifest["domain"], {"min": "2026-01-01T00:00:00.000Z", "max": "2026-01-10T00:00:00.000Z"})
        self.assertEqual(manifest["series"]["bucketSeconds"], 86400)
        self.assertEqual(manifest["series"]["alignedStart"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(manifest["series"]["alignedEndExclusive"], "2026-01-11T00:00:00.000Z")
        self.assertEqual(manifest["series"]["refreshGeneration"], "refresh-one")
        self.assertEqual(manifest["series"]["expiresAtEpoch"], 1018)
        self.assertEqual(len(manifest["series"]["key"]), 64)
        manifest_connection = connections[-1]
        self.assertIn(("SET LOCAL TIME ZONE 'UTC'", ()), manifest_connection.executed)
        self.assertEqual(manifest_connection.rollbacks, 1)

        window = service.execute_temporal_series(
            "local", source, query, "window", "refresh-one", manifest["series"], manifest["series"]["alignedStart"]
        )
        self.assertEqual(window["rows"], [["2026-01-01T00:00:00.000Z", "30.50"], ["2026-01-03T00:00:00.000Z", "12.00"]])
        self.assertEqual(window["range"], {"start": "2026-01-01T00:00:00.000Z", "endExclusive": "2026-01-11T00:00:00.000Z"})
        window_connection = connections[-1]
        sql, parameters = next(item for item in window_connection.executed if "pg_catalog.to_timestamp" in item[0])
        self.assertIn(">= %s", sql)
        self.assertIn("< %s", sql)
        self.assertEqual(parameters[:2], (86400, 86400))
        self.assertEqual(parameters[-1], 11)
        self.assertEqual(window_connection.rollbacks, 1)

        stale = {**manifest["series"], "bucketSeconds": 60}
        with self.assertRaises(ValidationError):
            service.execute_temporal_series("local", source, query, "window", "refresh-one", stale, stale["alignedStart"])
        stale_key = {**manifest["series"], "key": "0" * 64}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_temporal_series("local", source, query, "window", "refresh-one", stale_key, stale_key["alignedStart"])
        self.assertEqual(error.exception.code, "temporal_series_stale")
        with self.assertRaises(ValidationError):
            service.execute_temporal_series("local", source, query, "window", "refresh-two", manifest["series"], manifest["series"]["alignedStart"])
        expired = {**manifest["series"], "expiresAtEpoch": 0}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_temporal_series("local", source, query, "window", "refresh-one", expired, expired["alignedStart"])
        self.assertEqual(error.exception.code, "temporal_series_expired")

    def test_relation_detail_counts_and_pages_one_verified_snapshot(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 1, "type_category": "S", "type_name": "text"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            'count(*) AS "__schemer_count"': [{"__schemer_count": 3}],
            '"status" AS "__schemer_c0"': {
                "columns": ["__schemer_c0", "__schemer_c1"],
                "rows": [("paid", Decimal("30.50")), ("paid", Decimal("12"))],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "snapshotVersion": 2,
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_status", "label": "Status", "column": "status"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "decimal", "fractionDigits": 2}}],
            "filters": [{"id": "filter_group_status", "conditions": [{"id": "filter_status", "column": "status", "operator": "neq", "values": ["cancelled"]}]}],
            "sort": [],
            "limit": 100,
        }
        selection = {"dimensions": [{"targetId": "dimension_status", "value": "paid"}], "measureId": "measure_revenue"}
        detail = {
            "version": 1,
            "columns": [
                {"id": "detail_status", "label": "Status", "column": "status", "numberFormat": {"style": "auto"}, "searchable": True},
                {"id": "detail_amount", "label": "Amount", "column": "amount", "numberFormat": {"style": "decimal", "fractionDigits": 2}, "searchable": False},
            ],
            "rowIdentifier": None,
        }
        result = service.execute_relation_detail(
            "local", source, query, selection, detail, 0, 2,
            {"targetId": "detail_amount", "direction": "desc", "nulls": "last"},
            [{"targetId": "detail_status", "value": "acme"}],
        )
        self.assertEqual(result["rows"], [["paid", "30.50"], ["paid", "12"]])
        self.assertEqual(result["matchingRowCount"], 3)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["parameters"], ["cancelled", "paid", "%acme%", 2, 0])
        self.assertEqual(result["countParameters"], ["cancelled", "paid", "%acme%"])
        self.assertIsInstance(result["queryDurationMs"], int)
        self.assertTrue(result["queriedAt"].endswith("Z"))
        self.assertEqual(result["provenance"]["profile"], {"id": "local", "label": "Local"})
        self.assertNotIn("host", result["provenance"]["profile"])
        query_connection = connections[-1]
        self.assertEqual(query_connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn(('LOCK TABLE "public"."orders" IN ACCESS SHARE MODE', ()), query_connection.executed)
        count_index = next(index for index, item in enumerate(query_connection.executed) if '__schemer_count' in item[0])
        page_index = next(index for index, item in enumerate(query_connection.executed) if '__schemer_c0' in item[0])
        self.assertLess(count_index, page_index)
        self.assertNotIn("JOIN", result["sql"].upper())
        self.assertNotIn("acme", result["sql"])
        self.assertEqual(query_connection.rollbacks, 1)
        self.assertTrue(query_connection.closed)

        with self.assertRaises(ValidationError):
            service.execute_relation_detail("local", source, query, selection, detail, 0, 101, None, [])

    def test_relation_inspection_rejects_stale_kind_and_fingerprint(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [{"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"}],
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=responses))
        current = service.inspect_relation("local", "demo", "public", "orders")
        verified = service.inspect_relation("local", "demo", "public", "orders", "table", current["fingerprint"])
        self.assertEqual(verified["fingerprint"], current["fingerprint"])
        for kind, fingerprint in (("view", current["fingerprint"]), ("table", "0" * 64)):
            with self.subTest(kind=kind, fingerprint=fingerprint), self.assertRaises(PostgresServiceError) as error:
                service.inspect_relation("local", "demo", "public", "orders", kind, fingerprint)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(error.exception.code, "relation_changed")
        with self.assertRaises(ValidationError):
            service.inspect_relation("local", "demo", "public", "orders", "table", "invalid")

    def test_table_data_preview_validates_page_and_missing_table(self):
        with self.assertRaises(ValidationError):
            self.service.preview_table_data("local", "public", "events", limit=51)
        with self.assertRaises(ValidationError):
            self.service.preview_table_data("local", "public", "bad\x00table")
        connection = Connection(responses={"a.attname AS column_name": []})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.preview_table_data("local", "public", "missing")
        self.assertEqual(error.exception.code, "not_found")

    def test_separate_service_instances_share_profile_updates(self):
        first = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        second = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        first.save_profile("first", PROFILE)
        second.save_profile("second", {**PROFILE, "name": "Reporting", "dbname": "reports"})

        profiles = {profile["id"]: profile for profile in first.list_profiles()}
        self.assertEqual(set(profiles), {"local", "first", "second"})
        self.assertNotIn("password", profiles["first"])
        self.assertTrue((Path(self.temporary_directory.name) / ".postgres_profiles.lock").is_file())

    def test_read_only_sql_limits_rows_and_serializes_values(self):
        query = "SELECT id, amount FROM payments"
        rows = [(UUID(int=index + 1), Decimal(f"{index}.25")) for index in range(501)]
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            query: {"columns": ["id", "amount"], "rows": rows},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.execute_read_only_sql("local", "public", query)
        self.assertEqual(result["rowCount"], 500)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["rows"][0], [str(UUID(int=1)), "0.25"])
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertFalse(any("statement_timeout" in sql for sql, _ in connection.executed))
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_read_only_sql_cancellation_reaches_postgres_and_waits_for_rollback(self):
        started = threading.Event()
        cancelled = threading.Event()
        query = "SELECT pg_sleep(30)"

        class CancellationDiagnostic:
            message_primary = "canceling statement due to user request"

        class CancellationError(Exception):
            sqlstate = "57014"
            diag = CancellationDiagnostic()

        class BlockingCursor(Cursor):
            def execute(self, sql, params=()):
                if sql == query:
                    started.set()
                    if not cancelled.wait(2):
                        raise RuntimeError("cancellation did not reach the connection")
                    raise CancellationError()
                return super().execute(sql, params)

        class BlockingConnection(Connection):
            def cursor(self):
                return BlockingCursor(self)

            def cancel(self):
                cancelled.set()

        connection = BlockingConnection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        outcome = []

        def execute():
            try:
                service.execute_read_only_sql("local", "public", query, operation_id="operation-query")
            except Exception as error:
                outcome.append(error)

        worker = threading.Thread(target=execute)
        worker.start()
        self.assertTrue(started.wait(1))
        self.assertEqual(service.cancel_read_only_sql("operation-query"), {"requested": True})
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome[0].code, "execution_cancelled")
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_read_only_sql_honors_cancellation_before_connection_attachment(self):
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: connections.append(Connection()) or connections[-1],
        )
        service.cancel_read_only_sql("operation-before-connect")

        with self.assertRaises(PostgresServiceError) as caught:
            service.execute_read_only_sql("local", "public", "SELECT 1", operation_id="operation-before-connect")

        self.assertEqual(caught.exception.code, "execution_cancelled")
        self.assertEqual(connections, [])

    def test_read_only_sql_accepts_show_and_keeps_the_result_contract(self):
        statement = "SHOW lock_timeout"
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            statement: {"columns": ["lock_timeout"], "rows": [("250ms",)]},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        result = service.execute_read_only_sql("local", "public", statement)

        self.assertEqual(result["rows"], [["250ms"]])
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")

    def test_read_only_sql_rejects_invalid_or_failed_queries(self):
        for statement in ("", "SELECT 1; SELECT 2"):
            with self.subTest(statement=statement), self.assertRaises(ValidationError):
                self.service.execute_read_only_sql("local", "public", statement)
        metadata = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
        }

        class ReadOnlyDiagnostic:
            message_primary = "cannot execute UPDATE in a read-only transaction"
            message_detail = "The transaction was declared read only."
            message_hint = "Use a write-authorized operation."

        class ReadOnlyError(Exception):
            sqlstate = "25006"
            diag = ReadOnlyDiagnostic()

        connection = Connection(responses=metadata, fail_on="UPDATE payments", failure=ReadOnlyError())
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "UPDATE payments SET amount = 0")
        self.assertEqual(error.exception.details["postgres"], {
            "sqlstate": "25006",
            "message": "cannot execute UPDATE in a read-only transaction",
            "detail": "The transaction was declared read only.",
            "hint": "Use a write-authorized operation.",
        })

        connection = Connection(responses=metadata)
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(ValidationError) as error:
            service.execute_read_only_sql("local", "public", "DO $$ BEGIN NULL; END $$")
        self.assertEqual(error.exception.message, "The SQL query did not return a result set")

        connection = Connection(responses=metadata, fail_on="SELECT secret FROM payments")
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT secret FROM payments")
        self.assertEqual(error.exception.code, "sql_query_failed")
        self.assertNotIn("database detail", error.exception.message)
        self.assertEqual(connection.rollbacks, 1)

        class Diagnostic:
            message_primary = "SELECT DISTINCT ON expressions must match initial ORDER BY expressions"

        class QueryError(Exception):
            sqlstate = "42P10"
            diag = Diagnostic()

        connection = Connection(responses=metadata, fail_on="SELECT DISTINCT", failure=QueryError())
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT DISTINCT ON (id) id FROM payments ORDER BY created_at")
        self.assertEqual(error.exception.message, "Read-only SQL query failed")
        self.assertEqual(error.exception.details["postgres"]["sqlstate"], "42P10")

    def test_read_only_sql_verifies_exact_target_and_schemer_limits(self):
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_read_only_sql("local", "public", "SELECT 1", expected_profile_fingerprint="stale")
        self.assertEqual(error.exception.code, "profile_changed")

        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_read_only_sql("local", "public", "SELECT 1", database="other")
        self.assertEqual(error.exception.code, "database_changed")

        mismatch = Connection(responses={"SELECT current_database() AS database": [{"database": "other"}]})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: mismatch)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT 1", database="demo")
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any(sql == "SELECT 1" for sql, _ in mismatch.executed))

        privileged = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo", "rolsuper": True, "rolbypassrls": False}],
            "SELECT EXISTS": [{"exists": True}],
            "SELECT 1": {"columns": ["value"], "rows": [(1,)]},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: privileged)
        result = service.execute_read_only_sql("local", "public", "SELECT 1", database="demo")
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(privileged.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertEqual(privileged.rollbacks, 1)

        missing = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": False}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: missing)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "missing", "SELECT 1", database="demo")
        self.assertEqual(error.exception.code, "not_found")

        query = "SELECT value FROM metrics"
        limited = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            query: {"columns": ["value"], "rows": [(float("nan"),), ("x" * 1000,), ("later",)]},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: limited)
        result = service.execute_read_only_sql(
            "local", "public", query, database="demo", allow_explain=False,
            max_rows=100, max_columns=50, max_result_bytes=400,
        )
        self.assertEqual(result["rows"], [["nan"]])
        self.assertTrue(result["truncated"])
        json.dumps(result, allow_nan=False)
        with self.assertRaises(ValidationError):
            service.execute_read_only_sql("local", "public", "EXPLAIN SELECT 1", database="demo", allow_explain=False)

        wide = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            "SELECT * FROM wide": {"columns": [f"column_{index}" for index in range(51)], "rows": []},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: wide)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT * FROM wide", database="demo", max_columns=50)
        self.assertEqual(error.exception.code, "sql_result_too_wide")

    def test_canonical_fingerprint_ignores_layout_and_transients(self):
        first = {"tables": [{"id": "t", "name": "x", "x": 1, "color": "red"}], "postgres": {"profileId": "one", "importedAt": "then"}}
        second = {"postgres": {"profileId": "two", "importedAt": "now"}, "tables": [{"color": "blue", "x": 99, "name": "x", "id": "t"}]}
        self.assertEqual(canonical_fingerprint(first), canonical_fingerprint(second))
        second["tables"][0]["name"] = "y"
        self.assertNotEqual(canonical_fingerprint(first), canonical_fingerprint(second))

    def test_namespace_mutation_lock_retains_stricter_postgres_policy(self):
        connection = Connection()
        cursor = connection.cursor()

        self.service._acquire_namespace_mutation_lock(cursor, "public", "demo")

        timeout_sql = connection.executed[0][0]
        self.assertIn("current_setting('lock_timeout')", timeout_sql)
        self.assertIn("interval '5000 milliseconds'", timeout_sql)
        self.assertIn("ELSE pg_catalog.current_setting('lock_timeout')", timeout_sql)
        self.assertFalse(any("statement_timeout" in sql for sql, _ in connection.executed))
        self.assertIn("pg_advisory_xact_lock", connection.executed[1][0])

    def test_migration_row_presence_probe_uses_a_read_only_transaction(self):
        connection = Connection(responses={"AS has_rows": [{"has_rows": True}]})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)

        populated = service._tables_with_rows("local", "public", {"orders"})

        self.assertEqual(populated, {"orders"})
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertEqual(connection.rollbacks, 1)

    def test_introspection_maps_composite_keys_indexes_triggers_and_routines(self):
        columns = [
            {"table_name": "parent", "column_name": "tenant", "ordinal": 1, "data_type": "uuid", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "parent", "column_name": "number", "ordinal": 2, "data_type": "integer", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "child", "column_name": "tenant", "ordinal": 1, "data_type": "uuid", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "child", "column_name": "parent_number", "ordinal": 2, "data_type": "integer", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
        ]
        constraints = [
            {"constraint_name": "parent_pkey", "table_name": "parent", "constraint_type": "p", "columns": ["tenant", "number"], "target_namespace": None, "target_table": None, "target_columns": [], "update_action": "a", "delete_action": "a", "deferrable": False, "initially_deferred": False, "definition": "PRIMARY KEY (tenant, number)"},
            {"constraint_name": "child_parent_fkey", "table_name": "child", "constraint_type": "f", "columns": ["tenant", "parent_number"], "target_namespace": "public", "target_table": "parent", "target_columns": ["tenant", "number"], "update_action": "c", "delete_action": "r", "deferrable": False, "initially_deferred": False, "definition": "FOREIGN KEY ..."},
        ]
        indexes = [{"table_name": "child", "index_name": "child_tenant_idx", "definition": "CREATE INDEX child_tenant_idx ON public.child USING btree (tenant)", "is_unique": False, "method": "btree"}]
        routines = [{"name": "touch_child", "kind": "f", "identity_arguments": "", "arguments": "", "return_type": "trigger", "language": "plpgsql", "definition": "CREATE FUNCTION public.touch_child() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"}]
        triggers = [{"table_name": "child", "trigger_name": "child_touch", "definition": "CREATE TRIGGER child_touch BEFORE UPDATE ON public.child FOR EACH ROW EXECUTE FUNCTION public.touch_child()", "enabled": "O"}]
        schema = self.service._build_schema(
            "local", "public", {"database": "demo", "server_version": "16.3", "server_version_num": "160003"},
            columns, constraints, indexes, routines, [], triggers,
        )
        relation = schema["relationships"][0]
        self.assertEqual(len(relation["fromColumnIds"]), 2)
        self.assertEqual(len(relation["toColumnIds"]), 2)
        self.assertEqual(relation["onUpdate"], "CASCADE")
        parent = next(table for table in schema["tables"] if table["name"] == "parent")
        child = next(table for table in schema["tables"] if table["name"] == "child")
        self.assertEqual([column["name"] for column in parent["columns"] if column["primary"]], ["tenant", "number"])
        self.assertEqual(child["indexes"][0]["name"], "child_tenant_idx")
        self.assertEqual(child["triggers"][0]["name"], "child_touch")
        self.assertEqual(schema["functions"][0]["name"], "touch_child")

    def test_preview_is_immutable_and_destructive_drops_are_gated(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema()
        desired = empty_schema()
        desired["tables"] = [{"id": "t", "name": "new table", "columns": [{"id": "c", "name": "id", "type": "integer", "nullable": False}], "uniqueConstraints": []}]
        plan = self.service.preview("local", "public", desired, persist=False)
        self.assertIn('CREATE TABLE "public"."new table"', plan["steps"][0]["sql"])
        live = empty_schema()
        live["tables"] = desired["tables"]
        self.service.introspect = lambda profile_id, namespace: live
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("new table")
        omitted = self.service.preview("local", "public", empty_schema(), False, persist=False)
        self.assertEqual(omitted["steps"], [])
        self.assertEqual(omitted["warnings"], [])
        self.assertEqual(omitted["blockingDifferences"][0]["code"], "destructive_omitted")
        self.assertFalse(omitted["complete"])
        self.assertFalse(omitted["applyCapable"])
        included = self.service.preview("local", "public", empty_schema(), True, persist=False)
        self.assertTrue(included["steps"][0]["destructive"])
        self.assertTrue(included["complete"])
        self.assertTrue(included["applyCapable"])

    def test_unsupported_desired_difference_blocks_preview_while_information_stays_warning(self):
        live = empty_schema()
        live["tables"] = [{
            "id": "t", "name": "events", "columns": [{"id": "c", "name": "id", "type": "integer", "nullable": True}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        desired = copy.deepcopy(live)
        desired["tables"][0]["columns"][0].setdefault("postgres", {})["identity"] = "d"
        self.service.introspect = lambda profile_id, namespace: live

        preview = self.service.preview("local", "public", desired, persist=False)

        self.assertFalse(preview["complete"])
        self.assertEqual(preview["warnings"], [])
        self.assertEqual(preview["blockingDifferences"][0]["code"], "unsupported")
        self.assertIn("nextAction", preview["blockingDifferences"][0])

    def test_data_movement_warning_does_not_make_full_preview_incomplete(self):
        live = empty_schema()
        live["tables"] = [{
            "id": "t", "name": "events", "postgres": {"hasRows": True},
            "columns": [
                {"id": "a", "name": "first", "type": "integer", "nullable": True},
                {"id": "b", "name": "second", "type": "text", "nullable": True},
            ],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        desired = copy.deepcopy(live)
        desired["tables"][0]["columns"].reverse()
        self.service.introspect = lambda profile_id, namespace: copy.deepcopy(live)
        self.service._tables_with_rows = lambda profile_id, namespace, tables: {"events"}
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("events")

        preview = self.service.preview("local", "public", desired, True, persist=False)

        self.assertTrue(preview["complete"])
        self.assertTrue(preview["applyCapable"])
        self.assertEqual(preview["blockingDifferences"], [])
        self.assertEqual(preview["warnings"][0]["code"], "data_movement")

    def test_column_type_change_discloses_lock_and_possible_table_rewrite(self):
        live = empty_schema()
        live["tables"] = [{
            "id": "t", "name": "personnel_dim",
            "columns": [{"id": "c", "name": "certification_list", "type": "character varying", "nullable": True}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        desired = copy.deepcopy(live)
        desired["tables"][0]["columns"][0]["type"] = "uuid[]"
        self.service.introspect = lambda profile_id, namespace: copy.deepcopy(live)
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("personnel_dim")

        preview = self.service.preview("local", "public", desired, True, persist=False)

        self.assertTrue(preview["complete"])
        self.assertTrue(preview["applyCapable"])
        self.assertEqual(preview["blockingDifferences"], [])
        self.assertEqual(preview["warnings"][0]["code"], "data_movement")
        self.assertIn("ACCESS EXCLUSIVE", preview["warnings"][0]["message"])
        self.assertIn("rewrite every existing row", preview["warnings"][0]["message"])

    def test_partition_blocking_is_scoped_to_concrete_touches_and_dependencies(self):
        partition = {
            "id": "partitioned", "name": "events_by_month", "postgres": {"partitioned": True},
            "columns": [{"id": "partition_id", "name": "id", "type": "integer", "nullable": False}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }
        ordinary = {
            "id": "ordinary", "name": "notes",
            "columns": [{"id": "note_id", "name": "id", "type": "integer", "nullable": False}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }
        live = empty_schema()
        live["tables"] = [partition, ordinary]
        desired = copy.deepcopy(live)
        desired["tables"][1]["columns"].append({"id": "body", "name": "body", "type": "text", "nullable": True})
        self.service.introspect = lambda *args: copy.deepcopy(live)
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("notes")

        unrelated = self.service.preview("local", "public", desired, persist=False)
        self.assertTrue(unrelated["complete"])
        self.assertFalse(any(item.get("relation") == "events_by_month" for item in unrelated["blockingDifferences"]))

        touched = copy.deepcopy(live)
        touched["tables"][0]["columns"].append({"id": "payload", "name": "payload", "type": "text", "nullable": True})
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("events_by_month", kind="p")
        blocked = self.service.preview("local", "public", touched, persist=False)
        self.assertFalse(blocked["complete"])
        self.assertEqual(blocked["blockingDifferences"][0]["code"], "unsupported_relation")

        dependency = copy.deepcopy(live)
        dependency["relationships"] = [{
            "id": "fk_notes_partition", "name": "notes_partition_fkey", "constraintName": "notes_partition_fkey",
            "fromTableId": "ordinary", "fromColumnId": "note_id", "toTableId": "partitioned",
            "toColumnId": "partition_id", "targetNamespace": "public", "targetTableName": "events_by_month",
            "targetColumnNames": ["id"], "onUpdate": "NO ACTION", "onDelete": "NO ACTION",
            "matchType": "SIMPLE", "validated": True,
        }]
        self.service._migration_safety_assessment = lambda *args, **kwargs: {
            "status": "available", "relations": {
                "events_by_month": migration_assessment("events_by_month", kind="p")["relations"]["events_by_month"],
                "notes": migration_assessment("notes")["relations"]["notes"],
            },
        }
        blocked_dependency = self.service.preview("local", "public", dependency, persist=False)
        self.assertTrue(any(item.get("relation") == "events_by_month" for item in blocked_dependency["blockingDifferences"]))

    def test_view_dependency_checks_are_relation_and_column_scoped(self):
        live = empty_schema()
        live["tables"] = [{
            "id": "events", "name": "events",
            "columns": [{"id": "value", "name": "value", "type": "integer", "nullable": True}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        live["views"] = [{"id": "unrelated", "name": "other_view", "queryDefinition": "SELECT 1"}]
        desired = copy.deepcopy(live)
        desired["tables"][0]["columns"][0]["type"] = "bigint"
        self.service.introspect = lambda *args: copy.deepcopy(live)
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("events")

        unrelated = self.service.preview("local", "public", desired, True, persist=False)
        self.assertTrue(unrelated["complete"])
        self.assertTrue(any(" TYPE bigint" in step["sql"] for step in unrelated["steps"]))

        dependent = [{
            "column_name": "value", "dependent_namespace": "public",
            "dependent_relation": "event_totals", "dependent_kind": "view",
        }]
        self.service._migration_safety_assessment = lambda *args, **kwargs: migration_assessment("events", dependencies=dependent)
        blocked = self.service.preview("local", "public", desired, True, persist=False)
        self.assertFalse(blocked["complete"])
        self.assertIn("dependent objects", blocked["blockingDifferences"][0]["message"])

        removed = copy.deepcopy(live)
        removed["tables"][0]["columns"] = []
        blocked_drop = self.service.preview("local", "public", removed, True, persist=False)
        self.assertFalse(blocked_drop["complete"])
        self.assertFalse(any("DROP COLUMN" in step["sql"] for step in blocked_drop["steps"]))

    def test_reconstruction_requires_complete_metadata_neutral_inventory(self):
        live = empty_schema()
        live["tables"] = [{
            "id": "events", "name": "events",
            "columns": [
                {"id": "first", "name": "first", "type": "integer", "nullable": True},
                {"id": "second", "name": "second", "type": "text", "nullable": True},
            ],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        desired = copy.deepcopy(live)
        desired["tables"][0]["columns"].reverse()
        self.service.introspect = lambda *args: copy.deepcopy(live)
        self.service._tables_with_rows = lambda *args: set()

        for assessment, complete, code in (
            (migration_assessment("events"), True, None),
            (migration_assessment("events", blockers=["relation comment"]), False, "reconstruction_preservation_unsupported"),
            (migration_assessment("events", available=False), False, "reconstruction_inventory_unavailable"),
        ):
            with self.subTest(code=code):
                self.service._migration_safety_assessment = lambda *args, value=assessment, **kwargs: value
                preview = self.service.preview("local", "public", desired, True, persist=False)
                self.assertEqual(preview["complete"], complete)
                if code:
                    self.assertEqual(preview["blockingDifferences"][0]["code"], code)
                self.assertFalse(any(" CASCADE" in step["sql"].upper() for step in preview["steps"]))

    def test_migration_fingerprint_tracks_opaque_metadata_and_timezone_inputs(self):
        live = empty_schema("catalog")
        live["postgres"]["timeZone"] = "UTC"
        first = self.service._migration_fingerprint(live, migration_assessment("events", opaque="one"))
        opaque_changed = self.service._migration_fingerprint(live, migration_assessment("events", opaque="two"))
        timezone_changed = copy.deepcopy(live)
        timezone_changed["postgres"]["timeZone"] = "America/New_York"
        timezone_fingerprint = self.service._migration_fingerprint(
            timezone_changed, migration_assessment("events", opaque="one"),
        )

        self.assertNotEqual(first, opaque_changed)
        self.assertNotEqual(first, timezone_fingerprint)

    def test_migration_dependency_inventory_uses_pg_depend_and_pg_rewrite(self):
        connection = Connection(responses={
            "migration_relation_identity": [{"oid": 41, "relkind": "r"}],
            "migration_view_dependencies": [{
                "column_name": "value", "dependent_namespace": "reports",
                "dependent_relation": "event_totals", "dependent_kind": "view",
            }],
            "migration_reconstruction_inventory": [{
                "relation_oid": "41", "relation_xmin": "7", "owner": "developer", "current_role": "developer",
                "replica_identity": "d", "persistence": "p", "opaque_metadata": {},
            }],
        })

        assessment = self.service._migration_safety_assessment_connection(
            connection, "public", ["events"], {"events"},
        )

        dependencies = assessment["relations"]["events"]["viewDependencies"]
        self.assertEqual(dependencies["items"][0]["dependent_relation"], "event_totals")
        sql = next(sql for sql, _ in connection.executed if "migration_view_dependencies" in sql)
        self.assertIn("pg_catalog.pg_depend", sql)
        self.assertIn("pg_catalog.pg_rewrite", sql)
        self.assertIn("d.refobjsubid", sql)
        inventory_sql = next(sql for sql, _ in connection.executed if "migration_reconstruction_inventory" in sql)
        for catalog in (
            "pg_default_acl", "pg_policy", "pg_publication_rel", "pg_seclabel", "pg_extension",
            "pg_statistic_ext", "pg_index", "pg_constraint", "pg_trigger", "pg_inherits",
        ):
            self.assertIn(catalog, inventory_sql)

    def test_preview_rejects_additional_top_level_sql_statements(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema()
        desired = empty_schema()
        desired["tables"] = [{
            "id": "t", "name": "events", "uniqueConstraints": [],
            "columns": [{"id": "c", "name": "value", "type": "integer", "nullable": True, "default": "0; DROP TABLE important"}],
        }]
        with self.assertRaises(ValidationError):
            self.service.preview("local", "public", desired)
        desired["tables"][0]["columns"][0]["default"] = "0"
        desired["functions"] = [{
            "name": "safe", "kind": "function", "identityArguments": "",
            "definition": "CREATE FUNCTION public.safe() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 1; END; $$; DROP TABLE important",
        }]
        with self.assertRaises(ValidationError):
            self.service.preview("local", "public", desired)

if __name__ == "__main__":
    unittest.main()
