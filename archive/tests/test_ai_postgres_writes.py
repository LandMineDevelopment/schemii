import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import PostgresService, PostgresServiceError, canonical_fingerprint
from tests.test_postgres_service import Connection, PROFILE


TARGET_CATALOG = {
    "database": "demo", "namespace": "public", "relation": "events", "relationOid": "7", "relationXmin": "1",
    "catalogKind": "r", "requestedColumns": ["name"],
    "tree": [{"relation_oid": "7", "parent_oid": None, "level": 0, "namespace": "public", "name": "events", "catalog_kind": "r", "xmin": "1", "row_security": False, "force_row_security": False, "replica_identity": "d", "partition_bound": None, "is_leaf": True}],
    "columns": [{"relation_oid": "7", "name": "name", "ordinal": 1, "type_oid": "25", "type_modifier": -1, "type": "text", "collation_oid": "100", "nullable": False, "identity": "", "generated": "", "has_missing": False, "default_oid": None, "default_xmin": None, "default": None}],
    "constraints": [], "triggers": [], "policies": [], "rules": [], "types": [{"oid": "25", "xmin": "1", "namespace": "pg_catalog", "name": "text", "kind": "b", "category": "S", "base_type_oid": "0", "element_type_oid": "0", "composite_relation_oid": "0", "not_null": False, "default": None, "enum_values": [], "domain_constraints": []}],
    "casts": [], "dependencies": [], "requestedColumnPrivileges": [{"name": "name", "can_insert": True}],
    "catalogCompleteness": {"complete": True, "treeRelations": 1, "capturedAtSnapshot": True},
}
TARGET = {"kind": "table", "fingerprint": canonical_fingerprint({key: value for key, value in TARGET_CATALOG.items() if key != "requestedColumnPrivileges"}), "catalog": TARGET_CATALOG}


def inspection_responses(**overrides):
    values = {
        "ai_insert_relation": [{"database": "demo", "live_oid": 7, "catalog_kind": "r", "xmin": "1", "can_insert": True}],
        "ai_insert_tree": TARGET_CATALOG["tree"], "ai_insert_columns": TARGET_CATALOG["columns"],
        "ai_insert_constraints": [], "ai_insert_triggers": [], "ai_insert_policies": [], "ai_insert_rules": [],
        "ai_insert_types": TARGET_CATALOG["types"], "ai_insert_casts": [], "ai_insert_dependencies": [],
        "has_column_privilege": [{"name": "name", "can_insert": True}],
    }
    values.update(overrides)
    return values


class RowCountCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = -1

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if "pg_current_xact_id()::text" in sql:
            self.connection.rows = [{"xid": "42"}]
        elif "pg_xact_status" in sql:
            self.connection.rows = [{"status": self.connection.transaction_status}]
        else:
            self.connection.rows = []
        if sql.startswith("INSERT INTO"):
            self.rowcount = 2

    def fetchall(self):
        return list(self.connection.rows)

    def close(self):
        pass


class WriteConnection(Connection):
    def __init__(self, *, transaction_status="committed", fail_commit=False):
        super().__init__()
        self.rows = []
        self.transaction_status = transaction_status
        self.fail_commit = fail_commit

    def cursor(self):
        return RowCountCursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("lost commit response")


