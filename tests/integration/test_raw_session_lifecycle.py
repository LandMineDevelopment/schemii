"""Observe abandoned raw locks/backends directly, without a masking raw request."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import time

import pytest
from psycopg import pq, sql

from schemii.common.postgres import PsycopgPostgresGateway
from schemii.common.postgres.console.raw import RawSession
from schemii.common.postgres.errors import PostgresConnectionCapacityError
from schemii.schemii.console import raw_session
from schemii.schemii.console.raw_session import SqlCreate
from psycopg.errors import QueryCanceled
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
    with closing(service), postgres_metadata.connection_factory() as observer:
        assert raw.transaction_status == "idle"
        # Metadata admission wraps the native connection. Use its forwarded
        # method: assigning an attribute only changes the wrapper, leaving DDL
        # uncommitted and invisible to the separately owned raw session.
        observer.set_autocommit(True)
        with observer.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE TABLE {}.idle_marker (id integer)").format(
                    sql.Identifier(gateway_target.namespace)
                )
            )
        assert observer.info.transaction_status == pq.TransactionStatus.IDLE
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

        asyncio.run(check())
    assert connection.closed
    assert gateway._connection_capacity._total == 0


@pytest.mark.parametrize("kind", ["sql", "copy_upload", "copy_download"])
def test_shutdown_cancel_before_wire_dispatch_never_starts_sql_or_copy(
    postgres_metadata,
    gateway_target,
    monkeypatch,
    kind,
):
    from schemii.common.postgres.console import raw as raw_driver

    gateway = PsycopgPostgresGateway()
    connection = gateway._connect(gateway_target.connection, retained=True)
    raw = RawSession(connection, gateway_target.namespace, autocommit=True)
    service, session = policy_fixture()
    session["raw"] = raw
    service.sessions[session["id"]] = session
    pid = raw.backend_pid
    with postgres_metadata.connection_factory() as observer:
        observer.set_autocommit(True)
        with observer.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE TABLE {}.copy_marker (id integer)").format(
                    sql.Identifier(gateway_target.namespace)
                )
            )
        paused = threading.Event()
        proceed = threading.Event()
        if kind == "sql":
            body = SqlCreate(sql="SELECT pg_sleep(30)", commit_mode="each_statement")
            execution = service.reserve(session, body)
            execute = raw.execute

            def gated_execute(statement, publish):
                paused.set()
                assert proceed.wait(2)
                return execute(statement, publish)

            monkeypatch.setattr(raw, "execute", gated_execute)

            def run():
                service.run(session, execution, body)
        else:
            dispatch = raw_driver._RawDispatchCursor._execute_send

            def gated_dispatch(cursor, *args, **kwargs):
                paused.set()
                assert proceed.wait(2)
                return dispatch(cursor, *args, **kwargs)

            monkeypatch.setattr(
                raw_driver._RawDispatchCursor, "_execute_send", gated_dispatch
            )
            service.claim(session)

            def run():
                try:
                    if kind == "copy_upload":
                        raw.copy_upload("COPY copy_marker FROM STDIN", [b"1\n"])
                    else:
                        list(raw.copy_download("COPY (SELECT pg_sleep(30)) TO STDOUT"))
                finally:
                    service.release(session)

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                operation = pool.submit(run)
                assert paused.wait(1)
                shutdown = pool.submit(service.close)
                assert raw._cancelled.wait(1)
                proceed.set()
                if kind == "sql":
                    operation.result(timeout=2)
                    assert execution["status"] == "cancelled"
                    assert execution["sqlstate"] == "57014"
                else:
                    with pytest.raises(QueryCanceled):
                        operation.result(timeout=2)
                shutdown.result(timeout=2)
            with observer.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}.copy_marker").format(
                        sql.Identifier(gateway_target.namespace)
                    )
                )
                assert cursor.fetchone()["n"] == 0
                cursor.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE pid = %s", (pid,)
                )
                assert cursor.fetchone()["n"] == 0
        finally:
            proceed.set()
            service.close()
    assert not session["operation"].locked()
    assert service.sessions == {}
    assert gateway._connection_capacity._total == 0


@pytest.mark.parametrize("mode", ["each_statement", "whole_run"])
def test_raw_active_cancel_and_next_operation_recover_without_statement_timeout(
    postgres_metadata,
    gateway_target,
    mode,
):
    gateway = PsycopgPostgresGateway()
    connection = gateway._connect(gateway_target.connection, retained=True)
    raw = RawSession(connection, gateway_target.namespace, autocommit=True)
    service, session = policy_fixture()
    session["raw"] = raw
    service.sessions[session["id"]] = session
    try:
        with postgres_metadata.connection_factory() as observer:
            with observer.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE TABLE {}.cancel_marker (id integer)").format(
                        sql.Identifier(gateway_target.namespace)
                    )
                )
            observer.commit()
        assert raw.execute("SHOW statement_timeout", lambda _: None)["results"][0][
            "rows"
        ] == [["0"]]
        statement = (
            "INSERT INTO cancel_marker VALUES (1); SELECT pg_sleep(30)"
            if mode == "whole_run"
            else "SELECT pg_sleep(30)"
        )
        body = SqlCreate(sql=statement, commit_mode=mode)
        execution = service.reserve(session, body)
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(service.run, session, execution, body)
            with postgres_metadata.connection_factory() as observer:
                observer.set_autocommit(True)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with observer.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE pid = %s",
                            (raw.backend_pid,),
                        )
                        row = cursor.fetchone()
                    if row and row["wait_event"] == "PgSleep":
                        break
                    time.sleep(0.005)
                else:
                    pytest.fail("raw statement did not enter pg_sleep")
            service.cancel(session)
            operation.result(timeout=2)
        assert execution["status"] == "cancelled"
        assert raw.transaction_status == "idle"
        assert not session["operation"].locked()
        with postgres_metadata.connection_factory() as observer:
            with observer.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}.cancel_marker").format(
                        sql.Identifier(gateway_target.namespace)
                    )
                )
                assert cursor.fetchone()["n"] == 0
        next_body = SqlCreate(sql="SELECT 1", commit_mode="each_statement")
        fresh = service.reserve(session, next_body)
        service.run(session, fresh, next_body)
        assert fresh["status"] == "succeeded"
        assert fresh["results"][0]["rows"] == [["1"]]
    finally:
        service.close()
    assert gateway._connection_capacity._total == 0
