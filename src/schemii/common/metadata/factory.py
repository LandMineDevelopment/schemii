"""Metadata repository composition boundary."""

from dataclasses import dataclass

from schemii.common.connections.store import (
    ConnectionRepository,
    InMemoryConnectionRepository,
)


@dataclass(frozen=True)
class MetadataRepositories:
    connections: ConnectionRepository


def create_metadata_repositories() -> MetadataRepositories:
    """Return valid ephemeral repositories for the current prototype.

    TODO(metadata-postgres): replace this factory with PostgreSQL-backed users,
    sessions, wrapped per-user encryption keys, encrypted connection
    credentials, owner-scoped product resources, and migration of the local
    prototype user. Keep the repository contracts and route ownership checks
    stable when making that replacement.
    """
    return MetadataRepositories(connections=InMemoryConnectionRepository())
