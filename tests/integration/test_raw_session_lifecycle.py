"""Observe abandoned raw locks/backends directly, without a masking raw request."""

import asyncio

import pytest
from psycopg import sql

from schemii.common.postgres import PsycopgPostgresGateway
from schemii.common.postgres.console.raw import RawSession
from schemii.common.postgres.errors import PostgresConnectionCapacityError
from schemii.schemii.console import raw_session
from tests.integration import test_postgres_gateway_execution as gateway_execution
from tests.test_console import console_client
from tests.test_raw_console import policy_fixture

gateway_target = gateway_execution.gateway_target


def test_lifespan_expiry_rolls_back_releases_lock_backend_and_admission(
    postgres_metadata,
    gateway_target,
    monkeypatch,
):
    api, _ = console_client()
    service = api.app.state.raw_console
    gateway = PsycopgPostgresGateway(
        maximum_connections=2,
        maximum_connections_per_identity=2,
        connection_acquire_timeout=0.01,
    )
    connection = gateway._connect(gateway_target.connection, retained=True)
    raw = RawSession(connection, gateway_target.namespace, autocommit=True)
    _, session = policy_fixture()
    session.update(id="raw_abandoned", raw=raw, used=0)
    service.sessions[session["id"]] = session
    pid = raw.backend_pid
    with postgres_metadata.connection_factory() as observer:
        observer.autocommit = True
        with observer.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE TABLE {}.idle_marker (id integer)").format(
                    sql.Identifier(gateway_target.namespace)
                )
            )
        receipt = raw.execute(
            "BEGIN; INSERT INTO idle_marker VALUES (1); LOCK TABLE idle_marker IN ACCESS EXCLUSIVE MODE",
            lambda _: None,
        )
        assert receipt["errorMessage"] is None
        assert raw.transaction_status == "intrans"
        with pytest.raises(PostgresConnectionCapacityError):
            gateway._connect(gateway_target.connection, retained=True)
        with observer.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS n FROM pg_locks WHERE pid = %s AND mode = 'AccessExclusiveLock'",
                (pid,),
            )
            assert cursor.fetchone()["n"] == 1
        monkeypatch.setattr(service, "_clock", lambda: 1800)
        monkeypatch.setattr(raw_session, "RAW_SESSION_SWEEP_SECONDS", 0.005)

        async def check():
            async with api.app.router.lifespan_context(api.app):
                # Only a separate observer reads the database. No raw get/list/
                # activity action can accidentally trigger request-driven reap.
                for _ in range(200):
                    with observer.cursor() as cursor:
                        cursor.execute(
                            "SELECT count(*) AS n FROM pg_stat_activity WHERE pid = %s",
                            (pid,),
                        )
                        if cursor.fetchone()["n"] == 0:
                            break
                    await asyncio.sleep(0.005)
                else:
                    pytest.fail(
                        "lifespan maintenance did not release abandoned backend"
                    )
                assert session["id"] not in service.sessions
                with observer.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) AS n FROM pg_locks WHERE pid = %s", (pid,)
                    )
                    assert cursor.fetchone()["n"] == 0
                    cursor.execute(
                        sql.SQL("SELECT count(*) AS n FROM {}.idle_marker").format(
                            sql.Identifier(gateway_target.namespace)
                        )
                    )
                    assert cursor.fetchone()["n"] == 0
                fresh = gateway._connect(gateway_target.connection, retained=True)
                try:
                    with fresh.cursor() as cursor:
                        cursor.execute("SELECT 1 AS healthy")
                        assert cursor.fetchone()["healthy"] == 1
                finally:
                    fresh.close()

        try:
            asyncio.run(check())
        finally:
            service.close()
    assert connection.closed
    assert gateway._connection_capacity._total == 0
