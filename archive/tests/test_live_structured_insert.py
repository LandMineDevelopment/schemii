import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import PostgresService


@unittest.skipUnless(os.environ.get("SCHEMII_TEST_PG17_DSN"), "SCHEMII_TEST_PG17_DSN is not configured")
class LiveStructuredInsertSemanticsTests(unittest.TestCase):
    def test_partition_types_generation_defaults_constraints_trigger_and_rls(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        suffix = uuid.uuid4().hex[:10]
        schema = f"schemii_insert_{suffix}"
        role = f"schemii_insert_role_{suffix}"
        setup = psycopg.connect(dsn, autocommit=True)
        temporary_directory = tempfile.TemporaryDirectory()
        try:
            database = setup.execute("SELECT current_database()").fetchone()[0]
            setup.execute(f'CREATE ROLE "{role}"')
            setup.execute(f'CREATE SCHEMA "{schema}"')
            setup.execute(f'CREATE TYPE "{schema}".event_state AS ENUM (\'new\', \'ready\')')
            setup.execute(f'CREATE DOMAIN "{schema}".positive_integer AS integer CHECK (VALUE > 0)')
            setup.execute(f'''CREATE TABLE "{schema}".events (
                id bigint GENERATED ALWAYS AS IDENTITY,
                bucket integer NOT NULL,
                state "{schema}".event_state NOT NULL DEFAULT 'new',
                amount "{schema}".positive_integer NOT NULL,
                label text NOT NULL DEFAULT 'default-label',
                label_length integer GENERATED ALWAYS AS (length(label)) STORED,
                PRIMARY KEY (bucket, id), CHECK (label <> '')
            ) PARTITION BY RANGE (bucket)''')
            setup.execute(f'CREATE TABLE "{schema}".events_low PARTITION OF "{schema}".events FOR VALUES FROM (0) TO (100)')
            setup.execute(f'CREATE TABLE "{schema}".audit (event_id bigint, label text)')
            setup.execute(f'''CREATE FUNCTION "{schema}".audit_event() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN INSERT INTO "{schema}".audit VALUES (NEW.id, NEW.label); RETURN NEW; END
            $$''')
            setup.execute(f'CREATE TRIGGER audit_event AFTER INSERT ON "{schema}".events_low FOR EACH ROW EXECUTE FUNCTION "{schema}".audit_event()')
            setup.execute(f'CREATE TABLE "{schema}".tenant_events (tenant_id integer, label text NOT NULL)')
            setup.execute(f'ALTER TABLE "{schema}".tenant_events ENABLE ROW LEVEL SECURITY')
            setup.execute(f'''CREATE POLICY tenant_insert ON "{schema}".tenant_events FOR INSERT TO "{role}"
                              WITH CHECK (tenant_id = current_setting('schemii.tenant_id')::integer)''')
            setup.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            setup.execute(f'GRANT INSERT, SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
            setup.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')

            service = PostgresService(
                temporary_directory.name,
                connect_factory=lambda **kwargs: psycopg.connect(dsn, row_factory=dict_row),
            )
            service.save_profile("live", {
                "name": "Live disposable", "host": "localhost", "port": 5432, "dbname": database,
                "user": role, "password": "unused", "sslmode": "prefer", "timeout": 5,
            })
            connection = psycopg.connect(dsn, row_factory=dict_row)
            try:
                connection.execute(f'SET ROLE "{role}"')
                partition_target = service._inspect_ai_insert_target(connection, database, schema, "events", ["bucket", "amount"])
                self.assertEqual(partition_target["kind"], "partitioned_table")
                self.assertTrue(partition_target["catalog"]["triggers"])
                self.assertTrue(any(item["kind"] == "e" for item in partition_target["catalog"]["types"]))
                self.assertTrue(any(item["kind"] == "d" for item in partition_target["catalog"]["types"]))
                connection.execute(
                    f'''INSERT INTO "{schema}".events (bucket, amount)
                        SELECT bucket, amount FROM pg_catalog.jsonb_populate_recordset(
                            NULL::"{schema}".events, %s::jsonb) AS input''',
                    (json.dumps([{"bucket": 10, "amount": 2}]),),
                )
                row = connection.execute(f'SELECT state::text, label, label_length FROM "{schema}".events_low').fetchone()
                self.assertEqual(tuple(row.values()), ("new", "default-label", 13))
                self.assertEqual(connection.execute(f'SELECT count(*) AS count FROM "{schema}".audit').fetchone()["count"], 1)
                connection.commit()
                connection.execute(f'SET ROLE "{role}"')
                with self.assertRaises(psycopg.errors.GeneratedAlways):
                    connection.execute(
                        f'INSERT INTO "{schema}".events (id, bucket, amount) VALUES (%s, %s, %s)', (99, 11, 1),
                    )
                connection.rollback()
                connection.execute(f'SET ROLE "{role}"')
                with self.assertRaises(psycopg.errors.CheckViolation):
                    connection.execute(
                        f'INSERT INTO "{schema}".events (bucket, amount) VALUES (%s, %s)', (11, -1),
                    )
                connection.rollback()
                connection.execute(f'SET ROLE "{role}"')
                rls_target = service._inspect_ai_insert_target(connection, database, schema, "tenant_events", ["tenant_id", "label"])
                self.assertTrue(rls_target["catalog"]["policies"])
                connection.execute("SELECT pg_catalog.set_config('schemii.tenant_id', %s, false)", ("7",))
                connection.execute(f'INSERT INTO "{schema}".tenant_events VALUES (%s, %s)', (7, "allowed"))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(f'INSERT INTO "{schema}".tenant_events VALUES (%s, %s)', (8, "denied"))
                connection.rollback()
            finally:
                connection.close()
        finally:
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            setup.execute(f'DROP ROLE IF EXISTS "{role}"')
            setup.close()
            temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
