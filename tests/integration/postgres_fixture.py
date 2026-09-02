"""Shared public shape for real-PostgreSQL integration fixtures."""

from dataclasses import dataclass
from typing import Any, Callable

from schemii.common.metadata.factory import MetadataRepositories


@dataclass(frozen=True)
class PostgresMetadataHarness:
    owner_id: str
    environment: dict[str, str]
    repositories: MetadataRepositories

    @property
    def connection_factory(self) -> Callable[[], Any]:
        factory = self.repositories.connection_factory
        assert factory is not None
        return factory
