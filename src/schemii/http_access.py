from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from email.message import Message
from ipaddress import ip_address
from typing import Any
from urllib.parse import SplitResult, urlsplit


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PUBLIC_FORWARDED_HEADERS = {"x-forwarded-host", "x-forwarded-proto"}


@dataclass(frozen=True)
class PublicOrigin:
    hostname: str
    port: int


@dataclass(frozen=True)
class HttpAccessPolicy:
    behind_loopback_proxy: bool = False
    trusted_local_proxy: str | None = None
    public_origins: tuple[PublicOrigin, ...] = ()


def _parse_flag(value: str, variable: str) -> bool:
    if value not in {"0", "1"}:
        raise SystemExit(f"{variable} must be 0 or 1")
    return value == "1"


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_dns_hostname(hostname: str) -> bool:
    return (
        len(hostname) <= 253
        and hostname == hostname.lower()
        and hostname.isascii()
        and all(_DNS_LABEL.fullmatch(label) for label in hostname.split("."))
    )


def _split_https_origin(value: str, variable: str) -> SplitResult:
    if not value or value != value.strip() or _has_control(value):
        raise SystemExit(f"{variable} contains an invalid origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"{variable} contains an invalid origin") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "*" in value
        or not _valid_dns_hostname(hostname)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SystemExit(f"{variable} must contain only absolute HTTPS origins without paths")
    return parsed


def parse_public_origins(value: str, variable: str) -> tuple[PublicOrigin, ...]:
    if value == "":
        return ()
    entries = value.split(",")
    if any(not entry for entry in entries):
        raise SystemExit(f"{variable} must not contain empty origins")
    origins: list[PublicOrigin] = []
    for entry in entries:
        parsed = _split_https_origin(entry, variable)
        origin = PublicOrigin(parsed.hostname or "", parsed.port or 443)
        if origin in origins:
            raise SystemExit(f"{variable} must not contain duplicate origins")
        origins.append(origin)
    return tuple(origins)


def _parse_proxy_peer(value: str, variable: str) -> str | None:
    if value == "":
        return None
    if value != value.strip() or not value.isascii() or not _DNS_LABEL.fullmatch(value):
        raise SystemExit(f"{variable} must be one exact lowercase Docker DNS label")
    return value


def http_access_policy(env: Any, prefix: str) -> HttpAccessPolicy:
    proxy_variable = f"{prefix}_BEHIND_LOOPBACK_PROXY"
    local_peer_variable = f"{prefix}_TRUSTED_LOCAL_PROXY"
    origins_variable = f"{prefix}_PUBLIC_ORIGINS"
    behind_loopback_proxy = _parse_flag(env.get(proxy_variable, "0"), proxy_variable)
    trusted_local_proxy = _parse_proxy_peer(env.get(local_peer_variable, ""), local_peer_variable)
    public_origins = parse_public_origins(env.get(origins_variable, ""), origins_variable)
    if behind_loopback_proxy != bool(trusted_local_proxy):
        raise SystemExit(f"{proxy_variable} and {local_peer_variable} must be configured together")
    if public_origins and not behind_loopback_proxy:
        raise SystemExit(f"{origins_variable} requires {proxy_variable}=1")
    return HttpAccessPolicy(
        behind_loopback_proxy=behind_loopback_proxy,
        trusted_local_proxy=trusted_local_proxy,
        public_origins=public_origins,
    )


def _parse_authority(value: str) -> tuple[str, int | None] | None:
    if not value or value != value.strip() or _has_control(value) or any(character in value for character in "/?#@,\\"):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    if hostname not in {"localhost", "127.0.0.1", "::1"} and not _valid_dns_hostname(hostname):
        return None
    return hostname.lower(), port


def _proxy_peer_matches(client_host: str, peer_name: str | None) -> bool:
    if peer_name is None:
        return False
    try:
        client_address = ip_address(client_host)
        records = socket.getaddrinfo(peer_name, None, type=socket.SOCK_STREAM)
        addresses = {ip_address(record[4][0]) for record in records}
    except (OSError, ValueError):
        return False
    return len(addresses) == 1 and client_address in addresses


def is_local_request(
    client_host: str,
    host_header: str,
    origin: str | None,
    behind_loopback_proxy: bool = False,
    trusted_local_proxy: str | None = None,
) -> bool:
    authority = _parse_authority(host_header)
    if authority is None or authority[0] not in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        source_is_loopback = ip_address(client_host).is_loopback
    except ValueError:
        return False
    if not source_is_loopback and not (
        behind_loopback_proxy and _proxy_peer_matches(client_host, trusted_local_proxy)
    ):
        return False
    if origin is None:
        return True
    if not origin or origin != origin.strip() or _has_control(origin):
        return False
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (origin_port is not None and not 1 <= origin_port <= 65535)
    ):
        return False
    host_name, host_port = authority
    return host_name == parsed.hostname and (host_port or 80) == (origin_port or 80)


def _single_header(headers: Message, name: str) -> str | None:
    values = headers.get_all(name, [])
    if len(values) != 1:
        return None
    value = values[0]
    if not value or value != value.strip() or _has_control(value):
        return None
    return value


def request_is_allowed(client_host: str, headers: Message, policy: HttpAccessPolicy) -> bool:
    host_values = headers.get_all("Host", [])
    origin_values = headers.get_all("Origin", [])
    if len(host_values) != 1 or len(origin_values) > 1:
        return False
    host = _single_header(headers, "Host")
    origin = _single_header(headers, "Origin") if origin_values else None
    if host is None or (origin_values and origin is None):
        return False

    forwarded_names = {
        name.lower()
        for name in headers.keys()
        if name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
    }
    if not forwarded_names:
        return is_local_request(
            client_host,
            host,
            origin,
            policy.behind_loopback_proxy,
            policy.trusted_local_proxy,
        )
    if any(len(headers.get_all(name, [])) != 1 for name in forwarded_names):
        return False
    if not policy.public_origins or not policy.behind_loopback_proxy:
        return False
    if not _proxy_peer_matches(client_host, policy.trusted_local_proxy):
        return False
    if forwarded_names != _PUBLIC_FORWARDED_HEADERS:
        return False

    forwarded_host = _single_header(headers, "X-Forwarded-Host")
    forwarded_proto = _single_header(headers, "X-Forwarded-Proto")
    authority = _parse_authority(host)
    if (
        forwarded_host != host
        or forwarded_proto != "https"
        or authority is None
    ):
        return False
    host_name, host_port = authority
    effective_host_port = host_port or 443
    matching_origin = next(
        (candidate for candidate in policy.public_origins if candidate.hostname == host_name and candidate.port == effective_host_port),
        None,
    )
    if matching_origin is None or origin is None:
        return matching_origin is not None
    try:
        parsed_origin = _split_https_origin(origin, "Origin")
    except SystemExit:
        return False
    return parsed_origin.hostname == matching_origin.hostname and (parsed_origin.port or 443) == matching_origin.port
