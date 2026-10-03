"""The synthetic result executor must retain its PostgreSQL safety contract."""

import time
import uuid

from psycopg.errors import QueryCanceled, ReadOnlySqlTransaction
import pytest

from tests.integration.sql_oracles import execute as execute


def test_result_oracle_uses_read_only_catalog_utc_and_bounded_statements(execute):
    assert execute(
        "SELECT current_setting('transaction_read_only'), "
        "current_setting('search_path'), current_setting('TimeZone'), "
        "current_setting('statement_timeout'), "
        "pg_typeof(NULL::date)::text, pg_typeof(NULL::numeric)::text"
    ) == [["on", "pg_catalog", "UTC", "5s", "date", "numeric"]]


def test_result_oracle_refuses_persistent_schema_writes(execute, postgres_metadata):
    namespace = "oracle_guard_" + uuid.uuid4().hex
    with pytest.raises(ReadOnlySqlTransaction):
        execute(f"CREATE SCHEMA {namespace}")
    with postgres_metadata.connection_factory() as observer:
        with observer.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s", (namespace,)
            )
            assert cursor.fetchone() is None


def test_result_oracle_cancels_overdue_statements(execute):
    assert execute("SELECT set_config('statement_timeout', '50ms', true)") == [["50ms"]]
    started = time.monotonic()
    with pytest.raises(QueryCanceled) as cancelled:
        execute("SELECT pg_sleep(10)")
    assert cancelled.value.sqlstate == "57014"
    assert time.monotonic() - started < 2
