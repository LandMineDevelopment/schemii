"""Coordinated lifecycle for connections and product-owned references."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Protocol

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionMetadata,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    ResolvedPostgresConnection,
)
from .policy import AllowAllConnectionTargetPolicy, ConnectionTargetPolicy
from .store import ConnectionRepository


class ConnectionDependencyProvider(Protocol):
    dependency_name: str

    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...


class ConnectionInUseError(RuntimeError):
    def __init__(self, dependencies: dict[str, int]) -> None:
        self.dependencies = dependencies
        super().__init__("The PostgreSQL connection is used by product resources")


class ConnectionService:
    """Keep connection mutations and product references coherent.

    The prototype serializes each connection lifecycle through a bounded set
    of process locks. A future metadata PostgreSQL adapter can implement this
    boundary with transactions and foreign keys without changing route or
    product-service contracts.
    """

    def __init__(
        self,
        repository: ConnectionRepository,
        dependency_providers: tuple[ConnectionDependencyProvider, ...],
        *,
        target_policy: ConnectionTargetPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._dependency_providers = dependency_providers
        self._target_policy = target_policy or AllowAllConnectionTargetPolicy()
        self._locks = tuple(threading.RLock() for _ in range(64))

    def _lock_for(self, owner_id: str, connection_id: str) -> threading.RLock:
        return self._locks[hash((owner_id, connection_id)) % len(self._locks)]

    def list(self, owner_id: str) -> list[PostgresConnectionProfile]:
        return self._repository.list(owner_id)

    def get(self, owner_id: str, connection_id: str) -> PostgresConnectionProfile:
        return self._repository.get(owner_id, connection_id)

    def create(
        self,
        owner_id: str,
        request: PostgresConnectionCreate,
    ) -> PostgresConnectionProfile:
        self._target_policy.validate(request)
        return self._repository.create(owner_id, request)

    def update(
        self,
        owner_id: str,
        connection_id: str,
        request: PostgresConnectionUpdate,
    ) -> PostgresConnectionProfile:
        with self._lock_for(owner_id, connection_id):
            current = self._repository.get(owner_id, connection_id)
            changes = request.model_dump(
                exclude_unset=True,
                exclude={"expected_revision", "password"},
            )
            target = PostgresConnectionMetadata.model_validate(
                {
                    **current.model_dump(
                        include={
                            "name",
                            "host",
                            "port",
                            "database",
                            "username",
                            "ssl_mode",
                            "connect_timeout",
                        }
                    ),
                    **changes,
                }
            )
            self._target_policy.validate(target)
            return self._repository.update(owner_id, connection_id, request)

    @contextmanager
    def use(
        self,
        owner_id: str,
        connection_id: str,
    ) -> Iterator[ResolvedPostgresConnection]:
        """Prevent mutation or deletion while one product binds live work."""
        with self._lock_for(owner_id, connection_id):
            resolved = self._repository.resolve(owner_id, connection_id)
            self._target_policy.validate(resolved)
            yield resolved

    def delete(self, owner_id: str, connection_id: str, expected_revision: int) -> None:
        with self._lock_for(owner_id, connection_id):
            # Resolve first so a nonexistent connection reports not-found even
            # if a damaged product adapter contains an orphaned reference.
            self._repository.get(owner_id, connection_id)
            dependencies = {
                provider.dependency_name: count
                for provider in self._dependency_providers
                if (count := provider.count_for_connection(owner_id, connection_id))
            }
            if dependencies:
                raise ConnectionInUseError(dependencies)
            self._repository.delete(owner_id, connection_id, expected_revision)
