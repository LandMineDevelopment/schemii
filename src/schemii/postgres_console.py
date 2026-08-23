from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .postgres_common import NotFoundError, PostgresServiceError, ValidationError, narrow_statement_timeout, postgres_error_details, quote_identifier
from .result_limits import ResultLimitError, ResultLimiter, ResultLimits, json_utf8_size


MAX_STATEMENTS = 100
MAX_SCRIPT_CHARS = 100_000
MAX_ROWS_PER_RESULT = 500
MAX_COLUMNS_PER_RESULT = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CELL_BYTES = 64 * 1024
MAX_ROW_BYTES = 256 * 1024
MAX_CELL_NESTING = 8
MAX_COLLECTION_ITEMS = 1000
MAX_NOTICES = 50
MAX_NOTICE_BYTES = 8 * 1024
MAX_ACTIVE_EXECUTIONS = 4
MAX_ACTIVE_RESULTS = 32
MAX_RETAINED_SNAPSHOTS = 4
MAX_SPOOL_ROWS = 10_000
MAX_SPOOL_BYTES = 8 * 1024 * 1024
RESULT_TTL_SECONDS = 300
MAX_RESULT_TOMBSTONES = 256
MAX_TRANSACTION_TOMBSTONES = 256


def _utc_expiry(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ConsolePolicy:
    allow_write: bool = False
    human_write_intent: bool = False
    statement_limit: int | None = None
    operation_timeout_ms: int | None = None


def _scan_sql(value: str) -> tuple[list[int], list[str]]:
    semicolons: list[int] = []
    words: list[str] = []
    quote = None
    escape_string = False
    dollar_quote = None
    block_depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if dollar_quote:
            if value.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue
        if quote:
            if character == quote:
                if following == quote:
                    index += 2
                    continue
                quote = None
                escape_string = False
            elif character == "\\" and quote == "'" and escape_string and following:
                index += 2
                continue
            index += 1
            continue
        if block_depth:
            if character == "/" and following == "*":
                block_depth += 1
                index += 2
            elif character == "*" and following == "/":
                block_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if character == "-" and following == "-":
            newline = value.find("\n", index + 2)
            index = len(value) if newline == -1 else newline + 1
            continue
        if character == "/" and following == "*":
            block_depth = 1
            index += 2
            continue
        if character in {"'", '"'}:
            quote = character
            escape_string = (
                character == "'" and index > 0 and value[index - 1] in {"e", "E"}
                and (index < 2 or not (value[index - 2].isalnum() or value[index - 2] in {"_", "$"}))
            )
            index += 1
            continue
        if character == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", value[index:])
            if match:
                dollar_quote = match.group(0)
                index += len(dollar_quote)
                continue
        if character == ";":
            semicolons.append(index)
            index += 1
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(value) and (value[end].isalnum() or value[end] in {"_", "$"}):
                end += 1
            words.append(value[index:end].upper())
            index = end
            continue
        index += 1
    if quote or dollar_quote or block_depth:
        raise ValidationError("SQL contains an unterminated quote or comment")
    return semicolons, words


def top_level_semicolons(value: str) -> list[int]:
    return _scan_sql(value)[0]


def single_sql_statement(value: str, label: str) -> str:
    statement = value.strip()
    semicolons = top_level_semicolons(statement)
    if len(semicolons) > 1 or (semicolons and statement[semicolons[0] + 1:].strip()):
        raise ValidationError(f"{label} must contain exactly one SQL statement")
    return statement


def split_console_statements(value: Any, *, explicit: bool = False, statement_limit: int = MAX_STATEMENTS) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("sql must be a non-empty string")
    if "\x00" in value:
        raise ValidationError("sql must not contain null bytes")
    if len(value) > MAX_SCRIPT_CHARS:
        raise PostgresServiceError(400, "script_too_large", "SQL script exceeds the 100000-character limit")
    semicolons = top_level_semicolons(value)
    statements = []
    start = 0
    for end in [*semicolons, len(value)]:
        candidate = value[start:end].strip()
        if candidate and _scan_sql(candidate)[1]:
            statements.append(candidate)
        start = end + 1
    if not statements:
        raise ValidationError("sql must contain at least one statement")
    if len(statements) > statement_limit:
        raise PostgresServiceError(400, "too_many_statements", f"SQL script exceeds the {statement_limit}-statement Console limit")
    for statement in statements:
        words = _scan_sql(statement)[1]
        first = words[0] if words else ""
        transaction_control = first in {"BEGIN", "COMMIT", "END", "ROLLBACK", "ABORT", "SAVEPOINT", "RELEASE"}
        transaction_control = transaction_control or words[:2] in (["START", "TRANSACTION"], ["PREPARE", "TRANSACTION"], ["SET", "TRANSACTION"])
        transaction_control = transaction_control or words[:3] in (["SET", "LOCAL", "TRANSACTION"], ["SET", "SESSION", "TRANSACTION"])
        transaction_control = transaction_control or words[:4] == ["SET", "SESSION", "CHARACTERISTICS", "AS"] and len(words) > 4 and words[4] == "TRANSACTION"
        savepoint_control = first in {"SAVEPOINT", "RELEASE"} or words[:2] == ["ROLLBACK", "TO"] or words[:3] in (
            ["ROLLBACK", "WORK", "TO"], ["ROLLBACK", "TRANSACTION", "TO"],
        )
        if transaction_control and not (explicit and savepoint_control):
            raise PostgresServiceError(400, "unsupported_transaction_control", "Explicit transaction control is not supported")
    return statements


def _canonical_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a canonical UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{label} must be a canonical UUID string") from exc
    if str(parsed) != value:
        raise ValidationError(f"{label} must be a canonical UUID string")
    return value


class ConsoleExecutionRegistry:
    def __init__(self, maximum_active: int = MAX_ACTIVE_EXECUTIONS):
        self._maximum_active = maximum_active
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}

    def reserve(self, execution_id: str, console_id: str, profile_id: str, binding: str, server_id: str) -> None:
        with self._lock:
            if execution_id in self._entries:
                raise PostgresServiceError(409, "execution_conflict", "The execution ID is already active")
            if len(self._entries) >= self._maximum_active or any(
                entry["consoleId"] == console_id for entry in self._entries.values()
            ):
                raise PostgresServiceError(429, "execution_busy", "Console execution capacity is busy")
            self._entries[execution_id] = {
                "consoleId": console_id, "profileId": profile_id, "binding": binding,
                "serverId": server_id, "connection": None, "cancelRequested": False,
            }

    def attach(self, execution_id: str, connection: Any) -> bool:
        with self._lock:
            entry = self._entries[execution_id]
            entry["connection"] = connection
            requested = entry["cancelRequested"]
        if requested:
            connection.cancel()
        return requested

    def cancel(self, execution_id: str, profile_id: str, binding: str, server_id: str) -> dict[str, bool]:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or (entry["profileId"], entry["binding"], entry["serverId"]) != (profile_id, binding, server_id):
                raise PostgresServiceError(404, "execution_not_found", "Console execution was not found")
            entry["cancelRequested"] = True
            connection = entry["connection"]
        if connection is not None:
            try:
                connection.cancel()
            except Exception:
                pass
        return {"requested": True}

    def cancel_requested(self, execution_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(execution_id)
            return bool(entry and entry["cancelRequested"])

    def release(self, execution_id: str) -> None:
        with self._lock:
            self._entries.pop(execution_id, None)

    def close(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            for entry in entries:
                entry["cancelRequested"] = True
        for entry in entries:
            if entry["connection"] is not None:
                try:
                    entry["connection"].cancel()
                except Exception:
                    pass


class ExplicitTransactionRegistry:
    def __init__(self, service: Any, maximum_active: int, idle_seconds: int, lifetime_seconds: int):
        self.service = service
        self.maximum_active = maximum_active
        self.idle_seconds = idle_seconds
        self.lifetime_seconds = lifetime_seconds
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._stopping = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, name="console-transaction-expiry", daemon=True)
        self._sweeper.start()

    @staticmethod
    def state(connection: Any) -> str:
        status = getattr(getattr(connection, "info", None), "transaction_status", None)
        name = getattr(status, "name", None)
        if isinstance(name, str):
            return {"IDLE": "idle", "ACTIVE": "active", "INTRANS": "in_transaction",
                    "INERROR": "failed", "UNKNOWN": "unknown"}.get(name.upper(), "unknown")
        return {0: "idle", 1: "active", 2: "in_transaction", 3: "failed", 4: "unknown"}.get(status, "unknown")

    def add(self, transaction_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            if transaction_id in self._entries:
                raise PostgresServiceError(409, "transaction_conflict", "Transaction ID is already active")
            if len(self._entries) >= self.maximum_active:
                raise PostgresServiceError(
                    429, "transaction_capacity_exhausted",
                    "Console explicit transaction capacity is exhausted",
                    {"limitSource": "application", "maximumActiveTransactions": self.maximum_active},
                )
            now = self.service._clock()
            entry.update({"createdAtEpoch": now, "lastActivityAtEpoch": now})
            self._entries[transaction_id] = entry

    @staticmethod
    def _matches(entry: dict[str, Any], owner: dict[str, Any]) -> bool:
        return all(entry.get(key) == value for key, value in owner.items())

    def _sweep_loop(self) -> None:
        interval = min(30, max(1, min(self.idle_seconds, self.lifetime_seconds)))
        while not self._stopping.wait(interval):
            self.expire()

    def _tombstone(self, transaction_id: str, entry: dict[str, Any], state: str) -> None:
        self._tombstones[transaction_id] = {
            "owner": {key: entry[key] for key in ("application", "binding", "serverId", "profileId", "profileFingerprint")},
            "state": state, "closedAt": _utc_expiry(self.service._clock()),
        }
        while len(self._tombstones) > MAX_TRANSACTION_TOMBSTONES:
            self._tombstones.pop(next(iter(self._tombstones)))

    def require(self, transaction_id: str, owner: dict[str, Any], *, touch: bool = True) -> dict[str, Any]:
        self.expire()
        with self._lock:
            entry = self._entries.get(transaction_id)
            tombstone = self._tombstones.get(transaction_id)
            if entry is not None and self._matches(entry, owner):
                if touch:
                    entry["lastActivityAtEpoch"] = self.service._clock()
                return entry
        if tombstone is not None and self._matches(tombstone["owner"], owner):
            state = tombstone["state"]
            raise PostgresServiceError(410, f"transaction_{state}", f"Console transaction is {state}", {
                "transactionId": transaction_id, "state": state,
                "policy": "connection_lifecycle", "closedAt": tombstone["closedAt"],
            })
        if entry is None or not self._matches(entry, owner):
            raise PostgresServiceError(404, "transaction_not_found", "Console transaction was not found")
        raise PostgresServiceError(404, "transaction_not_found", "Console transaction was not found")

    def remove(self, transaction_id: str, state: str = "closed") -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.pop(transaction_id, None)
            if entry is not None:
                self._tombstone(transaction_id, entry, state)
            return entry

    def expire(self) -> None:
        now = self.service._clock()
        with self._lock:
            candidates = [
                (transaction_id, entry) for transaction_id, entry in self._entries.items()
                if now - entry["lastActivityAtEpoch"] >= self.idle_seconds
                or now - entry["createdAtEpoch"] >= self.lifetime_seconds
            ]
        for transaction_id, entry in candidates:
            if not entry["lock"].acquire(blocking=False):
                continue
            try:
                with self._lock:
                    current = self._entries.get(transaction_id)
                    now = self.service._clock()
                    if current is not entry or (
                        now - entry["lastActivityAtEpoch"] < self.idle_seconds
                        and now - entry["createdAtEpoch"] < self.lifetime_seconds
                    ):
                        continue
                    self._entries.pop(transaction_id)
                    self._tombstone(transaction_id, entry, "expired")
                self.service._console.results.close_matching({"transactionId": transaction_id}, "transaction_expired")
                try:
                    entry["connection"].rollback()
                except Exception:
                    pass
                self.service._close(entry["connection"])
            finally:
                entry["lock"].release()

    def close(self) -> None:
        self._stopping.set()
        with self._lock:
            entries = list(self._entries.items())
            self._entries.clear()
            for transaction_id, entry in entries:
                self._tombstone(transaction_id, entry, "closed")
        for _, entry in entries:
            with entry["lock"]:
                self.service._console.results.close_matching(
                    {"transactionId": entry.get("transactionId")}, "transaction_shutdown",
                ) if entry.get("transactionId") else None
            try:
                entry["connection"].rollback()
            except Exception:
                pass
            self.service._close(entry["connection"])
        if self._sweeper is not threading.current_thread():
            self._sweeper.join(timeout=1)


class ConsoleResultRegistry:
    """Owns transient Console rows without ever retaining or replaying SQL."""

    def __init__(self, console: Any, maximum_active: int = MAX_ACTIVE_RESULTS,
                 ttl_seconds: int = RESULT_TTL_SECONDS, clock: Any = time.time):
        self.console = console
        self.maximum_active = maximum_active
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._stopping = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, name="console-result-expiry", daemon=True)
        self._sweeper.start()

    @staticmethod
    def _matches(entry: dict[str, Any], owner: dict[str, Any]) -> bool:
        return all(entry["owner"].get(key) == value for key, value in owner.items())

    def _sweep_loop(self) -> None:
        while not self._stopping.wait(min(30, max(1, self.ttl_seconds))):
            self.expire()

    def _tombstone(self, entry: dict[str, Any], event: str) -> None:
        self._tombstones[entry["resultId"]] = {
            "owner": entry["owner"], "state": event,
            "closedAt": _utc_expiry(self.clock()),
        }
        while len(self._tombstones) > MAX_RESULT_TOMBSTONES:
            self._tombstones.pop(next(iter(self._tombstones)))

    @staticmethod
    def _close_entry(entry: dict[str, Any]) -> None:
        close = getattr(entry.get("cursor"), "close", None)
        if close:
            try:
                close()
            except Exception:
                pass
        cleanup = entry.get("cleanup")
        if cleanup:
            cleanup()

    def _remove(self, result_id: str, event: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.pop(result_id, None)
            if entry is not None:
                self._tombstone(entry, event)
        if entry is not None:
            self._close_entry(entry)
        return entry

    def expire(self) -> None:
        now = self.clock()
        with self._lock:
            expired = [entry for entry in self._entries.values() if entry["expiresAtEpoch"] <= now]
        for entry in expired:
            lock = entry["operationLock"]
            if not lock.acquire(blocking=False):
                continue
            try:
                self._remove(entry["resultId"], "expired")
            finally:
                lock.release()

    def add(self, *, owner: dict[str, Any], columns: list[dict[str, str]], page_size: int,
            retention: str, cursor: Any = None, rows: list[list[Any]] | None = None,
            pending: list[list[Any]] | None = None, cleanup: Any = None,
            operation_lock: Any = None, truncation_events: list[dict[str, Any]] | None = None,
            response_byte_limit: int = MAX_RESPONSE_BYTES) -> dict[str, Any]:
        self.expire()
        with self._lock:
            if self._stopping.is_set():
                raise PostgresServiceError(503, "console_shutting_down", "Console result retention is shutting down")
            if len(self._entries) >= self.maximum_active:
                raise PostgresServiceError(
                    429, "console_result_capacity_exhausted",
                    "Console result retention capacity is exhausted; close or exhaust another result",
                    {"limitSource": "application", "maximumActiveResults": self.maximum_active},
                )
            if cursor is not None:
                retained = {
                    (item["owner"].get("transactionId") or item["owner"]["executionId"])
                    for item in self._entries.values() if item.get("cursor") is not None
                }
                snapshot_id = owner.get("transactionId") or owner["executionId"]
                if snapshot_id not in retained and len(retained) >= MAX_RETAINED_SNAPSHOTS:
                    raise PostgresServiceError(
                        429, "console_result_capacity_exhausted",
                        "Console retained-snapshot capacity is exhausted; close or exhaust another result",
                        {"limitSource": "application", "maximumRetainedSnapshots": MAX_RETAINED_SNAPSHOTS},
                    )
            result_id = secrets.token_urlsafe(24)
            expires = self.clock() + self.ttl_seconds
            entry = {
                "resultId": result_id, "owner": dict(owner), "columns": columns,
                "pageSize": page_size, "retention": retention, "cursor": cursor,
                "rows": rows, "offset": 0, "pending": list(pending or []),
                "cleanup": cleanup, "operationLock": operation_lock or threading.Lock(),
                "cursorToken": None, "expiresAtEpoch": expires,
                "expiresAt": _utc_expiry(expires), "truncationEvents": list(truncation_events or []),
                "responseByteLimit": response_byte_limit,
            }
            self._entries[result_id] = entry
            return entry

    def _require(self, result_id: str, owner: dict[str, Any], cursor_token: str | None) -> dict[str, Any]:
        self.expire()
        with self._lock:
            entry = self._entries.get(result_id)
            tombstone = self._tombstones.get(result_id)
        if entry is None:
            if tombstone is not None and self._matches(tombstone, owner):
                state = tombstone["state"]
                raise PostgresServiceError(410, f"result_{state}", f"Console result is {state}", {"resultId": result_id})
            raise PostgresServiceError(404, "result_not_found", "Console result was not found")
        if not self._matches(entry, owner):
            raise PostgresServiceError(404, "result_not_found", "Console result was not found")
        if cursor_token is not None and not secrets.compare_digest(cursor_token, entry.get("cursorToken") or ""):
            raise PostgresServiceError(409, "result_cursor_stale", "Console result cursor is stale or belongs to another page")
        return entry

    def first_page(self, entry: dict[str, Any]) -> dict[str, Any]:
        return self._page(entry, initial=True)

    def page(self, result_id: str, owner: dict[str, Any], cursor_token: Any) -> dict[str, Any]:
        if not isinstance(cursor_token, str) or not cursor_token:
            raise ValidationError("cursor is required")
        entry = self._require(result_id, owner, cursor_token)
        lock = entry["operationLock"]
        if not lock.acquire(blocking=False):
            raise PostgresServiceError(409, "result_busy", "Console result or transaction is executing another operation")
        try:
            with self._lock:
                entry = self._entries.get(result_id)
            if entry is None:
                return self._require(result_id, owner, cursor_token)
            if entry["expiresAtEpoch"] <= self.clock():
                self._remove(result_id, "expired")
                raise PostgresServiceError(410, "result_expired", "Console result is expired", {"resultId": result_id})
            if not self._matches(entry, owner):
                raise PostgresServiceError(404, "result_not_found", "Console result was not found")
            if not secrets.compare_digest(cursor_token, entry.get("cursorToken") or ""):
                raise PostgresServiceError(409, "result_cursor_stale", "Console result cursor is stale or belongs to another page")
            try:
                return self._page(entry, initial=False)
            except Exception:
                self._remove(result_id, "transport_error")
                raise
        finally:
            lock.release()

    def _page(self, entry: dict[str, Any], *, initial: bool) -> dict[str, Any]:
        page_size = entry["pageSize"]
        byte_limit = entry["responseByteLimit"] if initial else MAX_RESPONSE_BYTES
        if entry["rows"] is not None:
            start = entry["offset"]
            candidates = entry["rows"][start:start + page_size]
            page_rows = self._fit_rows(candidates, byte_limit)
            entry["offset"] += len(page_rows)
            has_more = entry["offset"] < len(entry["rows"])
        else:
            raw_rows = list(entry["pending"])
            entry["pending"].clear()
            needed = page_size + 1 - len(raw_rows)
            if needed > 0:
                fetchmany = getattr(entry["cursor"], "fetchmany", None)
                raw_rows.extend(fetchmany(needed) if fetchmany else entry["cursor"].fetchall()[:needed])
            has_more = len(raw_rows) > page_size
            if has_more:
                entry["pending"].append(raw_rows.pop())
            page_rows, page_events = self.console._normalize_rows(
                raw_rows, [column["name"] for column in entry["columns"]], entry["owner"]["statementIndex"],
            )
            fitted = self._fit_rows(page_rows, byte_limit)
            if len(fitted) < len(page_rows):
                entry["pending"] = [*page_rows[len(fitted):], *entry["pending"]]
                has_more = True
            page_rows = fitted
            entry["truncationEvents"].extend(page_events)
        event = None
        if has_more:
            entry["cursorToken"] = secrets.token_urlsafe(32)
            next_cursor = entry["cursorToken"]
        else:
            next_cursor = None
            event = "exhausted"
            self._remove(entry["resultId"], event)
        page = {
            "resultId": entry["resultId"], "executionId": entry["owner"]["executionId"],
            "statementIndex": entry["owner"]["statementIndex"], "resultIndex": entry["owner"]["resultIndex"],
            "consoleId": entry["owner"]["consoleId"],
            "transactionId": entry["owner"].get("transactionId"),
            "target": {key: entry["owner"][key] for key in (
                "applicationId", "serverId", "profileId", "profileFingerprint", "database", "namespace",
            )},
            "columns": entry["columns"], "rows": page_rows, "pageSize": page_size,
            "returnedRows": len(page_rows), "hasMore": has_more, "nextCursor": next_cursor,
            "snapshotRetention": entry["retention"],
            "transactionRetention": entry["retention"] in {"managed_read_transaction", "explicit_transaction"},
            "expiresAt": entry["expiresAt"], "resourceState": "open" if has_more else "closed",
            "closureEvents": [event] if event else [],
            "truncationEvents": list(entry["truncationEvents"]),
        }
        page["truncated"] = bool(page["truncationEvents"])
        page["incomplete"] = has_more or page["truncated"]
        return page

    def _fit_rows(self, rows: list[list[Any]], byte_limit: int) -> list[list[Any]]:
        fitted = []
        size = 2048
        for row in rows:
            row_size = self.console._encoded_size(row)
            if size + row_size > byte_limit:
                break
            fitted.append(row)
            size += row_size
        return fitted

    def close_result(self, result_id: str, owner: dict[str, Any], event: str = "closed") -> dict[str, Any]:
        entry = self._require(result_id, owner, None)
        lock = entry["operationLock"]
        if not lock.acquire(blocking=False):
            raise PostgresServiceError(409, "result_busy", "Console result or transaction is executing another operation")
        try:
            entry = self._remove(result_id, event)
        finally:
            lock.release()
        return {"resultId": result_id, "closed": True, "closureEvents": [event],
                "executionId": owner["executionId"], "statementIndex": owner["statementIndex"],
                "resultIndex": owner["resultIndex"]}

    def close_matching(self, owner: dict[str, Any], event: str) -> list[dict[str, Any]]:
        with self._lock:
            matches = [entry for entry in self._entries.values() if self._matches(entry, owner)]
        closed = []
        for entry in matches:
            lock = entry["operationLock"]
            lock.acquire()
            try:
                with self._lock:
                    current = self._entries.get(entry["resultId"])
                if current is entry and self._matches(entry, owner) and self._remove(entry["resultId"], event):
                    closed.append({"resultId": entry["resultId"], "statementIndex": entry["owner"]["statementIndex"],
                                   "resultIndex": entry["owner"]["resultIndex"], "event": event})
            finally:
                lock.release()
        return closed

    def close(self) -> None:
        self._stopping.set()
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            lock = entry["operationLock"]
            lock.acquire()
            try:
                with self._lock:
                    current = self._entries.get(entry["resultId"])
                if current is entry:
                    self._remove(entry["resultId"], "shutdown")
            finally:
                lock.release()
        if self._sweeper is not threading.current_thread():
            self._sweeper.join(timeout=1)


class PostgresConsole:
    def __init__(self, service: Any, *, transaction_maximum: int = 4,
                 transaction_idle_seconds: int = 300, transaction_lifetime_seconds: int = 1800):
        self.service = service
        self.registry = ConsoleExecutionRegistry()
        self.transactions = ExplicitTransactionRegistry(
            service, transaction_maximum, transaction_idle_seconds, transaction_lifetime_seconds,
        )
        self.results = ConsoleResultRegistry(self, clock=service._clock)
        self._receipts: dict[str, dict[str, Any]] = {}
        self.result_limiter = ResultLimiter(ResultLimits(
            max_cell_bytes=MAX_CELL_BYTES, max_row_bytes=MAX_ROW_BYTES,
            max_result_bytes=MAX_RESPONSE_BYTES, max_nesting=MAX_CELL_NESTING,
            max_collection_items=MAX_COLLECTION_ITEMS,
        ))

    @staticmethod
    def _encoded_size(value: Any) -> int:
        return json_utf8_size(value)

    @staticmethod
    def _command(cursor: Any) -> str:
        status = getattr(cursor, "statusmessage", "")
        match = re.match(r"^[A-Z]+(?: [A-Z]+)?", status if isinstance(status, str) else "")
        return match.group(0)[:64] if match else "UNKNOWN"

    @staticmethod
    def _notice_collector(connection: Any) -> tuple[list[str], Any]:
        notices: list[str] = []

        def collect(diagnostic: Any) -> None:
            primary = getattr(diagnostic, "message_primary", None)
            text = primary if isinstance(primary, str) else str(diagnostic)
            text = " ".join(text.split())
            if text:
                notices.append(text)

        add_handler = getattr(connection, "add_notice_handler", None)
        if add_handler:
            add_handler(collect)
        return notices, collect

    @staticmethod
    def _take_notices(pending: list[str], remaining_count: int, remaining_bytes: int) -> tuple[list[str], int, int]:
        collected = []
        for notice in pending:
            if remaining_count <= 0 or remaining_bytes <= 0:
                break
            encoded = notice.encode("utf-8")
            if len(encoded) > remaining_bytes:
                notice = encoded[:remaining_bytes].decode("utf-8", errors="ignore")
                encoded = notice.encode("utf-8")
            if not notice:
                break
            collected.append(notice)
            remaining_count -= 1
            remaining_bytes -= len(encoded)
        pending.clear()
        return collected, remaining_count, remaining_bytes

    @staticmethod
    def _error(exc: Exception, statement_index: int, cancelled: bool) -> PostgresServiceError:
        if cancelled:
            return PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled", {"statementIndex": statement_index})
        diagnostic_details = postgres_error_details(exc, phase="execute", operation="console_statement")
        postgres = diagnostic_details["postgres"]
        sqlstate = postgres.get("sqlstate")
        details = {"statementIndex": statement_index, **diagnostic_details}
        if sqlstate:
            details["sqlstate"] = sqlstate
        if sqlstate == "57014":
            return PostgresServiceError(422, "sql_timeout", "PostgreSQL canceled the Console statement under its configured timeout", details)
        if sqlstate == "25001":
            return PostgresServiceError(
                422,
                "unsupported_in_transaction",
                "This command cannot run in the server-owned transaction",
                details,
            )
        return PostgresServiceError(422, "sql_query_failed", "Console SQL statement failed", details)

    @staticmethod
    def _canonical_mode(mode: Any) -> str:
        aliases = {"read": "managed_read", "write": "managed", "maintenance": "autocommit"}
        canonical = aliases.get(mode, mode)
        if canonical not in {"managed_read", "managed", "autocommit"}:
            raise ValidationError("mode must be managed_read, managed, autocommit, read, write, or maintenance")
        return canonical

    def _human_settings(self, supplied_revision: Any, supplied_fingerprint: Any, *, write: bool) -> dict[str, Any]:
        metadata = getattr(self.service, "_metadata_store", None)
        if metadata is None:
            raise PostgresServiceError(503, "console_settings_unavailable", "Durable Console settings are unavailable")
        settings = metadata.get_console_settings(self.service._application_name)
        if write and settings["writeIntent"] != "enabled":
            raise PostgresServiceError(403, "console_write_intent_disabled", "Human Console write intent is disabled")
        if isinstance(supplied_revision, bool) or supplied_revision != settings["revision"]:
            raise PostgresServiceError(409, "console_settings_changed", "Console settings changed; refresh before writing", {"currentRevision": settings["revision"]})
        if supplied_fingerprint is None:
            raise ValidationError("profileFingerprint is required for human writes")
        return settings

    def _receipt(self, context: dict[str, Any], state: str, outcome: str, completed: list[int], error: PostgresServiceError | None = None) -> dict[str, Any]:
        evidence = None
        reconciliation = None
        if error is not None:
            evidence = error.details.get("postgres") if isinstance(error.details, dict) else None
            reconciliation = error.details.get("reconciliationEvidence") if isinstance(error.details, dict) else None
        record = {
            **context, "state": state, "outcome": outcome,
            "completedStatementIndexes": list(completed), "errorCode": None if error is None else error.code,
            "postgresEvidence": evidence, "reconciliationEvidence": reconciliation,
        }
        public = {key: value for key, value in record.items() if key in {
            "executionId", "mode", "settingsRevision", "state", "outcome", "completedStatementIndexes", "errorCode",
            "postgresEvidence", "reconciliationEvidence",
        }}
        metadata = getattr(self.service, "_metadata_store", None)
        if metadata is not None:
            public = metadata.put_console_execution_receipt(record)
        owner = tuple(record[key] for key in (
            "applicationId", "sessionBinding", "serverId", "profileId", "profileFingerprint",
            "database", "namespace", "consoleId",
        ))
        self._receipts[record["executionId"]] = {"owner": owner, "receipt": public}
        return public

    def _reserve_receipt(self, context: dict[str, Any]) -> None:
        metadata = getattr(self.service, "_metadata_store", None)
        if metadata is None:
            raise PostgresServiceError(
                503, "console_receipts_unavailable",
                "Durable Console execution reservation is unavailable",
            )
        try:
            metadata.put_console_execution_receipt({
                **context, "state": "reserved", "outcome": "not_started",
                "completedStatementIndexes": [], "errorCode": None,
                "postgresEvidence": None, "reconciliationEvidence": None,
            })
        except Exception as exc:
            if getattr(exc, "code", None) == "execution_conflict":
                raise PostgresServiceError(
                    409, "execution_conflict",
                    "Execution ID is already reserved and cannot be replayed",
                ) from exc
            raise

    def _mark_running(self, context: dict[str, Any]) -> None:
        self._receipt(context, "running", "not_started", [])

    def _owner_context(self, execution_id: str, console_id: str, profile_id: str, profile_fingerprint: str,
                       database: str, namespace: str, binding: str, server_id: str, mode: str,
                       settings_revision: int | None = None) -> dict[str, Any]:
        return {
            "executionId": execution_id, "applicationId": self.service._application_name,
            "sessionBinding": binding, "serverId": server_id, "profileId": profile_id,
            "profileFingerprint": profile_fingerprint, "database": database, "namespace": namespace,
            "consoleId": console_id, "mode": mode, "settingsRevision": settings_revision,
        }

    def _normalize_rows(self, rows: list[Any], names: list[str], statement_index: int,
                        *, safe_truncation: bool = False) -> tuple[list[list[Any]], list[dict[str, Any]]]:
        normalized = []
        events = []
        for row in rows:
            values = [row.get(name) for name in names] if isinstance(row, dict) else list(row)
            try:
                candidate, row_events = self.result_limiter.row(values, row_index=len(normalized))
            except ResultLimitError as exc:
                if not safe_truncation:
                    raise PostgresServiceError(
                        422, f"sql_{exc.code}", exc.message,
                        {"statementIndex": statement_index, "limitSource": "application", **exc.details},
                    ) from exc
                events.append({"code": f"sql_{exc.code}", "policy": "display_truncated",
                               "statementIndex": statement_index, **exc.details})
                break
            normalized.append(candidate)
            events.extend(row_events)
        return normalized, events

    def _closed_page(self, owner: dict[str, Any], columns: list[dict[str, str]], rows: list[list[Any]],
                     page_size: int, retention: str, truncation_events: list[dict[str, Any]] | None = None,
                     event: str = "exhausted") -> dict[str, Any]:
        events = list(truncation_events or [])
        return {
            "resultId": secrets.token_urlsafe(24), "executionId": owner["executionId"],
            "statementIndex": owner["statementIndex"], "resultIndex": owner["resultIndex"],
            "consoleId": owner["consoleId"],
            "transactionId": owner.get("transactionId"), "columns": columns, "rows": rows,
            "target": {key: owner[key] for key in (
                "applicationId", "serverId", "profileId", "profileFingerprint", "database", "namespace",
            )},
            "pageSize": page_size, "returnedRows": len(rows), "hasMore": False, "nextCursor": None,
            "snapshotRetention": retention, "transactionRetention": retention == "explicit_transaction",
            "expiresAt": None, "resourceState": "closed", "closureEvents": [event],
            "truncationEvents": events, "truncated": bool(events), "incomplete": bool(events),
        }

    def _spool_rows(self, cursor: Any, names: list[str], statement_index: int) -> tuple[list[list[Any]], list[dict[str, Any]]]:
        rows: list[list[Any]] = []
        events: list[dict[str, Any]] = []
        total_bytes = 0
        exhausted = False
        while len(rows) < MAX_SPOOL_ROWS:
            remaining = MAX_SPOOL_ROWS - len(rows)
            raw = cursor.fetchmany(min(1000, remaining + 1))
            if not raw:
                exhausted = True
                break
            normalized, row_events = self._normalize_rows(raw, names, statement_index, safe_truncation=True)
            events.extend(row_events)
            for row in normalized:
                size = self._encoded_size(row)
                if total_bytes + size > MAX_SPOOL_BYTES or len(rows) >= MAX_SPOOL_ROWS:
                    events.append({
                        "code": "result_spool_limit", "policy": "display_truncated",
                        "limitSource": "application", "maximumRows": MAX_SPOOL_ROWS,
                        "maximumBytes": MAX_SPOOL_BYTES,
                    })
                    return rows, events
                rows.append(row)
                total_bytes += size
            if row_events or len(raw) > len(normalized):
                return rows, events
            if len(raw) < min(1000, remaining + 1):
                exhausted = True
                break
        if not exhausted:
            more = cursor.fetchmany(1)
            if more:
                events.append({
                    "code": "result_spool_limit", "policy": "display_truncated",
                    "limitSource": "application", "maximumRows": MAX_SPOOL_ROWS,
                    "maximumBytes": MAX_SPOOL_BYTES,
                })
        return rows, events

    def _statement_result(self, cursor: Any, statement_index: int, pending_notices: list[str],
                          remaining_notice_count: int, remaining_notice_bytes: int,
                          owner: dict[str, Any], mode: str, max_rows: int = MAX_ROWS_PER_RESULT,
                          *, cleanup: Any = None, operation_lock: Any = None,
                          response_byte_limit: int = MAX_RESPONSE_BYTES) -> tuple[dict[str, Any], int, int, bool]:
        notices, remaining_notice_count, remaining_notice_bytes = self._take_notices(
            pending_notices, remaining_notice_count, remaining_notice_bytes,
        )
        description = cursor.description
        page_owner = {**owner, "statementIndex": statement_index, "resultIndex": 0}
        if description is None:
            row_count = getattr(cursor, "rowcount", -1)
            page = self._closed_page(page_owner, [], [], max_rows, "none")
            entry = {"index": statement_index, "command": self._command(cursor), **page,
                     "rowCount": row_count if isinstance(row_count, int) and row_count >= 0 else 0,
                     "notices": notices}
            retained = False
        else:
            names = [item.name if hasattr(item, "name") else item[0] for item in description]
            if len(names) > MAX_COLUMNS_PER_RESULT:
                if mode in {"managed", "autocommit"}:
                    page = self._closed_page(
                        page_owner, [], [], max_rows, "server_spool",
                        [{"code": "sql_result_too_wide", "policy": "display_truncated",
                          "limitSource": "application", "maximumColumns": MAX_COLUMNS_PER_RESULT}],
                        "display_truncated",
                    )
                    entry = {"index": statement_index, "command": self._command(cursor), **page,
                             "rowCount": 0, "notices": notices, "limitEvents": page["truncationEvents"]}
                    return entry, remaining_notice_count, remaining_notice_bytes, False
                raise PostgresServiceError(
                    422, "sql_result_too_wide", f"SQL result exceeds the {MAX_COLUMNS_PER_RESULT}-column limit",
                    {"statementIndex": statement_index, "limitSource": "application"},
                )
            columns = [{"name": name} for name in names]
            retained = False
            if mode in {"managed", "autocommit"}:
                rows, events = self._spool_rows(cursor, names, statement_index)
                try:
                    resource = self.results.add(
                        owner=page_owner, columns=columns, page_size=max_rows, retention="server_spool",
                        rows=rows, truncation_events=events, response_byte_limit=response_byte_limit,
                    )
                    page = self.results.first_page(resource)
                except PostgresServiceError as exc:
                    if exc.code != "console_result_capacity_exhausted":
                        raise
                    capacity_event = {"code": exc.code, "policy": "display_truncated", "limitSource": "application",
                                      "maximumActiveResults": MAX_ACTIVE_RESULTS}
                    fallback_rows = self.results._fit_rows(rows[:max_rows], response_byte_limit)
                    page = self._closed_page(page_owner, columns, fallback_rows, max_rows, "server_spool",
                                             [*events, capacity_event], "display_truncated")
            else:
                resource = self.results.add(
                    owner=page_owner, columns=columns, page_size=max_rows,
                    retention="explicit_transaction" if mode == "explicit" else "managed_read_transaction",
                    cursor=cursor, cleanup=cleanup, operation_lock=operation_lock,
                    response_byte_limit=response_byte_limit,
                )
                page = self.results.first_page(resource)
                retained = page["hasMore"]
            entry = {"index": statement_index, "command": self._command(cursor), **page,
                     "rowCount": page["returnedRows"], "notices": notices,
                     "limitEvents": page["truncationEvents"]}
            # Legacy clients used truncated to mean the first response was incomplete.
            entry["truncated"] = page["hasMore"] or page["truncated"]
        return entry, remaining_notice_count, remaining_notice_bytes, retained

    def execute(self, profile_id: str, payload: Any, binding: str, server_id: str, policy: ConsolePolicy) -> dict[str, Any]:
        required = {"executionId", "consoleId", "database", "namespace", "sql", "mode", "settingsRevision", "profileFingerprint"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValidationError("Console execution request fields are invalid")
        execution_id = _canonical_uuid(payload["executionId"], "executionId")
        console_id = _canonical_uuid(payload["consoleId"], "consoleId")
        requested_mode = payload["mode"]
        mode = self._canonical_mode(requested_mode)
        if mode in {"managed", "autocommit"} and not policy.allow_write:
            raise PostgresServiceError(403, "console_write_not_authorized", "Write mode is not available")
        database = self.service._validate_database(payload["database"])
        namespace = self.service._validate_namespace(payload["namespace"])
        profile_id = self.service._validate_profile_id(profile_id)
        profile = self.service._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the requested database")
        profile_fingerprint = self.service.profile_context_fingerprint(profile_id)
        if payload["profileFingerprint"] != profile_fingerprint:
            raise PostgresServiceError(409, "console_target_changed", "The Console profile target changed; refresh before executing")
        settings = self._human_settings(
            payload["settingsRevision"], payload["profileFingerprint"], write=mode in {"managed", "autocommit"},
        ) if policy.human_write_intent else None
        settings_revision = settings["revision"] if settings is not None else None
        statement_limit = settings["statementLimit"] if settings else policy.statement_limit or MAX_STATEMENTS
        statements = split_console_statements(payload["sql"], statement_limit=statement_limit)
        context = self._owner_context(execution_id, console_id, profile_id, profile_fingerprint,
                                      database, namespace, binding, server_id, mode, settings_revision)
        self._reserve_receipt(context)
        connection = None
        cursor = None
        notice_handler = None
        statement_index = 0
        committed = False
        commit_started = False
        managed_lease = None
        commit_evidence = None
        completed: list[int] = []
        target_dispatch_started = False
        try:
            self.registry.reserve(execution_id, console_id, profile_id, binding, server_id)
            self._mark_running(context)
            connection = self.service._connect_profile(profile)
            target_dispatch_started = True
            if mode == "autocommit":
                connection.autocommit = True
            if self.registry.attach(execution_id, connection):
                raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled")
            pending_notices, notice_handler = self._notice_collector(connection)
            cursor = connection.cursor()
            if mode == "managed_read":
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            narrow_statement_timeout(cursor, policy.operation_timeout_ms, local=mode != "autocommit")
            cursor.execute("SELECT current_database() AS database")
            rows = cursor.fetchall()
            current_database = rows[0]["database"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS exists", (namespace,))
            rows = cursor.fetchall()
            exists = rows[0]["exists"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if not exists:
                raise NotFoundError("Namespace was not found")
            if mode in {"managed", "autocommit"} and self.service.profile_context_fingerprint(profile_id) != profile_fingerprint:
                raise PostgresServiceError(409, "console_target_changed", "The Console profile target changed")
            if mode == "autocommit":
                cursor.execute("SELECT pg_catalog.set_config('search_path', %s, false)", (quote_identifier(namespace),))
            else:
                cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (quote_identifier(namespace),))
            cursor.close()
            cursor = None

            if mode == "managed_read":
                managed_lease = {"references": 1, "closed": False, "lock": threading.Lock()}

                def retain_managed_result():
                    with managed_lease["lock"]:
                        managed_lease["references"] += 1
                    released = False

                    def release():
                        nonlocal released
                        with managed_lease["lock"]:
                            if released:
                                return
                            released = True
                            managed_lease["references"] -= 1
                            should_close = managed_lease["references"] == 0 and not managed_lease["closed"]
                            if should_close:
                                managed_lease["closed"] = True
                        if should_close:
                            try:
                                connection.rollback()
                            except Exception:
                                pass
                            self.service._close(connection)
                    return release

            limits = {
                "maxStatements": statement_limit,
                "maxRowsPerResult": settings["rowPageSize"] if settings else MAX_ROWS_PER_RESULT,
                "maxColumnsPerResult": MAX_COLUMNS_PER_RESULT, "maxResponseBytes": MAX_RESPONSE_BYTES,
                "maxCellBytes": MAX_CELL_BYTES, "maxRowBytes": MAX_ROW_BYTES,
                "maxCellNesting": MAX_CELL_NESTING, "maxCollectionItems": MAX_COLLECTION_ITEMS,
                "maxActiveResults": MAX_ACTIVE_RESULTS, "resultTtlSeconds": RESULT_TTL_SECONDS,
                "maxRetainedSnapshots": MAX_RETAINED_SNAPSHOTS,
                "maxSpoolRows": MAX_SPOOL_ROWS, "maxSpoolBytes": MAX_SPOOL_BYTES,
                "statementTimeoutSource": "policy_narrowing" if policy.operation_timeout_ms is not None else "postgresql",
            }
            result = {
                "executionId": execution_id,
                "target": {"profileId": profile_id, "database": database, "namespace": namespace},
                "mode": requested_mode, "canonicalMode": mode, "committed": False,
                "outcome": "not_started", "completedStatementIndexes": [], "statements": [], "limits": limits,
            }
            remaining_notice_count = MAX_NOTICES
            remaining_notice_bytes = MAX_NOTICE_BYTES
            response_byte_limit = max(4096, (MAX_RESPONSE_BYTES - 256 * 1024) // len(statements))
            for statement_index, statement in enumerate(statements):
                if self.registry.cancel_requested(execution_id):
                    raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled", {"statementIndex": statement_index})
                if mode == "autocommit":
                    search_cursor = connection.cursor()
                    try:
                        search_cursor.execute("SELECT pg_catalog.set_config('search_path', %s, false)", (quote_identifier(namespace),))
                    finally:
                        search_cursor.close()
                else:
                    search_cursor = connection.cursor()
                    try:
                        search_cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (quote_identifier(namespace),))
                    finally:
                        search_cursor.close()
                pending_notices.clear()
                cursor = connection.cursor()
                cursor.execute(statement)
                completed.append(statement_index)
                result["completedStatementIndexes"] = list(completed)
                cleanup = retain_managed_result() if mode == "managed_read" and cursor.description is not None else None
                try:
                    entry, remaining_notice_count, remaining_notice_bytes, retained = self._statement_result(
                        cursor, statement_index, pending_notices, remaining_notice_count, remaining_notice_bytes,
                        context, mode, settings["rowPageSize"] if settings else MAX_ROWS_PER_RESULT,
                        cleanup=cleanup, response_byte_limit=response_byte_limit,
                    )
                except Exception:
                    if cleanup is not None:
                        cleanup()
                    raise
                result["statements"].append(entry)
                if retained:
                    cursor = None
                else:
                    close = getattr(cursor, "close", None)
                    if close:
                        close()
                    cursor = None
                if self._encoded_size(result) > MAX_RESPONSE_BYTES:
                    if mode in {"managed", "autocommit"}:
                        entry["truncated"] = True
                        entry["incomplete"] = True
                        entry["truncationEvents"].append({"code": "sql_result_too_large", "policy": "display_truncated",
                                                          "limitSource": "application"})
                    else:
                        raise PostgresServiceError(422, "sql_result_too_large", "Console result metadata exceeds the byte limit",
                                                   {"statementIndex": statement_index, "limitSource": "application"})
            if mode == "managed":
                evidence_cursor = connection.cursor()
                evidence_cursor.execute("SELECT pg_catalog.pg_current_xact_id()::text AS xid, d.oid::text AS database_oid FROM pg_catalog.pg_database AS d WHERE d.datname = pg_catalog.current_database()")
                evidence_rows = evidence_cursor.fetchall()
                evidence_cursor.close()
                if evidence_rows:
                    evidence_row = evidence_rows[0]
                    commit_evidence = {
                        "targetXid": evidence_row["xid"] if isinstance(evidence_row, dict) else evidence_row[0],
                        "databaseOid": evidence_row["database_oid"] if isinstance(evidence_row, dict) else evidence_row[1],
                    }
                if self.registry.cancel_requested(execution_id):
                    raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled", {"statementIndex": statement_index})
                try:
                    commit_started = True
                    connection.commit()
                except Exception as exc:
                    raise PostgresServiceError(
                        500, "execution_outcome_unknown",
                        "Console write commit outcome is uncertain; verify PostgreSQL before running another write",
                         {"statementIndex": statement_index,
                          "reconciliationEvidence": {"commitAttempted": True, **(commit_evidence or {})},
                          **postgres_error_details(exc, phase="commit", operation="console_write", retry={"safe": False, "reconcileRequired": True})},
                    ) from exc
                result["committed"] = True
                result["outcome"] = "committed"
                committed = True
                commit_started = False
            elif mode == "autocommit":
                result["committed"] = bool(completed)
                result["outcome"] = "committed" if completed else "not_started"
                committed = True
            elif mode == "managed_read":
                result["resultResourcesOpen"] = sum(1 for entry in result["statements"] if entry["hasMore"])
                result["outcome"] = "transaction_open" if result["resultResourcesOpen"] else "rolled_back"
            self._receipt(context, "succeeded", result["outcome"], completed)
            return result
        except PostgresServiceError as error:
            self.results.close_matching({"executionId": execution_id}, "execution_failed")
            error.details = dict(error.details or {})
            if error.code == "execution_outcome_unknown" or commit_started:
                state, outcome = "uncertain", "uncertain"
            elif not target_dispatch_started:
                state = "cancelled" if error.code == "execution_cancelled" else "failed"
                outcome = "not_started"
                error.details.update({"completedStatementIndexes": [], "outcome": outcome,
                                      "reconciliationEvidence": {"postgresDispatchStarted": False}})
            elif mode == "autocommit":
                state = "cancelled" if error.code == "execution_cancelled" else "failed"
                outcome = "partial_committed" if completed else "not_started"
                error.details.update({"completedStatementIndexes": list(completed), "priorStatementsCommitted": bool(completed), "outcome": outcome})
            else:
                state = "cancelled" if error.code == "execution_cancelled" else "failed"
                outcome = "rolled_back"
                error.details.update({"completedStatementIndexes": list(completed), "outcome": outcome})
            self._receipt(context, state, outcome, completed, error)
            raise
        except Exception as exc:
            self.results.close_matching({"executionId": execution_id}, "execution_failed")
            if committed:
                raise PostgresServiceError(
                    503, "execution_receipt_unavailable",
                    "PostgreSQL committed the Console execution, but its durable status receipt is unavailable",
                    {"completedStatementIndexes": list(completed), "outcome": "committed"},
                ) from exc
            error = self._error(exc, statement_index, self.registry.cancel_requested(execution_id))
            postgres = error.details.get("postgres", {}) if isinstance(error.details, dict) else {}
            if not target_dispatch_started:
                error.details.update({"completedStatementIndexes": [], "outcome": "not_started",
                                      "reconciliationEvidence": {"postgresDispatchStarted": False}})
                self._receipt(context, "failed", "not_started", [], error)
                raise error from exc
            autocommit_uncertain = mode == "autocommit" and error.code != "execution_cancelled" and (
                not postgres.get("sqlstate") or str(postgres["sqlstate"]).startswith("08")
            )
            outcome = "uncertain" if autocommit_uncertain else "partial_committed" if mode == "autocommit" and completed else "not_started" if mode == "autocommit" else "uncertain" if commit_started else "rolled_back"
            state = "cancelled" if error.code == "execution_cancelled" else "uncertain" if outcome == "uncertain" else "failed"
            error.details.update({"completedStatementIndexes": list(completed), "outcome": outcome})
            if mode == "autocommit":
                error.details["priorStatementsCommitted"] = bool(completed)
            self._receipt(context, state, outcome, completed, error)
            raise error from exc
        finally:
            if connection is not None and notice_handler is not None:
                remove_handler = getattr(connection, "remove_notice_handler", None)
                if remove_handler:
                    try:
                        remove_handler(notice_handler)
                    except Exception:
                        pass
            if cursor is not None:
                close = getattr(cursor, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass
            if managed_lease is not None:
                # The execution reference is released after all statement cursors have
                # been registered, so an exhausted early result cannot close the shared snapshot.
                with managed_lease["lock"]:
                    managed_lease["references"] -= 1
                    should_close = managed_lease["references"] == 0 and not managed_lease["closed"]
                    if should_close:
                        managed_lease["closed"] = True
                if should_close:
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                    self.service._close(connection)
                self.registry.release(execution_id)
            elif connection is not None:
                if not committed and mode != "autocommit":
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                try:
                    self.service._close(connection)
                finally:
                    self.registry.release(execution_id)
            else:
                self.registry.release(execution_id)

    def status(self, profile_id: str, execution_id: Any, console_id: Any, database: Any,
               namespace: Any, binding: str, server_id: str) -> dict[str, Any]:
        profile_id = self.service._validate_profile_id(profile_id)
        execution_id = _canonical_uuid(execution_id, "executionId")
        console_id = _canonical_uuid(console_id, "consoleId")
        database = self.service._validate_database(database)
        namespace = self.service._validate_namespace(namespace)
        profile = self.service._profile(profile_id)
        fingerprint = self.service.profile_context_fingerprint(profile_id)
        metadata = getattr(self.service, "_metadata_store", None)
        if metadata is not None:
            return metadata.get_console_execution_receipt(
                execution_id, self.service._application_name, binding, server_id, profile_id,
                fingerprint, database, namespace, console_id,
            )
        stored = self._receipts.get(execution_id)
        owner = (self.service._application_name, binding, server_id, profile_id, fingerprint, database, namespace, console_id)
        if stored is None or stored["owner"] != owner:
            raise PostgresServiceError(404, "execution_not_found", "Console execution status was not found")
        return stored["receipt"]

    def _result_owner(self, profile_id: str, execution_id: Any, result_id: Any, console_id: Any,
                      database: Any, namespace: Any, statement_index: Any, result_index: Any,
                      binding: str, server_id: str) -> tuple[str, dict[str, Any]]:
        profile_id = self.service._validate_profile_id(profile_id)
        execution_id = _canonical_uuid(execution_id, "executionId")
        console_id = _canonical_uuid(console_id, "consoleId")
        database = self.service._validate_database(database)
        namespace = self.service._validate_namespace(namespace)
        if not isinstance(result_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", result_id):
            raise ValidationError("resultId is invalid")
        try:
            statement_index = int(statement_index)
            result_index = int(result_index)
        except (TypeError, ValueError) as exc:
            raise ValidationError("statementIndex and resultIndex must be non-negative integers") from exc
        if statement_index < 0 or result_index < 0:
            raise ValidationError("statementIndex and resultIndex must be non-negative integers")
        profile = self.service._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(404, "result_not_found", "Console result was not found")
        return result_id, {
            "applicationId": self.service._application_name, "sessionBinding": binding, "serverId": server_id,
            "profileId": profile_id, "profileFingerprint": self.service.profile_context_fingerprint(profile_id),
            "database": database, "namespace": namespace, "consoleId": console_id,
            "executionId": execution_id, "statementIndex": statement_index, "resultIndex": result_index,
        }

    def result_page(self, profile_id: str, execution_id: Any, result_id: Any, console_id: Any,
                    database: Any, namespace: Any, statement_index: Any, result_index: Any,
                    cursor: Any, binding: str, server_id: str) -> dict[str, Any]:
        result_id, owner = self._result_owner(
            profile_id, execution_id, result_id, console_id, database, namespace,
            statement_index, result_index, binding, server_id,
        )
        return self.results.page(result_id, owner, cursor)

    def close_result(self, profile_id: str, execution_id: Any, result_id: Any, console_id: Any,
                     database: Any, namespace: Any, statement_index: Any, result_index: Any,
                     binding: str, server_id: str) -> dict[str, Any]:
        result_id, owner = self._result_owner(
            profile_id, execution_id, result_id, console_id, database, namespace,
            statement_index, result_index, binding, server_id,
        )
        return self.results.close_result(result_id, owner)

    def _transaction_owner(self, profile_id: str, binding: str, server_id: str) -> dict[str, Any]:
        profile = self.service._profile(profile_id)
        return {"application": self.service._application_name, "binding": binding, "serverId": server_id,
                 "profileId": profile_id, "profileFingerprint": self.service.profile_context_fingerprint(profile_id)}

    def create_transaction(self, profile_id: str, payload: Any, binding: str, server_id: str,
                           policy: ConsolePolicy) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"transactionId", "consoleId", "database", "namespace", "settingsRevision", "profileFingerprint"}:
            raise ValidationError("Console transaction request fields are invalid")
        if not policy.allow_write:
            raise PostgresServiceError(403, "console_write_not_authorized", "Explicit transaction mode is not available")
        transaction_id = _canonical_uuid(payload["transactionId"], "transactionId")
        console_id = _canonical_uuid(payload["consoleId"], "consoleId")
        profile_id = self.service._validate_profile_id(profile_id)
        database = self.service._validate_database(payload["database"])
        namespace = self.service._validate_namespace(payload["namespace"])
        profile = self.service._profile(profile_id)
        fingerprint = self.service.profile_context_fingerprint(profile_id)
        if payload["profileFingerprint"] != fingerprint:
            raise PostgresServiceError(409, "console_target_changed", "The Console profile target changed; refresh before opening a transaction")
        settings = self._human_settings(payload["settingsRevision"], payload["profileFingerprint"], write=True) if policy.human_write_intent else None
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the requested database")
        connection = self.service._connect_profile(profile)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT current_database() AS database")
            rows = cursor.fetchall()
            current = rows[0]["database"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if current != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS exists", (namespace,))
            rows = cursor.fetchall()
            exists = rows[0]["exists"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if not exists:
                raise NotFoundError("Namespace was not found")
            cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (quote_identifier(namespace),))
            entry = {**self._transaction_owner(profile_id, binding, server_id), "database": database,
                     "namespace": namespace, "consoleId": console_id, "transactionId": transaction_id,
                     "settingsRevision": settings["revision"] if settings else None,
                     "statementLimit": settings["statementLimit"] if settings else MAX_STATEMENTS,
                     "rowPageSize": settings["rowPageSize"] if settings else MAX_ROWS_PER_RESULT,
                     "connection": connection, "lock": threading.RLock()}
            self.transactions.add(transaction_id, entry)
            connection = None
            return {"transactionId": transaction_id, "target": {"profileId": profile_id, "database": database, "namespace": namespace},
                    "consoleId": console_id, "state": self.transactions.state(entry["connection"]),
                    "limits": {"maximumActiveTransactions": self.transactions.maximum_active,
                               "idleSeconds": self.transactions.idle_seconds,
                               "absoluteLifetimeSeconds": self.transactions.lifetime_seconds,
                               "policy": "connection_lifecycle"}}
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    self.service._close(connection)

    def transaction_status(self, profile_id: str, transaction_id: Any, binding: str, server_id: str) -> dict[str, Any]:
        profile_id = self.service._validate_profile_id(profile_id)
        transaction_id = _canonical_uuid(transaction_id, "transactionId")
        entry = self.transactions.require(transaction_id, self._transaction_owner(profile_id, binding, server_id))
        return {"transactionId": transaction_id, "target": {"profileId": profile_id, "database": entry["database"], "namespace": entry["namespace"]},
                "consoleId": entry["consoleId"], "state": self.transactions.state(entry["connection"])}

    def execute_transaction(self, profile_id: str, transaction_id: Any, payload: Any,
                            binding: str, server_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"executionId", "sql"}:
            raise ValidationError("Console transaction execution fields are invalid")
        profile_id = self.service._validate_profile_id(profile_id)
        transaction_id = _canonical_uuid(transaction_id, "transactionId")
        execution_id = _canonical_uuid(payload["executionId"], "executionId")
        entry = self.transactions.require(transaction_id, self._transaction_owner(profile_id, binding, server_id))
        statements = split_console_statements(payload["sql"], explicit=True, statement_limit=entry["statementLimit"])
        completed: list[int] = []
        context = self._owner_context(execution_id, entry["consoleId"], profile_id, entry["profileFingerprint"],
                                      entry["database"], entry["namespace"], binding, server_id, "explicit", entry["settingsRevision"])
        context["transactionId"] = transaction_id
        self._reserve_receipt(context)
        if not entry["lock"].acquire(blocking=False):
            error = PostgresServiceError(409, "transaction_busy", "Console transaction is executing another request", {
                "reconciliationEvidence": {"postgresDispatchStarted": False},
            })
            self._receipt(context, "failed", "not_started", [], error)
            raise error
        try:
            self.registry.reserve(execution_id, entry["consoleId"], profile_id, binding, server_id)
            self._mark_running(context)
        except Exception as exc:
            entry["lock"].release()
            error = exc if isinstance(exc, PostgresServiceError) else PostgresServiceError(
                503, "execution_reservation_failed", "Console execution admission failed",
                {"reconciliationEvidence": {"postgresDispatchStarted": False}},
            )
            if isinstance(error, PostgresServiceError):
                error.details = {**(error.details or {}), "reconciliationEvidence": {"postgresDispatchStarted": False}}
                self._receipt(context, "failed", "not_started", [], error)
            raise
        cursor = None
        notice_handler = None
        statement_index = 0
        try:
            connection = entry["connection"]
            if self.registry.attach(execution_id, connection):
                raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled")
            pending, notice_handler = self._notice_collector(connection)
            result = {"executionId": execution_id, "transactionId": transaction_id, "mode": "explicit",
                      "canonicalMode": "explicit", "committed": False, "outcome": "transaction_open",
                      "completedStatementIndexes": [], "statements": []}
            notice_count, notice_bytes = MAX_NOTICES, MAX_NOTICE_BYTES
            response_byte_limit = max(4096, (MAX_RESPONSE_BYTES - 256 * 1024) // len(statements))
            for statement_index, statement in enumerate(statements):
                if self.registry.cancel_requested(execution_id):
                    raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled", {"statementIndex": statement_index})
                pending.clear()
                cursor = connection.cursor()
                cursor.execute(statement)
                completed.append(statement_index)
                result["completedStatementIndexes"] = list(completed)
                item, notice_count, notice_bytes, retained = self._statement_result(
                    cursor, statement_index, pending, notice_count, notice_bytes, context, "explicit",
                    entry["rowPageSize"], operation_lock=entry["lock"], response_byte_limit=response_byte_limit,
                )
                result["statements"].append(item)
                if retained:
                    cursor = None
                else:
                    cursor.close()
                    cursor = None
            result["resultResourcesOpen"] = sum(1 for item in result["statements"] if item["hasMore"])
            result["transactionState"] = self.transactions.state(connection)
            self._receipt(context, "succeeded", "transaction_open", completed)
            return result
        except PostgresServiceError as error:
            self.results.close_matching({"transactionId": transaction_id, "executionId": execution_id}, "execution_failed")
            error.details = dict(error.details or {})
            error.details.update({"completedStatementIndexes": list(completed), "outcome": "transaction_open",
                                  "transactionState": self.transactions.state(entry["connection"])})
            self._receipt(context, "cancelled" if error.code == "execution_cancelled" else "failed", "transaction_open", completed, error)
            raise
        except Exception as exc:
            self.results.close_matching({"transactionId": transaction_id, "executionId": execution_id}, "execution_failed")
            error = self._error(exc, statement_index, self.registry.cancel_requested(execution_id))
            error.details.update({"completedStatementIndexes": list(completed), "outcome": "transaction_open",
                                  "transactionState": self.transactions.state(entry["connection"])})
            self._receipt(context, "cancelled" if error.code == "execution_cancelled" else "failed", "transaction_open", completed, error)
            raise error from exc
        finally:
            if cursor is not None:
                cursor.close()
            if notice_handler is not None:
                remove = getattr(entry["connection"], "remove_notice_handler", None)
                if remove:
                    remove(notice_handler)
            self.registry.release(execution_id)
            entry["lock"].release()

    def finish_transaction(self, profile_id: str, transaction_id: Any, payload: Any, binding: str,
                           server_id: str, action: str) -> dict[str, Any]:
        if action not in {"commit", "rollback"} or not isinstance(payload, dict) or set(payload) != {"executionId"}:
            raise ValidationError("Console transaction completion fields are invalid")
        profile_id = self.service._validate_profile_id(profile_id)
        transaction_id = _canonical_uuid(transaction_id, "transactionId")
        execution_id = _canonical_uuid(payload["executionId"], "executionId")
        entry = self.transactions.require(transaction_id, self._transaction_owner(profile_id, binding, server_id))
        context = self._owner_context(execution_id, entry["consoleId"], profile_id, entry["profileFingerprint"],
                                      entry["database"], entry["namespace"], binding, server_id, "explicit", entry["settingsRevision"])
        context["transactionId"] = transaction_id
        self._reserve_receipt(context)
        if not entry["lock"].acquire(blocking=False):
            error = PostgresServiceError(409, "transaction_busy", "Console transaction is executing another request", {
                "reconciliationEvidence": {"postgresDispatchStarted": False},
            })
            self._receipt(context, "failed", "not_started", [], error)
            raise error
        try:
            self.registry.reserve(execution_id, entry["consoleId"], profile_id, binding, server_id)
            self._mark_running(context)
        except Exception as exc:
            entry["lock"].release()
            error = exc if isinstance(exc, PostgresServiceError) else PostgresServiceError(
                503, "execution_reservation_failed", "Console execution admission failed",
            )
            if isinstance(error, PostgresServiceError):
                error.details = {**(error.details or {}), "reconciliationEvidence": {"postgresDispatchStarted": False}}
                self._receipt(context, "failed", "not_started", [], error)
            raise
        connection = entry["connection"]
        attempted = False
        close_resource = False
        completed_action = None
        closed_results: list[dict[str, Any]] = []
        try:
            self.registry.attach(execution_id, connection)
            if self.registry.cancel_requested(execution_id):
                raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled")
            prior_state = self.transactions.state(connection)
            closed_results = self.results.close_matching({"transactionId": transaction_id}, f"transaction_{action}")
            attempted = True
            getattr(connection, action)()
            outcome = "committed" if action == "commit" and prior_state != "failed" else "rolled_back"
            completed_action = outcome
            close_resource = True
            self._receipt(context, "succeeded", outcome, [])
            return {"executionId": execution_id, "transactionId": transaction_id, "state": "closed", "outcome": outcome,
                    "resultClosurePolicy": "deterministic_before_completion", "closedResults": closed_results}
        except PostgresServiceError as error:
            self._receipt(context, "cancelled", "transaction_open", [], error)
            raise
        except Exception as exc:
            if completed_action is not None:
                raise PostgresServiceError(
                    503, "execution_receipt_unavailable",
                    f"PostgreSQL {completed_action.replace('_', ' ')} the explicit transaction, but its durable status receipt is unavailable",
                    {"outcome": completed_action, "resultClosurePolicy": "deterministic_before_completion",
                     "closedResults": closed_results},
                ) from exc
            error = PostgresServiceError(500, "execution_outcome_unknown" if action == "commit" and attempted else "sql_query_failed",
                                         "Console transaction completion outcome is uncertain" if action == "commit" else "Console rollback failed",
                                         {"reconciliationEvidence": {"commitAttempted": attempted},
                                          "resultClosurePolicy": "deterministic_before_completion",
                                           "closedResults": closed_results,
                                           **postgres_error_details(
                                               exc, phase=action, operation="console_transaction",
                                               rollback={"proven": False} if action == "rollback" else None,
                                               retry={"safe": False, "reconcileRequired": True} if action == "commit" else None,
                                           )})
            close_resource = True
            self._receipt(context, "uncertain", "uncertain", [], error)
            raise error from exc
        finally:
            if close_resource:
                self.transactions.remove(transaction_id)
                self.service._close(connection)
            self.registry.release(execution_id)
            entry["lock"].release()

    def cancel(self, profile_id: str, execution_id: Any, binding: str, server_id: str) -> dict[str, Any]:
        profile_id = self.service._validate_profile_id(profile_id)
        execution_id = _canonical_uuid(execution_id, "executionId")
        try:
            return self.registry.cancel(execution_id, profile_id, binding, server_id)
        except PostgresServiceError as exc:
            if exc.code != "execution_not_found":
                raise
            closed = self.results.close_matching({
                "applicationId": self.service._application_name, "sessionBinding": binding,
                "serverId": server_id, "profileId": profile_id, "executionId": execution_id,
            }, "cancelled")
            if not closed:
                raise
            return {"requested": False, "closedResults": closed}

    def create_write_grant(self, profile_id: str, payload: Any, binding: str, server_id: str) -> dict[str, Any]:
        raise PostgresServiceError(410, "console_write_grants_retired", "Console write grants are retired; use durable Console settings")

    def revoke_write_grant(self, profile_id: str, grant_id: Any, binding: str, server_id: str) -> dict[str, bool]:
        raise PostgresServiceError(410, "console_write_grants_retired", "Console write grants are retired; use durable Console settings")

    def close(self) -> None:
        self.registry.close()
        self.results.close()
        self.transactions.close()
