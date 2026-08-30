"""Owner-scoped connection repository contracts and prototype implementation."""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from pydantic import SecretStr

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionMetadata,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    ResolvedPostgresConnection,
)

MAX_CONNECTIONS_PER_OWNER = 100


class ConnectionRepositoryError(RuntimeError):
    """Base error for connection repository operations."""


class ConnectionNotFoundError(ConnectionRepositoryError):
    """The requested owner-scoped connection does not exist."""


class ConnectionConflictError(ConnectionRepositoryError):
    """The connection changed after the caller read it."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("PostgreSQL connection changed in another request")


class ConnectionLimitError(ConnectionRepositoryError):
    """The prototype owner has reached its bounded connection capacity."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__("The PostgreSQL connection limit has been reached")


@runtime_checkable
class ConnectionRepository(Protocol):
    def list(self, owner_id: str) -> list[PostgresConnectionProfile]: ...

    def get(self, owner_id: str, connection_id: str) -> PostgresConnectionProfile: ...

    def create(
        self, owner_id: str, request: PostgresConnectionCreate
    ) -> PostgresConnectionProfile: ...

    def update(
        self,
        owner_id: str,
        connection_id: str,
        request: PostgresConnectionUpdate,
    ) -> PostgresConnectionProfile: ...

    def delete(self, owner_id: str, connection_id: str, expected_revision: int) -> None: ...

    def resolve(self, owner_id: str, connection_id: str) -> ResolvedPostgresConnection: ...


class _ConnectionRecord:
    def __init__(
        self,
        profile: PostgresConnectionProfile,
        password: SecretStr | None,
    ) -> None:
        self.profile = profile
        self.password = password


class InMemoryConnectionRepository:
    """Ephemeral single-process adapter used until metadata PostgreSQL exists."""

    def __init__(self, *, max_connections_per_owner: int = MAX_CONNECTIONS_PER_OWNER) -> None:
        if max_connections_per_owner < 1:
            raise ValueError("max_connections_per_owner must be positive")
        self._records: dict[str, dict[str, _ConnectionRecord]] = {}
        self._lock = threading.RLock()
        self._max_connections_per_owner = max_connections_per_owner

    def list(self, owner_id: str) -> list[PostgresConnectionProfile]:
        with self._lock:
            records = self._records.get(owner_id, {})
            profiles = [record.profile.model_copy(deep=True) for record in records.values()]
        return sorted(profiles, key=lambda profile: (profile.name.casefold(), profile.id))

    def get(self, owner_id: str, connection_id: str) -> PostgresConnectionProfile:
        with self._lock:
            return self._record(owner_id, connection_id).profile.model_copy(deep=True)

    def create(
        self, owner_id: str, request: PostgresConnectionCreate
    ) -> PostgresConnectionProfile:
        with self._lock:
            owner_records = self._records.setdefault(owner_id, {})
            if len(owner_records) >= self._max_connections_per_owner:
                raise ConnectionLimitError(self._max_connections_per_owner)
            now = datetime.now(timezone.utc)
            connection_id = f"pg_{secrets.token_hex(16)}"
            profile = PostgresConnectionProfile(
                id=connection_id,
                revision=1,
                **request.model_dump(exclude={"password"}),
                credential_stored=request.password is not None,
                created_at=now,
                updated_at=now,
            )
            owner_records[connection_id] = _ConnectionRecord(
                profile,
                request.password,
            )
            return profile.model_copy(deep=True)

    def update(
        self,
        owner_id: str,
        connection_id: str,
        request: PostgresConnectionUpdate,
    ) -> PostgresConnectionProfile:
        with self._lock:
            record = self._record(owner_id, connection_id)
            if record.profile.revision != request.expected_revision:
                raise ConnectionConflictError(record.profile.revision)
            changes = request.model_dump(
                exclude_unset=True,
                exclude={"expected_revision", "password"},
            )
            metadata = PostgresConnectionMetadata.model_validate(
                {**self._metadata(record.profile), **changes}
            )
            password = (
                request.password
                if "password" in request.model_fields_set
                else record.password
            )
            updated = PostgresConnectionProfile(
                id=record.profile.id,
                revision=record.profile.revision + 1,
                **metadata.model_dump(),
                credential_stored=password is not None,
                created_at=record.profile.created_at,
                updated_at=datetime.now(timezone.utc),
            )
            record.profile = updated
            record.password = password
            return updated.model_copy(deep=True)

    def delete(self, owner_id: str, connection_id: str, expected_revision: int) -> None:
        with self._lock:
            record = self._record(owner_id, connection_id)
            if record.profile.revision != expected_revision:
                raise ConnectionConflictError(record.profile.revision)
            del self._records[owner_id][connection_id]

    def resolve(self, owner_id: str, connection_id: str) -> ResolvedPostgresConnection:
        with self._lock:
            record = self._record(owner_id, connection_id)
            return ResolvedPostgresConnection(
                id=record.profile.id,
                revision=record.profile.revision,
                **self._metadata(record.profile),
                password=record.password,
            )

    def _record(self, owner_id: str, connection_id: str) -> _ConnectionRecord:
        try:
            return self._records[owner_id][connection_id]
        except KeyError as error:
            raise ConnectionNotFoundError("PostgreSQL connection was not found") from error

    @staticmethod
    def _metadata(profile: PostgresConnectionProfile) -> dict[str, object]:
        return {
            field: getattr(profile, field)
            for field in PostgresConnectionMetadata.model_fields
        }
