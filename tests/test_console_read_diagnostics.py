from types import SimpleNamespace

import pytest

from schemii.common.postgres.console.gateway import PsycopgConsoleReadSession
from schemii.common.postgres.errors import PostgresConsoleQueryError


def test_unbounded_read_uses_one_forward_cursor_without_counting_or_offsets():
    class Cursor:
        description = ()
        def __init__(self, options):
            self.options = options
            self.executed = []
            self.fetch_sizes = []
            self.position = 0
        def execute(self, sql, args=None): self.executed.append((sql, args))
        def fetchmany(self, size):
            self.fetch_sizes.append(size)
            rows = [(number,) for number in range(self.position, self.position + size)]
            self.position += size
            return rows
        def close(self): pass

    cursors = []
    commits = []
    def make_cursor(**options):
        cursor = Cursor(options)
        cursors.append(cursor)
        return cursor
    connection = SimpleNamespace(
        cursor=make_cursor,
        commit=lambda: commits.append(True), close=lambda: None,
    )
    session = PsycopgConsoleReadSession(connection, 1, ["SELECT * FROM large_join"], page_memory_bytes=4096)
    cursor = cursors[0]
    assert cursor.executed == [("SELECT * FROM large_join", None)]
    assert cursor.options["scrollable"] is False
    assert cursor.options["withhold"] is False
    assert commits == []
    assert session.results[0].row_count is None
    assert session.page(0, 0, 3) == ((0,), (1,), (2,))
    assert session.page(0, 3, 2) == ((3,), (4,))
    assert cursor.fetch_sizes == [1, 1, 1, 1, 1]
    assert len(cursors) == 1
    session.close()


def test_export_uses_an_independent_forward_cursor():
    class Cursor:
        description = ()
        def __init__(self): self.position = 0
        def execute(self, _sql): pass
        def fetchmany(self, size):
            rows = [(number,) for number in range(self.position, self.position + size)]
            self.position += size
            return rows
        def close(self): pass

    cursors = []
    def make_cursor(**_options):
        cursor = Cursor()
        cursors.append(cursor)
        return cursor

    session = PsycopgConsoleReadSession(
        SimpleNamespace(cursor=make_cursor, close=lambda: None),
        1,
        ["SELECT value FROM source"],
        page_memory_bytes=4096,
    )
    assert session.page(0, 0, 2) == ((0,), (1,))
    assert session.export_page(0, 0, 3) == ((0,), (1,), (2,))
    assert session.page(0, 2, 2) == ((2,), (3,))
    assert len(cursors) == 2
    session.close()


def test_page_memory_boundary_buffers_instead_of_skipping_rows():
    class Cursor:
        description = ()
        def __init__(self): self.position = 0
        def execute(self, _sql): pass
        def fetchmany(self, size):
            values = (("a" * 16,), ("b" * 16,), ("c" * 16,))
            rows = values[self.position : self.position + size]
            self.position += len(rows)
            return rows
        def close(self): pass

    session = PsycopgConsoleReadSession(
        SimpleNamespace(cursor=lambda **_options: Cursor(), close=lambda: None),
        1,
        ["SELECT value FROM source"],
        page_memory_bytes=25,
    )
    assert session.page(0, 0, 3) == (("a" * 16,),)
    assert session.has_buffered_rows(0) is True
    assert session.page(0, 1, 3) == (("b" * 16,),)
    assert session.page(0, 2, 3) == (("c" * 16,),)
    assert session.has_buffered_rows(0) is False
    session.close()


def test_read_preparation_preserves_postgres_diagnostic_and_closes_connection():
    class DatabaseTimeout(Exception):
        diag = SimpleNamespace(message_primary="canceling statement due to statement timeout")
        sqlstate = "57014"

    closed = []
    cursor = SimpleNamespace(
        execute=lambda _sql: (_ for _ in ()).throw(DatabaseTimeout()),
        close=lambda: None,
    )
    connection = SimpleNamespace(
        cursor=lambda **_kwargs: cursor,
        close=lambda: closed.append(True),
    )
    with pytest.raises(PostgresConsoleQueryError, match="statement timeout") as caught:
        PsycopgConsoleReadSession(connection, 1, ["SELECT 1"], page_memory_bytes=4096)
    assert caught.value.sqlstate == "57014"
    assert closed == [True]


