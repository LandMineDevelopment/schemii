from __future__ import annotations

import threading
from typing import Any


class ReadOnlyQueryReservation:
    def __init__(self, registry: "ReadOnlyQueryCancellationRegistry", operation_id: str | None):
        self.registry = registry
        self.operation_id = operation_id
        self.cancel_requested = registry.reserve(operation_id) if operation_id is not None else False

    def attach(self, connection: Any) -> bool:
        return self.operation_id is not None and self.registry.attach(self.operation_id, connection)

    def requested(self) -> bool:
        return self.operation_id is not None and self.registry.requested(self.operation_id)

    def release(self) -> None:
        if self.operation_id is not None:
            self.registry.release(self.operation_id)


class ReadOnlyQueryCancellationRegistry:
    """Coordinates cancellation requests with a query's process-local connection."""

    def __init__(self):
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}

    def reserve(self, operation_id: str) -> bool:
        with self._lock:
            entry = self._entries.setdefault(
                operation_id, {"connection": None, "cancelRequested": False, "reserved": False},
            )
            if entry["reserved"]:
                raise RuntimeError("Read-only query operation is already active")
            entry["reserved"] = True
            return bool(entry["cancelRequested"])

    def reservation(self, operation_id: str | None) -> ReadOnlyQueryReservation:
        return ReadOnlyQueryReservation(self, operation_id)

    def attach(self, operation_id: str, connection: Any) -> bool:
        with self._lock:
            entry = self._entries[operation_id]
            entry["connection"] = connection
            requested = bool(entry["cancelRequested"])
        if requested:
            self._cancel_connection(connection)
        return requested

    def request(self, operation_id: str) -> dict[str, bool]:
        with self._lock:
            entry = self._entries.setdefault(
                operation_id, {"connection": None, "cancelRequested": False, "reserved": False},
            )
            entry["cancelRequested"] = True
            connection = entry["connection"]
        if connection is not None:
            self._cancel_connection(connection)
        return {"requested": True}

    def requested(self, operation_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(operation_id)
            return bool(entry and entry["cancelRequested"])

    def release(self, operation_id: str) -> None:
        with self._lock:
            self._entries.pop(operation_id, None)

    def close(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            for entry in entries:
                entry["cancelRequested"] = True
        for entry in entries:
            if entry["connection"] is not None:
                self._cancel_connection(entry["connection"])

    @staticmethod
    def _cancel_connection(connection: Any) -> None:
        try:
            connection.cancel()
        except Exception:
            # The executing request remains responsible for observing PostgreSQL's
            # terminal result and recording cancellation, timeout, or uncertainty.
            pass
