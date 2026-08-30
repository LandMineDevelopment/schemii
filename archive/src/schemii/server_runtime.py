from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .postgres_concurrency import EXECUTION_CAPACITIES


@dataclass(frozen=True)
class PostgresRuntimeConfig:
    global_capacity: int
    class_capacities: dict[str, int]
    target_capacity: int
    migration_plan_ttl_seconds: int
    temporal_manifest_ttl_seconds: int
    console_transaction_maximum: int
    console_transaction_idle_seconds: int
    console_transaction_lifetime_seconds: int


def _positive_integer(value: str, variable: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{variable} must be a positive integer") from exc
    if not isinstance(value, str) or str(parsed) != value or parsed < 1:
        raise SystemExit(f"{variable} must be a positive integer")
    return parsed


def postgres_runtime_config(env: Any) -> PostgresRuntimeConfig:
    global_capacity = _positive_integer(env.get("SCHEMII_POSTGRES_GLOBAL_CAPACITY", "12"), "SCHEMII_POSTGRES_GLOBAL_CAPACITY")
    target_capacity = _positive_integer(env.get("SCHEMII_POSTGRES_TARGET_CAPACITY", "4"), "SCHEMII_POSTGRES_TARGET_CAPACITY")
    if target_capacity >= global_capacity:
        raise SystemExit("SCHEMII_POSTGRES_TARGET_CAPACITY must be below SCHEMII_POSTGRES_GLOBAL_CAPACITY")
    capacities = {
        name: _positive_integer(
            env.get(f"SCHEMII_POSTGRES_{name.upper()}_CAPACITY", str(default)),
            f"SCHEMII_POSTGRES_{name.upper()}_CAPACITY",
        )
        for name, default in EXECUTION_CAPACITIES.items()
    }
    transaction_maximum = _positive_integer(
        env.get("SCHEMII_CONSOLE_TRANSACTION_MAXIMUM", "4"), "SCHEMII_CONSOLE_TRANSACTION_MAXIMUM",
    )
    transaction_idle = _positive_integer(
        env.get("SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS", "300"), "SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS",
    )
    transaction_lifetime = _positive_integer(
        env.get("SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS", "1800"), "SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS",
    )
    if transaction_maximum > 64:
        raise SystemExit("SCHEMII_CONSOLE_TRANSACTION_MAXIMUM must not exceed 64")
    if transaction_idle > 86400:
        raise SystemExit("SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS must not exceed 86400")
    if transaction_lifetime > 604800:
        raise SystemExit("SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS must not exceed 604800")
    if transaction_idle > transaction_lifetime:
        raise SystemExit("SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS must not exceed SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS")
    return PostgresRuntimeConfig(
        global_capacity=global_capacity,
        class_capacities=capacities,
        target_capacity=target_capacity,
        migration_plan_ttl_seconds=_positive_integer(
            env.get("SCHEMII_MIGRATION_PLAN_TTL_SECONDS", "900"), "SCHEMII_MIGRATION_PLAN_TTL_SECONDS",
        ),
        temporal_manifest_ttl_seconds=_positive_integer(
            env.get("SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS", "300"), "SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS",
        ),
        console_transaction_maximum=transaction_maximum,
        console_transaction_idle_seconds=transaction_idle,
        console_transaction_lifetime_seconds=transaction_lifetime,
    )


def parse_proxy_setting(value: str, variable: str) -> bool:
    if value not in {"0", "1"}:
        raise SystemExit(f"{variable} must be 0 or 1")
    return value == "1"


def parse_port(value: str, variable: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise SystemExit(f"{variable} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"{variable} must be from 1 to 65535")
    return port


def validate_static_directory(path: Path) -> None:
    if not path.is_dir():
        raise SystemExit(f"Static web directory does not exist: {path}")


def run_server(
    host: str,
    port: int,
    handler: type,
    application_name: str,
    *,
    server_factory: Callable[..., Any] = ThreadingHTTPServer,
    shutdown_callback: Callable[[], Any] | None = None,
    lifecycle_services: tuple[Any, ...] = (),
) -> None:
    server = server_factory((host, port), handler)
    try:
        for service in lifecycle_services:
            service.start()
        print(f"{application_name} running at http://{host}:{port}/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for service in reversed(lifecycle_services):
            service.close()
        if shutdown_callback is not None:
            shutdown_callback()
        server.server_close()


def begin_http_shutdown(handler: Any, thread_name: str) -> None:
    shutdown_thread = threading.Thread(target=handler.server.shutdown, name=thread_name, daemon=True)
    handler.send_json(202, {"shuttingDown": True})
    handler.wfile.flush()
    shutdown_thread.start()
