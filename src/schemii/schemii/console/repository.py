"""Durable authority for bounded SQL Console executions and retained results."""

from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator, Protocol

from schemii.common.postgres.console.execution import ConsoleQueryResult
from schemii.common.postgres.console.models import (
    ConsoleExecution,
    ConsoleResultColumn,
    ConsoleResultPage,
    ConsoleResultSummary,
)


class ConsoleRepositoryError(RuntimeError):
    pass


class ConsoleNotFoundError(ConsoleRepositoryError):
    pass


class ConsoleConflictError(ConsoleRepositoryError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ConsoleResultGoneError(ConsoleRepositoryError):
    pass


class ConsoleStorageUnavailableError(ConsoleRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ConsoleTarget:
    connection_id: str
    connection_revision: int
    database: str
    namespace: str


@dataclass(frozen=True, slots=True)
class ConsoleExecutionRecord:
    owner_id: str
    execution: ConsoleExecution
    workspace_revision: int
    target: ConsoleTarget
    statements: tuple[str, ...]
    page_size: int
    backend_pid: int | None = None
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class _StoredResult:
    id: str
    execution_id: str
    query: ConsoleQueryResult
    expires_at: datetime
    closed_at: datetime | None = None


class ConsoleRepository(Protocol):
    def recover_interrupted(self, now: datetime) -> None: ...

    def reserve(
        self,
        owner_id: str,
        workspace_id: str,
        console_id: str,
        workspace_revision: int,
        target: ConsoleTarget,
        statements: tuple[str, ...],
        page_size: int,
        now: datetime,
    ) -> ConsoleExecutionRecord: ...

    def claim(self, owner_id: str, execution_id: str, now: datetime) -> ConsoleExecutionRecord | None: ...

    def publish_backend(self, owner_id: str, execution_id: str, backend_pid: int, now: datetime) -> bool: ...

    def succeed(
        self,
        owner_id: str,
        execution_id: str,
        results: tuple[ConsoleQueryResult, ...],
        expires_at: datetime,
        now: datetime,
    ) -> ConsoleExecutionRecord: ...

    def fail(
        self,
        owner_id: str,
        execution_id: str,
        *,
        code: str,
        message: str,
        statement_index: int | None,
        cancelled: bool,
        now: datetime,
    ) -> ConsoleExecutionRecord: ...

    def get(self, owner_id: str, execution_id: str) -> ConsoleExecutionRecord: ...

    def request_cancel(self, owner_id: str, execution_id: str, now: datetime) -> ConsoleExecutionRecord: ...

    def page(
        self,
        owner_id: str,
        workspace_id: str,
        execution_id: str,
        result_id: str,
        cursor: str | None,
        now: datetime,
    ) -> ConsoleResultPage: ...

    def close_result(
        self,
        owner_id: str,
        workspace_id: str,
        execution_id: str,
        result_id: str,
        now: datetime,
    ) -> None: ...


def _execution(
    record: ConsoleExecutionRecord,
    *,
    status: str,
    revision: int,
    now: datetime,
    results: list[ConsoleResultSummary] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_statement_index: int | None = None,
) -> ConsoleExecution:
    return record.execution.model_copy(
        update={
            "revision": revision,
            "status": status,
            "completed_statement_indexes": (
                [result.statement_index for result in results] if results is not None else []
            ),
            "results": results or [],
            "error_code": error_code,
            "error_message": error_message,
            "error_statement_index": error_statement_index,
            "updated_at": now,
        }
    )


class InMemoryConsoleRepository:
    """Thread-safe adapter used by tests and non-durable development composition."""

    def __init__(self) -> None:
        self._records: dict[str, ConsoleExecutionRecord] = {}
        self._results: dict[str, _StoredResult] = {}
        self._cursors: dict[str, tuple[str, int, datetime, bool]] = {}
        self._lock = threading.RLock()

    def recover_interrupted(self, now: datetime) -> None:
        with self._lock:
            for identifier, record in list(self._records.items()):
                if record.execution.status not in {"reserved", "running"}:
                    continue
                self._records[identifier] = self._replace(
                    record,
                    execution=_execution(
                        record,
                        status="failed",
                        revision=record.execution.revision + 1,
                        now=now,
                        error_code="console_execution_interrupted",
                        error_message="Console execution stopped before producing a result",
                    ),
                )

    def reserve(self, owner_id, workspace_id, console_id, workspace_revision, target, statements, page_size, now):
        with self._lock:
            self._purge(now)
            if any(
                item.owner_id == owner_id
                and item.execution.workspace_id == workspace_id
                and item.execution.status in {"reserved", "running"}
                for item in self._records.values()
            ):
                raise ConsoleConflictError(
                    "console_execution_active",
                    "This workspace already has an active Console execution",
                )
            identifier = f"cex_{secrets.token_hex(16)}"
            record = ConsoleExecutionRecord(
                owner_id=owner_id,
                execution=ConsoleExecution(
                    id=identifier,
                    revision=1,
                    workspace_id=workspace_id,
                    console_id=console_id,
                    status="reserved",
                    completed_statement_indexes=[],
                    results=[],
                    created_at=now,
                    updated_at=now,
                ),
                workspace_revision=workspace_revision,
                target=target,
                statements=statements,
                page_size=page_size,
            )
            self._records[identifier] = record
            return record

    def _purge(self, now: datetime) -> None:
        expired_results = {
            identifier for identifier, result in self._results.items()
            if result.expires_at <= now or result.closed_at is not None
        }
        for identifier in expired_results:
            self._results.pop(identifier, None)
        self._cursors = {
            token: value for token, value in self._cursors.items()
            if value[0] not in expired_results and value[2] > now
        }
        cutoff = now - timedelta(days=7)
        for identifier, record in list(self._records.items()):
            if (
                record.execution.status not in {"reserved", "running"}
                and record.execution.updated_at < cutoff
            ):
                self._records.pop(identifier, None)

    def claim(self, owner_id, execution_id, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.status != "reserved" or record.cancel_requested:
                return None
            updated = self._replace(
                record,
                execution=_execution(
                    record,
                    status="running",
                    revision=record.execution.revision + 1,
                    now=now,
                ),
            )
            self._records[execution_id] = updated
            return updated

    def publish_backend(self, owner_id, execution_id, backend_pid, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.status != "running":
                return False
            updated = self._replace(
                record,
                execution=record.execution.model_copy(
                    update={"revision": record.execution.revision + 1, "updated_at": now}
                ),
                backend_pid=backend_pid,
            )
            self._records[execution_id] = updated
            return not updated.cancel_requested

    def succeed(self, owner_id, execution_id, results, expires_at, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.cancel_requested:
                return self.fail(
                    owner_id,
                    execution_id,
                    code="postgres_console_cancelled",
                    message="Console execution was cancelled",
                    statement_index=None,
                    cancelled=True,
                    now=now,
                )
            summaries: list[ConsoleResultSummary] = []
            for query in results:
                result_id = f"res_{secrets.token_hex(16)}"
                self._results[result_id] = _StoredResult(
                    id=result_id,
                    execution_id=execution_id,
                    query=query,
                    expires_at=expires_at,
                )
                summaries.append(
                    ConsoleResultSummary(
                        id=result_id,
                        statement_index=query.statement_index,
                        command=query.command,
                        columns=list(query.columns),
                        row_count=len(query.rows),
                        has_more=len(query.rows) > record.page_size,
                    )
                )
            updated = self._replace(
                record,
                execution=_execution(
                    record,
                    status="succeeded",
                    revision=record.execution.revision + 1,
                    now=now,
                    results=summaries,
                ),
                backend_pid=None,
            )
            self._records[execution_id] = updated
            return updated

    def fail(self, owner_id, execution_id, *, code, message, statement_index, cancelled, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.status in {"succeeded", "failed", "cancelled"}:
                return record
            updated = self._replace(
                record,
                execution=_execution(
                    record,
                    status="cancelled" if cancelled else "failed",
                    revision=record.execution.revision + 1,
                    now=now,
                    error_code=code,
                    error_message=message,
                    error_statement_index=statement_index,
                ),
                backend_pid=None,
                cancel_requested=cancelled or record.cancel_requested,
            )
            self._records[execution_id] = updated
            return updated

    def get(self, owner_id, execution_id):
        with self._lock:
            return self._record(owner_id, execution_id)

    def request_cancel(self, owner_id, execution_id, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.status == "reserved":
                return self.fail(
                    owner_id,
                    execution_id,
                    code="postgres_console_cancelled",
                    message="Console execution was cancelled",
                    statement_index=None,
                    cancelled=True,
                    now=now,
                )
            if record.execution.status != "running":
                return record
            updated = self._replace(
                record,
                execution=record.execution.model_copy(
                    update={"revision": record.execution.revision + 1, "updated_at": now}
                ),
                cancel_requested=True,
            )
            self._records[execution_id] = updated
            return updated

    def page(self, owner_id, workspace_id, execution_id, result_id, cursor, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            result = self._result(execution_id, result_id, now)
            offset = 0
            if cursor is not None:
                try:
                    cursor_result, offset, expires_at, consumed = self._cursors[cursor]
                except KeyError as error:
                    raise ConsoleResultGoneError() from error
                if consumed or cursor_result != result_id or expires_at <= now:
                    raise ConsoleResultGoneError()
                self._cursors[cursor] = (cursor_result, offset, expires_at, True)
            rows = result.query.rows[offset : offset + record.page_size]
            next_offset = offset + len(rows)
            next_cursor = None
            if next_offset < len(result.query.rows):
                next_cursor = f"crc_{secrets.token_hex(16)}"
                self._cursors[next_cursor] = (
                    result_id,
                    next_offset,
                    result.expires_at,
                    False,
                )
            return ConsoleResultPage(
                execution_id=execution_id,
                result_id=result_id,
                columns=list(result.query.columns),
                rows=[list(row) for row in rows],
                next_cursor=next_cursor,
                truncated=result.query.truncated,
                expires_at=result.expires_at,
            )

    def close_result(self, owner_id, workspace_id, execution_id, result_id, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            result = self._result(execution_id, result_id, now)
            self._results[result_id] = _StoredResult(
                id=result.id,
                execution_id=result.execution_id,
                query=result.query,
                expires_at=result.expires_at,
                closed_at=now,
            )

    def _record(self, owner_id: str, execution_id: str) -> ConsoleExecutionRecord:
        record = self._records.get(execution_id)
        if record is None or record.owner_id != owner_id:
            raise ConsoleNotFoundError()
        return record

    def _result(self, execution_id: str, result_id: str, now: datetime) -> _StoredResult:
        result = self._results.get(result_id)
        if (
            result is None
            or result.execution_id != execution_id
            or result.closed_at is not None
            or result.expires_at <= now
        ):
            raise ConsoleResultGoneError()
        return result

    @staticmethod
    def _replace(record: ConsoleExecutionRecord, **changes: Any) -> ConsoleExecutionRecord:
        values = {
            "owner_id": record.owner_id,
            "execution": record.execution,
            "workspace_revision": record.workspace_revision,
            "target": record.target,
            "statements": record.statements,
            "page_size": record.page_size,
            "backend_pid": record.backend_pid,
            "cancel_requested": record.cancel_requested,
            **changes,
        }
        return ConsoleExecutionRecord(**values)


class PostgresConsoleRepository:
    """PostgreSQL metadata adapter with atomic state transitions and cursors."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = self._connection_factory()
        try:
            yield connection
            connection.commit()
        except ConsoleRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            if getattr(error, "sqlstate", None) == "23505":
                raise ConsoleConflictError(
                    "console_execution_active",
                    "This workspace already has an active Console execution",
                ) from error
            raise ConsoleStorageUnavailableError(
                "Console metadata is temporarily unavailable"
            ) from error
        finally:
            connection.close()

    def recover_interrupted(self, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE schemii.console_executions
                    SET status = 'failed', revision = revision + 1,
                        backend_pid = NULL,
                        error_code = 'console_execution_interrupted',
                        error_message = 'Console execution stopped before producing a result',
                        updated_at = %s
                    WHERE status IN ('reserved', 'running')
                    """,
                    (now,),
                )

    def reserve(self, owner_id, workspace_id, console_id, workspace_revision, target, statements, page_size, now):
        identifier = f"cex_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM schemii.console_executions
                    WHERE status NOT IN ('reserved', 'running')
                      AND updated_at < %s
                    """,
                    (now - timedelta(days=7),),
                )
                cursor.execute(
                    """
                    INSERT INTO schemii.console_executions (
                        id, owner_id, workspace_id, console_id, workspace_revision,
                        connection_id, connection_revision, database_name, namespace,
                        status, statements, page_size, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              'reserved', %s::jsonb, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        identifier, owner_id, workspace_id, console_id,
                        workspace_revision, target.connection_id,
                        target.connection_revision, target.database, target.namespace,
                        json.dumps(statements), page_size, now, now,
                    ),
                )
                return self._record_from_row(cursor.fetchone())

    def claim(self, owner_id, execution_id, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE schemii.console_executions
                    SET status = 'running', revision = revision + 1, updated_at = %s
                    WHERE owner_id = %s AND id = %s
                      AND status = 'reserved' AND NOT cancel_requested
                    RETURNING *
                    """,
                    (now, owner_id, execution_id),
                )
                row = cursor.fetchone()
                return None if row is None else self._record_from_row(row)

    def publish_backend(self, owner_id, execution_id, backend_pid, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE schemii.console_executions
                    SET backend_pid = %s, revision = revision + 1, updated_at = %s
                    WHERE owner_id = %s AND id = %s AND status = 'running'
                    RETURNING cancel_requested
                    """,
                    (backend_pid, now, owner_id, execution_id),
                )
                row = cursor.fetchone()
                return row is not None and not bool(row["cancel_requested"])

    def succeed(self, owner_id, execution_id, results, expires_at, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_row(cursor, owner_id, execution_id)
                if bool(row["cancel_requested"]):
                    return self._fail_locked(
                        cursor, row, code="postgres_console_cancelled",
                        message="Console execution was cancelled", statement_index=None,
                        cancelled=True, now=now,
                    )
                summaries: list[ConsoleResultSummary] = []
                for query in results:
                    result_id = f"res_{secrets.token_hex(16)}"
                    cursor.execute(
                        """
                        INSERT INTO schemii.console_results (
                            id, execution_id, statement_index, command,
                            columns_document, rows_document, truncated, expires_at
                        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        """,
                        (
                            result_id, execution_id, query.statement_index, query.command,
                            json.dumps([column.model_dump(mode="json", by_alias=True) for column in query.columns]),
                            json.dumps(query.rows), query.truncated, expires_at,
                        ),
                    )
                    summaries.append(
                        ConsoleResultSummary(
                            id=result_id,
                            statement_index=query.statement_index,
                            command=query.command,
                            columns=list(query.columns),
                            row_count=len(query.rows),
                            has_more=len(query.rows) > int(row["page_size"]),
                        )
                    )
                cursor.execute(
                    """
                    UPDATE schemii.console_executions
                    SET status = 'succeeded', revision = revision + 1,
                        completed_statement_indexes = %s, backend_pid = NULL,
                        updated_at = %s
                    WHERE owner_id = %s AND id = %s AND status = 'running'
                    RETURNING *
                    """,
                    ([item.statement_index for item in summaries], now, owner_id, execution_id),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise ConsoleConflictError(
                        "console_execution_changed", "Console execution changed while completing"
                    )
                return self._record_from_row(updated, summaries=summaries)

    def fail(self, owner_id, execution_id, *, code, message, statement_index, cancelled, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_row(cursor, owner_id, execution_id)
                return self._fail_locked(
                    cursor, row, code=code, message=message,
                    statement_index=statement_index, cancelled=cancelled, now=now,
                )

    def get(self, owner_id, execution_id):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.console_executions WHERE owner_id = %s AND id = %s",
                    (owner_id, execution_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ConsoleNotFoundError()
                return self._record_from_row(row, summaries=self._summaries(cursor, execution_id))

    def request_cancel(self, owner_id, execution_id, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_row(cursor, owner_id, execution_id)
                if row["status"] == "reserved":
                    return self._fail_locked(
                        cursor, row, code="postgres_console_cancelled",
                        message="Console execution was cancelled", statement_index=None,
                        cancelled=True, now=now,
                    )
                if row["status"] != "running":
                    return self._record_from_row(row, summaries=self._summaries(cursor, execution_id))
                cursor.execute(
                    """
                    UPDATE schemii.console_executions
                    SET cancel_requested = true, revision = revision + 1, updated_at = %s
                    WHERE owner_id = %s AND id = %s
                    RETURNING *
                    """,
                    (now, owner_id, execution_id),
                )
                return self._record_from_row(cursor.fetchone())

    def page(self, owner_id, workspace_id, execution_id, result_id, cursor, now):
        with self._transaction() as connection:
            with connection.cursor() as db_cursor:
                record = self._owned_execution(db_cursor, owner_id, workspace_id, execution_id)
                db_cursor.execute(
                    """
                    SELECT * FROM schemii.console_results
                    WHERE id = %s AND execution_id = %s
                    FOR UPDATE
                    """,
                    (result_id, execution_id),
                )
                result = db_cursor.fetchone()
                if result is None or result["closed_at"] is not None or result["expires_at"] <= now:
                    raise ConsoleResultGoneError()
                offset = 0
                if cursor is not None:
                    db_cursor.execute(
                        """
                        UPDATE schemii.console_result_cursors
                        SET consumed_at = %s
                        WHERE token = %s AND result_id = %s
                          AND consumed_at IS NULL AND expires_at > %s
                        RETURNING row_offset
                        """,
                        (now, cursor, result_id, now),
                    )
                    cursor_row = db_cursor.fetchone()
                    if cursor_row is None:
                        raise ConsoleResultGoneError()
                    offset = int(cursor_row["row_offset"])
                rows = list(result["rows_document"])
                page_rows = rows[offset : offset + record.page_size]
                next_offset = offset + len(page_rows)
                next_cursor = None
                if next_offset < len(rows):
                    next_cursor = f"crc_{secrets.token_hex(16)}"
                    db_cursor.execute(
                        """
                        INSERT INTO schemii.console_result_cursors
                            (token, result_id, row_offset, expires_at)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (next_cursor, result_id, next_offset, result["expires_at"]),
                    )
                return ConsoleResultPage(
                    execution_id=execution_id,
                    result_id=result_id,
                    columns=[ConsoleResultColumn.model_validate(item) for item in result["columns_document"]],
                    rows=page_rows,
                    next_cursor=next_cursor,
                    truncated=bool(result["truncated"]),
                    expires_at=result["expires_at"],
                )

    def close_result(self, owner_id, workspace_id, execution_id, result_id, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._owned_execution(cursor, owner_id, workspace_id, execution_id)
                cursor.execute(
                    """
                    UPDATE schemii.console_results SET closed_at = %s
                    WHERE id = %s AND execution_id = %s AND closed_at IS NULL
                    RETURNING id
                    """,
                    (now, result_id, execution_id),
                )
                if cursor.fetchone() is None:
                    raise ConsoleResultGoneError()

    def _locked_row(self, cursor, owner_id, execution_id):
        cursor.execute(
            "SELECT * FROM schemii.console_executions WHERE owner_id = %s AND id = %s FOR UPDATE",
            (owner_id, execution_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConsoleNotFoundError()
        return row

    def _owned_execution(self, cursor, owner_id, workspace_id, execution_id):
        cursor.execute(
            """
            SELECT * FROM schemii.console_executions
            WHERE owner_id = %s AND workspace_id = %s AND id = %s
            """,
            (owner_id, workspace_id, execution_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConsoleNotFoundError()
        return self._record_from_row(row)

    def _fail_locked(self, cursor, row, *, code, message, statement_index, cancelled, now):
        if row["status"] in {"succeeded", "failed", "cancelled"}:
            return self._record_from_row(row, summaries=self._summaries(cursor, row["id"]))
        cursor.execute(
            """
            UPDATE schemii.console_executions
            SET status = %s, revision = revision + 1, backend_pid = NULL,
                cancel_requested = cancel_requested OR %s,
                error_code = %s, error_message = %s,
                error_statement_index = %s, updated_at = %s
            WHERE id = %s RETURNING *
            """,
            (
                "cancelled" if cancelled else "failed", cancelled, code,
                message[:2048], statement_index, now, row["id"],
            ),
        )
        return self._record_from_row(cursor.fetchone())

    @staticmethod
    def _summaries(cursor, execution_id):
        cursor.execute(
            """
            SELECT console_results.id, statement_index, command, columns_document,
                   jsonb_array_length(rows_document) AS row_count,
                   jsonb_array_length(rows_document) > execution.page_size AS has_more
            FROM schemii.console_results
            JOIN schemii.console_executions AS execution
              ON execution.id = console_results.execution_id
            WHERE console_results.execution_id = %s ORDER BY statement_index
            """,
            (execution_id,),
        )
        return [
            ConsoleResultSummary(
                id=row["id"], statement_index=int(row["statement_index"]),
                command=row["command"],
                columns=[ConsoleResultColumn.model_validate(item) for item in row["columns_document"]],
                row_count=int(row["row_count"]), has_more=bool(row["has_more"]),
            )
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _record_from_row(row, *, summaries=None):
        execution = ConsoleExecution(
            id=row["id"], revision=int(row["revision"]),
            workspace_id=row["workspace_id"], console_id=row["console_id"],
            status=row["status"],
            completed_statement_indexes=list(row["completed_statement_indexes"]),
            results=summaries or [], error_code=row["error_code"],
            error_message=row["error_message"],
            error_statement_index=row["error_statement_index"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
        return ConsoleExecutionRecord(
            owner_id=row["owner_id"], execution=execution,
            workspace_revision=int(row["workspace_revision"]),
            target=ConsoleTarget(
                connection_id=row["connection_id"],
                connection_revision=int(row["connection_revision"]),
                database=row["database_name"], namespace=row["namespace"],
            ),
            statements=tuple(row["statements"]), page_size=int(row["page_size"]),
            backend_pid=row["backend_pid"],
            cancel_requested=bool(row["cancel_requested"]),
        )
