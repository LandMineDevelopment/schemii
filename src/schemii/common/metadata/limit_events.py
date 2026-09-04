"""Privacy-safe metadata journal for administrator-configured limit events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Literal, Protocol

from schemii.common.errors import MetadataStorageUnavailableError

from .users import ensure_local_metadata_user


LimitOutcome = Literal["rejected", "evicted"]


@dataclass(frozen=True, slots=True)
class LimitEventNotice:
    """The non-sensitive facts needed to explain and record one reached limit."""

    resource: str
    limit_name: str
    configured_limit: int | float
    observed_value: int | float | None = None
    outcome: LimitOutcome = "rejected"


@dataclass(frozen=True, slots=True)
class LimitEvent:
    notice: LimitEventNotice
    occurred_at: datetime
    error_code: str
    owner_id: str | None = None
    request_id: str | None = None
    method: str | None = None
    path: str | None = None
    workspace_id: str | None = None
    connection_id: str | None = None


class LimitEventRecorder(Protocol):
    def record(self, event: LimitEvent) -> None: ...


class InMemoryLimitEventRecorder:
    def __init__(self, *, retention_days: int = 30, maximum_events: int = 100_000) -> None:
        self._retention = timedelta(days=retention_days)
        self._maximum_events = maximum_events
        self._events: list[LimitEvent] = []
        self._lock = RLock()

    def record(self, event: LimitEvent) -> None:
        cutoff = event.occurred_at - self._retention
        with self._lock:
            self._events = [item for item in self._events if item.occurred_at >= cutoff]
            self._events.append(event)
            if len(self._events) > self._maximum_events:
                del self._events[: len(self._events) - self._maximum_events]

    def events(self) -> tuple[LimitEvent, ...]:
        """Testing/operations seam; limit events are not exposed as product data."""

        with self._lock:
            return tuple(self._events)


class PostgresLimitEventRecorder:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        retention_days: int = 30,
        maximum_events: int = 100_000,
    ) -> None:
        self._connection_factory = connection_factory
        self._retention = timedelta(days=retention_days)
        self._maximum_events = maximum_events

    def record(self, event: LimitEvent) -> None:
        connection: Any | None = None
        try:
            connection = self._connection_factory()
            with connection.cursor() as cursor:
                if event.owner_id is not None:
                    ensure_local_metadata_user(cursor, event.owner_id)
                cursor.execute(
                    """
                    INSERT INTO metadata.limit_events (
                        occurred_at, owner_id, request_id, request_method,
                        request_path, workspace_id, connection_id, error_code,
                        resource, limit_name, configured_limit, observed_value,
                        outcome
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        event.occurred_at,
                        event.owner_id,
                        event.request_id,
                        event.method,
                        event.path,
                        event.workspace_id,
                        event.connection_id,
                        event.error_code,
                        event.notice.resource,
                        event.notice.limit_name,
                        event.notice.configured_limit,
                        event.notice.observed_value,
                        event.notice.outcome,
                    ),
                )
                cursor.execute(
                    "DELETE FROM metadata.limit_events WHERE occurred_at < %s",
                    (event.occurred_at - self._retention,),
                )
                cursor.execute(
                    """
                    DELETE FROM metadata.limit_events
                    WHERE id IN (
                        SELECT id FROM metadata.limit_events
                        ORDER BY occurred_at DESC, id DESC
                        OFFSET %s
                    )
                    """,
                    (self._maximum_events,),
                )
            connection.commit()
        except Exception as error:
            if connection is not None:
                connection.rollback()
            raise MetadataStorageUnavailableError(
                "Limit event metadata could not be recorded"
            ) from error
        finally:
            if connection is not None:
                connection.close()


def new_limit_event(
    notice: LimitEventNotice,
    *,
    error_code: str,
    owner_id: str | None = None,
    request_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    workspace_id: str | None = None,
    connection_id: str | None = None,
) -> LimitEvent:
    return LimitEvent(
        notice=notice,
        occurred_at=datetime.now(timezone.utc),
        error_code=error_code,
        owner_id=owner_id,
        request_id=request_id,
        method=method,
        path=path,
        workspace_id=workspace_id,
        connection_id=connection_id,
    )
