from __future__ import annotations

import inspect
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterator

from .postgres_common import PostgresServiceError


EXECUTION_CAPACITIES = {
    "catalog": 8,
    "read": 8,
    "console": 4,
    "write": 1,
}


@dataclass
class _ClassState:
    capacity: int
    active: int = 0
    admitted: int = 0
    rejected: int = 0
    completed: int = 0
    failed: int = 0
    wait_ms: float = 0.0
    run_ms: float = 0.0


@dataclass
class _TargetState:
    active: int = 0
    admitted: int = 0
    rejected: int = 0
    completed: int = 0


class PostgresExecutionController:
    """Process-local admission control for independent PostgreSQL connections."""

    def __init__(
        self,
        capacities: dict[str, int] | None = None,
        *,
        global_capacity: int = 12,
        target_capacity: int = 4,
        clock: Callable[[], float] = time.perf_counter,
    ):
        configured = dict(EXECUTION_CAPACITIES if capacities is None else capacities)
        if set(configured) != set(EXECUTION_CAPACITIES) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in configured.values()
        ):
            raise ValueError("PostgreSQL execution capacities are invalid")
        if isinstance(global_capacity, bool) or not isinstance(global_capacity, int) or global_capacity < 1:
            raise ValueError("global PostgreSQL execution capacity must be positive")
        if isinstance(target_capacity, bool) or not isinstance(target_capacity, int) or target_capacity < 1:
            raise ValueError("target PostgreSQL execution capacity must be positive")
        self._states = {name: _ClassState(value) for name, value in configured.items()}
        self._global_capacity = global_capacity
        self._target_capacity = target_capacity
        self._targets: dict[str, _TargetState] = {}
        self._global_active = 0
        self._closed = False
        self._lock = threading.Lock()
        self._local = threading.local()
        self._clock = clock

    @contextmanager
    def execution(self, execution_class: str, target: str | None = None) -> Iterator[None]:
        if execution_class not in self._states:
            raise ValueError("unknown PostgreSQL execution class")
        if target is not None and (not isinstance(target, str) or not target):
            raise ValueError("PostgreSQL execution target must be a non-empty string")
        active_targets = getattr(self._local, "targets", set())
        if getattr(self._local, "active", False):
            if target is None or target in active_targets:
                yield
                return
            self._acquire_target(execution_class, target)
            self._local.targets = {*active_targets, target}
            try:
                yield
            finally:
                self._local.targets = active_targets
                self._release_target(target)
            return
        requested_at = self._clock()
        with self._lock:
            state = self._states[execution_class]
            if self._closed:
                raise PostgresServiceError(
                    503, "postgres_execution_unavailable",
                    "PostgreSQL execution is unavailable while the service is stopping",
                    {"boundary": "process_admission", "scope": "service",
                     "executionClass": execution_class, "retryable": True},
                )
            target_state = self._targets.setdefault(target, _TargetState()) if target is not None else None
            exhausted_scope = (
                "class" if state.active >= state.capacity else
                "global" if self._global_active >= self._global_capacity else
                "target" if target_state is not None and target_state.active >= self._target_capacity else None
            )
            if exhausted_scope is not None:
                state.rejected += 1
                if target_state is not None:
                    target_state.rejected += 1
                raise PostgresServiceError(
                    429, "postgres_execution_busy",
                    "Process PostgreSQL admission capacity is busy; retry the request",
                    {"boundary": "process_admission", "scope": exhausted_scope,
                     "executionClass": execution_class, "targetFingerprint": target, "retryable": True},
                )
            state.active += 1
            state.admitted += 1
            state.wait_ms += max(0.0, (self._clock() - requested_at) * 1000)
            self._global_active += 1
            if target_state is not None:
                target_state.active += 1
                target_state.admitted += 1
        started_at = self._clock()
        failed = False
        self._local.active = True
        self._local.targets = {target} if target is not None else set()
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._local.active = False
            self._local.targets = set()
            elapsed = max(0.0, (self._clock() - started_at) * 1000)
            with self._lock:
                state.active -= 1
                state.completed += 1
                state.failed += int(failed)
                state.run_ms += elapsed
                self._global_active -= 1
                if target is not None:
                    target_state = self._targets[target]
                    target_state.active -= 1
                    target_state.completed += 1

    def _acquire_target(self, execution_class: str, target: str) -> None:
        with self._lock:
            state = self._targets.setdefault(target, _TargetState())
            if state.active >= self._target_capacity:
                state.rejected += 1
                self._states[execution_class].rejected += 1
                raise PostgresServiceError(
                    429, "postgres_execution_busy",
                    "Process PostgreSQL admission capacity is busy; retry the request",
                    {"boundary": "process_admission", "scope": "target",
                     "executionClass": execution_class, "targetFingerprint": target, "retryable": True},
                )
            state.active += 1
            state.admitted += 1

    def _release_target(self, target: str) -> None:
        with self._lock:
            state = self._targets[target]
            state.active -= 1
            state.completed += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "stopping" if self._closed else "available",
                "global": {"active": self._global_active, "capacity": self._global_capacity},
                "targetCapacity": self._target_capacity,
                "target": {
                    "active": sum(state.active for state in self._targets.values()),
                    "capacityPerTarget": self._target_capacity,
                    "tracked": len(self._targets),
                },
                "classes": {
                    name: {
                        "active": state.active, "capacity": state.capacity,
                        "admitted": state.admitted, "rejected": state.rejected,
                        "completed": state.completed, "failed": state.failed,
                        "waitMs": round(state.wait_ms, 3), "runMs": round(state.run_ms, 3),
                    }
                    for name, state in self._states.items()
                },
                "targets": {
                    fingerprint: {
                        "active": state.active, "capacity": self._target_capacity,
                        "admitted": state.admitted, "rejected": state.rejected,
                        "completed": state.completed,
                    }
                    for fingerprint, state in self._targets.items()
                },
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True


def postgres_execution(execution_class: str):
    if execution_class not in EXECUTION_CAPACITIES:
        raise ValueError("unknown PostgreSQL execution class")

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def admitted(self, *args, **kwargs):
            profile_id = signature.bind(self, *args, **kwargs).arguments.get("profile_id")
            target = self.admission_target(profile_id) if isinstance(profile_id, str) else None
            with self.execution(execution_class, target):
                return function(self, *args, **kwargs)
        return admitted
    return decorate
