"""Coordinated lifecycle for connections and product-owned references."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Protocol, runtime_checkable

from .dependencies import ConnectionDeletionImpact, ConnectionDependentResource
from .models import (
    PostgresConnectionCreate,
    PostgresConnectionMetadata,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    ResolvedPostgresConnection,
    SCHEMII_CONNECTION_OWNER_ID,
)
from .policy import AllowAllConnectionTargetPolicy, ConnectionTargetPolicy
from .store import (
    ConnectionInUseError,
    ConnectionMutationGuard,
    ConnectionMutationGuardRegistrar,
    ConnectionMutationOperation,
    ConnectionRepository,
    ConnectionNotFoundError,
)


class ConnectionDependencyProvider(Protocol):
    dependency_name: str

    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...

    def dependencies_for_connection(
        self,
        owner_id: str,
        connection_id: str,
    ) -> tuple[ConnectionDependentResource, ...]: ...


@runtime_checkable
class TransactionalConnectionDependencyProvider(Protocol):
    """Product dependency checks that share a durable connection transaction."""

    def guard_connection_mutation(
        self,
        cursor: Any,
        owner_id: str,
        connection_id: str,
        operation: ConnectionMutationOperation,
        changed_fields: frozenset[str],
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
        self._auth: Any | None = None
        if isinstance(repository, ConnectionMutationGuardRegistrar):
            repository.set_mutation_guard(self._transactional_guard())

    def register_dependency_provider(self, provider: ConnectionDependencyProvider) -> None:
        """Attach a composed service's dependency guard before serving requests."""
        self._dependency_providers = tuple(
            current for current in self._dependency_providers
            if current.dependency_name != provider.dependency_name
        ) + (provider,)

    def set_authority(self, auth: Any) -> None:
        """Install the account authority after the application is assembled."""
        self._auth = auth

    def for_product(self, product: str) -> ProductConnectionAccess:
        if product not in {"schemii", "schemoo", "schemer"}:
            raise ValueError("Unknown product")
        return ProductConnectionAccess(self, product)

    def _transactional_guard(self) -> ConnectionMutationGuard:
        def guard(
            cursor: Any,
            owner_id: str,
            connection_id: str,
            operation: ConnectionMutationOperation,
            changed_fields: frozenset[str],
        ) -> None:
            for provider in self._dependency_providers:
                if not isinstance(provider, TransactionalConnectionDependencyProvider):
                    continue
                provider.guard_connection_mutation(
                    cursor,
                    owner_id,
                    connection_id,
                    operation,
                    changed_fields,
                )

        return guard

    def _lock_for(self, owner_id: str, connection_id: str) -> threading.RLock:
        return self._locks[hash((owner_id, connection_id)) % len(self._locks)]

    def list(self, owner_id: str) -> list[PostgresConnectionProfile]:
        return self._repository.list(owner_id)

    def list_schemii_owned(self) -> list[PostgresConnectionProfile]:
        """Inventory the application-owned pool, never a person's private profiles."""
        return self._repository.list(SCHEMII_CONNECTION_OWNER_ID)

    def create_schemii_owned(self, request: PostgresConnectionCreate) -> PostgresConnectionProfile:
        """Store a database-admin-provisioned read-only login in the shared pool."""
        if request.password is None:
            raise ValueError("Schemii-owned profiles require an explicit credential")
        return self.create(SCHEMII_CONNECTION_OWNER_ID, request)

    def get_schemii_owned(self, connection_id: str) -> PostgresConnectionProfile:
        return self.get(SCHEMII_CONNECTION_OWNER_ID, connection_id)

    def update_schemii_owned(
        self, connection_id: str, request: PostgresConnectionUpdate,
    ) -> PostgresConnectionProfile:
        return self.update(SCHEMII_CONNECTION_OWNER_ID, connection_id, request)

    def delete_schemii_owned(self, connection_id: str, expected_revision: int) -> None:
        self.delete(SCHEMII_CONNECTION_OWNER_ID, connection_id, expected_revision)

    def get(self, owner_id: str, connection_id: str) -> PostgresConnectionProfile:
        return self._repository.get(owner_id, connection_id)

    def deletion_impact(
        self,
        owner_id: str,
        connection_id: str,
    ) -> ConnectionDeletionImpact:
        """Describe the exact owner-scoped resources retaining a connection."""

        with self._lock_for(owner_id, connection_id):
            connection = self._repository.get(owner_id, connection_id)
            dependencies = tuple(
                dependency
                for provider in self._dependency_providers
                for dependency in provider.dependencies_for_connection(
                    owner_id,
                    connection_id,
                )
            )
            return ConnectionDeletionImpact.build(
                connection.id,
                connection.revision,
                dependencies,
            )

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
            identity_fields = frozenset(
                field
                for field in ("host", "port", "database", "username")
                if getattr(current, field) != getattr(target, field)
            )
            if identity_fields:
                dependencies = {
                    provider.dependency_name: count
                    for provider in self._dependency_providers
                    if (count := provider.count_for_connection(owner_id, connection_id))
                }
                if dependencies:
                    raise ConnectionInUseError(dependencies)
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


