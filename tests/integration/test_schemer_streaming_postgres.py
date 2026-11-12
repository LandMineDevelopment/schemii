"""Opt-in stream ownership/snapshot oracles on an explicitly disposable source.

These compose the real Console service and PostgreSQL gateway, without starting
an application, touching user workspaces, or using the metadata endpoint as data.
"""

import asyncio
from contextlib import contextmanager
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from schemii.common.api.errors import ApiProblem
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.postgres.gateway import PsycopgPostgresGateway
from schemii.schemii.console.repository import InMemoryConsoleRepository
from schemii.schemii.console.service import ConsoleService
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository
from schemii.schemer import streaming


@pytest.fixture
def source_runtime():
    dsn = os.environ.get("SCHEMII_TEST_SOURCE_DSN")
    password = os.environ.get("SCHEMII_TEST_SOURCE_PASSWORD")
    if not dsn or not password:
        pytest.skip("requires explicitly disposable SCHEMII_TEST_SOURCE_DSN/PASSWORD")
    parameters = conninfo_to_dict(dsn)
    namespace = "schemer_stream_" + uuid4().hex
    owner = "stream_" + uuid4().hex
    gateway = PsycopgPostgresGateway(
        maximum_connections=1, maximum_connections_per_identity=1
    )
    connections = ConnectionService(InMemoryConnectionRepository(), ())
    profile = connections.create(
        owner,
        PostgresConnectionCreate(
            name="Owned stream source",
            host=parameters.get("host") or parameters.get("hostaddr") or "localhost",
            port=int(parameters.get("port") or 5432),
            database=parameters.get("dbname") or parameters["user"],
            username=parameters["user"],
            password=password,
            ssl_mode="prefer",
            connect_timeout=2,
        ),
    )
    console = ConsoleService(
        repository=InMemoryConsoleRepository(),
        connection_access=connections.for_product("schemer"),
        postgres=gateway,
        workspaces=InMemoryWorkspaceRepository(),
        row_page_size=1,
    )

    @contextmanager
    def writer():
        with psycopg.connect(
            dsn,
            password=password,
            autocommit=True,
            options="-c statement_timeout=5000 -c lock_timeout=1000",
        ) as connection:
            yield connection

    created = False
    try:
        with writer() as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace))
            )
            created = True
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {}.events(id integer PRIMARY KEY, amount integer)"
                ).format(sql.Identifier(namespace))
            )
            connection.execute(
                sql.SQL("INSERT INTO {}.events VALUES (1,10),(2,20)").format(
                    sql.Identifier(namespace)
                )
            )
        yield SimpleNamespace(
            console=console,
            postgres=gateway,
            writer=writer,
            namespace=namespace,
            owner=owner,
            model=SimpleNamespace(
                connection_id=profile.id, database=profile.database, namespace=namespace
            ),
        )
    finally:
        console.close()
        if created:
            with writer() as connection:
                connection.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}.events").format(
                        sql.Identifier(namespace)
                    )
                )
                connection.execute(
                    sql.SQL("DROP SCHEMA {}").format(sql.Identifier(namespace))
                )
        assert gateway.observation_snapshot()["ordinary"]["permits"] == 0


def plans(source, *, sleep=0.01):
    with source.writer() as connection:
        table = (
            sql.SQL("{}.events")
            .format(sql.Identifier(source.namespace))
            .as_string(connection)
        )
    return [
        {
            "tileId": "detail",
            "plan": {
                "sql": f"SELECT id, amount, pg_sleep({sleep}) FROM {table} ORDER BY id"
            },
        },
        {
            "tileId": "kpi",
            "scalarKpi": True,
            "plan": {"sql": f"SELECT COUNT(*) FROM {table}"},
        },
    ]


def assert_retired(source):
    observed = source.console.observation_snapshot()
    for key in [
        "readSessionHandles",
        "resultPageTokens",
        "transientResults",
        "nativeCursors",
    ]:
        assert observed[key] == 0, (key, observed)
    assert source.postgres.observation_snapshot()["ordinary"]["permits"] == 0


def test_real_stream_prioritizes_kpi_and_keeps_one_snapshot_across_sweeps(
    source_runtime,
):
    source = source_runtime

    async def run():
        seen = []
        async for event in streaming.events(
            source, source.owner, source.model, plans(source)
        ):
            seen.append(event)
            if event["type"] == "rows" and event["tileId"] == "kpi":
                assert event["rows"] == [[2]]
                assert not [
                    item
                    for item in seen
                    if item["type"] == "rows" and item["tileId"] == "detail"
                ]
                assert (
                    source.postgres.observation_snapshot()["ordinary"][
                        "retainedPermits"
                    ]
                    == 1
                )
                assert source.console.observation_snapshot()["readSessionHandles"] == 1
                with source.writer() as connection:
                    connection.execute(
                        sql.SQL("UPDATE {}.events SET amount=100").format(
                            sql.Identifier(source.namespace)
                        )
                    )
            if event["type"] == "complete" and event["tileId"] == "detail":
                assert_retired(source)
        detail = [
            row[:2]
            for item in seen
            if item["type"] == "rows" and item["tileId"] == "detail"
            for row in item["rows"]
        ]
        assert detail == [[1, 10], [2, 20]]
        assert len([item for item in seen if item["type"] == "execution"]) == 1
        assert not [item for item in seen if item["type"] == "error"]

    asyncio.run(run())
    assert_retired(source)


@pytest.mark.parametrize("termination", ["cancel", "revoke"])
def test_real_stream_stops_sleeping_detail_after_completed_kpi_and_returns_admission(
    source_runtime, termination
):
    source = source_runtime
    revoked = False

    def check():
        if revoked:
            raise ApiProblem(403, "report_access_revoked", "Report access was removed.")

    source.report_access = SimpleNamespace(check=check)

    async def run():
        nonlocal revoked
        seen, pid = [], None

        async def consume():
            nonlocal pid
            async for event in streaming.events(
                source, source.owner, source.model, plans(source, sleep=5)
            ):
                seen.append(event)
                if event["type"] == "complete" and event["tileId"] == "kpi":
                    execution_id = next(
                        item["executionId"]
                        for item in seen
                        if item["type"] == "execution"
                    )
                    # Inspect only this test's exact retained runtime; public
                    # receipts intentionally omit a completed run's backend PID.
                    with source.console._transient_results_lock:
                        pid = source.console._active_read_sessions[
                            execution_id
                        ].postgres.backend_pid

        work = asyncio.create_task(consume())
        deadline = time.monotonic() + 3
        sleeping = False
        while time.monotonic() < deadline and not work.done():
            await asyncio.sleep(0.01)
            if pid is not None:
                with source.writer() as connection:
                    row = connection.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (pid,)
                    ).fetchone()
                    sleeping = bool(row and row[0] == "PgSleep")
            if sleeping:
                break
        try:
            assert sleeping, "expected the exact owned detail backend in PgSleep"
            assert any(
                item["type"] == "complete" and item["tileId"] == "kpi" for item in seen
            )
            if termination == "cancel":
                work.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(work, 3)
            else:
                revoked = True
                await asyncio.wait_for(work, 3)
                assert (
                    next(item for item in seen if item["type"] == "error")["code"]
                    == "report_access_revoked"
                )
            assert not [
                item
                for item in seen
                if item["type"] == "rows" and item["tileId"] == "detail"
            ]
        finally:
            if not work.done():
                work.cancel()
                try:
                    await work
                except asyncio.CancelledError:
                    pass
        assert_retired(source)

    asyncio.run(run())
