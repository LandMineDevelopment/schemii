from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from schemii.common.postgres.console.gateway import (
    PsycopgConsoleReadSession,
    PsycopgConsoleTransaction,
    execute_console_statements,
)
from schemii.common.postgres.errors import PostgresConsoleCancelledError
from schemii.common.query_executions.cancellation import (
    QueryCancellationRegistry,
    cancellable_connection,
)


class Connection:
    def __init__(self, *, block_execute=False, block_fetch=False):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.block_execute = block_execute
        self.block_fetch = block_fetch
        self.signals = 0
        self.commits = 0
        self.executed = []

    def cursor(self, **kwargs):
        return Cursor(self)

    def cancel_safe(self, *, timeout):
        assert timeout <= 1
        self.signals += 1
        self.cancelled.set()

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def wait(self):
        self.started.set()
        assert self.cancelled.wait(3), "query did not receive cancellation"
        raise RuntimeError("PostgreSQL cancelled query")


class Cursor:
    description = ()
    statusmessage = "UPDATE 1"

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement):
        self.connection.executed.append(statement)
        if self.connection.block_execute:
            self.connection.wait()

    def fetchmany(self, size):
        if self.connection.block_fetch:
            self.connection.wait()
        return []

    def close(self):
        pass


@pytest.mark.parametrize("operation", ["initial_read", "page", "export", "write"])
def test_stop_signals_exact_in_use_connection_and_normalizes_error(operation):
    registry = QueryCancellationRegistry()
    connection = Connection(block_execute=operation in {"initial_read", "write"},
                            block_fetch=operation in {"page", "export"})
    session = None
    if operation in {"page", "export"}:
        session = PsycopgConsoleReadSession(connection, 1, ["SELECT 1"], page_memory_bytes=4096)

    def run():
        with registry.scope("owner", "chat", "turn"):
            if operation == "initial_read":
                PsycopgConsoleReadSession(connection, 1, ["SELECT 1"], page_memory_bytes=4096)
            elif operation == "page":
                session.page(0, 0, 10)
            elif operation == "export":
                session.export_page(0, 0, 10)
            else:
                PsycopgConsoleTransaction(connection, 1).execute(["UPDATE test SET value = 1"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert connection.started.wait(2)
        registry.cancel("other-owner", "chat")
        registry.cancel("owner", "other-chat")
        registry.cancel("owner", "chat", "old-turn")
        assert connection.signals == 0
        registry.cancel("owner", "chat", "turn")
        with pytest.raises(PostgresConsoleCancelledError):
            future.result(timeout=2)
    assert connection.signals == 1
    registry.cancel("owner", "chat")
    assert connection.signals == 1


def test_stop_before_database_registration_does_not_dispatch_sql():
    registry = QueryCancellationRegistry()
    connection = Connection()
    with registry.scope("owner", "chat", "turn"):
        registry.cancel("owner", "chat")
        with pytest.raises(PostgresConsoleCancelledError):
            execute_console_statements(connection, ["SELECT 1"])
    assert connection.executed == []


def test_permission_change_prevents_dispatch_and_commit():
    registry = QueryCancellationRegistry()
    connection = Connection()
    authorized = True
    with registry.scope("owner", "chat", "turn", lambda: authorized):
        authorized = False
        with pytest.raises(PostgresConsoleCancelledError):
            execute_console_statements(connection, ["SELECT 1"])
        with pytest.raises(PostgresConsoleCancelledError):
            PsycopgConsoleTransaction(connection, 1).commit()
    assert connection.commits == 0
    assert connection.executed == []


def test_completed_operation_is_detached_before_connection_reuse():
    registry = QueryCancellationRegistry()
    connection = Connection()
    with registry.scope("owner", "chat", "turn"):
        execute_console_statements(connection, ["UPDATE test SET value = 1"])
        registry.cancel("owner", "chat")
    assert connection.signals == 0
    with registry.scope("owner", "chat", "new-turn"):
        execute_console_statements(connection, ["UPDATE test SET value = 2"])
    assert len(connection.executed) == 2


def test_stop_does_not_cancel_or_rewrite_an_already_dispatched_commit():
    registry = QueryCancellationRegistry()
    connection = Connection()
    finish_commit = threading.Event()

    def commit():
        connection.started.set()
        assert finish_commit.wait(2)
        connection.commits += 1

    connection.commit = commit

    def run():
        with registry.scope("owner", "chat", "turn"):
            PsycopgConsoleTransaction(connection, 1).commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert connection.started.wait(1)
        registry.cancel("owner", "chat")
        assert connection.signals == 0
        finish_commit.set()
        future.result(timeout=2)
    assert connection.commits == 1


def test_cancel_fences_remaining_batch_statements():
    registry = QueryCancellationRegistry()
    connection = Connection()
    original = connection.cursor

    def cursor(**kwargs):
        result = original(**kwargs)
        execute = result.execute

        def execute_and_stop(statement):
            execute(statement)
            registry.cancel("owner", "chat")

        result.execute = execute_and_stop
        return result

    connection.cursor = cursor
    with registry.scope("owner", "chat", "turn"):
        with pytest.raises(PostgresConsoleCancelledError):
            execute_console_statements(connection, ["UPDATE first", "UPDATE second"])
    assert connection.executed == ["UPDATE first"]


def test_unregister_waits_for_signal_before_connection_can_be_reused():
    registry = QueryCancellationRegistry()
    connection = Connection()
    signal_started = threading.Event()
    finish_signal = threading.Event()
    operation_finished = threading.Event()
    allow_finish = threading.Event()

    def cancel_safe(**kwargs):
        signal_started.set()
        assert finish_signal.wait(2)

    connection.cancel_safe = cancel_safe

    def run():
        try:
            with registry.scope("owner", "chat", "turn"):
                with cancellable_connection(connection):
                    connection.started.set()
                    assert allow_finish.wait(2)
        except PostgresConsoleCancelledError:
            pass
        finally:
            operation_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        operation = pool.submit(run)
        assert connection.started.wait(1)
        stopping = pool.submit(registry.cancel, "owner", "chat")
        assert signal_started.wait(1)
        allow_finish.set()
        assert not operation_finished.wait(0.05)
        finish_signal.set()
        operation.result(timeout=2)
        stopping.result(timeout=2)
    assert operation_finished.is_set()


@pytest.mark.parametrize("export", [False, True])
def test_cancelled_page_releases_snapshot_and_requires_explicit_replay(export):
    from schemii.common.metadata.models import LOCAL_PROTOTYPE_USER_ID
    from schemii.schemii.console.service import ConsoleServiceError
    from test_shared_query_executions import setup_target

    _, postgres, service, args = setup_target()
    registry = QueryCancellationRegistry()
    connection = Connection(block_fetch=True)
    session = PsycopgConsoleReadSession(connection, 1, ["SELECT 1"], page_memory_bytes=4096)
    postgres.open_console_read_session = lambda *args, **kwargs: session
    receipt = service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **args)
    service.run(LOCAL_PROTOTYPE_USER_ID, receipt.id)
    result_id = service.get_owned(LOCAL_PROTOTYPE_USER_ID, receipt.id).results[0].id

    def read():
        with registry.scope(LOCAL_PROTOTYPE_USER_ID, "chat", "turn"):
            if export:
                list(service.export_csv(LOCAL_PROTOTYPE_USER_ID, None, receipt.id, result_id))
            else:
                service.page(LOCAL_PROTOTYPE_USER_ID, None, receipt.id, result_id, None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        assert connection.started.wait(2)
        registry.cancel(LOCAL_PROTOTYPE_USER_ID, "chat")
        with pytest.raises((ConsoleServiceError, PostgresConsoleCancelledError)):
            future.result(timeout=2)
    assert session._closed
    assert receipt.id not in service._active_read_sessions
    with pytest.raises(ConsoleServiceError) as replay:
        service.page(LOCAL_PROTOTYPE_USER_ID, None, receipt.id, result_id, None)
    assert replay.value.code == "console_result_replay_required"
