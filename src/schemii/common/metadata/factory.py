"""Metadata repository composition boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from schemii.common.ai.credential_store import MemoryAiCredentialStore, PostgresAiCredentialStore
from schemii.common.connections.store import (
    ConnectionRepository,
    InMemoryConnectionRepository,
)
from schemii.common.connections.policy import (
    AllowAllConnectionTargetPolicy,
    ConnectionTargetPolicy,
    MetadataControlPlaneTargetPolicy,
)

from .config import MetadataConfig
from .crypto import CredentialCipher
from .database import (
    MetadataConnectionFactory,
    MetadataMigrator,
    MetadataReadinessProbe,
    packaged_migrations,
)
from .migrations import MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE
from .limit_events import (
    InMemoryLimitEventRecorder,
    LimitEventRecorder,
    PostgresLimitEventRecorder,
)
from .secrets import read_encryption_key


def _memory_readiness_probe() -> None:
    """In-memory repositories have no external dependency to probe."""


def _memory_ai_credentials() -> MemoryAiCredentialStore:
    from schemii.common.ai.credential_store import MemoryAiCredentialStore

    return MemoryAiCredentialStore()


@dataclass(frozen=True)
class MetadataRepositories:
    connections: ConnectionRepository
    limit_events: LimitEventRecorder = field(
        default_factory=InMemoryLimitEventRecorder,
        repr=False,
        compare=False,
    )
    storage: str = "memory"
    durable: bool = False
    readiness_probe: Callable[[], None] = field(
        default=_memory_readiness_probe,
        repr=False,
        compare=False,
    )
    target_policy: ConnectionTargetPolicy = field(
        default_factory=AllowAllConnectionTargetPolicy,
        repr=False,
        compare=False,
    )
    connection_factory: Callable[[], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    ai_credentials: MemoryAiCredentialStore | PostgresAiCredentialStore = field(
        default_factory=_memory_ai_credentials,
        repr=False,
        compare=False,
    )

    def check_readiness(self) -> None:
        self.readiness_probe()


def create_metadata_repositories(
    env: Mapping[str, str] | None = None,
    *,
    migration_packages: tuple[str, ...] = (COMMON_MIGRATION_PACKAGE,),
    maximum_connections_per_owner: int = 100,
    limit_event_retention_days: int = 30,
    maximum_limit_events: int = 100_000,
) -> MetadataRepositories:
    """Build metadata adapters with an explicitly owned migration composition."""

    config = MetadataConfig.from_env(env)
    if config is None:
        return MetadataRepositories(
            connections=InMemoryConnectionRepository(
                max_connections_per_owner=maximum_connections_per_owner
            ),
            limit_events=InMemoryLimitEventRecorder(
                retention_days=limit_event_retention_days,
                maximum_events=maximum_limit_events,
            ),
        )
    from schemii.common.connections.postgres_store import PostgresConnectionRepository
    from schemii.common.ai.credential_store import PostgresAiCredentialStore

    connection_factory = MetadataConnectionFactory(config)
    MetadataMigrator(
        connection_factory,
        packaged_migrations(migration_packages),
    ).migrate()
    cipher = CredentialCipher(read_encryption_key(config.encryption_key_file))
    readiness_factory = MetadataConnectionFactory(
        config,
        statement_timeout_ms=2_000,
        application_name="schemii-metadata-readiness",
    )
    return MetadataRepositories(
        connections=PostgresConnectionRepository(
            connection_factory,
            cipher,
            max_connections_per_owner=maximum_connections_per_owner,
        ),
        limit_events=PostgresLimitEventRecorder(
            connection_factory,
            retention_days=limit_event_retention_days,
            maximum_events=maximum_limit_events,
        ),
        storage="postgresql",
        durable=True,
        readiness_probe=MetadataReadinessProbe(readiness_factory),
        target_policy=MetadataControlPlaneTargetPolicy.from_dsn(
            config.dsn,
            host_aliases=config.target_host_aliases,
        ),
        connection_factory=connection_factory,
        ai_credentials=PostgresAiCredentialStore(connection_factory, cipher),
    )