class RetiredAiPlanAuthorityTests(unittest.TestCase):
    def test_insert_privilege_probes_are_advisory_for_postgres_final_authority(self):
        responses = inspection_responses(
            ai_insert_relation=[{"database": "demo", "live_oid": 7, "catalog_kind": "r", "xmin": "1", "can_insert": False}],
            has_column_privilege=[{"name": "name", "can_insert": False}],
        )
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: Connection(responses=responses))
            service.save_profile("local", PROFILE)

            target = service._inspect_ai_insert_target(Connection(responses=responses), "demo", "public", "events", ["name"])

        self.assertEqual(target["catalog"]["requestedColumnPrivileges"], [{"name": "name", "can_insert": False}])

    def test_insert_inspection_accepts_postgresql_semantic_categories_and_partitions(self):
        tree = [
            {**TARGET_CATALOG["tree"][0], "catalog_kind": "p", "is_leaf": False},
            {**TARGET_CATALOG["tree"][0], "relation_oid": "8", "parent_oid": "7", "name": "events_2026", "partition_bound": "FOR VALUES FROM (0) TO (100)"},
        ]
        columns = [
            {**TARGET_CATALOG["columns"][0], "default": "public.make_name()", "default_oid": "20", "default_xmin": "3", "identity": "a"},
            {**TARGET_CATALOG["columns"][0], "relation_oid": "8", "generated": "s"},
        ]
        user_types = [TARGET_CATALOG["types"][0],
            {**TARGET_CATALOG["types"][0], "oid": "90", "namespace": "public", "name": "status", "kind": "e", "enum_values": [["new", 1.0]]},
            {**TARGET_CATALOG["types"][0], "oid": "91", "namespace": "public", "name": "positive", "kind": "d", "base_type_oid": "23", "domain_constraints": [["70", "4", "positive_check", "CHECK ((VALUE > 0))"]]},
            {**TARGET_CATALOG["types"][0], "oid": "92", "namespace": "public", "name": "payload", "kind": "c", "composite_relation_oid": "93"},
        ]
        dependencies = [
            {"source_class_oid": "2604", "source_oid": "20", "referenced_class_oid": "1255", "referenced_oid": "30", "referenced_sub_id": 0, "dependency_type": "n", "kind": "function", "namespace": "public", "name": "make_name", "function_xmin": "5", "language_oid": "14", "volatility": "v", "parallel_safety": "u", "function_source": "begin return 'x'; end", "operator_xmin": None, "operator_function_oid": None, "type_xmin": None, "relation_xmin": None, "relation_kind": None},
            {"source_class_oid": "2606", "source_oid": "40", "referenced_class_oid": "2617", "referenced_oid": "41", "referenced_sub_id": 0, "dependency_type": "n", "kind": "operator", "namespace": "public", "name": "===", "function_xmin": None, "language_oid": None, "volatility": None, "parallel_safety": None, "function_source": None, "operator_xmin": "6", "operator_function_oid": "30", "type_xmin": None, "relation_xmin": None, "relation_kind": None},
        ]
        responses = inspection_responses(
            ai_insert_relation=[{"database": "demo", "live_oid": 7, "catalog_kind": "p", "xmin": "1", "can_insert": True}],
            ai_insert_tree=tree, ai_insert_columns=columns, ai_insert_types=user_types,
            ai_insert_constraints=[{"oid": "40", "xmin": "2", "relation_oid": "8", "name": "check_it", "type": "c", "definition": "CHECK (public.ok(name))"}],
            ai_insert_triggers=[{"oid": "50", "xmin": "2", "relation_oid": "8", "name": "audit", "enabled": "O", "internal": False, "function_oid": "30", "definition": "CREATE TRIGGER audit ..."}],
            ai_insert_policies=[{"oid": "60", "xmin": "2", "relation_oid": "7", "name": "tenant", "command": "a", "permissive": True, "roles": "{0}", "using_expression": None, "check_expression": "tenant_id = 1"}],
            ai_insert_rules=[{"oid": "61", "xmin": "2", "relation_oid": "7", "name": "also_log", "event": "3", "instead": False, "enabled": "O", "definition": "CREATE RULE ..."}],
            ai_insert_dependencies=dependencies,
        )
        with tempfile.TemporaryDirectory() as directory:
            target = PostgresService(directory, connect_factory=lambda **kwargs: Connection())._inspect_ai_insert_target(Connection(responses=responses), "demo", "public", "events", ["name"])
        self.assertEqual(target["kind"], "partitioned_table")
        self.assertEqual({item["kind"] for item in target["catalog"]["dependencies"]}, {"function", "operator"})
        self.assertEqual({item["kind"] for item in target["catalog"]["types"]}, {"b", "e", "d", "c"})

    def test_incomplete_tree_snapshot_is_precise_application_limitation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: Connection())
            with self.assertRaises(PostgresServiceError) as caught:
                service._inspect_ai_insert_target(Connection(responses=inspection_responses(ai_insert_tree=[])), "demo", "public", "events", ["name"])
        self.assertEqual(caught.exception.code, "application_limitation")
        self.assertEqual(caught.exception.details["catalog"], "partition_tree")

    def test_fingerprint_changes_for_every_mutation_sensitive_catalog_group(self):
        baseline = canonical_fingerprint(TARGET_CATALOG)
        for field in ("tree", "columns", "constraints", "triggers", "policies", "rules", "types", "casts", "dependencies"):
            with self.subTest(field=field):
                changed = dict(TARGET_CATALOG)
                changed[field] = list(changed[field]) + [{"mutation": field}]
                self.assertNotEqual(canonical_fingerprint(changed), baseline)

    def test_ai_json_plan_authority_is_unavailable_without_metadata_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            service.save_profile("local", PROFILE)
            with self.assertRaises(PostgresServiceError) as caught:
                service.preview_ai_insert_rows(
                    "operation_preview", "local", "demo", "public", "events", [{"name": "launch"}],
                    {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64},
                )
            self.assertEqual(caught.exception.code, "durable_migrations_unavailable")
            self.assertFalse((Path(directory) / "ai_migration_plans").exists())

    def test_legacy_json_plans_are_archived_but_never_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "ai_migration_plans"
            legacy.mkdir()
            (legacy / "ai_plan_old.json").write_text('{"state":"ready"}', encoding="utf-8")
            PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            self.assertFalse(legacy.exists())
            self.assertTrue((Path(directory) / "retired_ai_migration_plans" / "ai_plan_old.retired.json").exists())

    def test_process_local_apply_facades_cannot_execute_without_durable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            service.save_profile("local", PROFILE)
            service.introspect = lambda *args: {
                "projectName": "demo.public", "tables": [], "relationships": [], "functions": [], "views": [],
                "postgres": {"namespace": "public", "database": "demo", "fingerprint": "a" * 64},
            }
            with self.assertRaises(PostgresServiceError) as preview_error:
                service.preview("local", "public", service.introspect("local", "public"))
            self.assertEqual(preview_error.exception.code, "durable_migrations_unavailable")
            with self.assertRaises(PostgresServiceError) as apply_error:
                service.apply("local", "12345678-1234-4123-8123-123456789abc", False)
            self.assertEqual(apply_error.exception.code, "durable_migrations_unavailable")
            self.assertFalse(hasattr(service, "_plans"))


if __name__ == "__main__":
    unittest.main()