class ProductConnectionAccess:
    """Resolve an actor's private or explicitly role-managed database identity.

    Resource owners remain the signed-in actor. Managed credentials remain with
    their original owner and are only resolved while the matching product grant
    is active. Callers persist ``profile.owner_id`` beside ``profile.id``.
    """

    def __init__(self, connections: ConnectionService, product: str) -> None:
        self._connections = connections
        self.product = product

    def _authority(self, actor_id: str) -> Any | None:
        auth = self._connections._auth
        if auth is None or not auth.enabled:
            return None
        capabilities = set(auth.capabilities(actor_id))
        required = {f"{self.product}:access"}
        if self.product == "schemer":
            required.add("schemer:author")
        if not required.issubset(capabilities):
            raise ConnectionNotFoundError("PostgreSQL connection was not found")
        return auth

    def _owner(self, actor_id: str, connection_id: str) -> str:
        auth = self._authority(actor_id)
        try:
            personal = self._connections.get(actor_id, connection_id)
        except ConnectionNotFoundError:
            if auth is None:
                raise
        else:
            if personal.ownership != "user":
                raise ConnectionNotFoundError("PostgreSQL connection was not found")
            return actor_id
        grant = auth.connection_access(actor_id, connection_id, self.product)
        if grant is None:
            raise ConnectionNotFoundError("PostgreSQL connection was not found")
        shared = self._connections.get(grant["owner_id"], connection_id)
        if shared.ownership != "schemii":
            raise ConnectionNotFoundError("PostgreSQL connection was not found")
        return grant["owner_id"]

    def list(self, actor_id: str) -> list[PostgresConnectionProfile]:
        auth = self._authority(actor_id)
        profiles = {
            profile.id: profile.model_copy(update={"owner_id": actor_id})
            for profile in self._connections.list(actor_id)
            if profile.ownership == "user"
        }
        if auth is not None:
            for grant in auth.connection_grants(actor_id):
                connection_id = grant["connection_id"]
                if connection_id in profiles:
                    continue
                if auth.connection_access(actor_id, connection_id, self.product) is None:
                    continue
                owner_id = grant["owner_id"]
                try:
                    profile = self._connections.get(owner_id, connection_id)
                except ConnectionNotFoundError:
                    continue
                if profile.ownership != "schemii":
                    continue
                profiles[connection_id] = profile.model_copy(update={"owner_id": owner_id})
        return sorted(profiles.values(), key=lambda item: (item.name.casefold(), item.id))

    def get(self, actor_id: str, connection_id: str) -> PostgresConnectionProfile:
        owner_id = self._owner(actor_id, connection_id)
        profile = self._connections.get(owner_id, connection_id)
        return profile.model_copy(update={"owner_id": owner_id})

    @contextmanager
    def use(
        self, actor_id: str, connection_id: str
    ) -> Iterator[ResolvedPostgresConnection]:
        owner_id = self._owner(actor_id, connection_id)
        with self._connections.use(owner_id, connection_id) as resolved:
            yield resolved.model_copy(update={"owner_id": owner_id})
