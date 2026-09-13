"""Regression checks for raw COPY ownership and release on interrupted downloads."""
from collections import OrderedDict
import threading

import pytest

from schemii.common.api.errors import ApiProblem
from schemii.schemii.console.raw_session import RawSessionService, SqlCreate


class CopySource:
    transaction_status = "idle"

    def __init__(self, error=None):
        self.error = error
        self.closed_streams = 0
        self.executed = []

    def execute(self, sql, publish):
        self.executed.append(sql)
        if sql == "BEGIN":
            self.transaction_status = "intrans"
        return {"results": [], "errorMessage": None, "sqlstate": None}

    def copy_download(self, sql):
        try:
            if self.error:
                raise self.error
            yield b"first row\n"
            yield b"second row\n"
        finally:
            self.closed_streams += 1

    def cancel(self):
        pass


def copy_fixture(error=None):
    manager = RawSessionService(None, None, None)
    session = {
        "raw": CopySource(error), "operation": threading.Lock(),
        "signalLock": threading.RLock(), "status": "open",
        "tickets": OrderedDict(), "currentExecutionId": None, "revision": 1,
        "pendingStatements": [], "pendingStatementsTruncated": False, "transactionStartedAt": None,
    }
    ticket = {
        "id": "cpy_test", "sql": "COPY (SELECT 1) TO STDOUT",
        "direction": "download", "status": "ready", "errorMessage": None,
        "commitMode": "manual",
    }
    return manager, session, ticket


def test_copy_response_closed_before_first_byte_releases_session():
    manager, session, ticket = copy_fixture()
    stream = manager.download(session, ticket)
    stream.close()
    assert not session["operation"].locked()
    assert session["status"] == "open"
    assert ticket["status"] == "failed"
    assert ticket["errorMessage"]


def test_copy_response_interrupted_between_chunks_closes_postgres_stream():
    manager, session, ticket = copy_fixture()
    stream = manager.download(session, ticket)
    assert next(stream) == b"first row\n"
    stream.close()
    assert session["raw"].closed_streams == 1
    assert not session["operation"].locked()
    assert ticket["status"] == "failed"


def test_copy_postgres_error_is_visible_and_releases_session():
    manager, session, ticket = copy_fixture(ValueError("permission denied for table private"))
    stream = manager.download(session, ticket)
    with pytest.raises(ValueError, match="permission denied"):
        next(stream)
    assert not session["operation"].locked()
    assert ticket["status"] == "failed"
    assert "permission denied" in ticket["errorMessage"]


@pytest.mark.parametrize(("sql", "direction"), [
    ("DELETE FROM events", "download"),
    ("COPY events FROM STDIN", "download"),
    ("COPY events TO STDOUT", "upload"),
    ("COPY events TO '/tmp/events.csv'", "download"),
    ("COPY events TO STDOUT; DELETE FROM events", "download"),
])
def test_copy_ticket_rejects_wrong_protocol_without_executing(sql, direction):
    manager, session, _ = copy_fixture()
    with pytest.raises(ApiProblem):
        manager.ticket(session, SqlCreate(sql=sql), direction)
    assert session["raw"].executed == []
    assert not session["operation"].locked()


def test_copy_response_disconnect_cancels_blocked_read_and_releases_session():
    import anyio
    from schemii.schemii.console.raw_session import CopyStreamingResponse

    started, cancelled = threading.Event(), threading.Event()
    manager, session, ticket = copy_fixture()

    def blocking_copy(sql):
        started.set()
        assert cancelled.wait(2), "disconnect did not cancel the PostgreSQL read"
        raise ValueError("canceling statement due to user request")
        yield b"unreachable"

    session["raw"].copy_download = blocking_copy
    session["raw"].cancel = cancelled.set
    response = CopyStreamingResponse(manager.download(session, ticket))

    async def run():
        async def receive():
            assert await anyio.to_thread.run_sync(started.wait, 2)
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        with pytest.raises(ExceptionGroup):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    anyio.run(run)
    assert cancelled.is_set()
    assert not session["operation"].locked()
    assert ticket["status"] == "failed"


def test_cancelled_upload_waits_for_write_before_closing_protocol(monkeypatch):
    import anyio
    from types import SimpleNamespace
    from schemii.schemii.console import raw_session

    started, proceed, finished = threading.Event(), threading.Event(), threading.Event()
    manager, session, ticket = copy_fixture()
    ticket.update(sql="COPY events FROM STDIN", direction="upload")
    session["tickets"][ticket["id"]] = ticket
    cleanups = []

    class Transfer:
        def __enter__(self):
            return self

        def write(self, chunk):
            started.set()
            assert proceed.wait(2)
            finished.set()

        def __exit__(self, *error):
            cleanups.append(("exit", finished.is_set()))

    class Cursor:
        statusmessage = "COPY 1"

        def copy(self, sql):
            return Transfer()

        def close(self):
            cleanups.append(("close", finished.is_set()))

    session["raw"].connection = SimpleNamespace(cursor=Cursor)
    monkeypatch.setattr(raw_session, "owned", lambda *args: session)
    monkeypatch.setattr(raw_session, "service", lambda request: manager)

    class Request:
        async def stream(self):
            yield b"1\n"
            await anyio.sleep_forever()

    async def run():
        async with anyio.create_task_group() as group:
            with anyio.CancelScope() as scope:
                async def cancel():
                    assert await anyio.to_thread.run_sync(started.wait, 2)
                    scope.cancel()
                    proceed.set()
                group.start_soon(cancel)
                await raw_session.upload_copy("ws", "session", ticket["id"], Request(), None)

    anyio.run(run)
    assert cleanups == [("exit", True), ("close", True)]
    assert ticket["status"] == "failed"
    assert not session["operation"].locked()
