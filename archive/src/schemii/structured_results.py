from __future__ import annotations

import csv
import io
import json
import re
import secrets
import threading
from typing import Any, Callable

from .postgres_common import PostgresServiceError, ValidationError
from .result_limits import ResultLimitError, fit_serialized_envelope, json_utf8_size
from .retained_resources import RetainedResourceRegistry


STRUCTURED_RESULT_TTL_SECONDS = 300
MAX_ACTIVE_AGGREGATE_RESULTS = 100
MAX_ACTIVE_DETAIL_RESULTS = 8
MAX_RETAINED_DETAIL_SNAPSHOTS = 4
MAX_STRUCTURED_RESULT_BYTES = 16 * 1024 * 1024
MAX_AGGREGATE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DETAIL_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DETAIL_RETAINED_ROWS = 10_000
MAX_STRUCTURED_PAGE_BYTES = 1024 * 1024
MAX_EXPORT_BYTES = 20 * 1024 * 1024


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    text = str(value)
    return f"'{text}" if isinstance(value, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def csv_export(columns: list[dict[str, Any]], rows: list[list[Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    writer.writerow([column.get("label") or column.get("name") or column.get("id") or "" for column in columns])
    writer.writerows([_csv_cell(value) for value in row] for row in rows)
    return output.getvalue().encode("utf-8")


class StructuredResultRegistry(RetainedResourceRegistry):
    """Schemer result pages backed by bounded rows or one retained PostgreSQL snapshot."""

    def __init__(self, *, clock: Any, ttl_seconds: int = STRUCTURED_RESULT_TTL_SECONDS):
        self.process_marker = secrets.token_hex(8)
        self.maximum_snapshots = MAX_RETAINED_DETAIL_SNAPSHOTS
        self.maximum_aggregate_results = MAX_ACTIVE_AGGREGATE_RESULTS
        self.maximum_detail_results = MAX_ACTIVE_DETAIL_RESULTS
        self.maximum_aggregate_bytes = MAX_AGGREGATE_TOTAL_BYTES
        self.maximum_detail_bytes = MAX_DETAIL_TOTAL_BYTES
        self._structured_metrics = {"pages": 0, "exports": 0, "cancelled": 0, "aggregateEvicted": 0}
        super().__init__(
            label="Structured result", maximum_active=MAX_ACTIVE_AGGREGATE_RESULTS + MAX_ACTIVE_DETAIL_RESULTS,
            ttl_seconds=ttl_seconds, clock=clock,
            capacity_code="structured_result_capacity_exhausted",
            capacity_message="Structured result retention capacity is exhausted; close or wait for another result to expire",
            stopping_code="structured_results_shutting_down",
            stopping_message="Structured result retention is shutting down",
            sweeper_name="structured-result-expiry",
        )

    def _active_bytes(self, kind: str | None = None, *, excluding: str | None = None) -> int:
        return sum(
            entry.get("retainedBytes", 0) for result_id, entry in self._entries.items()
            if result_id != excluding and (kind is None or entry.get("kind") == kind)
        )

    def _prepare_capacity_locked(
        self, entry: dict[str, Any], evicted: list[dict[str, Any]],
    ) -> None:
        if entry.get("kind") != "aggregate":
            return
        retained_bytes = entry.get("retainedBytes", 0)
        if retained_bytes > MAX_STRUCTURED_RESULT_BYTES:
            return
        while True:
            aggregates = [current for current in self._entries.values() if current.get("kind") == "aggregate"]
            over_count = len(aggregates) >= self.maximum_aggregate_results
            over_bytes = self._active_bytes("aggregate") + retained_bytes > self.maximum_aggregate_bytes
            if not over_count and not over_bytes:
                return
            removed = False
            for candidate in sorted(aggregates, key=lambda current: current["lastAccessEpoch"]):
                operation_lock = candidate["operationLock"]
                if not operation_lock.acquire(blocking=False):
                    continue
                try:
                    if self._remove_locked(candidate, "expired"):
                        evicted.append(candidate)
                        self._structured_metrics["aggregateEvicted"] += 1
                        removed = True
                        break
                finally:
                    operation_lock.release()
            if not removed:
                return

    def _validate_capacity_locked(self, entry: dict[str, Any]) -> None:
        kind = entry.get("kind")
        retained_bytes = entry.get("retainedBytes", 0)
        entries = [current for current in self._entries.values() if current.get("kind") == kind]
        if kind == "aggregate" and len(entries) >= self.maximum_aggregate_results:
            self._metrics["capacityRejected"] += 1
            raise PostgresServiceError(
                429, "aggregate_result_capacity_exhausted",
                "Retained aggregate capacity is busy because every evictable aggregate is active",
                {"limitSource": "application", "maximumAggregateResults": self.maximum_aggregate_results},
            )
        if kind == "detail" and len(entries) >= self.maximum_detail_results:
            self._metrics["capacityRejected"] += 1
            raise PostgresServiceError(
                429, "detail_result_capacity_exhausted",
                "Retained detail capacity is exhausted; close or wait for another detail result to expire",
                {"limitSource": "application", "maximumDetailResults": self.maximum_detail_results},
            )
        snapshots = sum(bool(current.get("cursor")) for current in self._entries.values())
        if entry.get("cursor") is not None and snapshots >= self.maximum_snapshots:
            self._metrics["capacityRejected"] += 1
            raise PostgresServiceError(
                429, "structured_result_snapshot_capacity_exhausted",
                "Structured detail snapshot capacity is exhausted; close or wait for another detail result to expire",
                {"limitSource": "application", "maximumRetainedSnapshots": self.maximum_snapshots},
            )
        maximum_total_bytes = self.maximum_aggregate_bytes if kind == "aggregate" else self.maximum_detail_bytes
        if retained_bytes > MAX_STRUCTURED_RESULT_BYTES or self._active_bytes(kind) + retained_bytes > maximum_total_bytes:
            self._metrics["capacityRejected"] += 1
            code = "aggregate_result_memory_capacity_exhausted" if kind == "aggregate" else "detail_result_memory_capacity_exhausted"
            raise PostgresServiceError(
                429, code, f"Retained {kind} memory capacity is exhausted",
                {"limitSource": "application", "maximumResultBytes": MAX_STRUCTURED_RESULT_BYTES,
                 "maximumTotalBytes": maximum_total_bytes},
            )

    def add(
        self,
        *,
        owner: dict[str, Any],
        kind: str,
        columns: list[dict[str, Any]],
        page_size: int,
        template: dict[str, Any],
        rows: list[list[Any]] | None = None,
        retained_bytes: int = 0,
        cursor: Any = None,
        cleanup: Callable[[], None] | None = None,
        normalize_row: Callable[[Any, int], tuple[list[Any], list[dict[str, Any]]]] | None = None,
        maximum_rows: int | None = None,
    ) -> dict[str, Any]:
        if kind not in {"aggregate", "detail"}:
            raise ValueError("structured result kind is invalid")
        result_id = f"{self.process_marker}_{secrets.token_urlsafe(20)}"
        entry = {
            "resultId": result_id,
            "owner": dict(owner),
            "bindingToken": secrets.token_urlsafe(32),
            "kind": kind,
            "columns": columns,
            "pageSize": page_size,
            "template": template,
            "rows": list(rows or []),
            "retainedBytes": retained_bytes,
            "cursor": cursor,
            "cleanup": cleanup,
            "normalizeRow": normalize_row,
            "maximumRows": maximum_rows if maximum_rows is not None else len(rows or []),
            "exhausted": cursor is None,
            "snapshotReleased": cursor is None,
            "retentionTruncated": False,
            "terminalReason": None,
            "cursorOffsets": {},
            "offsetCursors": {},
            "pageStarts": [0],
            "operationLock": threading.Lock(),
            "lastAccessEpoch": self.clock(),
        }
        try:
            return self._add_entry(entry)
        except Exception:
            self._close_entry(entry)
            raise

    def _restart_or_missing(self, result_id: str) -> None:
        marker = result_id.split("_", 1)[0] if isinstance(result_id, str) else ""
        if marker and marker != self.process_marker:
            raise PostgresServiceError(
                410, "result_restarted",
                "The process-local structured result ended when the server restarted; run the query again explicitly",
                {"resultId": result_id, "state": "restarted", "automaticReplay": False},
            )
        raise PostgresServiceError(404, "result_not_found", "Structured result was not found")

    def _ensure_active_entry(self, entry: dict[str, Any]) -> None:
        with self._lock:
            current = self._entries.get(entry["resultId"])
            tombstone = self._tombstones.get(entry["resultId"])
        if current is entry:
            if entry["expiresAtEpoch"] > self.clock():
                return
            self._remove(entry["resultId"], "expired")
            raise PostgresServiceError(
                410, "result_expired", "Structured result is expired",
                {"resultId": entry["resultId"], "state": "expired", "automaticReplay": False},
            )
        if tombstone is not None and self._matches(tombstone, entry["owner"]):
            state = tombstone["state"]
            raise PostgresServiceError(
                410, f"result_{state}", f"Structured result is {state}",
                {"resultId": entry["resultId"], "state": state, "closedAt": tombstone["closedAt"],
                 "automaticReplay": False},
            )
        self._restart_or_missing(entry["resultId"])

    def require(self, result_id: Any, owner: dict[str, Any], binding_token: Any) -> dict[str, Any]:
        if not isinstance(result_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", result_id):
            raise ValidationError("resultId is invalid")
        if not isinstance(binding_token, str) or not binding_token:
            raise ValidationError("result binding is required")
        self.expire()
        with self._lock:
            entry = self._entries.get(result_id)
            tombstone = self._tombstones.get(result_id)
        owner_matches = entry is not None and self._matches(entry, owner)
        token_matches = owner_matches and secrets.compare_digest(binding_token, entry["bindingToken"])
        if not token_matches:
            if entry is None and tombstone is not None and self._matches(tombstone, owner):
                state = tombstone["state"]
                raise PostgresServiceError(
                    410, f"result_{state}", f"Structured result is {state}",
                    {"resultId": result_id, "state": state, "closedAt": tombstone["closedAt"],
                     "automaticReplay": False},
                )
            if entry is None:
                self._restart_or_missing(result_id)
            raise PostgresServiceError(404, "result_not_found", "Structured result was not found")
        with self._lock:
            if self._entries.get(result_id) is entry:
                entry["lastAccessEpoch"] = self.clock()
        return entry

    @staticmethod
    def public_binding(entry: dict[str, Any]) -> dict[str, Any]:
        owner = entry["owner"]
        return {key: owner.get(key) for key in (
            "applicationId", "serverId", "resultKind", "profileId", "profileFingerprint", "database", "namespace",
            "dashboardId", "dashboardRevision", "widgetId", "queryDigest", "authorityDigest",
            "relation", "relationKind", "relationFingerprint",
        )}

    def _cursor_for_offset(self, entry: dict[str, Any], offset: int) -> str:
        existing = entry["offsetCursors"].get(offset)
        if existing:
            return existing
        token = secrets.token_urlsafe(24)
        entry["offsetCursors"][offset] = token
        entry["cursorOffsets"][token] = offset
        return token

    def _release_snapshot(self, entry: dict[str, Any]) -> None:
        cursor = entry.get("cursor")
        cleanup = entry.get("cleanup")
        entry["cursor"] = None
        entry["cleanup"] = None
        entry["snapshotReleased"] = True
        if cursor is not None:
            close = getattr(cursor, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
        if cleanup:
            cleanup()

    def _truncate_retention(self, entry: dict[str, Any], reason: str) -> None:
        entry["retentionTruncated"] = True
        entry["terminalReason"] = reason
        entry["exhausted"] = True
        self._release_snapshot(entry)

    def _ensure_rows(self, entry: dict[str, Any], count: int) -> None:
        while not entry["exhausted"] and len(entry["rows"]) < count:
            remaining = entry["maximumRows"] + 1 - len(entry["rows"])
            if remaining <= 0:
                self._truncate_retention(entry, "row_capacity")
                return
            raw_rows = entry["cursor"].fetchmany(min(256, remaining))
            if not raw_rows:
                entry["exhausted"] = True
                self._release_snapshot(entry)
                return
            for raw in raw_rows:
                if len(entry["rows"]) >= entry["maximumRows"]:
                    self._truncate_retention(entry, "row_capacity")
                    return
                row, events = entry["normalizeRow"](raw, len(entry["rows"]))
                row_bytes = json_utf8_size(row)
                with self._lock:
                    total_after = self._active_bytes(entry["kind"], excluding=entry["resultId"]) + entry["retainedBytes"] + row_bytes
                    maximum_total_bytes = self.maximum_aggregate_bytes if entry["kind"] == "aggregate" else self.maximum_detail_bytes
                    capacity_exhausted = entry["retainedBytes"] + row_bytes > MAX_STRUCTURED_RESULT_BYTES or total_after > maximum_total_bytes
                    if not capacity_exhausted:
                        entry["rows"].append(row)
                        entry["retainedBytes"] += row_bytes
                        entry["template"]["limitEvents"].extend(events)
                if capacity_exhausted:
                    self._truncate_retention(entry, "byte_capacity")
                    return

    def _envelope(
        self, entry: dict[str, Any], offset: int, returned: int, has_next: bool, *, commit: bool,
    ) -> dict[str, Any]:
        previous_starts = [start for start in entry["pageStarts"] if start < offset]
        previous_offset = previous_starts[-1] if previous_starts else None
        next_offset = offset + returned if has_next else None
        if commit and next_offset is not None and next_offset not in entry["pageStarts"]:
            entry["pageStarts"].append(next_offset)
            entry["pageStarts"].sort()

        def cursor_for(value: int | None) -> str | None:
            if value is None:
                return None
            if commit:
                return self._cursor_for_offset(entry, value)
            return entry["offsetCursors"].get(value) or "x" * 32

        return {
            "version": 1,
            "id": entry["resultId"],
            "kind": entry["kind"],
            "binding": entry["bindingToken"],
            "state": "retained",
            "processLocal": True,
            "retention": "repeatable_read_snapshot" if entry["kind"] == "detail" else "bounded_memory",
            "snapshotState": "released" if entry["snapshotReleased"] else "open",
            "expiresAt": entry["expiresAt"],
            "availableRows": len(entry["rows"]) if entry["exhausted"] else None,
            "page": {
                "offset": offset,
                "pageSize": entry["pageSize"],
                "returnedRows": returned,
                "hasNext": has_next,
                "hasPrevious": previous_offset is not None,
                "nextCursor": cursor_for(next_offset),
                "previousCursor": cursor_for(previous_offset),
            },
            "export": {"formats": ["json", "csv"], "persistentUntilExpiry": True},
            "limits": {
                "maximumRows": entry["maximumRows"],
                "maximumBytes": MAX_STRUCTURED_RESULT_BYTES,
                "terminalTruncation": entry["retentionTruncated"],
                "terminalReason": entry["terminalReason"],
            },
        }

    def _page_response(
        self, entry: dict[str, Any], offset: int, rows: list[list[Any]], *, commit: bool,
    ) -> dict[str, Any]:
        end = offset + len(rows)
        has_next = end < len(entry["rows"]) or not entry["exhausted"]
        envelope = self._envelope(entry, offset, len(rows), has_next, commit=commit)
        template = entry["template"]
        if entry["kind"] == "aggregate":
            response = {
                **template,
                "rows": rows,
                "rowCount": len(entry["rows"]),
                "truncated": bool(template["semanticTruncated"] or entry["retentionTruncated"]),
                "resultResource": envelope,
            }
            response.pop("semanticTruncated", None)
            return response
        absolute_offset = template["initialOffset"] + offset
        response = {
            **template,
            "rows": rows,
            "offset": absolute_offset,
            "nextOffset": absolute_offset + len(rows),
            "hasMore": has_next,
            "truncated": bool(template["truncated"] or entry["retentionTruncated"]),
            "resultResource": envelope,
        }
        response.pop("initialOffset", None)
        return response

    def _page(self, entry: dict[str, Any], offset: int) -> dict[str, Any]:
        self._ensure_rows(entry, offset + entry["pageSize"] + 1)
        candidates = entry["rows"][offset:offset + entry["pageSize"]]
        try:
            rows, _preview = fit_serialized_envelope(
                candidates,
                lambda values: self._page_response(entry, offset, values, commit=False),
                MAX_STRUCTURED_PAGE_BYTES,
            )
        except ResultLimitError as error:
            raise PostgresServiceError(
                422, "structured_result_page_metadata_too_large",
                "Structured result page metadata exceeds the serialized response byte limit",
                error.details,
            ) from error
        if candidates and not rows:
            raise PostgresServiceError(
                422, "structured_result_page_too_large",
                "A structured result row cannot fit in the serialized response page",
                {"limitSource": "application", "maximumBytes": MAX_STRUCTURED_PAGE_BYTES},
            )
        response = self._page_response(entry, offset, rows, commit=True)
        if json_utf8_size(response) > MAX_STRUCTURED_PAGE_BYTES:
            raise PostgresServiceError(
                422, "structured_result_page_metadata_too_large",
                "Structured result page metadata exceeds the serialized response byte limit",
                {"limitSource": "application", "maximumBytes": MAX_STRUCTURED_PAGE_BYTES},
            )
        return response

    def first_page(self, entry: dict[str, Any]) -> dict[str, Any]:
        lock = entry["operationLock"]
        lock.acquire()
        try:
            self._ensure_active_entry(entry)
            entry["lastAccessEpoch"] = self.clock()
            return self._page(entry, 0)
        except Exception:
            self._remove(entry["resultId"], "transport_error")
            raise
        finally:
            lock.release()

    def page(self, entry: dict[str, Any], cursor: Any) -> dict[str, Any]:
        if not isinstance(cursor, str) or not cursor:
            raise ValidationError("cursor is required")
        offset = entry["cursorOffsets"].get(cursor)
        if offset is None:
            raise PostgresServiceError(409, "result_cursor_stale", "Structured result cursor is invalid or belongs to another result")
        lock = entry["operationLock"]
        if not lock.acquire(blocking=False):
            raise PostgresServiceError(409, "result_busy", "Structured result is serving another page or export")
        try:
            self._ensure_active_entry(entry)
            entry["lastAccessEpoch"] = self.clock()
            self._structured_metrics["pages"] += 1
            return self._page(entry, offset)
        except Exception:
            self._remove(entry["resultId"], "transport_error")
            raise
        finally:
            lock.release()

    def export(self, entry: dict[str, Any], format_name: Any) -> tuple[str, str, bytes, dict[str, str]]:
        if format_name not in {"json", "csv"}:
            raise ValidationError("format must be json or csv")
        lock = entry["operationLock"]
        if not lock.acquire(blocking=False):
            raise PostgresServiceError(409, "result_busy", "Structured result is serving another page or export")
        try:
            self._ensure_active_entry(entry)
            entry["lastAccessEpoch"] = self.clock()
            self._ensure_rows(entry, entry["maximumRows"] + 1)
            document_resource = self._envelope(entry, 0, len(entry["rows"]), False, commit=True)
            if format_name == "json":
                content = json.dumps(
                    {"columns": entry["columns"], "rows": entry["rows"], "resultResource": document_resource},
                    ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                ).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                content = csv_export(entry["columns"], entry["rows"])
                content_type = "text/csv; charset=utf-8"
            if len(content) > MAX_EXPORT_BYTES:
                raise PostgresServiceError(
                    422, "structured_result_export_too_large",
                    "Structured result export exceeds the application export byte limit",
                    {"limitSource": "application", "maximumBytes": MAX_EXPORT_BYTES},
                )
            self._structured_metrics["exports"] += 1
            extension = "json" if format_name == "json" else "csv"
            filename = f"schemer-{entry['kind']}-{entry['resultId'][-12:]}.{extension}"
            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Schemer-Result-Truncated": "true" if entry["retentionTruncated"] else "false",
            }
            return content_type, filename, content, headers
        except Exception:
            self._remove(entry["resultId"], "export_error")
            raise
        finally:
            lock.release()

    def cancel(self, entry: dict[str, Any]) -> dict[str, Any]:
        lock = entry["operationLock"]
        if not lock.acquire(blocking=False):
            raise PostgresServiceError(409, "result_busy", "Structured result is serving another page or export")
        try:
            self._ensure_active_entry(entry)
            self._remove(entry["resultId"], "cancelled")
            self._structured_metrics["cancelled"] += 1
            return {"resultId": entry["resultId"], "state": "cancelled", "closed": True}
        finally:
            lock.release()

    def metrics(self) -> dict[str, Any]:
        base = super().metrics()
        with self._lock:
            entries = list(self._entries.values())
            aggregate_bytes = self._active_bytes("aggregate")
            detail_bytes = self._active_bytes("detail")
            counters = dict(self._structured_metrics)
            aggregate_count = sum(entry["kind"] == "aggregate" for entry in entries)
            detail_count = sum(entry["kind"] == "detail" for entry in entries)
        return {
            **base,
            "activeSnapshots": sum(not entry["snapshotReleased"] for entry in entries),
            "snapshotCapacity": self.maximum_snapshots,
            "retainedBytes": aggregate_bytes + detail_bytes,
            "memoryCapacityBytes": self.maximum_aggregate_bytes + self.maximum_detail_bytes,
            "aggregateResults": {"active": aggregate_count, "capacity": self.maximum_aggregate_results,
                                 "retainedBytes": aggregate_bytes, "memoryCapacityBytes": self.maximum_aggregate_bytes},
            "detailResults": {"active": detail_count, "capacity": self.maximum_detail_results,
                              "retainedBytes": detail_bytes, "memoryCapacityBytes": self.maximum_detail_bytes},
            "maximumResultBytes": MAX_STRUCTURED_RESULT_BYTES,
            "maximumDetailRows": MAX_DETAIL_RETAINED_ROWS,
            "ddlImplication": "Open detail snapshots hold ACCESS SHARE until exhausted, exported, cancelled, expired, or shutdown",
            **counters,
        }
