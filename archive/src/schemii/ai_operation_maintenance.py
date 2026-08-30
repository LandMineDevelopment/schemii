from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .metadata import MetadataStoreError


def _integer(values: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(values.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum}")
    return value


@dataclass(frozen=True)
class AiOperationMaintenanceConfig:
    interval_seconds: int = 30
    heartbeat_seconds: int = 20
    lease_seconds: int = 90
    operation_stale_seconds: int = 0
    reservation_stale_seconds: int = 300
    delivery_stale_seconds: int = 120
    cleanup_retention_seconds: int = 604800
    recovery_batch_size: int = 100
    cleanup_batch_size: int = 500

    def __post_init__(self) -> None:
        ranges = {
            "interval_seconds": (1, 3600), "heartbeat_seconds": (1, 1200),
            "lease_seconds": (3, 3600), "operation_stale_seconds": (0, 86400),
            "reservation_stale_seconds": (1, 86400), "delivery_stale_seconds": (1, 86400),
            "cleanup_retention_seconds": (3600, 31536000), "recovery_batch_size": (1, 10000),
            "cleanup_batch_size": (1, 10000),
        }
        for name, (minimum, maximum) in ranges.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"AI operation maintenance {name} must be from {minimum} to {maximum}")
        if self.heartbeat_seconds * 2 >= self.lease_seconds:
            raise ValueError("AI operation heartbeat must be less than half the lease duration")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AiOperationMaintenanceConfig":
        values = os.environ if env is None else env
        prefix = "SCHEMII_AI_MAINTENANCE_"
        return cls(
            interval_seconds=_integer(values, prefix + "INTERVAL_SECONDS", 30, 1, 3600),
            heartbeat_seconds=_integer(values, prefix + "HEARTBEAT_SECONDS", 20, 1, 1200),
            lease_seconds=_integer(values, prefix + "LEASE_SECONDS", 90, 3, 3600),
            operation_stale_seconds=_integer(values, prefix + "OPERATION_STALE_SECONDS", 0, 0, 86400),
            reservation_stale_seconds=_integer(values, prefix + "RESERVATION_STALE_SECONDS", 300, 1, 86400),
            delivery_stale_seconds=_integer(values, prefix + "DELIVERY_STALE_SECONDS", 120, 1, 86400),
            cleanup_retention_seconds=_integer(values, prefix + "CLEANUP_RETENTION_SECONDS", 604800, 3600, 31536000),
            recovery_batch_size=_integer(values, prefix + "RECOVERY_BATCH_SIZE", 100, 1, 10000),
            cleanup_batch_size=_integer(values, prefix + "CLEANUP_BATCH_SIZE", 500, 1, 10000),
        )


class OperationLeaseLost(Exception):
    pass


