"""Deployment-owned admission policy for PostgreSQL targets."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Protocol

from psycopg.conninfo import conninfo_to_dict

from .models import PostgresConnectionMetadata, ResolvedPostgresConnection


class ConnectionTargetForbiddenError(RuntimeError):
    """A requested target crosses a deployment control-plane boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "connection_target_forbidden",
    ) -> None:
        super().__init__(message)
        self.code = code


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
class CompositeConnectionTargetPolicy:
    """Apply each deployment policy to the same connection target."""

    policies: tuple[ConnectionTargetPolicy, ...]

    def validate(
        self,
        target: PostgresConnectionMetadata | ResolvedPostgresConnection,
    ) -> None:
        for policy in self.policies:
            policy.validate(target)


@dataclass(frozen=True)
class InternalOnlyConnectionTargetPolicy:
    """Admit only PostgreSQL host identities selected by the operator in either egress mode.

    Internal network ranges are not an authorization boundary: their meaning
    differs by deployment, and resolving arbitrary user-supplied names before
    admission would still permit DNS rebinding. Instead, an internal-only
    deployment names the exact host aliases that its private network exposes.
    The deployment remains responsible for controlling DNS for those aliases.
    """

    allowed_hosts: frozenset[str]

    @classmethod
    def from_hosts(
        cls,
        hosts: tuple[str, ...],
    ) -> "InternalOnlyConnectionTargetPolicy":
        allowed_hosts = frozenset(
            _normalize_configured_host(host) for host in hosts
        )
        if not allowed_hosts:
            raise ValueError(
                "internal-only target egress requires at least one allowed host"
            )
        return cls(allowed_hosts=allowed_hosts)

    def validate(
        self,
        target: PostgresConnectionMetadata | ResolvedPostgresConnection,
    ) -> None:
        if _normalize_host(target.host) not in self.allowed_hosts:
            raise ConnectionTargetForbiddenError(
                "This PostgreSQL host is not allowed by the deployment's approved target policy",
                code="connection_target_not_allowed",
            )


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
                "The application metadata server cannot be used as a PostgreSQL target",
                code="metadata_control_plane_target_forbidden",
            )


def _normalize_host(value: str) -> str:
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host.rstrip(".").casefold()


def _normalize_configured_host(value: str) -> str:
    host = value.strip()
    if not host or "," in host or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in host
    ):
        raise ValueError(
            "allowed target hosts must identify one server and contain no whitespace or control characters"
        )
    return _normalize_host(host)
