"""Psycopg execution primitives for the shared human SQL Console."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import secrets
import threading
from typing import Any, Protocol, runtime_checkable

from pglast import parse_sql

from schemii.common.postgres.errors import (
    PostgresCommitUncertainError,
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

    def close(self) -> None: ...


@dataclass(slots=True)
class _ReadCursor:
    cursor: Any | None
    rows: tuple[tuple[Any, ...], ...]


class PsycopgConsoleReadSession:
    """Hold scrollable PostgreSQL cursors; never copy result rows to metadata."""

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
        results: list[ConsoleQueryResult] = []
        statement_index = 0
        try:
            for statement_index, statement in enumerate(statements):
                parsed = parse_sql(statement)
                node_name = type(parsed[0].stmt).__name__ if parsed else ""
                if node_name != "SelectStmt":
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
                    continue

                row_count = self._count(statement)
                cursor = self._database_connection.cursor(
                    name=f"schemii_{secrets.token_hex(12)}",
                    scrollable=True,
                    withhold=True,
                    row_factory=lambda _cursor: lambda values: tuple(values),
                )
                cursor.execute(statement)
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
                self._readers[statement_index] = _ReadCursor(cursor=cursor, rows=())
                results.append(
                    ConsoleQueryResult(
                        statement_index=statement_index,
                        command="SELECT",
                        columns=columns,
                        rows=(),
                        truncated=False,
                        row_count=row_count,
                        replayable=True,
                    )
                )
            self._database_connection.commit()
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
        except Exception:
            self.close()
            raise PostgresConsoleQueryError(
                "PostgreSQL rejected the query",
                statement_index=statement_index,
            ) from None

    def _count(self, statement: str) -> int:
        cursor: Any | None = None
        try:
            cursor = self._database_connection.cursor()
            cursor.execute(
                f"SELECT count(*)::bigint AS row_count FROM ({statement}) "
                "AS schemii_count_source"
            )
            row = cursor.fetchone()
            if isinstance(row, Mapping):
                value = row.get("row_count")
            elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                value = row[0] if row else None
            else:
                value = None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PostgresConsoleQueryError(
                    "PostgreSQL did not return a valid result count",
                    statement_index=0,
                )
            return value
        finally:
            _safe_close(cursor)

    def page(self, statement_index, offset, page_size):
        with self._lock:
            if self._closed or offset < 0 or page_size < 1:
                raise PostgresQueryError()
            try:
                reader = self._readers[statement_index]
            except KeyError as error:
                raise PostgresQueryError() from error
            if reader.cursor is None:
                raw_rows = reader.rows[offset : offset + page_size]
            else:
                reader.cursor.scroll(offset, mode="absolute")
                raw_rows = reader.cursor.fetchmany(page_size)
            rows: list[tuple[Any, ...]] = []
            used_bytes = 0
            for raw_row in raw_rows:
                converted = tuple(
                    json_console_value(value, maximum_bytes=self._maximum_cell_bytes)
                    for value in raw_row
                )
                row_bytes = len(
                    json.dumps(converted, ensure_ascii=False).encode("utf-8")
                )
                if rows and used_bytes + row_bytes > self._page_memory_bytes:
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
            return tuple(rows)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for reader in self._readers.values():
                _safe_close(reader.cursor)
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

    results: list[ConsoleQueryResult] = []
    statement_index = 0
    try:
        for statement_index, statement in enumerate(statements):
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