class AiOperationMaintenance:
    """One lifecycle-owned loop for metadata recovery and active claim heartbeats."""

    def __init__(self, store: Any, config: AiOperationMaintenanceConfig):
        self.store = store
        self.config = config
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._attempts: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._started_at: str | None = None
        self._last_success_at: str | None = None
        self._last_error_at: str | None = None
        self._last_error_code: str | None = None
        self._consecutive_failures = 0
        self._last_counts: dict[str, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            self._started_at = self._now().isoformat().replace("+00:00", "Z")
            self._thread = threading.Thread(target=self._run, name="ai-operation-maintenance", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=min(10, self.config.interval_seconds + 1))
        with self._lock:
            self._thread = None
            self._attempts.clear()

    def track(self, operation_id: str, attempt_id: str, claim_token: str) -> None:
        with self._lock:
            self._attempts[attempt_id] = {
                "operationId": operation_id, "token": claim_token, "lost": False,
                "nextHeartbeat": self._now() + timedelta(seconds=self.config.heartbeat_seconds),
            }
        self._wake.set()

    def release(self, attempt_id: str) -> None:
        with self._lock:
            self._attempts.pop(attempt_id, None)

    def assert_owned(self, attempt_id: str) -> None:
        with self._lock:
            tracked = self._attempts.get(attempt_id)
            if tracked is None or tracked["lost"]:
                raise OperationLeaseLost("AI operation execution lease was lost")
            token = tracked["token"]
        try:
            self.store.heartbeat_operation(attempt_id, token, lease_seconds=self.config.lease_seconds)
        except MetadataStoreError as error:
            if error.code not in {"invalid_claim", "operation_not_running", "operation_lease_expired"}:
                raise
            self._mark_lost(attempt_id, token)
            raise OperationLeaseLost("AI operation execution lease was lost") from error

    def health(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            return {
                "required": True,
                "status": "available" if running and self._consecutive_failures == 0 else "degraded",
                "running": running,
                "activeAttempts": len(self._attempts),
                "startedAt": self._started_at,
                "lastSuccessAt": self._last_success_at,
                "lastErrorAt": self._last_error_at,
                "lastErrorCode": self._last_error_code,
                "consecutiveFailures": self._consecutive_failures,
                "lastCounts": dict(self._last_counts),
            }

    def run_once(self) -> dict[str, int]:
        now = self._now()
        abandoned = self.store.abandon_stale_operations(
            stale_before=now - timedelta(seconds=self.config.operation_stale_seconds),
            limit=self.config.recovery_batch_size,
        )
        recovered = self.store.recover_stale_results(
            reserved_before=now - timedelta(seconds=self.config.reservation_stale_seconds),
            delivering_before=now - timedelta(seconds=self.config.delivery_stale_seconds),
            limit=self.config.recovery_batch_size,
        )
        cleaned = self.store.cleanup(
            before=now - timedelta(seconds=self.config.cleanup_retention_seconds),
            limit=self.config.cleanup_batch_size,
        )
        return {
            "operationsAbandoned": len(abandoned), "reservationsReleased": len(recovered["released"]),
            "deliveriesUncertain": len(recovered["uncertain"]), **cleaned,
        }

    def _run(self) -> None:
        next_maintenance = self._now()
        while not self._stop.is_set():
            now = self._now()
            self._heartbeat_due(now)
            if now >= next_maintenance:
                try:
                    counts = self.run_once()
                except Exception as error:
                    self._record_failure(error)
                else:
                    with self._lock:
                        self._last_counts = counts
                        self._last_success_at = self._now().isoformat().replace("+00:00", "Z")
                        self._consecutive_failures = 0
                        self._last_error_code = None
                next_maintenance = self._now() + timedelta(seconds=self.config.interval_seconds)
            timeout = min(self.config.heartbeat_seconds, self.config.interval_seconds)
            self._wake.wait(timeout=timeout)
            self._wake.clear()

    def _heartbeat_due(self, now: datetime) -> None:
        with self._lock:
            attempts = [(attempt_id, item["token"]) for attempt_id, item in self._attempts.items()
                        if not item["lost"] and item["nextHeartbeat"] <= now]
        for attempt_id, token in attempts:
            try:
                self.store.heartbeat_operation(attempt_id, token, lease_seconds=self.config.lease_seconds)
            except MetadataStoreError as error:
                if error.code in {"invalid_claim", "operation_not_running", "operation_lease_expired"}:
                    self._mark_lost(attempt_id, token)
                else:
                    self._record_failure(error)
            except Exception as error:
                self._record_failure(error)
            else:
                with self._lock:
                    tracked = self._attempts.get(attempt_id)
                    if tracked is not None:
                        tracked["nextHeartbeat"] = self._now() + timedelta(seconds=self.config.heartbeat_seconds)

    def _mark_lost(self, attempt_id: str, token: str) -> None:
        with self._lock:
            tracked = self._attempts.get(attempt_id)
            if tracked is not None:
                tracked["lost"] = True
        try:
            self.store.abandon_operation_attempt(attempt_id, token)
        except MetadataStoreError:
            pass

    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._last_error_at = self._now().isoformat().replace("+00:00", "Z")
            self._last_error_code = getattr(error, "code", type(error).__name__)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
