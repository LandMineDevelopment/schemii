"""Cancel real PostgreSQL work without signals to retained/reused backends."""

from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from schemii.common.postgres.console.gateway import (
    PsycopgConsoleReadSession,
    execute_console_statements,
)
from schemii.common.postgres.errors import PostgresConsoleCancelledError
from schemii.common.query_executions.cancellation import QueryCancellationRegistry


@pytest.mark.parametrize("paging", [False, True])
def test_stop_interrupts_real_pg_sleep(postgres_metadata, paging):
    registry = QueryCancellationRegistry()
    with postgres_metadata.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '5s'")
        session = (
            PsycopgConsoleReadSession(
                connection, connection.info.backend_pid,
                ["SELECT pg_sleep(10)"], page_memory_bytes=4096,
            ) if paging else None
        )

        def run():
            with registry.scope(postgres_metadata.owner_id, "chat", "turn"):
                if session is not None:
                    return session.page(0, 0, 1)
                return execute_console_statements(connection, ["SELECT pg_sleep(10)"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            with postgres_metadata.connection_factory() as observer:
                observer.autocommit = True
                deadline = time.monotonic() + 3
                sleeping = False
                while time.monotonic() < deadline:
                    with observer.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE pid = %s",
                            (connection.info.backend_pid,),
                        )
                        row = cursor.fetchone()
                    if row and row["wait_event"] == "PgSleep":
                        sleeping = True
                        break
                    time.sleep(0.01)
                assert sleeping, "query never entered pg_sleep"
            started = time.monotonic()
            registry.cancel(postgres_metadata.owner_id, "chat", "turn")
            with pytest.raises(PostgresConsoleCancelledError):
                future.result(timeout=2)
            assert time.monotonic() - started < 2
        connection.rollback()
        # A late stop for the old scope must not poison new work on this backend.
        registry.cancel(postgres_metadata.owner_id, "chat")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS healthy")
            assert cursor.fetchone()["healthy"] == 1
