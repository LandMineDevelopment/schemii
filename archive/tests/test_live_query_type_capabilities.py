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
class LiveQueryTypeCapabilityTests(unittest.TestCase):
    def test_domains_enum_and_exact_custom_aggregate_are_catalog_derived(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        schema = f"schemer_caps_{uuid.uuid4().hex[:10]}"
        setup = psycopg.connect(dsn, autocommit=True)
        temporary_directory = tempfile.TemporaryDirectory()
        try:
            database = setup.execute("SELECT current_database()").fetchone()[0]
            setup.execute(f'CREATE SCHEMA "{schema}"')
            setup.execute(f'CREATE DOMAIN "{schema}".positive_numeric AS numeric CHECK (VALUE > 0)')
            setup.execute(f'CREATE DOMAIN "{schema}".nested_numeric AS "{schema}".positive_numeric')
            setup.execute(f'CREATE DOMAIN "{schema}".event_time AS timestamp without time zone')
            setup.execute(f'CREATE DOMAIN "{schema}".short_label AS varchar(40)')
            setup.execute(f'CREATE TYPE "{schema}".mood AS ENUM (\'calm\', \'busy\')')
            setup.execute(f'''CREATE FUNCTION "{schema}".mood_min(state "{schema}".mood, value "{schema}".mood)
                              RETURNS "{schema}".mood LANGUAGE sql IMMUTABLE
                              RETURN CASE WHEN state::text <= value::text THEN state ELSE value END''')
            setup.execute(f'''CREATE AGGREGATE "{schema}".min("{schema}".mood)
                              (SFUNC = "{schema}".mood_min, STYPE = "{schema}".mood)''')
            setup.execute(f'''CREATE TABLE "{schema}".events (
                              amount "{schema}".nested_numeric,
                              occurred_at "{schema}".event_time,
                              state "{schema}".mood,
                              label varchar(40),
                              domain_label "{schema}".short_label)''')
            service = PostgresService(temporary_directory.name, connect_factory=lambda **kwargs: psycopg.connect(dsn, row_factory=dict_row))
            service.save_profile("live", {"name": "Live", "host": "localhost", "port": 5432, "dbname": database, "user": "live", "password": "unused", "sslmode": "prefer", "timeout": 5})
            descriptor = service.inspect_relation("live", database, schema, "events")
            columns = {column["name"]: column for column in descriptor["columns"]}
            self.assertEqual(columns["amount"]["capabilities"]["type"]["name"], "numeric")
            self.assertTrue(columns["amount"]["capabilities"]["numeric"])
            self.assertEqual(columns["occurred_at"]["capabilities"]["temporal"], "timestamp")
            self.assertTrue(columns["state"]["capabilities"]["groupable"])
            for name in ("label", "domain_label"):
                capabilities = columns[name]["capabilities"]
                self.assertTrue(capabilities["groupable"])
                self.assertTrue(capabilities["distinct"])
                self.assertTrue(capabilities["sortable"])
                equality = next(item for item in capabilities["filterOperators"] if item["name"] == "eq")
                self.assertEqual(equality["operator"]["namespace"], "pg_catalog")
                self.assertEqual(equality["operator"]["name"], "=")
            minimum = next(item for item in columns["state"]["capabilities"]["aggregates"] if item["name"] == "minimum")
            self.assertEqual(minimum["aggregate"]["namespace"], schema)
        finally:
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            setup.close()
            temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
