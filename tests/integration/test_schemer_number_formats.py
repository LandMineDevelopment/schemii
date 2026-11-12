"""Disposable PostgreSQL wire/CSV precision oracle; no application deployment."""

import csv
import io

import pytest

from schemii.common.postgres.console.gateway import (
    PsycopgConsoleReadSession,
    execute_console_statements,
)
from schemii.schemer.streaming import _csv_value


@pytest.mark.parametrize("mode", ["stream", "bounded"])
def test_sql_scalar_precision_typed_json_arrays_and_full_raw_csv(
    postgres_metadata, mode
):
    query = "SELECT 9007199254740993::bigint AS big, -9007199254740993::bigint AS negative, -12345678901234567890.123456789::numeric AS exact, '9007199254740993'::jsonb AS json_scalar, ARRAY[9007199254740993::bigint] AS items, '-1;=2'::text AS hostile"
    with postgres_metadata.connection_factory() as connection:
        session = None
        try:
            if mode == "stream":
                session = PsycopgConsoleReadSession(
                    connection, 1, [query], page_memory_bytes=4096
                )
                result = session.results[0]
                rows = session.page(0, 0, 10)
                assert session.export_page(0, 0, 10) == rows
            else:
                (result,) = execute_console_statements(connection, [query])
                rows = result.rows
            (row,) = rows
            assert row[:3] == (
                "9007199254740993",
                "-9007199254740993",
                "-12345678901234567890.123456789",
            )
            assert row[3] == 9007199254740993 and type(row[3]) is int
            assert row[4] == [9007199254740993]
            output = io.StringIO()
            csv.writer(output).writerow(
                [
                    _csv_value(value, column.data_type)
                    for value, column in zip(row, result.columns)
                ]
            )
            (csv_row,) = list(csv.reader(io.StringIO(output.getvalue())))
            assert csv_row[:3] == list(row[:3]) and csv_row[-1] == "'-1;=2"
        finally:
            if session:
                session.close()
            else:
                connection.rollback()
