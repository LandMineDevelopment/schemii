"""Metadata repository composition boundary."""

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from schemii.common.connections.store import (
    ConnectionRepository,
    InMemoryConnectionRepository,
)

from .config import MetadataConfig
from .crypto import CredentialCipher
from .database import MetadataConnectionFactory, MetadataMigrator
from .secrets import read_encryption_key


@dataclass(frozen=True)
class MetadataRepositories:
    connections: ConnectionRepository
    storage: str = "memory"
    durable: bool = False
    connection_factory: Callable[[], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


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
    return MetadataRepositories(
        connections=PostgresConnectionRepository(connection_factory, cipher),
        storage="postgresql",
        durable=True,
        connection_factory=connection_factory,
    )