def test_mixed_read_progress_retains_outer_statement_index(monkeypatch):
    from schemii.common.query_executions.activity import report_progress
    from schemii.common.postgres.console import gateway
    monkeypatch.setattr(gateway, "execute_incremental",
                        lambda connection, statement, consume: ((), "SHOW"))

    class Cursor:
        description = ()
        statusmessage = "SHOW"
        def execute(self, _sql): pass
        def close(self): pass

    connection = SimpleNamespace(cursor=lambda **kwargs: Cursor(), close=lambda: None)
    progress = []
    with report_progress(lambda index, completed: progress.append((index, completed))):
        session = PsycopgConsoleReadSession(connection, 1,
            ["SELECT 1", "SHOW timezone", "SHOW statement_timeout"], page_memory_bytes=4096)
    assert progress == [(0, False), (1, False), (1, True), (2, False), (2, True)]
    session.close()


@pytest.mark.parametrize("export", [False, True])
def test_cell_limit_invalidates_consumed_forward_snapshot(export):
    from tests.test_console import (
        RetainedReadGateway, console_client, execution_body, target_workspace,
    )
    from schemii.common.postgres.errors import PostgresConsoleLimitError
    from schemii.common.query_executions.errors import ConsoleServiceError
    from schemii.schemii.console.service import ConsoleExecutionCreate
    gateway = RetainedReadGateway()
    api, _ = console_client(gateway)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    owner = "user_local_prototype"
    receipt = service.reserve(owner, workspace["id"], ConsoleExecutionCreate.model_validate(execution_body(workspace, "SELECT 1")))
    service.run(owner, receipt.id)
    receipt = service.get_owned(owner, receipt.id)
    result_id = receipt.results[0].id
    def reject(*args):
        raise PostgresConsoleLimitError("oversized cell", statement_index=0,
            resource="console_result_cell", limit_name="console.results.maximum_cell_bytes",
            limit=4, observed=12)
    gateway.sessions[0].page = reject
    gateway.sessions[0].export_page = reject
    if export:
        stream = service.export_csv(owner, workspace["id"], receipt.id, result_id)
        next(stream)
        with pytest.raises(PostgresConsoleLimitError):
            next(stream)
    else:
        with pytest.raises(ConsoleServiceError) as caught:
            service.page(owner, workspace["id"], receipt.id, result_id, None)
        assert caught.value.code == "postgres_console_result_limit"
    assert gateway.sessions[0].closed
    assert receipt.id not in service._active_read_sessions
    assert result_id not in service._transient_results
    with pytest.raises(ConsoleServiceError) as caught:
        service.page(owner, workspace["id"], receipt.id, result_id, None)
    assert caught.value.status == 410


@pytest.mark.parametrize(("oids", "expected_first_fetch"), [
    ((20,), 256), ((21, 23), 170), ((16, 20, 700, 701), 102),
    ((20,) * 256, 1), ((25,), 1), ((1700,), 1), ((20, 25), 1), ((99999,), 1),
])
def test_fixed_scalar_batching_respects_byte_allowance_and_untrusted_types(monkeypatch, oids, expected_first_fetch):
    from schemii.common.postgres.console import gateway
    monkeypatch.setattr(gateway, "_console_type_names", lambda *args: {})
    class Cursor:
        description = tuple(SimpleNamespace(name="v", type_code=oid) for oid in oids)
        position = 0
        fetch_sizes = []
        def execute(self, _sql): pass
        def fetchmany(self, size):
            self.fetch_sizes.append(size)
            batch = [tuple(-9223372036854775808 if oid == 20 else -1.7976931348623157e308 if oid in (700, 701) else True if oid == 16 else "x" for oid in oids) for _ in range(min(size, 1000 - self.position))]
            self.position += len(batch)
            return batch
        def close(self): pass
    cursor = Cursor()
    session = PsycopgConsoleReadSession(SimpleNamespace(cursor=lambda **kwargs: cursor, close=lambda: None),
        1, ["SELECT scalars"], page_memory_bytes=65536)
    try:
        page = session.page(0, 0, 1000)
        assert cursor.fetch_sizes[0] == expected_first_fetch
        import json
        assert sum(len(json.dumps(row).encode()) for row in page) <= 65536
        assert cursor.position == len(page) + len(session._readers[0].pending)
        if any(oid not in (16, 20, 21, 23, 700, 701) for oid in oids):
            assert set(cursor.fetch_sizes) == {1}
            assert len(session._readers[0].pending) <= 1
    finally:
        session.close()
