"""Psycopg execution primitives for the shared human SQL Console."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import secrets
import threading
from typing import Any, Protocol, runtime_checkable

from pglast import parse_sql
from schemii.common.query_executions.activity import report_progress, statement_progress
from schemii.common.query_executions.cancellation import (
    cancellable_connection,
    check_query_authority,
)

from schemii.common.postgres.errors import (
    PostgresCommitUncertainError,
    PostgresConsoleCancelledError,
    PostgresConsoleLimitError,
    PostgresConsoleQueryError,
    PostgresGatewayError,
    PostgresQueryError,
)

from .execution import (
    MAX_CONSOLE_RESULT_BYTES,
    ConsoleQueryResult,
    ConsoleValueLimitError,
    json_console_value,
)
from .models import ConsoleResultColumn


@runtime_checkable
class PostgresConsoleTransaction(Protocol):
    """One process-bound PostgreSQL transaction retained between requests."""

    @property
    def backend_pid(self) -> int: ...

    def execute(self, statements: Sequence[str]) -> tuple[ConsoleQueryResult, ...]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class PostgresConsoleReadSession(Protocol):
    """One transient PostgreSQL-owned result snapshot retained between pages."""

    @property
    def backend_pid(self) -> int: ...

    @property
    def results(self) -> tuple[ConsoleQueryResult, ...]: ...

    def page(
        self, statement_index: int, offset: int, page_size: int
    ) -> tuple[tuple[Any, ...], ...]: ...

    def export_page(
        self, statement_index: int, offset: int, page_size: int
    ) -> tuple[tuple[Any, ...], ...]: ...

    def has_buffered_rows(self, statement_index: int) -> bool: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _ReadCursor:
    cursor: Any | None
    rows: tuple[tuple[Any, ...], ...]
    statement: str | None = None
    position: int = 0
    pending: tuple[tuple[Any, ...], ...] = ()
    export_cursor: Any | None = None
    export_position: int = 0
    export_pending: tuple[tuple[Any, ...], ...] = ()


class PsycopgConsoleReadSession:
    """Hold forward-only PostgreSQL cursors; never copy result rows to metadata."""

    def __init__(
        self,
        database_connection: Any,
        backend_pid: int,
        statements: Sequence[str],
        *,
        page_memory_bytes: int,
        maximum_cell_bytes: int = 256 * 1024,
    ) -> None:
        self._database_connection = database_connection
        self._backend_pid = backend_pid
        self._page_memory_bytes = page_memory_bytes
        self._maximum_cell_bytes = maximum_cell_bytes
        self._closed = False
        self._cancelled = threading.Event()
        self._lock = threading.RLock()
        self._readers: dict[int, _ReadCursor] = {}
        self._results = self._prepare(statements)

    @property
    def backend_pid(self) -> int:
        return self._backend_pid

    @property
    def results(self) -> tuple[ConsoleQueryResult, ...]:
        return self._results

    def _prepare(self, statements: Sequence[str]) -> tuple[ConsoleQueryResult, ...]:
        with cancellable_connection(self._database_connection):
            return self._prepare_statements(statements)

    def _prepare_statements(self, statements: Sequence[str]) -> tuple[ConsoleQueryResult, ...]:
        results: list[ConsoleQueryResult] = []
        statement_index = 0
        try:
            for statement_index, statement in enumerate(statements):
                check_query_authority()
                statement_progress(statement_index)
                parsed = parse_sql(statement)
                node_name = type(parsed[0].stmt).__name__ if parsed else ""
                if node_name != "SelectStmt":
                    # The nested single-statement executor numbers from zero;
                    # only this outer script owns progress statement indexes.
                    with report_progress(None):
                        materialized = execute_console_statements(
                            self._database_connection,
                            (statement,),
                            maximum_result_bytes=self._page_memory_bytes,
                            maximum_cell_bytes=self._maximum_cell_bytes,
                        )[0]
                    result = ConsoleQueryResult(
                        statement_index=statement_index,
                        command=materialized.command,
                        columns=materialized.columns,
                        rows=(),
                        truncated=materialized.truncated,
                        row_count=len(materialized.rows),
                        replayable=False,
                    )
                    self._readers[statement_index] = _ReadCursor(
                        cursor=None, rows=materialized.rows
                    )
                    results.append(result)
                    statement_progress(statement_index, True)
                    continue

                query = statement.rstrip().removesuffix(";")
                cursor = self._open_cursor(query, statement_index)
                description = tuple(cursor.description or ())
                type_names = _console_type_names(
                    self._database_connection,
                    tuple(column.type_code for column in description),
                )
                columns = tuple(
                    ConsoleResultColumn(
                        name=column.name,
                        data_type=type_names.get(
                            column.type_code, f"oid:{column.type_code}"
                        ),
                    )
                    for column in description
                )
                self._readers[statement_index] = _ReadCursor(
                    cursor=cursor, rows=(), statement=query
                )
                results.append(
                    ConsoleQueryResult(
                        statement_index=statement_index,
                        command="SELECT",
                        columns=columns,
                        rows=(),
                        truncated=False,
                        # Exact counting can cost more than the requested page
                        # and must never delay an unbounded result's first rows.
                        row_count=None,
                        replayable=True,
                    )
                )
            return tuple(results)
        except PostgresGatewayError:
            self.close()
            raise
        except ConsoleValueLimitError as error:
            self.close()
            raise PostgresConsoleLimitError(
                str(error),
                statement_index=statement_index,
                resource="console_result_cell",
                limit_name="console.results.maximum_cell_bytes",
                limit=error.limit,
                observed=error.observed,
            ) from error
        except Exception as error:
            diagnostic = getattr(error, "diag", None)
            message = getattr(diagnostic, "message_primary", None)
            sqlstate = getattr(error, "sqlstate", None)
            self.close()
            raise PostgresConsoleQueryError(
                message if isinstance(message, str) and message else "PostgreSQL rejected the query",
                statement_index=statement_index,
                sqlstate=sqlstate if isinstance(sqlstate, str) else None,
            ) from None

    def _open_cursor(self, statement: str, statement_index: int) -> Any:
        cursor = self._database_connection.cursor(
            name=f"schemii_read_{statement_index}_{secrets.token_hex(8)}",
            scrollable=False,
            withhold=False,
            row_factory=lambda _cursor: lambda values: tuple(values),
        )
        cursor.execute(statement)
        return cursor

    def _fetch_forward(
        self,
        reader: _ReadCursor,
        *,
        statement_index: int,
        offset: int,
        page_size: int,
        export: bool = False,
    ) -> tuple[tuple[Any, ...], ...]:
        cursor = reader.export_cursor if export else reader.cursor
        position = reader.export_position if export else reader.position
        pending = reader.export_pending if export else reader.pending
        if cursor is None:
            if not export or reader.statement is None:
                return reader.rows[offset : offset + page_size]
            cursor = self._open_cursor(reader.statement, statement_index)
            reader.export_cursor = cursor
        if offset != position:
            raise PostgresQueryError(
                "Result pages must be read in sequence; rerun the query to restart this result"
            )
        raw_rows = list(pending)
        if len(raw_rows) < page_size:
            raw_rows.extend(cursor.fetchmany(page_size - len(raw_rows)))

        rows: list[tuple[Any, ...]] = []
        used_bytes = 0
        remaining: tuple[tuple[Any, ...], ...] = ()
        for row_index, raw_row in enumerate(raw_rows):
            converted = tuple(
                json_console_value(value, maximum_bytes=self._maximum_cell_bytes)
                for value in raw_row
            )
            row_bytes = len(json.dumps(converted, ensure_ascii=False).encode("utf-8"))
            if rows and used_bytes + row_bytes > self._page_memory_bytes:
                remaining = tuple(raw_rows[row_index:])
                break
            if row_bytes > self._page_memory_bytes:
                raise PostgresConsoleLimitError(
                    f"One PostgreSQL row is {row_bytes} bytes, above the configured Console page memory limit of {self._page_memory_bytes} bytes. Narrow the selected columns or ask the administrator to raise console.results.page_memory_bytes.",
                    statement_index=statement_index,
                    resource="console_result_page",
                    limit_name="console.results.page_memory_bytes",
                    limit=self._page_memory_bytes,
                    observed=row_bytes,
                )
            rows.append(converted)
            used_bytes += row_bytes
        if export:
            reader.export_position += len(rows)
            reader.export_pending = remaining
        else:
            reader.position += len(rows)
            reader.pending = remaining
        return tuple(rows)

    def page(self, statement_index, offset, page_size):
        with self._lock, cancellable_connection(self._database_connection):
            if self._cancelled.is_set():
                raise PostgresConsoleCancelledError()
            return self._page(statement_index, offset, page_size)

    def _page(self, statement_index, offset, page_size):
        with self._lock:
            if self._closed or offset < 0 or page_size < 1:
                raise PostgresQueryError()
            try:
                reader = self._readers[statement_index]
            except KeyError as error:
                raise PostgresQueryError() from error
            try:
                raw_rows = self._fetch_forward(
                    reader,
                    statement_index=statement_index,
                    offset=offset,
                    page_size=page_size,
                )
            except PostgresGatewayError:
                raise
            except Exception as error:
                if self._cancelled.is_set():
                    raise PostgresConsoleCancelledError() from None
                diagnostic = getattr(error, "diag", None)
                message = getattr(diagnostic, "message_primary", None)
                sqlstate = getattr(error, "sqlstate", None)
                raise PostgresConsoleQueryError(
                    message if isinstance(message, str) and message else "PostgreSQL rejected the query",
                    statement_index=statement_index,
                    sqlstate=sqlstate if isinstance(sqlstate, str) else None,
                ) from None
            return raw_rows

    def export_page(self, statement_index, offset, page_size):
        """Read a separate forward-only portal so export cannot disturb UI paging."""

        with self._lock, cancellable_connection(self._database_connection):
            if self._cancelled.is_set():
                raise PostgresConsoleCancelledError()
            return self._export_page(statement_index, offset, page_size)

    def _export_page(self, statement_index, offset, page_size):

        with self._lock:
            if self._closed or offset < 0 or page_size < 1:
                raise PostgresQueryError()
            try:
                reader = self._readers[statement_index]
            except KeyError as error:
                raise PostgresQueryError() from error
            try:
                raw_rows = self._fetch_forward(
                    reader,
                    statement_index=statement_index,
                    offset=offset,
                    page_size=page_size,
                    export=True,
                )
            except PostgresGatewayError:
                raise
            except Exception as error:
                if self._cancelled.is_set():
                    raise PostgresConsoleCancelledError() from None
                diagnostic = getattr(error, "diag", None)
                message = getattr(diagnostic, "message_primary", None)
                sqlstate = getattr(error, "sqlstate", None)
                raise PostgresConsoleQueryError(
                    message if isinstance(message, str) and message else "PostgreSQL rejected the query",
                    statement_index=statement_index,
                    sqlstate=sqlstate if isinstance(sqlstate, str) else None,
                ) from None
            return raw_rows

    def has_buffered_rows(self, statement_index: int) -> bool:
        """Report rows already fetched from PostgreSQL but held for the next page."""

        with self._lock:
            reader = self._readers.get(statement_index)
            return bool(reader is not None and reader.pending)

    def cancel(self) -> None:
        """Cancel the owned connection without waiting for its blocked fetch lock."""
        if self._closed:
            return
        self._cancelled.set()
        cancel_safe = getattr(self._database_connection, "cancel_safe", None)
        if callable(cancel_safe):
            cancel_safe(timeout=1.0)
        else:
            self._database_connection.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for reader in self._readers.values():
                _safe_close(reader.cursor)
                _safe_close(reader.export_cursor)
            self._readers.clear()
            _safe_close(self._database_connection)


class PsycopgConsoleTransaction:
    """Own one psycopg connection until the human commits or rolls back."""

    def __init__(
        self,
        database_connection: Any,
        backend_pid: int,
        *,
        maximum_result_bytes: int = MAX_CONSOLE_RESULT_BYTES,
        maximum_cell_bytes: int = 256 * 1024,
    ) -> None:
        self._database_connection = database_connection
        self._backend_pid = backend_pid
        self._closed = False
        self._maximum_result_bytes = maximum_result_bytes
        self._maximum_cell_bytes = maximum_cell_bytes

    @property
    def backend_pid(self) -> int:
        return self._backend_pid

    def execute(self, statements: Sequence[str]) -> tuple[ConsoleQueryResult, ...]:
        if self._closed:
            raise PostgresQueryError()
        return execute_console_statements(
            self._database_connection,
            statements,
            maximum_result_bytes=self._maximum_result_bytes,
            maximum_cell_bytes=self._maximum_cell_bytes,
        )

    def commit(self) -> None:
        if self._closed:
            raise PostgresQueryError()
        # Once COMMIT has been dispatched its outcome must never be guessed or
        # rewritten as a cancellation. Only fence it before dispatch.
        check_query_authority()
        try:
            self._database_connection.commit()
        except Exception:
            raise PostgresCommitUncertainError() from None
        finally:
            self.close()

    def rollback(self) -> None:
        if self._closed:
            return
        try:
            self._database_connection.rollback()
        except Exception:
            raise PostgresQueryError() from None
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._database_connection.close()
        except Exception:
            pass


def execute_console_statements(
    database_connection: Any,
    statements: Sequence[str],
    *,
    maximum_result_bytes: int = MAX_CONSOLE_RESULT_BYTES,
    maximum_cell_bytes: int = 256 * 1024,
) -> tuple[ConsoleQueryResult, ...]:
    """Execute validated statements and materialize only bounded JSON-safe results."""

    with cancellable_connection(database_connection):
        return _execute_console_statements(
            database_connection, statements,
            maximum_result_bytes=maximum_result_bytes,
            maximum_cell_bytes=maximum_cell_bytes,
        )


def _execute_console_statements(
    database_connection: Any,
    statements: Sequence[str],
    *,
    maximum_result_bytes: int,
    maximum_cell_bytes: int,
) -> tuple[ConsoleQueryResult, ...]:

    results: list[ConsoleQueryResult] = []
    statement_index = 0
    try:
        for statement_index, statement in enumerate(statements):
            check_query_authority()
            statement_progress(statement_index)
            cursor: Any | None = None
            try:
                cursor = database_connection.cursor(
                    row_factory=lambda _cursor: lambda values: tuple(values)
                )
                cursor.execute(statement)
                description = tuple(cursor.description or ())
                rows: list[tuple[Any, ...]] = []
                result_bytes = 0
                truncated = False
                while description:
                    check_query_authority()
                    raw_rows = cursor.fetchmany(100)
                    if not raw_rows:
                        break
                    for raw_row in raw_rows:
                        converted = tuple(
                            json_console_value(
                                value, maximum_bytes=maximum_cell_bytes
                            )
                            for value in raw_row
                        )
                        row_bytes = len(
                            json.dumps(converted, ensure_ascii=False).encode("utf-8")
                        )
                        if result_bytes + row_bytes > maximum_result_bytes:
                            raise PostgresConsoleLimitError(
                                f"This statement needs more than the configured {maximum_result_bytes}-byte Console memory page. Narrow the selected columns or use managed read and its streaming download.",
                                statement_index=statement_index,
                                resource="console_result_memory",
                                limit_name="console.results.page_memory_bytes",
                                limit=maximum_result_bytes,
                                observed=result_bytes + row_bytes,
                            )
                        rows.append(converted)
                        result_bytes += row_bytes
                type_names = _console_type_names(
                    database_connection,
                    tuple(column.type_code for column in description),
                )
                columns = tuple(
                    ConsoleResultColumn(
                        name=column.name,
                        data_type=type_names.get(
                            column.type_code,
                            f"oid:{column.type_code}",
                        ),
                    )
                    for column in description
                )
                status_message = str(cursor.statusmessage or "OK")
                results.append(
                    ConsoleQueryResult(
                        statement_index=statement_index,
                        command=status_message.split(" ", 1)[0],
                        columns=columns,
                        rows=tuple(rows),
                        truncated=truncated,
                    )
                )
                statement_progress(statement_index, True)
            finally:
                _safe_close(cursor)
        return tuple(results)
    except PostgresGatewayError:
        raise
    except ConsoleValueLimitError as error:
        raise PostgresConsoleLimitError(
            str(error),
            statement_index=statement_index,
            resource="console_result_cell",
            limit_name="console.results.maximum_cell_bytes",
            limit=error.limit,
            observed=error.observed,
        ) from error
    except Exception as error:
        diagnostic = getattr(error, "diag", None)
        message = getattr(diagnostic, "message_primary", None)
        sqlstate = getattr(error, "sqlstate", None)
        raise PostgresConsoleQueryError(
            message if isinstance(message, str) and message else "PostgreSQL rejected the query",
            statement_index=statement_index,
            sqlstate=sqlstate if isinstance(sqlstate, str) else None,
        ) from None


def _console_type_names(
    database_connection: Any,
    type_oids: tuple[int, ...],
) -> dict[int, str]:
    if not type_oids:
        return {}
    cursor: Any | None = None
    try:
        cursor = database_connection.cursor()
        cursor.execute(
            "SELECT oid::integer AS oid, format_type(oid, NULL) AS data_type "
            "FROM pg_type WHERE oid = ANY(%s::oid[])",
            (list(set(type_oids)),),
        )
        rows = cursor.fetchall()
        if not isinstance(rows, Sequence):
            raise TypeError
        return {
            int(row["oid"]): str(row["data_type"])
            for row in rows
            if isinstance(row, Mapping)
            and row.get("oid") is not None
            and row.get("data_type") is not None
        }
    finally:
        _safe_close(cursor)


def _safe_close(resource: Any | None) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass
