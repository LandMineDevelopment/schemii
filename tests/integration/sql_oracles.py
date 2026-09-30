"""Run synthetic SQL through the existing disposable PostgreSQL fixture.

Each test owns one connection and read-only transaction. CTEs own all source
rows: there are no source tables, app fixtures or additional schemas to clean.
The restricted search path prevents a missing CTE from reading a shared table.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from psycopg.rows import tuple_row

from tests.integration.postgres_fixture import PostgresMetadataHarness


@pytest.fixture
def execute(
    postgres_metadata: PostgresMetadataHarness,
) -> Iterator[Callable[[str], list[list[Any]]]]:
    with postgres_metadata.connection_factory() as connection:
        try:
            with connection.cursor(row_factory=tuple_row) as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL search_path = pg_catalog")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute("SET LOCAL TIME ZONE 'UTC'")

                def run(statement: str) -> list[list[Any]]:
                    cursor.execute(statement)
                    return [list(row) for row in cursor.fetchall()]

                yield run
        finally:
            # Roll back even when an assertion/query fails; then close the owned
            # connection rather than retaining a cursor or result snapshot.
            connection.rollback()
