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
    ConsoleTransaction,
)
from .models import ConsoleHistoryEntry, ConsoleSavedQuery


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


class ConsoleLimitReachedError(ConsoleRepositoryError):
    def __init__(self, resource: str, limit: int, observed: int) -> None:
        self.resource = resource
        self.limit = limit
        self.observed = observed
        super().__init__(f"The {resource} limit has been reached")


class ConsoleStorageUnavailableError(ConsoleRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ConsoleTarget:
    connection_id: str
    connection_revision: int
    database: str
    namespace: str
    connection_owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConsoleExecutionRecord:
    owner_id: str
    execution: ConsoleExecution
    workspace_revision: int | None
    target: ConsoleTarget
    statements: tuple[str, ...]
    page_size: int
    backend_pid: int | None = None
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class ConsoleTransactionRecord:
    owner_id: str
    transaction: ConsoleTransaction
    workspace_revision: int
    target: ConsoleTarget
    backend_pid: int
    maximum_expires_at: datetime


@dataclass(frozen=True, slots=True)
class _StoredResult:
    id: str
    execution_id: str
    statement_index: int
    command: str
    columns: tuple[ConsoleResultColumn, ...]
    row_count: int | None
    truncated: bool
    replayable: bool
    expires_at: datetime
    closed_at: datetime | None = None


class ConsoleRepository(Protocol):
    def settings(self, owner_id: str) -> tuple[int, int | None]: ...

    def update_settings(self, owner_id: str, revision: int, page_size: int) -> None: ...

    def recover_interrupted(self, now: datetime) -> None: ...

    def prune_operational_receipts(self, before: datetime) -> None: ...

    def prune_history(self, limit: int) -> None: ...

    def reserve(
        self,
        owner_id: str,
        workspace_id: str | None,
        console_id: str,
        workspace_revision: int | None,
        target: ConsoleTarget,
        statements: tuple[str, ...],
        page_size: int,
        now: datetime,
        *,
        transaction_id: str | None = None,
        expected_transaction_revision: int | None = None,
        transaction_expires_at: datetime | None = None,
    ) -> ConsoleExecutionRecord: ...

    def recover_transactions(self, now: datetime) -> None: ...

    def create_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        console_id: str,
        workspace_revision: int,
        target: ConsoleTarget,
        backend_pid: int,
        created_at: datetime,
        expires_at: datetime,
        maximum_expires_at: datetime,
    ) -> ConsoleTransactionRecord: ...

    def get_transaction(
        self,
        owner_id: str,
        transaction_id: str,
    ) -> ConsoleTransactionRecord: ...

    def finish_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
        expected_revision: int,
        terminal_status: str,
        now: datetime,
    ) -> ConsoleTransactionRecord: ...

    def fail_transaction(
        self,
        owner_id: str,
        transaction_id: str,
        now: datetime,
    ) -> ConsoleTransactionRecord: ...

    def expire_transaction(
        self,
        owner_id: str,
        transaction_id: str,
        now: datetime,
    ) -> ConsoleTransactionRecord: ...

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

    def list_history(
        self, owner_id: str, workspace_id: str, limit: int
    ) -> list[ConsoleHistoryEntry]: ...

    def record_history(
        self,
        owner_id: str,
        workspace_id: str,
        sql: str,
        ran_at: datetime,
        limit: int,
    ) -> None: ...

    def list_saved_queries(
        self, owner_id: str, workspace_id: str
    ) -> list[ConsoleSavedQuery]: ...

    def create_saved_query(
        self, owner_id: str, workspace_id: str, name: str, sql: str,
        starter: bool, now: datetime, limit: int,
    ) -> ConsoleSavedQuery: ...

    def update_saved_query(
        self, owner_id: str, workspace_id: str, query_id: str,
        expected_revision: int, name: str | None, sql: str | None, now: datetime,
    ) -> ConsoleSavedQuery: ...

    def delete_saved_query(
        self, owner_id: str, workspace_id: str, query_id: str,
        expected_revision: int,
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
        self._transactions: dict[str, ConsoleTransactionRecord] = {}
        self._results: dict[str, _StoredResult] = {}
        self._cursors: dict[str, tuple[str, int, datetime, bool]] = {}
        self._saved_queries: dict[str, tuple[str, ConsoleSavedQuery]] = {}
        self._history: list[tuple[str, str, ConsoleHistoryEntry]] = []
        self._settings: dict[str, tuple[int, int]] = {}
        self._lock = threading.RLock()

    def settings(self, owner_id: str) -> tuple[int, int | None]:
        with self._lock:
            return self._settings.get(owner_id, (1, None))

    def update_settings(self, owner_id: str, revision: int, page_size: int) -> None:
        with self._lock:
            if self.settings(owner_id)[0] != revision:
                raise ConsoleConflictError("console_settings_changed", "Console preferences changed; reload them before saving")
            self._settings[owner_id] = (revision + 1, page_size)

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

    def recover_transactions(self, now: datetime) -> None:
        with self._lock:
            for identifier, record in list(self._transactions.items()):
                if record.transaction.status not in {"open", "failed"}:
                    continue
                self._transactions[identifier] = self._replace_transaction(
                    record,
                    transaction=record.transaction.model_copy(
                        update={
                            "revision": record.transaction.revision + 1,
                            "status": "expired",
                            "updated_at": now,
                            "expires_at": now,
                        }
                    ),
                )

    def prune_operational_receipts(self, before: datetime) -> None:
        with self._lock:
            removable = {
                identifier for identifier, record in self._records.items()
                if record.execution.status not in {"reserved", "running"}
                and record.execution.updated_at < before
            }
            for identifier in removable:
                self._records.pop(identifier, None)
            self._results = {
                identifier: result for identifier, result in self._results.items()
                if result.execution_id not in removable
            }
            removable_transactions = {
                identifier for identifier, record in self._transactions.items()
                if record.transaction.status not in {"open", "failed"}
                and record.transaction.updated_at < before
                and not any(
                    execution.execution.transaction_id == identifier
                    for execution in self._records.values()
                )
            }
            for identifier in removable_transactions:
                self._transactions.pop(identifier, None)

    def prune_history(self, limit: int) -> None:
        with self._lock:
            if limit <= 0:
                self._history.clear()
                return
            retained: list[tuple[str, str, ConsoleHistoryEntry]] = []
            counts: dict[tuple[str, str], int] = {}
            for value in sorted(
                self._history, key=lambda item: item[2].ran_at, reverse=True
            ):
                key = (value[0], value[1])
                if counts.get(key, 0) >= limit:
                    continue
                retained.append(value)
                counts[key] = counts.get(key, 0) + 1
            self._history = retained

    def reserve(
        self, owner_id, workspace_id, console_id, workspace_revision, target,
        statements, page_size, now, *, transaction_id=None,
        expected_transaction_revision=None, transaction_expires_at=None,
    ):
        with self._lock:
            self._purge(now)
            if any(
                item.owner_id == owner_id
                and item.execution.workspace_id == workspace_id
                and (workspace_id is not None or item.execution.console_id == console_id)
                and item.execution.status in {"reserved", "running"}
                for item in self._records.values()
            ):
                raise ConsoleConflictError(
                    "console_execution_active",
                    "This query editor already has an active execution" if workspace_id is None
                    else "This workspace already has an active Console execution",
                )
            identifier = f"cex_{secrets.token_hex(16)}"
            transaction_record = None
            if transaction_id is not None:
                transaction_record = self._transaction_record(owner_id, transaction_id)
                if transaction_record.transaction.workspace_id != workspace_id:
                    raise ConsoleNotFoundError()
                if transaction_record.transaction.status != "open":
                    raise ConsoleConflictError(
                        "console_transaction_not_open",
                        "The Console transaction is not open",
                    )
                if transaction_record.transaction.revision != expected_transaction_revision:
                    raise ConsoleConflictError(
                        "console_transaction_changed",
                        "The Console transaction changed after the editor loaded",
                    )
                if transaction_expires_at is None:
                    raise ConsoleConflictError(
                        "console_transaction_expired",
                        "The Console transaction expiry is unavailable",
                    )
            record = ConsoleExecutionRecord(
                owner_id=owner_id,
                execution=ConsoleExecution(
                    id=identifier,
                    revision=1,
                    workspace_id=workspace_id,
                    console_id=console_id,
                    transaction_id=transaction_id,
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
            if transaction_record is not None:
                self._transactions[transaction_id] = self._replace_transaction(
                    transaction_record,
                    transaction=transaction_record.transaction.model_copy(
                        update={
                            "revision": transaction_record.transaction.revision + 1,
                            "execution_ids": [
                                *transaction_record.transaction.execution_ids,
                                identifier,
                            ],
                            "updated_at": now,
                            "expires_at": transaction_expires_at,
                        }
                    ),
                )
            return record

    def create_transaction(
        self, owner_id, workspace_id, console_id, workspace_revision, target,
        backend_pid, created_at, expires_at, maximum_expires_at,
    ):
        with self._lock:
            if any(
                item.owner_id == owner_id
                and item.transaction.workspace_id == workspace_id
                and item.transaction.status in {"open", "failed"}
                for item in self._transactions.values()
            ):
                raise ConsoleConflictError(
                    "console_transaction_active",
                    "This workspace already has an open Console transaction",
                )
            identifier = f"ctx_{secrets.token_hex(16)}"
            record = ConsoleTransactionRecord(
                owner_id=owner_id,
                transaction=ConsoleTransaction(
                    id=identifier,
                    workspace_id=workspace_id,
                    console_id=console_id,
                    revision=1,
                    status="open",
                    execution_ids=[],
                    created_at=created_at,
                    updated_at=created_at,
                    expires_at=expires_at,
                ),
                workspace_revision=workspace_revision,
                target=target,
                backend_pid=backend_pid,
                maximum_expires_at=maximum_expires_at,
            )
            self._transactions[identifier] = record
            return record

    def get_transaction(self, owner_id, transaction_id):
        with self._lock:
            return self._transaction_record(owner_id, transaction_id)

    def finish_transaction(
        self, owner_id, workspace_id, transaction_id, expected_revision,
        terminal_status, now,
    ):
        if terminal_status not in {"committed", "rolled_back", "uncertain"}:
            raise ValueError("Unsupported Console transaction terminal status")
        with self._lock:
            record = self._transaction_record(owner_id, transaction_id)
            if record.transaction.workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            if record.transaction.revision != expected_revision:
                raise ConsoleConflictError(
                    "console_transaction_changed",
                    "The Console transaction changed after the confirmation opened",
                )
            if record.transaction.status not in {"open", "failed"}:
                return record
            updated = self._replace_transaction(
                record,
                transaction=record.transaction.model_copy(
                    update={
                        "revision": record.transaction.revision + 1,
                        "status": terminal_status,
                        "updated_at": now,
                        "expires_at": now,
                    }
                ),
            )
            self._transactions[transaction_id] = updated
            return updated

    def fail_transaction(self, owner_id, transaction_id, now):
        with self._lock:
            record = self._transaction_record(owner_id, transaction_id)
            if record.transaction.status != "open":
                return record
            updated = self._replace_transaction(
                record,
                transaction=record.transaction.model_copy(
                    update={
                        "revision": record.transaction.revision + 1,
                        "status": "failed",
                        "updated_at": now,
                    }
                ),
            )
            self._transactions[transaction_id] = updated
            return updated

    def expire_transaction(self, owner_id, transaction_id, now):
        with self._lock:
            record = self._transaction_record(owner_id, transaction_id)
            if record.transaction.status not in {"open", "failed"}:
                return record
            updated = self._replace_transaction(
                record,
                transaction=record.transaction.model_copy(
                    update={
                        "revision": record.transaction.revision + 1,
                        "status": "expired",
                        "updated_at": now,
                        "expires_at": now,
                    }
                ),
            )
            self._transactions[transaction_id] = updated
            return updated

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
                    statement_index=query.statement_index,
                    command=query.command,
                    columns=query.columns,
                    row_count=(query.row_count if query.row_count is not None else (None if query.replayable else len(query.rows))),
                    truncated=query.truncated,
                    replayable=query.replayable,
                    expires_at=expires_at,
                )
                summaries.append(
                    ConsoleResultSummary(
                        id=result_id,
                        statement_index=query.statement_index,
                        command=query.command,
                        columns=list(query.columns),
                        row_count=(query.row_count if query.row_count is not None else (None if query.replayable else len(query.rows))),
                        has_more=(query.row_count > record.page_size if query.row_count is not None else query.replayable),
                        replayable=query.replayable,
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
            raise ConsoleResultGoneError()

    def close_result(self, owner_id, workspace_id, execution_id, result_id, now):
        with self._lock:
            record = self._record(owner_id, execution_id)
            if record.execution.workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            result = self._result(execution_id, result_id, now)
            self._results[result_id] = _StoredResult(
                id=result.id,
                execution_id=result.execution_id,
                statement_index=result.statement_index,
                command=result.command,
                columns=result.columns,
                row_count=result.row_count,
                truncated=result.truncated,
                replayable=result.replayable,
                expires_at=result.expires_at,
                closed_at=now,
            )

    def list_history(self, owner_id, workspace_id, limit):
        with self._lock:
            entries = [
                entry for history_owner, history_workspace, entry in self._history
                if history_owner == owner_id and history_workspace == workspace_id
            ]
            return sorted(
                entries,
                key=lambda entry: entry.ran_at,
                reverse=True,
            )[:limit]

    def record_history(self, owner_id, workspace_id, sql, ran_at, limit):
        if limit <= 0:
            return
        with self._lock:
            self._history.append(
                (owner_id, workspace_id, ConsoleHistoryEntry(sql=sql, ran_at=ran_at))
            )
            owned = [
                index for index, (history_owner, history_workspace, _entry)
                in enumerate(self._history)
                if history_owner == owner_id and history_workspace == workspace_id
            ]
            for index in reversed(owned[:-limit]):
                del self._history[index]

    def list_saved_queries(self, owner_id, workspace_id):
        with self._lock:
            return sorted(
                [query for query_owner, query in self._saved_queries.values()
                 if query_owner == owner_id and query.workspace_id == workspace_id],
                key=lambda query: (query.updated_at, query.id),
                reverse=True,
            )

    def create_saved_query(self, owner_id, workspace_id, name, sql, starter, now, limit):
        with self._lock:
            count = sum(
                query_owner == owner_id and query.workspace_id == workspace_id
                for query_owner, query in self._saved_queries.values()
            )
            if count >= limit:
                raise ConsoleLimitReachedError("saved queries", limit, count)
            if any(
                query_owner == owner_id
                and query.workspace_id == workspace_id
                and query.name.casefold() == name.casefold()
                for query_owner, query in self._saved_queries.values()
            ):
                raise ConsoleConflictError("console_saved_query_name_exists", "A saved query already uses this name")
            query = ConsoleSavedQuery(
                id=f"sq_{secrets.token_hex(16)}", workspace_id=workspace_id,
                revision=1, name=name, sql=sql, starter=starter,
                created_at=now, updated_at=now,
            )
            self._saved_queries[query.id] = (owner_id, query)
            return query

    def update_saved_query(
        self, owner_id, workspace_id, query_id, expected_revision, name, sql, now,
    ):
        with self._lock:
            stored = self._saved_queries.get(query_id)
            if stored is None or stored[0] != owner_id or stored[1].workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            query = stored[1]
            if query.revision != expected_revision:
                raise ConsoleConflictError("console_saved_query_changed", "The saved query changed after it was opened")
            next_name = name if name is not None else query.name
            if any(
                identifier != query_id and query_owner == owner_id
                and candidate.workspace_id == workspace_id
                and candidate.name.casefold() == next_name.casefold()
                for identifier, (query_owner, candidate) in self._saved_queries.items()
            ):
                raise ConsoleConflictError("console_saved_query_name_exists", "A saved query already uses this name")
            updated = query.model_copy(update={
                "revision": query.revision + 1,
                "name": next_name,
                "sql": sql if sql is not None else query.sql,
                "updated_at": now,
            })
            self._saved_queries[query_id] = (owner_id, updated)
            return updated

    def delete_saved_query(self, owner_id, workspace_id, query_id, expected_revision):
        with self._lock:
            stored = self._saved_queries.get(query_id)
            if stored is None or stored[0] != owner_id or stored[1].workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            if stored[1].revision != expected_revision:
                raise ConsoleConflictError("console_saved_query_changed", "The saved query changed after it was opened")
            del self._saved_queries[query_id]

    def _record(self, owner_id: str, execution_id: str) -> ConsoleExecutionRecord:
        record = self._records.get(execution_id)
        if record is None or record.owner_id != owner_id:
            raise ConsoleNotFoundError()
        return record

    def _transaction_record(
        self,
        owner_id: str,
        transaction_id: str,
    ) -> ConsoleTransactionRecord:
        record = self._transactions.get(transaction_id)
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

    @staticmethod
    def _replace_transaction(
        record: ConsoleTransactionRecord,
        **changes: Any,
    ) -> ConsoleTransactionRecord:
        values = {
            "owner_id": record.owner_id,
            "transaction": record.transaction,
            "workspace_revision": record.workspace_revision,
            "target": record.target,
            "backend_pid": record.backend_pid,
            "maximum_expires_at": record.maximum_expires_at,
            **changes,
        }
        return ConsoleTransactionRecord(**values)


class PostgresConsoleRepository:
    """PostgreSQL metadata adapter with atomic state transitions and cursors."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def settings(self, owner_id: str) -> tuple[int, int | None]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision, row_page_size FROM schemii.console_preferences WHERE owner_id = %s",
                    (owner_id,),
                )
                row = cursor.fetchone()
                return (row["revision"], row["row_page_size"]) if row else (1, None)

    def update_settings(self, owner_id: str, revision: int, page_size: int) -> None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                if revision == 1:
                    cursor.execute(
                        """INSERT INTO schemii.console_preferences (owner_id, revision, row_page_size)
                        VALUES (%s, 2, %s) ON CONFLICT (owner_id) DO NOTHING RETURNING revision""",
                        (owner_id, page_size),
                    )
                else:
                    cursor.execute(
                        """UPDATE schemii.console_preferences SET revision = revision + 1, row_page_size = %s
                        WHERE owner_id = %s AND revision = %s RETURNING revision""",
                        (page_size, owner_id, revision),
                    )
                if cursor.fetchone() is None:
                    raise ConsoleConflictError("console_settings_changed", "Console preferences changed; reload them before saving")

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
                constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
                if constraint == "console_transactions_one_active_workspace":
                    raise ConsoleConflictError(
                        "console_transaction_active",
                        "This workspace already has an open Console transaction",
                    ) from error
                if constraint == "console_saved_queries_owner_workspace_name":
                    raise ConsoleConflictError(
                        "console_saved_query_name_exists",
                        "A saved query already uses this name",
                    ) from error
                if constraint == "console_executions_one_active_console":
                    raise ConsoleConflictError(
                        "console_execution_active",
                        "This query editor already has an active execution",
                    ) from error
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

    def prune_operational_receipts(self, before):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM schemii.console_executions
                    WHERE status NOT IN ('reserved', 'running')
                      AND updated_at < %s
                    """,
                    (before,),
                )
                cursor.execute(
                    """
                    DELETE FROM schemii.console_transactions AS transaction
                    WHERE status NOT IN ('open', 'failed')
                      AND updated_at < %s
                      AND NOT EXISTS (
                          SELECT 1 FROM schemii.console_executions AS execution
                          WHERE execution.transaction_id = transaction.id
                      )
                    """,
                    (before,),
                )

    def prune_history(self, limit):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                if limit <= 0:
                    cursor.execute("DELETE FROM schemii.console_query_history")
                    return
                cursor.execute(
                    """
                    DELETE FROM schemii.console_query_history
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id, row_number() OVER (
                                PARTITION BY owner_id, workspace_id
                                ORDER BY ran_at DESC, id DESC
                            ) AS retained_position
                            FROM schemii.console_query_history
                        ) AS ranked
                        WHERE retained_position > %s
                    )
                    """,
                    (limit,),
                )

    def recover_transactions(self, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE schemii.console_transactions
                    SET status = 'expired', revision = revision + 1,
                        updated_at = %s, expires_at = LEAST(%s, maximum_expires_at)
                    WHERE status IN ('open', 'failed')
                    """,
                    (now, now),
                )

    def reserve(
        self, owner_id, workspace_id, console_id, workspace_revision, target,
        statements, page_size, now, *, transaction_id=None,
        expected_transaction_revision=None, transaction_expires_at=None,
    ):
        identifier = f"cex_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                transaction_row = None
                if transaction_id is not None:
                    transaction_row = self._locked_transaction(
                        cursor, owner_id, transaction_id
                    )
                    if transaction_row["workspace_id"] != workspace_id:
                        raise ConsoleNotFoundError()
                    if transaction_row["status"] != "open":
                        raise ConsoleConflictError(
                            "console_transaction_not_open",
                            "The Console transaction is not open",
                        )
                    if int(transaction_row["revision"]) != expected_transaction_revision:
                        raise ConsoleConflictError(
                            "console_transaction_changed",
                            "The Console transaction changed after the editor loaded",
                        )
                    if (
                        transaction_expires_at is None
                        or transaction_expires_at > transaction_row["maximum_expires_at"]
                        or transaction_row["expires_at"] <= now
                    ):
                        raise ConsoleConflictError(
                            "console_transaction_expired",
                            "The Console transaction has expired",
                        )
                cursor.execute(
                    """
                    INSERT INTO schemii.console_executions (
                        id, owner_id, workspace_id, console_id, workspace_revision,
                        connection_id, connection_revision, database_name, namespace,
                        transaction_id, status, statements, page_size, created_at, updated_at, connection_owner_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, 'reserved', %s::jsonb, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        identifier, owner_id, workspace_id, console_id,
                        workspace_revision, target.connection_id,
                        target.connection_revision, target.database, target.namespace,
                        transaction_id, json.dumps(statements), page_size, now, now, target.connection_owner_id or owner_id,
                    ),
                )
                record = self._record_from_row(cursor.fetchone())
                if transaction_row is not None:
                    cursor.execute(
                        """
                        UPDATE schemii.console_transactions
                        SET revision = revision + 1, updated_at = %s, expires_at = %s
                        WHERE owner_id = %s AND id = %s AND revision = %s
                          AND status = 'open'
                        RETURNING id
                        """,
                        (
                            now,
                            transaction_expires_at,
                            owner_id,
                            transaction_id,
                            expected_transaction_revision,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise ConsoleConflictError(
                            "console_transaction_changed",
                            "The Console transaction changed while reserving execution",
                        )
                return record

    def create_transaction(
        self, owner_id, workspace_id, console_id, workspace_revision, target,
        backend_pid, created_at, expires_at, maximum_expires_at,
    ):
        identifier = f"ctx_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO schemii.console_transactions (
                        id, owner_id, workspace_id, console_id, workspace_revision,
                        connection_owner_id, connection_id, connection_revision, database_name, namespace,
                        backend_pid, status, created_at, updated_at, expires_at,
                        maximum_expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, 'open', %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        identifier, owner_id, workspace_id, console_id,
                        workspace_revision, target.connection_owner_id or owner_id, target.connection_id,
                        target.connection_revision, target.database, target.namespace,
                        backend_pid, created_at, created_at, expires_at,
                        maximum_expires_at,
                    ),
                )
                return self._transaction_from_row(cursor, cursor.fetchone())

    def get_transaction(self, owner_id, transaction_id):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM schemii.console_transactions
                    WHERE owner_id = %s AND id = %s
                    """,
                    (owner_id, transaction_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ConsoleNotFoundError()
                return self._transaction_from_row(cursor, row)

    def finish_transaction(
        self, owner_id, workspace_id, transaction_id, expected_revision,
        terminal_status, now,
    ):
        if terminal_status not in {"committed", "rolled_back", "uncertain"}:
            raise ValueError("Unsupported Console transaction terminal status")
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_transaction(cursor, owner_id, transaction_id)
                if row["workspace_id"] != workspace_id:
                    raise ConsoleNotFoundError()
                if row["status"] not in {"open", "failed"}:
                    return self._transaction_from_row(cursor, row)
                if int(row["revision"]) != expected_revision:
                    raise ConsoleConflictError(
                        "console_transaction_changed",
                        "The Console transaction changed after the confirmation opened",
                    )
                cursor.execute(
                    """
                    UPDATE schemii.console_transactions
                    SET status = %s, revision = revision + 1,
                        updated_at = %s, expires_at = %s
                    WHERE owner_id = %s AND id = %s AND revision = %s
                    RETURNING *
                    """,
                    (
                        terminal_status, now, now, owner_id, transaction_id,
                        expected_revision,
                    ),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise ConsoleConflictError(
                        "console_transaction_changed",
                        "The Console transaction changed while it was closing",
                    )
                return self._transaction_from_row(cursor, updated)

    def fail_transaction(self, owner_id, transaction_id, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_transaction(cursor, owner_id, transaction_id)
                if row["status"] != "open":
                    return self._transaction_from_row(cursor, row)
                cursor.execute(
                    """
                    UPDATE schemii.console_transactions
                    SET status = 'failed', revision = revision + 1, updated_at = %s
                    WHERE owner_id = %s AND id = %s AND status = 'open'
                    RETURNING *
                    """,
                    (now, owner_id, transaction_id),
                )
                return self._transaction_from_row(cursor, cursor.fetchone())

    def expire_transaction(self, owner_id, transaction_id, now):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._locked_transaction(cursor, owner_id, transaction_id)
                if row["status"] not in {"open", "failed"}:
                    return self._transaction_from_row(cursor, row)
                cursor.execute(
                    """
                    UPDATE schemii.console_transactions
                    SET status = 'expired', revision = revision + 1,
                        updated_at = %s, expires_at = %s
                    WHERE owner_id = %s AND id = %s
                    RETURNING *
                    """,
                    (now, now, owner_id, transaction_id),
                )
                return self._transaction_from_row(cursor, cursor.fetchone())

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
                            columns_document, row_count, replayable,
                            truncated, expires_at
                        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                        """,
                        (
                            result_id, execution_id, query.statement_index, query.command,
                            json.dumps([column.model_dump(mode="json", by_alias=True) for column in query.columns]),
                            (query.row_count if query.row_count is not None else (None if query.replayable else len(query.rows))),
                            query.replayable, query.truncated, expires_at,
                        ),
                    )
                    summaries.append(
                        ConsoleResultSummary(
                            id=result_id,
                            statement_index=query.statement_index,
                            command=query.command,
                            columns=list(query.columns),
                            row_count=(query.row_count if query.row_count is not None else (None if query.replayable else len(query.rows))),
                            has_more=(query.row_count > int(row["page_size"]) if query.row_count is not None else query.replayable),
                            replayable=query.replayable,
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
                self._owned_execution(db_cursor, owner_id, workspace_id, execution_id)
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
                # Raw values are process-bound data-plane state. The repository
                # verifies ownership/lifecycle only and can never reconstruct a
                # page from metadata.
                raise ConsoleResultGoneError()

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

    def list_history(self, owner_id, workspace_id, limit):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT sql, ran_at FROM schemii.console_query_history
                    WHERE owner_id = %s AND workspace_id = %s
                    ORDER BY ran_at DESC, id DESC
                    LIMIT %s
                    """,
                    (owner_id, workspace_id, limit),
                )
                return [ConsoleHistoryEntry(sql=row["sql"], ran_at=row["ran_at"])
                        for row in cursor.fetchall()]

    def record_history(self, owner_id, workspace_id, sql, ran_at, limit):
        if limit <= 0:
            return
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO schemii.console_query_history
                        (owner_id, workspace_id, sql, ran_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (owner_id, workspace_id, sql, ran_at),
                )
                cursor.execute(
                    """
                    DELETE FROM schemii.console_query_history
                    WHERE id IN (
                        SELECT id FROM schemii.console_query_history
                        WHERE owner_id = %s AND workspace_id = %s
                        ORDER BY ran_at DESC, id DESC
                        OFFSET %s
                    )
                    """,
                    (owner_id, workspace_id, limit),
                )

    def list_saved_queries(self, owner_id, workspace_id):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM schemii.console_saved_queries
                    WHERE owner_id = %s AND workspace_id = %s
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (owner_id, workspace_id),
                )
                return [self._saved_query(row) for row in cursor.fetchall()]

    def create_saved_query(self, owner_id, workspace_id, name, sql, starter, now, limit):
        identifier = f"sq_{secrets.token_hex(16)}"
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (owner_id, workspace_id),
                )
                if cursor.fetchone() is None:
                    raise ConsoleNotFoundError()
                cursor.execute(
                    """
                    SELECT count(*)::integer AS count
                    FROM schemii.console_saved_queries
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (owner_id, workspace_id),
                )
                count = int(cursor.fetchone()["count"])
                if count >= limit:
                    raise ConsoleLimitReachedError("saved queries", limit, count)
                cursor.execute(
                    """
                    INSERT INTO schemii.console_saved_queries
                        (id, owner_id, workspace_id, name, sql, starter,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (identifier, owner_id, workspace_id, name, sql, starter, now, now),
                )
                return self._saved_query(cursor.fetchone())

    def update_saved_query(
        self, owner_id, workspace_id, query_id, expected_revision, name, sql, now,
    ):
        assignments = []
        values: list[Any] = []
        if name is not None:
            assignments.append("name = %s")
            values.append(name)
        if sql is not None:
            assignments.append("sql = %s")
            values.append(sql)
        assignments.extend(["revision = revision + 1", "updated_at = %s"])
        values.extend([now, owner_id, workspace_id, query_id, expected_revision])
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE schemii.console_saved_queries
                    SET {', '.join(assignments)}
                    WHERE owner_id = %s AND workspace_id = %s AND id = %s
                      AND revision = %s
                    RETURNING *
                    """,
                    values,
                )
                row = cursor.fetchone()
                if row is None:
                    self._saved_query_conflict(cursor, owner_id, workspace_id, query_id)
                return self._saved_query(row)

    def delete_saved_query(self, owner_id, workspace_id, query_id, expected_revision):
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM schemii.console_saved_queries
                    WHERE owner_id = %s AND workspace_id = %s AND id = %s
                      AND revision = %s
                    RETURNING id
                    """,
                    (owner_id, workspace_id, query_id, expected_revision),
                )
                if cursor.fetchone() is None:
                    self._saved_query_conflict(cursor, owner_id, workspace_id, query_id)

    @staticmethod
    def _saved_query(row):
        return ConsoleSavedQuery(
            id=row["id"], workspace_id=row["workspace_id"],
            revision=int(row["revision"]), name=row["name"], sql=row["sql"],
            starter=bool(row.get("starter", False)),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _saved_query_conflict(cursor, owner_id, workspace_id, query_id):
        cursor.execute(
            """
            SELECT revision FROM schemii.console_saved_queries
            WHERE owner_id = %s AND workspace_id = %s AND id = %s
            """,
            (owner_id, workspace_id, query_id),
        )
        if cursor.fetchone() is None:
            raise ConsoleNotFoundError()
        raise ConsoleConflictError(
            "console_saved_query_changed",
            "The saved query changed after it was opened",
        )

    def _locked_row(self, cursor, owner_id, execution_id):
        cursor.execute(
            "SELECT * FROM schemii.console_executions WHERE owner_id = %s AND id = %s FOR UPDATE",
            (owner_id, execution_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConsoleNotFoundError()
        return row

    @staticmethod
    def _locked_transaction(cursor, owner_id, transaction_id):
        cursor.execute(
            """
            SELECT * FROM schemii.console_transactions
            WHERE owner_id = %s AND id = %s FOR UPDATE
            """,
            (owner_id, transaction_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConsoleNotFoundError()
        return row

    def _owned_execution(self, cursor, owner_id, workspace_id, execution_id):
        cursor.execute(
            """
            SELECT * FROM schemii.console_executions
            WHERE owner_id = %s AND workspace_id IS NOT DISTINCT FROM %s AND id = %s
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
                   row_count, replayable,
                   COALESCE(row_count > execution.page_size, replayable) AS has_more
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
                row_count=(int(row["row_count"]) if row["row_count"] is not None else None),
                has_more=bool(row["has_more"]),
                replayable=bool(row["replayable"]),
            )
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _record_from_row(row, *, summaries=None):
        execution = ConsoleExecution(
            id=row["id"], revision=int(row["revision"]),
            workspace_id=row["workspace_id"], console_id=row["console_id"],
            transaction_id=row.get("transaction_id"),
            status=row["status"],
            completed_statement_indexes=list(row["completed_statement_indexes"]),
            results=summaries or [], error_code=row["error_code"],
            error_message=row["error_message"],
            error_statement_index=row["error_statement_index"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
        return ConsoleExecutionRecord(
            owner_id=row["owner_id"], execution=execution,
            workspace_revision=(int(row["workspace_revision"]) if row["workspace_revision"] is not None else None),
            target=ConsoleTarget(
                connection_id=row["connection_id"],
                connection_revision=int(row["connection_revision"]),
                database=row["database_name"], namespace=row["namespace"],
                connection_owner_id=(row.get("connection_owner_id") if row.get("connection_owner_id") != row["owner_id"] else None),
            ),
            statements=tuple(row["statements"]), page_size=int(row["page_size"]),
            backend_pid=row["backend_pid"],
            cancel_requested=bool(row["cancel_requested"]),
        )

    @staticmethod
    def _transaction_from_row(cursor, row):
        cursor.execute(
            """
            SELECT id FROM schemii.console_executions
            WHERE transaction_id = %s ORDER BY created_at, id
            """,
            (row["id"],),
        )
        execution_ids = [item["id"] for item in cursor.fetchall()]
        return ConsoleTransactionRecord(
            owner_id=row["owner_id"],
            transaction=ConsoleTransaction(
                id=row["id"],
                workspace_id=row["workspace_id"],
                console_id=row["console_id"],
                revision=int(row["revision"]),
                status=row["status"],
                execution_ids=execution_ids,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                expires_at=row["expires_at"],
            ),
            workspace_revision=int(row["workspace_revision"]),
            target=ConsoleTarget(
                connection_id=row["connection_id"],
                connection_revision=int(row["connection_revision"]),
                database=row["database_name"],
                namespace=row["namespace"],
                connection_owner_id=(row.get("connection_owner_id") if row.get("connection_owner_id") != row["owner_id"] else None),
            ),
            backend_pid=int(row["backend_pid"]),
            maximum_expires_at=row["maximum_expires_at"],
        )
