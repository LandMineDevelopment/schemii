"""Validated, non-secret administrator policy loaded once at process startup."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping


def _integer(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _seconds(name: str, value: float, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


@dataclass(frozen=True, slots=True)
class PostgresConnectionPolicy:
    """Bound PostgreSQL sessions opened by this Schemii process."""

    maximum_total: int = 20
    maximum_per_identity: int = 4
    acquire_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        _integer("postgres.connections.maximum_total", self.maximum_total, 2, 1_000)
        _integer(
            "postgres.connections.maximum_per_identity",
            self.maximum_per_identity,
            2,
            self.maximum_total,
        )
        _seconds(
            "postgres.connections.acquire_timeout_seconds",
            self.acquire_timeout_seconds,
            0.1,
            300,
        )


@dataclass(frozen=True, slots=True)
class PostgresCatalogPolicy:
    """Bound catalog materialization; these limits never scan application rows."""

    maximum_namespaces: int = 10_000
    maximum_tables: int = 2_000
    maximum_columns: int = 30_000
    maximum_constraints: int = 20_000
    maximum_indexes: int = 10_000
    maximum_triggers: int = 10_000
    maximum_functions: int = 5_000
    maximum_views: int = 5_000
    maximum_types: int = 5_000
    maximum_total_objects: int = 60_000
    maximum_definition_bytes: int = 256 * 1024
    maximum_total_text_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        ceilings = {
            "maximum_namespaces": 50_000,
            "maximum_tables": 10_000,
            "maximum_columns": 100_000,
            "maximum_constraints": 100_000,
            "maximum_indexes": 100_000,
            "maximum_triggers": 100_000,
            "maximum_functions": 50_000,
            "maximum_views": 50_000,
            "maximum_types": 50_000,
            "maximum_total_objects": 250_000,
            "maximum_definition_bytes": 1024 * 1024,
            "maximum_total_text_bytes": 128 * 1024 * 1024,
        }
        for name, ceiling in ceilings.items():
            _integer(f"postgres.catalog.{name}", getattr(self, name), 1, ceiling)


@dataclass(frozen=True, slots=True)
class PostgresTimeoutPolicy:
    """Application ceilings for bounded target operations."""

    catalog_statement_seconds: int = 15
    migration_statement_seconds: int = 120
    lock_wait_seconds: int = 5

    def __post_init__(self) -> None:
        _integer("postgres.timeouts.catalog_statement_seconds", self.catalog_statement_seconds, 1, 3_600)
        _integer("postgres.timeouts.migration_statement_seconds", self.migration_statement_seconds, 1, 86_400)
        _integer("postgres.timeouts.lock_wait_seconds", self.lock_wait_seconds, 1, 3_600)


@dataclass(frozen=True, slots=True)
class ConsolePolicy:
    maximum_statements_per_run: int = 20
    transaction_idle_seconds: int = 5 * 60
    transaction_maximum_seconds: int = 30 * 60
    maximum_saved_queries_per_workspace: int = 100

    def __post_init__(self) -> None:
        _integer("console.maximum_statements_per_run", self.maximum_statements_per_run, 1, 500)
        _integer("console.transaction_idle_seconds", self.transaction_idle_seconds, 5, 86_400)
        _integer("console.transaction_maximum_seconds", self.transaction_maximum_seconds, 5, 604_800)
        _integer(
            "console.maximum_saved_queries_per_workspace",
            self.maximum_saved_queries_per_workspace,
            1,
            10_000,
        )
        if self.transaction_idle_seconds > self.transaction_maximum_seconds:
            raise ValueError("console.transaction_idle_seconds cannot exceed transaction_maximum_seconds")


@dataclass(frozen=True, slots=True)
class ConsoleResultPolicy:
    """Bound transient application memory without imposing a result row limit."""

    page_rows: int = 100
    page_memory_bytes: int = 4 * 1024 * 1024
    maximum_cell_bytes: int = 256 * 1024
    maximum_live_read_sessions: int = 12
    result_ttl_seconds: int = 15 * 60

    def __post_init__(self) -> None:
        _integer("console.results.page_rows", self.page_rows, 1, 1_000)
        _integer("console.results.page_memory_bytes", self.page_memory_bytes, 64 * 1024, 64 * 1024 * 1024)
        _integer("console.results.maximum_cell_bytes", self.maximum_cell_bytes, 1024, 16 * 1024 * 1024)
        _integer("console.results.maximum_live_read_sessions", self.maximum_live_read_sessions, 1, 1_000)
        _integer("console.results.result_ttl_seconds", self.result_ttl_seconds, 30, 24 * 60 * 60)
        if self.maximum_cell_bytes > self.page_memory_bytes:
            raise ValueError("console.results.maximum_cell_bytes cannot exceed page_memory_bytes")


@dataclass(frozen=True, slots=True)
class ConsoleHistoryPolicy:
    """Bound replay convenience data independently from execution state."""

    limit: int = 100

    def __post_init__(self) -> None:
        _integer("console.history.limit", self.limit, 0, 10_000)


@dataclass(frozen=True, slots=True)
class MigrationPolicy:
    review_ttl_seconds: int = 15 * 60
    execution_lease_seconds: int = 2 * 60
    worker_poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        _integer("migrations.review_ttl_seconds", self.review_ttl_seconds, 30, 86_400)
        _integer("migrations.execution_lease_seconds", self.execution_lease_seconds, 10, 3_600)
        _seconds("migrations.worker_poll_seconds", self.worker_poll_seconds, 0.1, 60)
        if self.worker_poll_seconds >= self.execution_lease_seconds:
            raise ValueError("migrations.worker_poll_seconds must be shorter than execution_lease_seconds")


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    maximum_connections_per_user: int = 100
    maximum_workspaces_per_user: int = 1_000
    design_history_actions_per_workspace: int = 100

    def __post_init__(self) -> None:
        _integer("resources.maximum_connections_per_user", self.maximum_connections_per_user, 1, 10_000)
        _integer("resources.maximum_workspaces_per_user", self.maximum_workspaces_per_user, 1, 100_000)
        _integer(
            "resources.design_history_actions_per_workspace",
            self.design_history_actions_per_workspace,
            1,
            10_000,
        )


@dataclass(frozen=True, slots=True)
class LimitEventPolicy:
    """Retention for safe operational limit events (never SQL or row data)."""

    retention_days: int = 30
    maximum_events: int = 100_000

    def __post_init__(self) -> None:
        _integer("metadata.limit_events.retention_days", self.retention_days, 1, 365)
        _integer("metadata.limit_events.maximum_events", self.maximum_events, 100, 10_000_000)


@dataclass(frozen=True, slots=True)
class AiPolicy:
    """Bound assistant work and transient data disclosure."""

    enabled: bool = True
    maximum_concurrent_turns: int = 4
    maximum_concurrent_turns_per_user: int = 2
    maximum_chats_per_workspace: int = 50
    chat_retention_days: int = 90
    message_history_limit: int = 200
    activity_history_limit: int = 1_000
    maximum_proposals_per_turn: int = 10
    prompt_bytes: int = 64 * 1024
    response_bytes: int = 256 * 1024
    proposal_bytes: int = 512 * 1024
    proposal_bytes_per_turn: int = 1024 * 1024
    context_bytes: int = 512 * 1024
    result_context_rows: int = 100
    result_context_bytes: int = 256 * 1024
    transient_response_memory_bytes: int = 16 * 1024 * 1024
    transient_response_ttl_seconds: int = 15 * 60
    proposal_ttl_seconds: int = 15 * 60
    provider_timeout_seconds: int = 5 * 60
    runtime_status_cache_seconds: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("ai.enabled must be a boolean")
        _integer("ai.maximum_concurrent_turns", self.maximum_concurrent_turns, 1, 1_000)
        _integer(
            "ai.maximum_concurrent_turns_per_user",
            self.maximum_concurrent_turns_per_user,
            1,
            self.maximum_concurrent_turns,
        )
        _integer("ai.maximum_chats_per_workspace", self.maximum_chats_per_workspace, 1, 10_000)
        _integer("ai.chat_retention_days", self.chat_retention_days, 1, 3_650)
        _integer("ai.message_history_limit", self.message_history_limit, 1, 10_000)
        _integer("ai.activity_history_limit", self.activity_history_limit, 10, 100_000)
        _integer("ai.maximum_proposals_per_turn", self.maximum_proposals_per_turn, 1, 100)
        _integer("ai.prompt_bytes", self.prompt_bytes, 1_024, 1024 * 1024)
        _integer("ai.response_bytes", self.response_bytes, 1_024, 8 * 1024 * 1024)
        _integer("ai.proposal_bytes", self.proposal_bytes, 1_024, 4 * 1024 * 1024)
        _integer("ai.proposal_bytes_per_turn", self.proposal_bytes_per_turn, self.proposal_bytes, 16 * 1024 * 1024)
        _integer("ai.context_bytes", self.context_bytes, 16 * 1024, 8 * 1024 * 1024)
        _integer("ai.result_context_rows", self.result_context_rows, 1, 1_000)
        _integer("ai.result_context_bytes", self.result_context_bytes, 16 * 1024, 8 * 1024 * 1024)
        if self.result_context_bytes > self.context_bytes:
            raise ValueError("ai.result_context_bytes cannot exceed ai.context_bytes")
        _integer(
            "ai.transient_response_memory_bytes",
            self.transient_response_memory_bytes,
            1024 * 1024,
            512 * 1024 * 1024,
        )
        _integer(
            "ai.transient_response_ttl_seconds",
            self.transient_response_ttl_seconds,
            30,
            86_400,
        )
        _integer("ai.proposal_ttl_seconds", self.proposal_ttl_seconds, 30, 86_400)
        _integer("ai.provider_timeout_seconds", self.provider_timeout_seconds, 10, 3_600)
        _integer("ai.runtime_status_cache_seconds", self.runtime_status_cache_seconds, 1, 300)


@dataclass(frozen=True, slots=True)
class AdminConfig:
    """Complete administrator policy; credentials deliberately live elsewhere."""

    postgres_connections: PostgresConnectionPolicy = PostgresConnectionPolicy()
    postgres_catalog: PostgresCatalogPolicy = PostgresCatalogPolicy()
    postgres_timeouts: PostgresTimeoutPolicy = PostgresTimeoutPolicy()
    console: ConsolePolicy = ConsolePolicy()
    console_results: ConsoleResultPolicy = ConsoleResultPolicy()
    console_history: ConsoleHistoryPolicy = ConsoleHistoryPolicy()
    migrations: MigrationPolicy = MigrationPolicy()
    resources: ResourcePolicy = ResourcePolicy()
    limit_events: LimitEventPolicy = LimitEventPolicy()
    ai: AiPolicy = AiPolicy()

    def __post_init__(self) -> None:
        if self.console_results.maximum_live_read_sessions >= self.postgres_connections.maximum_total:
            raise ValueError(
                "console.results.maximum_live_read_sessions must leave at least one PostgreSQL connection available"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AdminConfig":
        values = os.environ if env is None else env
        raw_path = values.get("SCHEMII_CONFIG_FILE", "").strip()
        if not raw_path:
            return cls()
        path = Path(raw_path)
        if not path.is_absolute():
            raise ValueError("SCHEMII_CONFIG_FILE must be an absolute path")
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"Schemii config file does not exist: {path}") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"Schemii config file is not readable TOML: {path}") from error
        return cls.from_document(document)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "AdminConfig":
        _reject_unknown(document, {"postgres", "console", "migrations", "resources", "metadata", "ai"}, "root")
        postgres = _mapping(document.get("postgres"), "postgres")
        console = _mapping(document.get("console"), "console")
        metadata = _mapping(document.get("metadata"), "metadata")
        _reject_unknown(postgres, {"connections", "catalog", "timeouts"}, "postgres")
        _reject_unknown(console, {"results", "history", *ConsolePolicy.__dataclass_fields__}, "console")
        _reject_unknown(metadata, {"limit_events"}, "metadata")

        return cls(
            postgres_connections=PostgresConnectionPolicy(**_table(postgres, "connections", PostgresConnectionPolicy, "postgres.connections")),
            postgres_catalog=PostgresCatalogPolicy(**_table(postgres, "catalog", PostgresCatalogPolicy, "postgres.catalog")),
            postgres_timeouts=PostgresTimeoutPolicy(**_table(postgres, "timeouts", PostgresTimeoutPolicy, "postgres.timeouts")),
            console=ConsolePolicy(**{key: console[key] for key in ConsolePolicy.__dataclass_fields__ if key in console}),
            console_results=ConsoleResultPolicy(**_table(console, "results", ConsoleResultPolicy, "console.results")),
            console_history=ConsoleHistoryPolicy(**_table(console, "history", ConsoleHistoryPolicy, "console.history")),
            migrations=MigrationPolicy(**_root_table(document, "migrations", MigrationPolicy)),
            resources=ResourcePolicy(**_root_table(document, "resources", ResourcePolicy)),
            limit_events=LimitEventPolicy(**_table(metadata, "limit_events", LimitEventPolicy, "metadata.limit_events")),
            ai=AiPolicy(**_root_table(document, "ai", AiPolicy)),
        )


def _root_table(document: Mapping[str, Any], name: str, model: type[Any]) -> dict[str, Any]:
    values = _mapping(document.get(name), name)
    _reject_unknown(values, set(model.__dataclass_fields__), name)
    return values


def _table(parent: Mapping[str, Any], key: str, model: type[Any], name: str) -> dict[str, Any]:
    values = _mapping(parent.get(key), name)
    _reject_unknown(values, set(model.__dataclass_fields__), name)
    return values


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a TOML table")
    return dict(value)


def _reject_unknown(values: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unknown Schemii config key at {location}: {unknown[0]}")
