from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .postgres_common import PostgresServiceError


def utc_expiry(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class RetainedResourceRegistry:
    """Shared process-local ownership, expiry, tombstone, and cleanup lifecycle."""

    def __init__(
        self,
        *,
        label: str,
        maximum_active: int,
        ttl_seconds: int,
        clock: Any = time.time,
        maximum_tombstones: int = 256,
        capacity_code: str = "result_capacity_exhausted",
        capacity_message: str = "Result retention capacity is exhausted",
        stopping_code: str = "result_retention_shutting_down",
        stopping_message: str = "Result retention is shutting down",
        sweeper_name: str = "retained-result-expiry",
    ):
        self.label = label
        self.maximum_active = maximum_active
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.maximum_tombstones = maximum_tombstones
        self.capacity_code = capacity_code
        self.capacity_message = capacity_message
        self.stopping_code = stopping_code
        self.stopping_message = stopping_message
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._stopping = threading.Event()
        self._metrics = {
            "created": 0, "expired": 0, "closed": 0, "capacityRejected": 0,
        }
        self._sweeper = threading.Thread(target=self._sweep_loop, name=sweeper_name, daemon=True)
        self._sweeper.start()

    @staticmethod
    def _matches(entry: dict[str, Any], owner: dict[str, Any]) -> bool:
        return all(entry["owner"].get(key) == value for key, value in owner.items())

    def _sweep_loop(self) -> None:
        while not self._stopping.wait(min(30, max(1, self.ttl_seconds))):
            self.expire()

    def _tombstone(self, entry: dict[str, Any], event: str) -> None:
        self._tombstones[entry["resultId"]] = {
            "owner": entry["owner"], "state": event, "closedAt": utc_expiry(self.clock()),
        }
        while len(self._tombstones) > self.maximum_tombstones:
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
            try:
                cleanup()
            except Exception:
                pass

    def _prepare_capacity_locked(
        self, entry: dict[str, Any], evicted: list[dict[str, Any]],
    ) -> None:
        """Allow specialized registries to evict safe resources before admission."""

    def _validate_capacity_locked(self, entry: dict[str, Any]) -> None:
        """Allow specialized registries to enforce additional atomic capacity limits."""

    def _remove_locked(self, entry: dict[str, Any], event: str) -> bool:
        current = self._entries.get(entry["resultId"])
        if current is not entry:
            return False
        self._entries.pop(entry["resultId"])
        self._tombstone(entry, event)
        self._metrics["expired" if event == "expired" else "closed"] += 1
        return True

    def _add_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.expire()
        evicted: list[dict[str, Any]] = []
        try:
            with self._lock:
                if self._stopping.is_set():
                    raise PostgresServiceError(503, self.stopping_code, self.stopping_message)
                self._prepare_capacity_locked(entry, evicted)
                if len(self._entries) >= self.maximum_active:
                    self._metrics["capacityRejected"] += 1
                    raise PostgresServiceError(
                        429, self.capacity_code, self.capacity_message,
                        {"limitSource": "application", "maximumActiveResults": self.maximum_active},
                    )
                self._validate_capacity_locked(entry)
                result_id = entry.get("resultId") or secrets.token_urlsafe(24)
                expires = self.clock() + self.ttl_seconds
                entry.update({
                    "resultId": result_id,
                    "owner": dict(entry["owner"]),
                    "operationLock": entry.get("operationLock") or threading.Lock(),
                    "expiresAtEpoch": expires,
                    "expiresAt": utc_expiry(expires),
                })
                self._entries[result_id] = entry
                self._metrics["created"] += 1
                return entry
        finally:
            for evicted_entry in evicted:
                self._close_entry(evicted_entry)

    def _remove(self, result_id: str, event: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(result_id)
            if entry is not None and not self._remove_locked(entry, event):
                entry = None
        if entry is not None:
            self._close_entry(entry)
        return entry

    def _require_entry(self, result_id: str, owner: dict[str, Any]) -> dict[str, Any]:
        self.expire()
        with self._lock:
            entry = self._entries.get(result_id)
            tombstone = self._tombstones.get(result_id)
        if entry is None:
            if tombstone is not None and self._matches(tombstone, owner):
                state = tombstone["state"]
                raise PostgresServiceError(
                    410, f"result_{state}", f"{self.label} is {state}",
                    {"resultId": result_id, "state": state, "closedAt": tombstone["closedAt"]},
                )
            raise PostgresServiceError(404, "result_not_found", f"{self.label} was not found")
        if not self._matches(entry, owner):
            raise PostgresServiceError(404, "result_not_found", f"{self.label} was not found")
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

    def close_matching_entries(self, owner: dict[str, Any], event: str) -> list[dict[str, Any]]:
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
                    closed.append(entry)
            finally:
                lock.release()
        return closed

    def metrics(self) -> dict[str, Any]:
        self.expire()
        with self._lock:
            entries = list(self._entries.values())
            counters = dict(self._metrics)
        return {
            "status": "stopping" if self._stopping.is_set() else "available",
            "processLocal": True,
            "active": len(entries),
            "capacity": self.maximum_active,
            "ttlSeconds": self.ttl_seconds,
            "tombstones": len(self._tombstones),
            **counters,
        }

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
