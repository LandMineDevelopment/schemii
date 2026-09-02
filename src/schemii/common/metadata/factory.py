"""Metadata repository composition boundary."""

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

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
)
from .secrets import read_encryption_key


def _memory_readiness_probe() -> None:
    """In-memory repositories have no external dependency to probe."""


@dataclass(frozen=True)
class MetadataRepositories:
    connections: ConnectionRepository
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

    def check_readiness(self) -> None:
        self.readiness_probe()


def create_metadata_repositories(
    env: Mapping[str, str] | None = None,
) -> MetadataRepositories:
    """Use durable PostgreSQL when configured, otherwise isolate tests in memory."""

    config = MetadataConfig.from_env(env)
    if config is None:
        return MetadataRepositories(connections=InMemoryConnectionRepository())
    from schemii.common.connections.postgres_store import PostgresConnectionRepository

    connection_factory = MetadataConnectionFactory(config)
    MetadataMigrator(connection_factory).migrate()
    cipher = CredentialCipher(read_encryption_key(config.encryption_key_file))
    readiness_factory = MetadataConnectionFactory(
        config,
        statement_timeout_ms=2_000,
        application_name="schemii-metadata-readiness",
    )
    return MetadataRepositories(
        connections=PostgresConnectionRepository(connection_factory, cipher),
        storage="postgresql",
        durable=True,
        readiness_probe=MetadataReadinessProbe(readiness_factory),
        target_policy=MetadataControlPlaneTargetPolicy.from_dsn(
            config.dsn,
            host_aliases=config.target_host_aliases,
        ),
        connection_factory=connection_factory,
    )
