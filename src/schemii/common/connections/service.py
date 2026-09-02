"""Coordinated lifecycle for connections and product-owned references."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Protocol, runtime_checkable

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionMetadata,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    ResolvedPostgresConnection,
)
from .policy import AllowAllConnectionTargetPolicy, ConnectionTargetPolicy
from .store import (
    ConnectionInUseError,
    ConnectionMutationGuard,
    ConnectionMutationGuardRegistrar,
    ConnectionMutationOperation,
    ConnectionRepository,
)


class ConnectionDependencyProvider(Protocol):
    dependency_name: str

    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...


@runtime_checkable
class TransactionalConnectionDependencyProvider(Protocol):
    """Product dependency checks that share a durable connection transaction."""

    def guard_connection_mutation(
        self,
        cursor: Any,
        owner_id: str,
        connection_id: str,
        operation: ConnectionMutationOperation,
    ) -> None: ...


class ConnectionService:
    """Keep connection mutations and product references coherent.

    Process locks serialize in-memory mutations with live uses. Durable
    repositories additionally run product dependency checks under the same
    transaction and row lock as the connection mutation, closing cross-process
    check-then-change races without coupling common code to product tables.
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
        transactional_providers = tuple(
            provider
            for provider in dependency_providers
            if isinstance(provider, TransactionalConnectionDependencyProvider)
        )
        if transactional_providers and isinstance(
            repository, ConnectionMutationGuardRegistrar
        ):
            repository.set_mutation_guard(
                self._transactional_guard(transactional_providers)
            )

    @staticmethod
    def _transactional_guard(
        providers: tuple[TransactionalConnectionDependencyProvider, ...],
    ) -> ConnectionMutationGuard:
        def guard(
            cursor: Any,
            owner_id: str,
            connection_id: str,
            operation: ConnectionMutationOperation,
        ) -> None:
            for provider in providers:
                provider.guard_connection_mutation(
                    cursor,
                    owner_id,
                    connection_id,
                    operation,
                )

        return guard

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
