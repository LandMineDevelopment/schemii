"""Deployment-owned admission policy for PostgreSQL targets."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Protocol

from psycopg.conninfo import conninfo_to_dict

from .models import PostgresConnectionMetadata, ResolvedPostgresConnection


class ConnectionTargetForbiddenError(RuntimeError):
    """A requested target crosses a deployment control-plane boundary."""


class ConnectionTargetPolicy(Protocol):
    def validate(
        self,
        target: PostgresConnectionMetadata | ResolvedPostgresConnection,
    ) -> None: ...


@dataclass(frozen=True)
class AllowAllConnectionTargetPolicy:
    """Explicit policy used only by injected in-memory test services."""

    def validate(
        self,
        target: PostgresConnectionMetadata | ResolvedPostgresConnection,
    ) -> None:
        del target


@dataclass(frozen=True)
class MetadataControlPlaneTargetPolicy:
    """Forbid saved targets that resolve to the configured metadata server.

    Host aliases are deployment configuration because DNS aliases cannot be
    safely inferred from a libpq DSN. Every database on the matching host and
    port is protected: allowing a different database would still expose the
    PostgreSQL control-plane server and its bootstrap role to target traffic.
    """

    hosts: frozenset[str]
    ports: frozenset[int]
    database: str

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        host_aliases: tuple[str, ...] = (),
    ) -> "MetadataControlPlaneTargetPolicy":
        values = conninfo_to_dict(dsn)
        database = values.get("dbname") or values.get("user")
        if not database:
            raise ValueError("metadata DSN must identify a database")
        raw_hosts = [
            *(values.get("host", "localhost").split(",")),
            *(values.get("hostaddr", "").split(",")),
            *host_aliases,
        ]
        hosts = frozenset(
            normalized
            for raw_host in raw_hosts
            if raw_host.strip()
            for normalized in (_normalize_host(raw_host),)
        )
        if not hosts:
            raise ValueError("metadata DSN must identify at least one host")
        raw_ports = values.get("port", "5432").split(",")
        try:
            ports = frozenset(int(port) for port in raw_ports if port)
        except ValueError as error:
            raise ValueError("metadata DSN contains an invalid port") from error
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("metadata DSN contains an invalid port")
        return cls(hosts=hosts, ports=ports, database=database)

    def validate(
        self,
        target: PostgresConnectionMetadata | ResolvedPostgresConnection,
    ) -> None:
        if (
            _normalize_host(target.host) in self.hosts
            and target.port in self.ports
        ):
            raise ConnectionTargetForbiddenError(
                "The application metadata server cannot be used as a PostgreSQL target"
            )


def _normalize_host(value: str) -> str:
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host.rstrip(".").casefold()
