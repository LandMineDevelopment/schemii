import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import PostgresService, PostgresServiceError, quote_identifier
from schemii.query_type_capabilities import snapshot_column


class TrackingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self.connection.cursor(*args, **kwargs)

    def rollback(self):
        self.rollbacks += 1
        self.connection.rollback()

    def close(self):
        self.closed = True
        self.connection.close()

    def __getattr__(self, name):
        return getattr(self.connection, name)


@unittest.skipUnless(os.environ.get("SCHEMII_TEST_PG17_DSN"), "SCHEMII_TEST_PG17_DSN is not configured")
class LiveVerifiedRelationSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls.psycopg = psycopg
        cls.sql = sql
        cls.dict_row = staticmethod(dict_row)
        cls.dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        with psycopg.connect(cls.dsn) as connection:
            version = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if not 170000 <= version < 180000:
            raise unittest.SkipTest("SCHEMII_TEST_PG17_DSN must target PostgreSQL 17")

    def setUp(self):
        suffix = uuid.uuid4().hex[:12]
        self.schema = f"schemii_guard_{suffix}"
        self.server = f"schemii_guard_files_{suffix}"
        self.reader_role = f"schemii_guard_reader_{suffix}"
        self.reader_password = f"reader_{suffix}_test_only"
        self.foreign_available = False
        self.file_fdw_created = False
        self.setup_connection = self.psycopg.connect(self.dsn, autocommit=True)
        self.database = self.setup_connection.execute("SELECT current_database()").fetchone()[0]
        schema = self.sql.Identifier(self.schema)
        self.setup_connection.execute(self.sql.SQL("CREATE SCHEMA {}").format(schema))
        self.setup_connection.execute(self.sql.SQL("CREATE TABLE {}.ordinary (id integer)").format(schema))
        self.setup_connection.execute(self.sql.SQL("CREATE TYPE {}.order_state AS ENUM ('open')").format(schema))
        self.setup_connection.execute(self.sql.SQL("CREATE TABLE {}.typed (state {}.order_state)").format(schema, schema))
        self.setup_connection.execute(
            self.sql.SQL("CREATE TABLE {}.partitioned (id integer) PARTITION BY RANGE (id)").format(schema)
        )
        self.setup_connection.execute(
            self.sql.SQL(
                "CREATE FUNCTION {}.must_not_run() RETURNS integer LANGUAGE plpgsql VOLATILE "
                "AS $$ BEGIN RAISE EXCEPTION 'view executed'; END $$"
            ).format(schema)
        )
        self.setup_connection.execute(
            self.sql.SQL("CREATE VIEW {}.viewed AS SELECT {}.must_not_run() AS id").format(schema, schema)
        )
        self.setup_connection.execute(self.sql.SQL("CREATE TABLE {}.denied (id integer)").format(schema))
        self.setup_connection.execute(
            self.sql.SQL("CREATE MATERIALIZED VIEW {}.mat_populated AS SELECT id FROM {}.ordinary").format(schema, schema)
        )
        self.setup_connection.execute(
            self.sql.SQL("CREATE MATERIALIZED VIEW {}.mat_unpopulated AS SELECT id FROM {}.ordinary WITH NO DATA").format(
                schema, schema,
            )
        )

        extension_exists = self.setup_connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'file_fdw')"
        ).fetchone()[0]
        try:
            self.setup_connection.execute("CREATE EXTENSION IF NOT EXISTS file_fdw")
            self.file_fdw_created = not extension_exists
            self.setup_connection.execute(
                self.sql.SQL("CREATE SERVER {} FOREIGN DATA WRAPPER file_fdw").format(self.sql.Identifier(self.server))
            )
            self.setup_connection.execute(
                self.sql.SQL(
                    "CREATE FOREIGN TABLE {}.foreigned (id integer) SERVER {} "
                    "OPTIONS (filename {}, format 'csv')"
                ).format(
                    schema,
                    self.sql.Identifier(self.server),
                    self.sql.Literal(f"/tmp/{self.server}-must-not-be-opened.csv"),
                )
            )
            self.foreign_available = True
        except self.psycopg.Error:
            if self.file_fdw_created:
                self.setup_connection.execute("DROP EXTENSION IF EXISTS file_fdw CASCADE")
                self.file_fdw_created = False

        self.setup_connection.execute(
            self.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                self.sql.Identifier(self.reader_role), self.sql.Literal(self.reader_password),
            )
        )
        self.setup_connection.execute(
            self.sql.SQL("ALTER ROLE {} SET lock_timeout = '125ms'").format(self.sql.Identifier(self.reader_role))
        )
        self.setup_connection.execute(
            self.sql.SQL("ALTER ROLE {} SET default_transaction_isolation = 'repeatable read'").format(
                self.sql.Identifier(self.reader_role),
            )
        )
        self.setup_connection.execute(
            self.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                schema, self.sql.Identifier(self.reader_role),
            )
        )
        readable = ["ordinary", "typed", "partitioned", "viewed", "mat_populated", "mat_unpopulated"]
        if self.foreign_available:
            readable.append("foreigned")
        self.setup_connection.execute(
            self.sql.SQL("GRANT SELECT ON {} TO {}").format(
                self.sql.SQL(", ").join(
                    self.sql.SQL("{}.{}").format(schema, self.sql.Identifier(relation))
                    for relation in readable
                ),
                self.sql.Identifier(self.reader_role),
            )
        )

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.connections = []
        self.connections_lock = threading.Lock()
        self.connection_opened = threading.Event()
        self.service = PostgresService(
            self.temporary_directory.name,
            connect_factory=self._connect,
            lock_timeout_ms=3000,
        )
        self.service.save_profile("live", {
            "name": "Live disposable",
            "host": "localhost",
            "port": 5432,
            "dbname": self.database,
            "user": "postgres",
            "password": "unused",
            "sslmode": "prefer",
            "timeout": 5,
        })
        self.service.save_profile("reader", {
            "name": "Restricted disposable",
            "host": "localhost",
            "port": 5432,
            "dbname": self.database,
            "user": self.reader_role,
            "password": self.reader_password,
            "sslmode": "prefer",
            "timeout": 5,
        })

    def tearDown(self):
        try:
            for connection in self.connections:
                if not connection.closed:
                    try:
                        connection.rollback()
                    finally:
                        connection.close()
            self.setup_connection.execute(
                self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.schema))
            )
            self.setup_connection.execute(
                self.sql.SQL("DROP SERVER IF EXISTS {} CASCADE").format(self.sql.Identifier(self.server))
            )
            if self.file_fdw_created:
                self.setup_connection.execute("DROP EXTENSION IF EXISTS file_fdw CASCADE")
            self.setup_connection.execute(
                self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(self.reader_role))
            )
        finally:
            self.setup_connection.close()
            self.temporary_directory.cleanup()

    def _connect(self, **kwargs):
        connection_kwargs = {"row_factory": self.dict_row}
        if kwargs.get("user") == self.reader_role:
            connection_kwargs.update(user=self.reader_role, password=self.reader_password)
        tracked = TrackingConnection(self.psycopg.connect(self.dsn, **connection_kwargs))
        with self.connections_lock:
            self.connections.append(tracked)
        self.connection_opened.set()
        return tracked

    def target(self, relation, profile_id="live"):
        return {
            "profileId": profile_id,
            "database": self.database,
            "namespace": self.schema,
            "relation": relation,
        }

    def assert_connections_released(self, expected=None):
        self.assertTrue(self.connections)
        if expected is not None:
            self.assertEqual(len(self.connections), expected)
        self.assertTrue(all(connection.rollbacks == 1 and connection.closed for connection in self.connections))

    def wait_for_relation_lock(self, relation, mode, *, granted, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waiting = self.setup_connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_locks l
                    JOIN pg_catalog.pg_class c ON c.oid = l.relation
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s AND l.mode = %s AND l.granted = %s
                )
                """,
                (self.schema, relation, mode, granted),
            ).fetchone()[0]
            if waiting:
                return True
            time.sleep(0.01)
        return False

    def test_all_supported_kinds_include_unpopulated_materialized_view_and_release(self):
        expected = {
            "ordinary": "table",
            "partitioned": "partitioned_table",
            "viewed": "view",
            "mat_populated": "materialized_view",
            "mat_unpopulated": "materialized_view",
        }
        if self.foreign_available:
            expected["foreigned"] = "foreign_table"

        guard_xmins = []
        execute_statement = self.service._execute_statement

        def instrumented_execute(connection, query, params=()):
            execute_statement(connection, query, params)
            if query.startswith("PREPARE"):
                guard_xmins.append(self.setup_connection.execute(
                    "SELECT backend_xmin FROM pg_catalog.pg_stat_activity WHERE pid = %s",
                    (connection.info.backend_pid,),
                ).fetchone()[0])

        self.service._execute_statement = instrumented_execute
        with self.assertRaisesRegex(RuntimeError, "caller failure"):
            with self.service.verified_relation_catalog_snapshots([
                self.target(relation) for relation in reversed(expected)
            ]) as snapshots:
                descriptors = {item["relation"]: item["descriptor"] for item in snapshots}
                self.assertEqual({name: descriptor["kind"] for name, descriptor in descriptors.items()}, expected)
                self.assertFalse(descriptors["mat_unpopulated"]["materialized"]["populated"])
                relation_locks = self.setup_connection.execute(
                    """
                    SELECT c.relname, l.mode
                    FROM pg_catalog.pg_locks l
                    JOIN pg_catalog.pg_class c ON c.oid = l.relation
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = ANY(%s) AND l.granted
                    """,
                    (self.schema, list(expected)),
                ).fetchall()
                locked = {name for name, mode in relation_locks if mode == "AccessShareLock"}
                self.assertEqual(locked, set(expected))
                self.assertEqual(self.connections[0].execute("SHOW lock_timeout").fetchone()["lock_timeout"], "3s")
                raise RuntimeError("caller failure")

        self.assertEqual(guard_xmins, [None] * len(expected))
        self.assert_connections_released(expected=2)

    def test_repeatable_read_prepare_assigns_backend_xmin_but_show_and_set_local_do_not(self):
        connection = self.psycopg.connect(self.dsn)
        observer = self.psycopg.connect(self.dsn, autocommit=True)
        try:
            backend_pid = connection.info.backend_pid

            def backend_xmin():
                return observer.execute(
                    "SELECT backend_xmin FROM pg_catalog.pg_stat_activity WHERE pid = %s",
                    (backend_pid,),
                ).fetchone()[0]

            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self.assertIsNone(backend_xmin())
            self.assertIsInstance(connection.execute("SHOW lock_timeout").fetchone()[0], str)
            self.assertIsNone(backend_xmin())
            connection.execute("SET LOCAL lock_timeout = '3s'")
            self.assertIsNone(backend_xmin())
            connection.execute(
                self.sql.SQL("PREPARE xmin_guard AS SELECT NULL FROM {}.ordinary WHERE FALSE").format(
                    self.sql.Identifier(self.schema),
                )
            )
            self.assertIsNotNone(backend_xmin())
        finally:
            connection.rollback()
            connection.close()
            observer.close()

    def test_stricter_role_lock_timeout_is_retained(self):
        with self.service.verified_relation_catalog_snapshots([self.target("ordinary", "reader")]):
            self.assertEqual(len(self.connections), 2)
            self.assertEqual(self.connections[0].execute("SHOW lock_timeout").fetchone()["lock_timeout"], "125ms")
            self.assertEqual(self.connections[0].execute("SHOW transaction_isolation").fetchone()["transaction_isolation"], "read committed")
            self.assertEqual(self.connections[1].execute("SHOW transaction_isolation").fetchone()["transaction_isolation"], "repeatable read")

        self.assert_connections_released(expected=2)

    def test_prepare_defers_permission_authority_to_constant_false_execution_and_releases(self):
        with self.assertRaises(PostgresServiceError) as caught:
            with self.service.verified_relation_catalog_snapshots([self.target("denied", "reader")]):
                self.fail("a role without SELECT must not receive a descriptor")

        self.assertEqual(caught.exception.code, "introspection_failed")
        self.assertEqual(caught.exception.details["postgres"]["sqlstate"], "42501")
        self.assert_connections_released(expected=2)

    def test_ddl_committed_before_guard_lock_is_visible_in_snapshot(self):
        ddl = self.psycopg.connect(self.dsn)
        result = {}
        try:
            ddl.execute(
                self.sql.SQL("ALTER TABLE {}.ordinary ADD COLUMN committed_before_guard text").format(
                    self.sql.Identifier(self.schema),
                )
            )

            def inspect_after_ddl():
                try:
                    with self.service.verified_relation_catalog_snapshots([self.target("ordinary")]) as snapshots:
                        result["descriptor"] = snapshots[0]["descriptor"]
                except Exception as exc:
                    result["error"] = exc

            worker = threading.Thread(target=inspect_after_ddl)
            worker.start()
            self.assertTrue(self.connection_opened.wait(1))
            self.assertTrue(
                self.wait_for_relation_lock("ordinary", "AccessShareLock", granted=False),
                "relation guard did not request its lock behind the earlier DDL transaction",
            )
            self.assertTrue(worker.is_alive(), "relation guard did not wait for the earlier DDL transaction")
            ddl.commit()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            if "error" in result:
                raise result["error"]
            self.assertIn("committed_before_guard", [column["name"] for column in result["descriptor"]["columns"]])
            self.assert_connections_released(expected=2)
        finally:
            ddl.rollback()
            ddl.close()

    def test_ddl_after_guard_times_out_or_waits_until_context_release(self):
        blocking_started = threading.Event()
        blocking_finished = threading.Event()
        blocking_error = []

        with self.service.verified_relation_catalog_snapshots([self.target("ordinary")]):
            timeout_ddl = self.psycopg.connect(self.dsn)
            try:
                timeout_ddl.execute("SET LOCAL lock_timeout = '150ms'")
                with self.assertRaises(self.psycopg.errors.LockNotAvailable):
                    timeout_ddl.execute(
                        self.sql.SQL("ALTER TABLE {}.ordinary ADD COLUMN must_time_out integer").format(
                            self.sql.Identifier(self.schema),
                        )
                    )
                timeout_ddl.rollback()
            finally:
                timeout_ddl.close()

            def run_blocking_ddl():
                connection = self.psycopg.connect(self.dsn)
                try:
                    blocking_started.set()
                    connection.execute(
                        self.sql.SQL("ALTER TABLE {}.ordinary ADD COLUMN after_release integer").format(
                            self.sql.Identifier(self.schema),
                        )
                    )
                    connection.commit()
                except Exception as exc:
                    blocking_error.append(exc)
                finally:
                    connection.close()
                    blocking_finished.set()

            worker = threading.Thread(target=run_blocking_ddl)
            worker.start()
            self.assertTrue(blocking_started.wait(1))
            self.assertTrue(
                self.wait_for_relation_lock("ordinary", "AccessExclusiveLock", granted=False),
                "DDL did not reach the relation lock while the guard was active",
            )
            self.assertFalse(blocking_finished.is_set(), "DDL completed while the relation guard was active")

        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(blocking_error, [])
        self.assertTrue(blocking_finished.is_set())
        columns = self.setup_connection.execute(
            """
            SELECT attname
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = 'ordinary' AND a.attnum > 0 AND NOT a.attisdropped
            """,
            (self.schema,),
        ).fetchall()
        self.assertIn("after_release", [row[0] for row in columns])
        self.assertNotIn("must_time_out", [row[0] for row in columns])
        self.assert_connections_released(expected=2)

    def test_dependency_catalog_ddl_after_snapshot_is_a_subsequent_change_rejected_by_verification(self):
        with self.service.verified_relation_catalog_snapshots([self.target("typed")]) as snapshots:
            descriptor = snapshots[0]["descriptor"]
            source = {
                **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
                "snapshotVersion": 2,
                "columns": [snapshot_column(column) for column in descriptor["columns"]],
            }
            self.setup_connection.execute(
                self.sql.SQL("ALTER TYPE {}.order_state ADD VALUE 'closed'").format(self.sql.Identifier(self.schema))
            )

        self.assert_connections_released(expected=2)
        verification = self.service.verify_relation_source("live", source)

        self.assertEqual(verification["status"], "changed")
        self.assertFalse(verification["matches"])
        self.assertNotEqual(verification["expectedFingerprint"], verification["currentFingerprint"])
        self.assertEqual(len(self.connections), 3)
        self.assertTrue(self.connections[2].closed)

    def test_connected_database_mismatch_after_guards_rolls_back_and_closes(self):
        wrong_database = "template1" if self.database != "template1" else "postgres"
        connection_number = 0

        def mismatched_connect(**_kwargs):
            nonlocal connection_number
            connection_number += 1
            connection = self.psycopg.connect(
                self.dsn,
                dbname=self.database if connection_number == 1 else wrong_database,
                row_factory=self.dict_row,
            )
            tracked = TrackingConnection(connection)
            self.connections.append(tracked)
            return tracked

        self.service._connect_factory = mismatched_connect

        with self.assertRaises(PostgresServiceError) as caught:
            with self.service.verified_relation_catalog_snapshots([self.target("ordinary")]):
                self.fail("wrong connected database must not yield descriptors")

        self.assertEqual(caught.exception.code, "database_changed")
        self.assertEqual(connection_number, 2)
        self.assert_connections_released(expected=2)


if __name__ == "__main__":
    unittest.main()
