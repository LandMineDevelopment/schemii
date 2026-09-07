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
    assert cursor.fetch_sizes == [3, 2]
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
