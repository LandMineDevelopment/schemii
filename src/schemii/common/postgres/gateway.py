"""Shared, read-only PostgreSQL catalog gateway."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import secrets
import threading
import time
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import ValidationError

from schemii.common.connections.models import (
    MAX_CONNECT_TIMEOUT_SECONDS,
    ResolvedPostgresConnection,
)

from .errors import (
    conversion_validation_message,
    PostgresCatalogLimitError,
    PostgresCatalogValidationError,
    PostgresCommitUncertainError,
    PostgresConnectionError,
    PostgresConnectionCapacityError,
    PostgresConsoleCancelledError,
    PostgresDatabaseMismatchError,
    PostgresDriverUnavailableError,
    PostgresGatewayError,
    PostgresInvalidNamespaceError,
    PostgresMigrationExecutionError,
    PostgresMigrationPreconditionError,
    PostgresMigrationStaleError,
    PostgresNamespaceNotFoundError,
    PostgresQueryError,
    PostgresTransactionStatusError,
)
from .console.execution import ConsoleQueryResult, validate_read_only_statements
from .console.gateway import (
    PostgresConsoleReadSession,
    PostgresConsoleTransaction,
    PsycopgConsoleReadSession,
    PsycopgConsoleTransaction,
    execute_console_statements,
)
from .models import (
    PostgresCatalog,
    PostgresCheckConstraint,
    PostgresColumn,
    PostgresConnectionTestResult,
    PostgresExclusionConstraint,
    PostgresForeignKeyRelationship,
    PostgresFunction,
    PostgresIndex,
    PostgresMaterializedView,
    PostgresNamespace,
    PostgresNotNullConstraint,
    PostgresPrimaryKey,
    PostgresTable,
    PostgresTrigger,
    PostgresType,
    PostgresUniqueConstraint,
    PostgresView,
    build_postgres_catalog,
)
from .queries import (
    COLUMNS_QUERY,
    CONNECTION_TEST_QUERY,
    CONSTRAINTS_QUERY,
    FUNCTIONS_QUERY,
    INDEXES_QUERY,
    METADATA_QUERY,
    NAMESPACES_QUERY,
    NAMESPACE_EXISTS_QUERY,
    TABLES_QUERY,
    TRIGGERS_QUERY,
    TYPES_QUERY,
    VIEWS_QUERY,
)


IDLE_TRANSACTION_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class PostgresCatalogLimits:
    """Application-side limits that prevent unbounded catalog materialization."""

    max_namespaces: int = 10_000
    max_tables: int = 2_000
    max_columns: int = 30_000
    max_constraints: int = 20_000
    max_indexes: int = 10_000
    max_triggers: int = 10_000
    max_functions: int = 5_000
    max_views: int = 5_000
    max_types: int = 5_000
    max_total_objects: int = 60_000
    max_definition_bytes: int = 256 * 1024
    max_total_text_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        hard_limits = {
            "max_namespaces": 50_000,
            "max_tables": 10_000,
            "max_columns": 100_000,
            "max_constraints": 100_000,
            "max_indexes": 100_000,
            "max_triggers": 100_000,
            "max_functions": 50_000,
            "max_views": 50_000,
            "max_types": 50_000,
            "max_total_objects": 250_000,
            "max_definition_bytes": 1024 * 1024,
            "max_total_text_bytes": 128 * 1024 * 1024,
        }
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{item.name} must be an integer")
            if value < 1 or value > hard_limits[item.name]:
                raise ValueError(f"{item.name} is outside its supported range")


@dataclass(frozen=True, slots=True)
class PostgresMigrationResult:
    """Commit evidence returned only after PostgreSQL confirms commit."""

    catalog: PostgresCatalog
    transaction_id: str
    target_identity: dict[str, Any]
    completed_step_count: int


@dataclass(frozen=True, slots=True)
class PostgresTransactionRecovery:
    """Transaction outcome bound to the PostgreSQL target that reported it."""

    status: Literal["committed", "aborted", "in progress"]
    target_identity: dict[str, Any]


@runtime_checkable
class PostgresGateway(Protocol):
    def test_connection(
        self,
        connection: ResolvedPostgresConnection,
    ) -> PostgresConnectionTestResult: ...

    def namespace_exists(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> bool: ...

    def list_namespaces(
        self,
        connection: ResolvedPostgresConnection,
    ) -> tuple[PostgresNamespace, ...]: ...

    def introspect(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> PostgresCatalog: ...

    def table_emptiness(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        table_names: Sequence[str],
    ) -> dict[str, bool]: ...

    def table_column_rebuild_blockers(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        table_names: Sequence[str],
    ) -> dict[str, tuple[str, ...]]: ...

    def execute_console(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[int], bool],
    ) -> tuple[ConsoleQueryResult, ...]: ...

    def open_console_read_session(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[int], bool],
        page_memory_bytes: int,
    ) -> PostgresConsoleReadSession: ...

    def open_console_transaction(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> "PostgresConsoleTransaction": ...

    def cancel_console(
        self,
        connection: ResolvedPostgresConnection,
        backend_pid: int,
    ) -> bool: ...

    def validate_column_conversion(
        self, connection: ResolvedPostgresConnection, query: str,
    ) -> str | None: ...

    def execute_migration(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        expected_catalog_fingerprint: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[str, dict[str, Any]], None],
        on_intended: Callable[[PostgresCatalog], None],
        required_empty_tables: Sequence[str] = (),
        conversion_tables: Sequence[str] = (),
    ) -> PostgresMigrationResult: ...

    def transaction_status(
        self,
        connection: ResolvedPostgresConnection,
        transaction_id: str,
    ) -> PostgresTransactionRecovery: ...


def _portable_dict_row(cursor: Any) -> Callable[[Sequence[Any]], dict[str, Any]]:
    """A psycopg-compatible row factory kept local for lazy driver loading."""

    names = [column.name for column in cursor.description]
    return lambda values: dict(zip(names, values))


class _ConnectionCapacity:
    """Process-local admission control; PostgreSQL remains the pool authority."""

    def __init__(self, maximum_total: int, maximum_per_identity: int) -> None:
        self._maximum_total = maximum_total
        self._maximum_per_identity = maximum_per_identity
        self._total = 0
        self._by_identity: dict[str, int] = {}
        self._retained_total = 0
        self._retained_by_identity: dict[str, int] = {}
        self._condition = threading.Condition()

    def acquire(self, identity: str, timeout: float, *, retained: bool = False) -> tuple[str, int, int] | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                identity_count = self._by_identity.get(identity, 0)
                retained_identity = self._retained_by_identity.get(identity, 0)
                # All long-lived session types share this quota. Keep one of
                # the existing slots for catalog, validation and ordinary work.
                retained_per_identity = max(1, self._maximum_per_identity - 1)
                retained_total = max(1, self._maximum_total - 1)
                reached = None
                if identity_count >= self._maximum_per_identity:
                    reached = ("postgres.connections.maximum_per_identity", self._maximum_per_identity, identity_count)
                elif self._total >= self._maximum_total:
                    reached = ("postgres.connections.maximum_total", self._maximum_total, self._total)
                elif retained and retained_identity >= retained_per_identity:
                    reached = ("postgres.connections.maximum_per_identity", retained_per_identity, retained_identity)
                elif retained and self._retained_total >= retained_total:
                    reached = ("postgres.connections.maximum_total", retained_total, self._retained_total)
                if reached is None:
                    self._total += 1
                    self._by_identity[identity] = identity_count + 1
                    if retained:
                        self._retained_total += 1
                        self._retained_by_identity[identity] = retained_identity + 1
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(remaining):
                    return reached

    def release(self, identity: str, *, retained: bool = False) -> None:
        with self._condition:
            count = self._by_identity.get(identity, 0)
            if count <= 0:
                return
            self._total -= 1
            if count == 1:
                self._by_identity.pop(identity, None)
            else:
                self._by_identity[identity] = count - 1
            if retained:
                self._retained_total -= 1
                retained_count = self._retained_by_identity.get(identity, 0)
                if retained_count <= 1:
                    self._retained_by_identity.pop(identity, None)
                else:
                    self._retained_by_identity[identity] = retained_count - 1
            self._condition.notify_all()


class _LeasedConnection:
    def __init__(self, connection: Any, capacity: _ConnectionCapacity, identity: str, *, retained: bool = False) -> None:
        self._connection = connection
        self._capacity = capacity
        self._identity = identity
        self._retained = retained
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._capacity.release(self._identity, retained=self._retained)


class PsycopgPostgresGateway:
    """Read PostgreSQL authority through psycopg without retaining snapshots."""

    def __init__(
        self,
        *,
        connect_factory: Callable[..., Any] | None = None,
        limits: PostgresCatalogLimits | None = None,
        clock: Callable[[], datetime] | None = None,
        maximum_connections: int = 20,
        maximum_connections_per_identity: int = 4,
        connection_acquire_timeout: float = 5.0,
        maximum_console_statements: int = 20,
        console_page_memory_bytes: int = 4 * 1024 * 1024,
        console_maximum_cell_bytes: int = 256 * 1024,
        catalog_statement_timeout_seconds: int = 15,
        migration_statement_timeout_seconds: int = 120,
        lock_timeout_seconds: int = 5,
        console_idle_transaction_seconds: int = 300,
    ) -> None:
        if maximum_connections < 1:
            raise ValueError("Maximum PostgreSQL connections must be positive")
        if not 1 <= maximum_connections_per_identity <= maximum_connections:
            raise ValueError("Per-identity PostgreSQL capacity is invalid")
        if connection_acquire_timeout <= 0:
            raise ValueError("PostgreSQL connection acquire timeout must be positive")
        if maximum_console_statements < 1:
            raise ValueError("Maximum Console statements must be positive")
        self._connect_factory = connect_factory
        self._limits = limits or PostgresCatalogLimits()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connection_capacity = _ConnectionCapacity(
            maximum_connections, maximum_connections_per_identity
        )
        self._retained_connection_reclaimers = []
        self._monitor_capacity = _ConnectionCapacity(2, 1)
        self._control_capacity = _ConnectionCapacity(2, 1)
        self._connection_acquire_timeout = connection_acquire_timeout
        self._maximum_console_statements = maximum_console_statements
        self._console_page_memory_bytes = console_page_memory_bytes
        self._console_maximum_cell_bytes = console_maximum_cell_bytes
        self._catalog_statement_timeout_ms = catalog_statement_timeout_seconds * 1000
        self._migration_statement_timeout_ms = migration_statement_timeout_seconds * 1000
        self._lock_timeout_ms = lock_timeout_seconds * 1000
        self._console_idle_transaction_timeout_ms = console_idle_transaction_seconds * 1000

    def test_connection(
        self,
        connection: ResolvedPostgresConnection,
    ) -> PostgresConnectionTestResult:
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            row = self._one(self._execute_rows(database_connection, CONNECTION_TEST_QUERY))
            self._require_database(row, connection.database)
            return PostgresConnectionTestResult(
                database=row["database"],
                server_version=row["server_version"],
            )
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValidationError):
            raise PostgresQueryError() from None
        finally:
            self._cleanup(database_connection)

    def namespace_exists(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> bool:
        namespace = self._validated_namespace(namespace)
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            metadata = self._one(self._execute_rows(database_connection, METADATA_QUERY))
            self._require_database(metadata, connection.database)
            row = self._one(
                self._execute_rows(
                    database_connection,
                    NAMESPACE_EXISTS_QUERY,
                    (namespace,),
                )
            )
            if type(row.get("namespace_exists")) is not bool:
                raise PostgresQueryError()
            return row["namespace_exists"]
        finally:
            self._cleanup(database_connection)

    def list_namespaces(
        self,
        connection: ResolvedPostgresConnection,
    ) -> tuple[PostgresNamespace, ...]:
        """Return visible non-temporary schemas through a bounded read-only query."""

        database_connection: Any | None = None
        limit = self._limits.max_namespaces
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            metadata = self._one(self._execute_rows(database_connection, METADATA_QUERY))
            self._require_database(metadata, connection.database)
            rows = self._execute_rows(
                database_connection,
                NAMESPACES_QUERY,
                (limit + 1,),
            )
            if len(rows) > limit:
                raise PostgresCatalogLimitError("namespaces", limit, len(rows))
            return tuple(
                PostgresNamespace(
                    name=row["namespace_name"],
                    system=row["is_system"],
                )
                for row in rows
            )
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValidationError):
            raise PostgresQueryError() from None
        finally:
            self._cleanup(database_connection)

    def readable_columns(self, connection, namespace):
        """Report-facing visibility under the exact query identity."""
        from .queries import READABLE_COLUMNS_QUERY
        namespace = self._validated_namespace(namespace)
        database_connection = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            metadata = self._one(self._execute_rows(database_connection, METADATA_QUERY))
            self._require_database(metadata, connection.database)
            rows = self._execute_rows(database_connection, READABLE_COLUMNS_QUERY,
                                      (namespace, self._limits.max_columns + 1))
            if len(rows) > self._limits.max_columns:
                raise PostgresCatalogLimitError("columns", self._limits.max_columns, len(rows))
            return {(row["relation_name"], row["column_name"]) for row in rows}
        finally:
            self._cleanup(database_connection)

    def introspect(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> PostgresCatalog:
        namespace = self._validated_namespace(namespace)
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection, repeatable_read=True)
            return self._introspect_connection(database_connection, connection, namespace)
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError):
            raise PostgresCatalogValidationError() from None
        finally:
            self._cleanup(database_connection)

    def table_emptiness(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        table_names: Sequence[str],
    ) -> dict[str, bool]:
        """Read exact table emptiness without copying or counting application rows."""

        namespace = self._validated_namespace(namespace)
        names = self._validated_table_names(table_names)
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection, repeatable_read=True)
            return self._table_emptiness_connection(
                database_connection,
                namespace,
                names,
            )
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValueError):
            raise PostgresCatalogValidationError() from None
        finally:
            self._cleanup(database_connection)

    def table_column_rebuild_blockers(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        table_names: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        """Inventory column metadata that an in-place physical rebuild cannot preserve."""

        namespace = self._validated_namespace(namespace)
        names = self._validated_table_names(table_names)
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection, repeatable_read=True)
            result: dict[str, tuple[str, ...]] = {}
            for table_name in names:
                row = self._one(self._execute_rows(
                    database_connection,
                    """
                    /* schemii_column_rebuild_safety */
                    SELECT
                        EXISTS (
                            SELECT 1 FROM pg_attribute a
                            WHERE a.attrelid = c.oid AND a.attnum > 0
                              AND NOT a.attisdropped AND a.attacl IS NOT NULL
                        ) AS column_privileges,
                        EXISTS (
                            SELECT 1 FROM pg_description d
                            WHERE d.classoid = 'pg_class'::regclass
                              AND d.objoid = c.oid AND d.objsubid > 0
                        ) AS column_comments,
                        EXISTS (
                            SELECT 1 FROM pg_seclabel s
                            WHERE s.classoid = 'pg_class'::regclass
                              AND s.objoid = c.oid AND s.objsubid > 0
                        ) AS column_security_labels,
                        EXISTS (
                            SELECT 1
                            FROM pg_attribute a
                            JOIN pg_type t ON t.oid = a.atttypid
                            WHERE a.attrelid = c.oid AND a.attnum > 0
                              AND NOT a.attisdropped
                              AND (
                                  a.attstattarget <> -1
                                  OR a.attstorage <> t.typstorage
                                  OR a.attcompression::text <> ''
                              )
                        ) AS column_storage_settings,
                        EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,
                        EXISTS (SELECT 1 FROM pg_statistic_ext e WHERE e.stxrelid = c.oid) AS extended_statistics,
                        EXISTS (SELECT 1 FROM pg_publication_rel p WHERE p.prrelid = c.oid) AS publications,
                        c.relreplident <> 'd' AS custom_replica_identity,
                        EXISTS (
                            SELECT 1
                            FROM pg_depend d
                            JOIN pg_rewrite r
                              ON r.oid = d.objid
                             AND d.classid = 'pg_rewrite'::regclass
                            JOIN pg_class dependent ON dependent.oid = r.ev_class
                            WHERE d.refclassid = 'pg_class'::regclass
                              AND d.refobjid = c.oid
                              AND dependent.oid <> c.oid
                        ) AS dependent_views,
                        EXISTS (
                            SELECT 1 FROM pg_rewrite r
                            WHERE r.ev_class = c.oid AND r.rulename <> '_RETURN'
                        ) AS custom_rules,
                        EXISTS (
                            SELECT 1
                            FROM pg_attribute a
                            JOIN pg_sequence identity_sequence
                              ON identity_sequence.seqrelid = pg_get_serial_sequence(
                                  format('%%I.%%I', n.nspname, c.relname), a.attname
                              )::regclass
                            WHERE a.attrelid = c.oid
                              AND a.attnum > 0
                              AND NOT a.attisdropped
                              AND a.attidentity <> ''
                              AND (
                                  identity_sequence.seqstart <> 1
                                  OR identity_sequence.seqincrement <> 1
                                  OR identity_sequence.seqmin <> 1
                                  OR identity_sequence.seqcache <> 1
                                  OR identity_sequence.seqcycle
                                  OR identity_sequence.seqmax <> CASE identity_sequence.seqtypid
                                      WHEN 'int2'::regtype THEN 32767
                                      WHEN 'int4'::regtype THEN 2147483647
                                      ELSE 9223372036854775807
                                  END
                              )
                        ) AS custom_identity_sequence,
                        EXISTS (
                            SELECT 1
                            FROM pg_attribute a
                            JOIN pg_class sequence_class
                              ON sequence_class.oid = pg_get_serial_sequence(
                                  format('%%I.%%I', n.nspname, c.relname), a.attname
                              )::regclass
                            WHERE a.attrelid = c.oid
                              AND a.attnum > 0
                              AND NOT a.attisdropped
                              AND a.attidentity <> ''
                              AND (
                                  sequence_class.relacl IS NOT NULL
                                  OR EXISTS (
                                      SELECT 1 FROM pg_description d
                                      WHERE d.classoid = 'pg_class'::regclass
                                        AND d.objoid = sequence_class.oid
                                  )
                              )
                        ) AS identity_sequence_metadata
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
                    """,
                    (namespace, table_name),
                ))
                fields = {
                    "column_privileges": "Column-specific privileges would be lost.",
                    "column_comments": "Column comments would be lost.",
                    "column_security_labels": "Column security labels would be lost.",
                    "column_storage_settings": "Custom column statistics or storage settings would be lost.",
                    "policies": "Row-security policies may depend on physical columns.",
                    "extended_statistics": "Extended statistics depend on physical columns.",
                    "publications": "Publication column lists or row filters may depend on physical columns.",
                    "custom_replica_identity": "The table uses a custom replica identity.",
                    "dependent_views": "A database view or materialized view depends on the table.",
                    "custom_rules": "A custom PostgreSQL rule depends on the table.",
                    "custom_identity_sequence": "A custom identity sequence configuration would be lost.",
                    "identity_sequence_metadata": "Identity-sequence privileges or comments would be lost.",
                }
                if not row:
                    raise PostgresCatalogValidationError()
                for key in fields:
                    if type(row.get(key)) is not bool:
                        raise PostgresCatalogValidationError()
                result[table_name] = tuple(
                    message for key, message in fields.items() if row[key]
                )
            return result
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValueError):
            raise PostgresCatalogValidationError() from None
        finally:
            self._cleanup(database_connection)

    def execute_console(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[int], bool],
    ) -> tuple[ConsoleQueryResult, ...]:
        """Run bounded SELECT-shaped statements in one read-only transaction."""

        namespace = self._validated_namespace(namespace)
        validated = validate_read_only_statements(
            list(statements), statement_limit=self._maximum_console_statements
        )
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection, repeatable_read=True)
            self._set_console_namespace(database_connection, namespace)
            identity = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT current_database() AS database, pg_backend_pid() AS backend_pid",
                )
            )
            self._require_database(identity, connection.database)
            backend_pid = identity.get("backend_pid")
            if isinstance(backend_pid, bool) or not isinstance(backend_pid, int):
                raise PostgresCatalogValidationError()
            if not on_started(backend_pid):
                raise PostgresConsoleCancelledError()
            results = execute_console_statements(
                database_connection,
                validated,
                maximum_result_bytes=self._console_page_memory_bytes,
                maximum_cell_bytes=self._console_maximum_cell_bytes,
            )
            database_connection.rollback()
            return results
        except PostgresGatewayError:
            raise
        finally:
            self._cleanup(database_connection)

    def open_console_read_session(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[int], bool],
        page_memory_bytes: int,
    ) -> PostgresConsoleReadSession:
        """Open PostgreSQL-owned, scrollable results for incremental paging."""

        namespace = self._validated_namespace(namespace)
        validated = validate_read_only_statements(
            list(statements), statement_limit=self._maximum_console_statements
        )
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection, retained=True)
            self._begin_read_only(
                database_connection,
                repeatable_read=True,
                idle_timeout_ms=self._console_idle_transaction_timeout_ms,
            )
            self._set_console_namespace(database_connection, namespace)
            # Console reads optimize for delivering the next visible page, not
            # for completing an unbounded result before the user sees rows.
            self._execute_statement(
                database_connection, "SET LOCAL cursor_tuple_fraction = 0.0001"
            )
            identity = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT current_database() AS database, pg_backend_pid() AS backend_pid",
                )
            )
            self._require_database(identity, connection.database)
            backend_pid = identity.get("backend_pid")
            if isinstance(backend_pid, bool) or not isinstance(backend_pid, int):
                raise PostgresCatalogValidationError()
            if not on_started(backend_pid):
                raise PostgresConsoleCancelledError()
            session = PsycopgConsoleReadSession(
                database_connection,
                backend_pid,
                validated,
                page_memory_bytes=page_memory_bytes,
                maximum_cell_bytes=self._console_maximum_cell_bytes,
            )
            database_connection = None
            return session
        except PostgresGatewayError:
            raise
        except Exception:
            raise PostgresConnectionError() from None
        finally:
            self._cleanup(database_connection)

    def open_console_transaction(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> PostgresConsoleTransaction:
        """Open one bounded write transaction retained by the application process."""

        namespace = self._validated_namespace(namespace)
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection, retained=True)
            self._begin_console_write(database_connection)
            self._set_console_namespace(database_connection, namespace)
            identity = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT current_database() AS database, pg_backend_pid() AS backend_pid",
                )
            )
            self._require_database(identity, connection.database)
            backend_pid = identity.get("backend_pid")
            if isinstance(backend_pid, bool) or not isinstance(backend_pid, int):
                raise PostgresCatalogValidationError()
            transaction = PsycopgConsoleTransaction(
                database_connection,
                backend_pid,
                maximum_result_bytes=self._console_page_memory_bytes,
                maximum_cell_bytes=self._console_maximum_cell_bytes,
            )
            database_connection = None
            return transaction
        except PostgresGatewayError:
            raise
        except Exception:
            raise PostgresConnectionError() from None
        finally:
            self._cleanup(database_connection)

    def cancel_console(
        self,
        connection: ResolvedPostgresConnection,
        backend_pid: int,
    ) -> bool:
        """Request cancellation of one backend on the already-authorized target."""

        if isinstance(backend_pid, bool) or not isinstance(backend_pid, int) or backend_pid < 1:
            raise PostgresQueryError()
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection, control=True)
            self._begin_read_only(database_connection)
            row = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT pg_cancel_backend(%s) AS cancelled",
                    (backend_pid,),
                )
            )
            if type(row.get("cancelled")) is not bool:
                raise PostgresQueryError()
            return row["cancelled"]
        finally:
            self._cleanup(database_connection)

    def console_activity(self, connection: ResolvedPostgresConnection, backend_pid: int, *, started_before: datetime) -> dict:
        """Use a small independent admission pool so saturated query slots remain observable."""
        database_connection = None
        try:
            database_connection = self._connect(connection, monitoring=True)
            self._begin_read_only(database_connection)
            self._set_timeout_ceiling(database_connection, "statement_timeout", 1000)
            rows = self._execute_rows(database_connection,
                "SELECT state, wait_event_type, wait_event, pg_blocking_pids(pid) AS blockers "
                "FROM pg_stat_activity WHERE pid = %s AND datname = current_database() "
                "AND usename = current_user AND application_name = 'schemii' AND backend_start <= %s",
                (backend_pid, started_before))
            row = rows[0] if rows else {}
            visible = bool(row.get("state"))
            return {
                "databaseState": row.get("state"),
                "waitEventType": row.get("wait_event_type"),
                "waitEvent": row.get("wait_event"),
                "blockerPids": list(row.get("blockers") or [])[:32],
                "monitoringAvailable": visible,
                "monitoringMessage": None if visible else "Database session ended or activity visibility is restricted.",
                "statementTimeoutMs": None,
                "lockTimeoutMs": None,
                "configuredStatementTimeoutMs": self._catalog_statement_timeout_ms,
                "configuredLockTimeoutMs": self._lock_timeout_ms,
            }
        finally:
            self._cleanup(database_connection)

    def validate_column_conversion(
        self, connection: ResolvedPostgresConnection, query: str,
    ) -> str | None:
        """Evaluate a server-compiled scalar check without returning row data."""
        database_connection = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            self._execute_statement(database_connection, "SET LOCAL row_security = off")
            self._execute_statement(database_connection, "SET LOCAL extra_float_digits = 3")
            with database_connection.cursor() as cursor:
                cursor.execute(query)
                row = cursor.fetchone()
            if not isinstance(row, Mapping):
                return conversion_validation_message(None)
            if row.get("invalid") is True:
                return conversion_validation_message("SC001")
            if row.get("invalid") is False or (type(row.get("count")) is int and row["count"] >= 0):
                return None
            return conversion_validation_message(None)
        except PostgresConnectionCapacityError:
            raise
        except Exception as error:
            return conversion_validation_message(getattr(error, "sqlstate", None))
        finally:
            self._cleanup(database_connection)

    def execute_migration(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
        expected_catalog_fingerprint: str,
        statements: Sequence[str],
        *,
        on_started: Callable[[str, dict[str, Any]], None],
        on_intended: Callable[[PostgresCatalog], None],
        required_empty_tables: Sequence[str] = (),
        conversion_tables: Sequence[str] = (),
    ) -> PostgresMigrationResult:
        """Validate and apply one immutable server plan in one target transaction."""

        namespace = self._validated_namespace(namespace)
        empty_preconditions = self._validated_table_names(required_empty_tables)
        conversion_locks = self._validated_table_names(conversion_tables)
        database_connection: Any | None = None
        completed = 0
        commit_attempted = False
        try:
            database_connection = self._connect(connection)
            # Conversion checks must see rows committed while waiting for a lock.
            # READ COMMITTED supplies that fresh snapshot; ACCESS EXCLUSIVE holds
            # every converted table stable from inspection through final commit.
            self._begin_write(database_connection, fresh_snapshots=bool(conversion_locks))
            self._execute_rows(
                database_connection,
                "SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(%s))",
                (namespace,),
            )
            for table_name in sorted(set(empty_preconditions) | set(conversion_locks)):
                self._execute_statement(
                    database_connection,
                    f"LOCK TABLE {self._qualified(namespace, table_name)} IN ACCESS EXCLUSIVE MODE",
                )
            if conversion_locks:
                self._execute_statement(database_connection, "SET LOCAL row_security = off")
                self._execute_statement(database_connection, "SET LOCAL extra_float_digits = 3")
            live = self._introspect_connection(database_connection, connection, namespace)
            if live.fingerprint != expected_catalog_fingerprint:
                raise PostgresMigrationStaleError(live.fingerprint)
            emptiness = self._table_emptiness_connection(
                database_connection,
                namespace,
                empty_preconditions,
            )
            nonempty = tuple(name for name, is_empty in emptiness.items() if not is_empty)
            if nonempty:
                raise PostgresMigrationPreconditionError(nonempty)
            identity = self._target_identity(database_connection, connection)
            xid_row = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT pg_current_xact_id()::text AS transaction_id",
                )
            )
            transaction_id = xid_row.get("transaction_id")
            if not isinstance(transaction_id, str) or not transaction_id:
                raise PostgresCatalogValidationError()
            on_started(transaction_id, identity)
            for statement in statements:
                if not isinstance(statement, str) or not statement.strip():
                    raise PostgresCatalogValidationError()
                self._execute_statement(database_connection, statement)
                completed += 1
            result_catalog = self._introspect_connection(
                database_connection,
                connection,
                namespace,
            )
            on_intended(result_catalog)
            commit_attempted = True
            try:
                database_connection.commit()
            except Exception:
                raise PostgresCommitUncertainError() from None
            return PostgresMigrationResult(
                catalog=result_catalog,
                transaction_id=transaction_id,
                target_identity=identity,
                completed_step_count=completed,
            )
        except (
            PostgresMigrationStaleError,
            PostgresMigrationPreconditionError,
            PostgresCommitUncertainError,
        ):
            raise
        except PostgresGatewayError as error:
            if completed or commit_attempted:
                raise PostgresMigrationExecutionError(completed, sqlstate=getattr(error, "sqlstate", None) if conversion_locks else None) from error
            raise
        except Exception as error:
            if commit_attempted:
                raise PostgresCommitUncertainError() from None
            raise PostgresMigrationExecutionError(completed, sqlstate=getattr(error, "sqlstate", None) if conversion_locks else None) from error
        finally:
            self._cleanup(database_connection)

    def transaction_status(
        self,
        connection: ResolvedPostgresConnection,
        transaction_id: str,
    ) -> PostgresTransactionRecovery:
        if not isinstance(transaction_id, str) or not transaction_id.isdigit():
            raise PostgresTransactionStatusError()
        database_connection: Any | None = None
        try:
            database_connection = self._connect(connection)
            self._begin_read_only(database_connection)
            identity = self._target_identity(database_connection, connection)
            row = self._one(
                self._execute_rows(
                    database_connection,
                    "SELECT pg_xact_status(%s::xid8) AS status",
                    (transaction_id,),
                )
            )
            status = row.get("status")
            if status not in {"committed", "aborted", "in progress"}:
                raise PostgresTransactionStatusError()
            return PostgresTransactionRecovery(
                status=status,
                target_identity=identity,
            )
        except PostgresGatewayError:
            raise
        except Exception:
            raise PostgresTransactionStatusError() from None
        finally:
            self._cleanup(database_connection)

    def _target_identity(
        self,
        database_connection: Any,
        connection: ResolvedPostgresConnection,
    ) -> dict[str, Any]:
        """Read the stable target evidence persisted before any migration DDL."""

        identity = self._one(
            self._execute_rows(
                database_connection,
                """
                SELECT current_database() AS database,
                       (SELECT oid::text FROM pg_database WHERE datname = current_database()) AS database_oid,
                       current_setting('server_version_num') AS server_version_num,
                       COALESCE(inet_server_addr()::text, 'local') AS server_address,
                       COALESCE(inet_server_port(), 0) AS server_port
                """,
            )
        )
        self._require_database(identity, connection.database)
        if (
            not isinstance(identity.get("database_oid"), str)
            or not identity["database_oid"]
            or not isinstance(identity.get("server_version_num"), str)
            or not identity["server_version_num"].isdigit()
            or not isinstance(identity.get("server_address"), str)
            or not identity["server_address"]
            or isinstance(identity.get("server_port"), bool)
            or not isinstance(identity.get("server_port"), int)
            or identity["server_port"] < 0
        ):
            raise PostgresCatalogValidationError()
        return identity

    def register_retained_connection_reclaimer(self, reclaimer) -> None:
        """Register each owner of evictable inactive cursor sessions."""
        if reclaimer not in self._retained_connection_reclaimers:
            self._retained_connection_reclaimers.append(reclaimer)

    def _connect(self, connection: ResolvedPostgresConnection, *, monitoring: bool = False, control: bool = False, retained: bool = False) -> Any:
        factory = self._connect_factory
        row_factory = _portable_dict_row
        if factory is None:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError:
                raise PostgresDriverUnavailableError() from None
            factory = psycopg.connect
            row_factory = dict_row

        parameters: dict[str, Any] = {
            "host": connection.host,
            "port": connection.port,
            "dbname": connection.database,
            "user": connection.username,
            "sslmode": connection.ssl_mode.value,
            "connect_timeout": min(
                MAX_CONNECT_TIMEOUT_SECONDS,
                max(1, connection.connect_timeout),
            ),
            "application_name": "schemii",
            "autocommit": False,
            "row_factory": row_factory,
        }
        if connection.password is not None:
            parameters["password"] = connection.password.get_secret_value()
        identity = f"{connection.id}:{connection.revision}"
        capacity = self._control_capacity if control else self._monitor_capacity if monitoring else self._connection_capacity
        if monitoring or control:
            parameters["connect_timeout"] = min(2, parameters["connect_timeout"])
        reached = capacity.acquire(identity, 0, retained=retained)
        # Reclaim only idle, replayable managed cursors. Callback runs outside
        # the capacity condition; raw sessions and user transactions are never
        # candidates. Admission is rechecked atomically after each reclamation.
        while reached and retained and self._retained_connection_reclaimers:
            if not any(reclaimer(connection.id, connection.revision,
                    reached[0] == "postgres.connections.maximum_total")
                    for reclaimer in tuple(self._retained_connection_reclaimers)):
                break
            reached = capacity.acquire(identity, 0, retained=retained)
        if reached:
            reached = capacity.acquire(identity,
                0.05 if monitoring or control else self._connection_acquire_timeout,
                retained=retained)
        if reached is not None:
            limit_name, limit, observed = reached
            raise PostgresConnectionCapacityError(limit_name, limit, observed)
        try:
            opened = factory(**parameters)
            return _LeasedConnection(opened, capacity, identity, retained=retained)
        except PostgresGatewayError:
            capacity.release(identity, retained=retained)
            raise
        except Exception:
            capacity.release(identity, retained=retained)
            raise PostgresConnectionError() from None

    @staticmethod
    def _execute_statement(database_connection: Any, query: str) -> None:
        cursor: Any | None = None
        try:
            cursor = database_connection.cursor()
            cursor.execute(query)
        except Exception as error:
            raise PostgresQueryError(sqlstate=getattr(error, "sqlstate", None)) from None
        finally:
            PsycopgPostgresGateway._safe_close(cursor)

    @classmethod
    def _set_timeout_ceiling(
        cls,
        database_connection: Any,
        setting: str,
        configured_milliseconds: int,
    ) -> None:
        """Apply an application ceiling without weakening a stricter role setting."""

        if setting not in {
            "statement_timeout",
            "lock_timeout",
            "idle_in_transaction_session_timeout",
        }:
            raise ValueError("unsupported PostgreSQL timeout setting")
        value = f"{configured_milliseconds}ms"
        cls._execute_statement(
            database_connection,
            f"SELECT set_config('{setting}', CASE "
            f"WHEN current_setting('{setting}')::interval = interval '0' "
            f"OR current_setting('{setting}')::interval > interval '{value}' "
            f"THEN '{value}' ELSE current_setting('{setting}') END, true)",
        )

    def _begin_read_only(
        self,
        database_connection: Any,
        *,
        repeatable_read: bool = False,
        idle_timeout_ms: int = IDLE_TRANSACTION_TIMEOUT_MS,
    ) -> None:
        isolation = " ISOLATION LEVEL REPEATABLE READ" if repeatable_read else ""
        self._execute_statement(
            database_connection,
            f"BEGIN TRANSACTION{isolation}, READ ONLY" if isolation else "BEGIN TRANSACTION READ ONLY",
        )
        self._set_timeout_ceiling(
            database_connection, "statement_timeout", self._catalog_statement_timeout_ms
        )
        self._set_timeout_ceiling(
            database_connection, "lock_timeout", self._lock_timeout_ms
        )
        self._set_timeout_ceiling(
            database_connection,
            "idle_in_transaction_session_timeout",
            idle_timeout_ms,
        )

    def _begin_write(self, database_connection: Any, *, fresh_snapshots: bool = False) -> None:
        isolation = "READ COMMITTED" if fresh_snapshots else "SERIALIZABLE"
        self._execute_statement(database_connection, f"BEGIN TRANSACTION ISOLATION LEVEL {isolation}")
        self._set_timeout_ceiling(
            database_connection,
            "statement_timeout",
            self._migration_statement_timeout_ms,
        )
        self._set_timeout_ceiling(
            database_connection, "lock_timeout", self._lock_timeout_ms
        )
        self._set_timeout_ceiling(
            database_connection,
            "idle_in_transaction_session_timeout",
            IDLE_TRANSACTION_TIMEOUT_MS,
        )

    def _begin_console_write(self, database_connection: Any) -> None:
        """Begin a human-controlled transaction using PostgreSQL's normal isolation."""

        self._execute_statement(database_connection, "BEGIN TRANSACTION")
        self._set_timeout_ceiling(
            database_connection, "statement_timeout", self._catalog_statement_timeout_ms
        )
        self._set_timeout_ceiling(
            database_connection, "lock_timeout", self._lock_timeout_ms
        )
        self._set_timeout_ceiling(
            database_connection,
            "idle_in_transaction_session_timeout",
            self._console_idle_transaction_timeout_ms,
        )

    @classmethod
    def _set_console_namespace(
        cls,
        database_connection: Any,
        namespace: str,
    ) -> None:
        cls._execute_statement(
            database_connection,
            f"SET LOCAL search_path TO {cls._quote_identifier(namespace)}, pg_catalog",
        )

    def _introspect_connection(
        self,
        database_connection: Any,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> PostgresCatalog:
        """Inspect using the caller's transaction snapshot and locks."""

        metadata = self._one(self._execute_rows(database_connection, METADATA_QUERY))
        self._require_database(metadata, connection.database)
        namespace_row = self._one(
            self._execute_rows(
                database_connection,
                NAMESPACE_EXISTS_QUERY,
                (namespace,),
            )
        )
        if namespace_row.get("namespace_exists") is not True:
            if namespace_row.get("namespace_exists") is False:
                raise PostgresNamespaceNotFoundError()
            raise PostgresCatalogValidationError()

        text_budget = [0]
        groups = [
            self._bounded_rows(
                database_connection, "types", TYPES_QUERY, namespace,
                self._limits.max_types, text_budget, ("definition",),
            ),
            self._bounded_rows(
                database_connection, "tables", TABLES_QUERY, namespace,
                self._limits.max_tables, text_budget, ("partition_key",),
            ),
            self._bounded_rows(
                database_connection, "columns", COLUMNS_QUERY, namespace,
                self._limits.max_columns, text_budget, ("default_expression",),
            ),
            self._bounded_rows(
                database_connection, "constraints", CONSTRAINTS_QUERY, namespace,
                self._limits.max_constraints, text_budget, ("definition",),
            ),
            self._bounded_rows(
                database_connection, "indexes", INDEXES_QUERY, namespace,
                self._limits.max_indexes, text_budget, ("definition", "predicate"),
            ),
            self._bounded_rows(
                database_connection, "triggers", TRIGGERS_QUERY, namespace,
                self._limits.max_triggers, text_budget, ("definition",),
            ),
            self._bounded_rows(
                database_connection, "functions", FUNCTIONS_QUERY, namespace,
                self._limits.max_functions, text_budget,
                ("identity_arguments", "arguments", "return_type", "definition"),
            ),
            self._bounded_rows(
                database_connection, "views", VIEWS_QUERY, namespace,
                self._limits.max_views, text_budget, ("query_definition",),
            ),
        ]
        if sum(len(rows) for rows in groups) > self._limits.max_total_objects:
            raise PostgresCatalogLimitError(
                "total_objects",
                self._limits.max_total_objects,
                sum(len(rows) for rows in groups),
            )
        return self._build_catalog(
            namespace=namespace,
            metadata=metadata,
            type_rows=groups[0],
            table_rows=groups[1],
            column_rows=groups[2],
            constraint_rows=groups[3],
            index_rows=groups[4],
            trigger_rows=groups[5],
            function_rows=groups[6],
            view_rows=groups[7],
        )

    @staticmethod
    def _execute_rows(
        database_connection: Any,
        query: str,
        parameters: tuple[Any, ...] = (),
        *,
        maximum: int | None = None,
        row_consumer: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        cursor: Any | None = None
        try:
            cursor = (
                database_connection.cursor(name=f"schemii_catalog_{secrets.token_hex(8)}")
                if maximum is not None
                else database_connection.cursor()
            )
            cursor.execute(query, parameters)
            rows: list[dict[str, Any]] = []
            if maximum is not None and hasattr(cursor, "fetchmany"):
                while len(rows) <= maximum:
                    raw_rows = cursor.fetchmany(min(32, maximum + 1 - len(rows)))
                    if not isinstance(raw_rows, Sequence):
                        raise TypeError
                    if not raw_rows:
                        break
                    for row in raw_rows:
                        if not isinstance(row, Mapping):
                            raise TypeError
                        converted = dict(row)
                        if row_consumer is not None:
                            row_consumer(converted)
                        rows.append(converted)
            else:
                raw_rows = cursor.fetchall()
                if not isinstance(raw_rows, Sequence):
                    raise TypeError
                for row in raw_rows:
                    if not isinstance(row, Mapping):
                        raise TypeError
                    converted = dict(row)
                    if row_consumer is not None:
                        row_consumer(converted)
                    rows.append(converted)
            return rows
        except PostgresGatewayError:
            raise
        except Exception:
            raise PostgresQueryError() from None
        finally:
            PsycopgPostgresGateway._safe_close(cursor)

    @staticmethod
    def _one(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) != 1:
            raise PostgresQueryError()
        return rows[0]

    @staticmethod
    def _require_database(row: Mapping[str, Any], expected_database: str) -> None:
        database = row.get("database")
        if not isinstance(database, str):
            raise PostgresQueryError()
        if database != expected_database:
            raise PostgresDatabaseMismatchError()

    @staticmethod
    def _validated_namespace(namespace: str) -> str:
        if (
            not isinstance(namespace, str)
            or not namespace
            or "\x00" in namespace
            or len(namespace.encode("utf-8")) > 63
        ):
            raise PostgresInvalidNamespaceError()
        return namespace

    @classmethod
    def _validated_table_names(cls, table_names: Sequence[str]) -> tuple[str, ...]:
        names = tuple(sorted(set(table_names)))
        for name in names:
            if (
                not isinstance(name, str)
                or not name
                or "\x00" in name
                or len(name.encode("utf-8")) > 63
            ):
                raise PostgresCatalogValidationError()
        return names

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return f'"{identifier.replace(chr(34), chr(34) * 2)}"'

    @classmethod
    def _qualified(cls, namespace: str, table_name: str) -> str:
        return f"{cls._quote_identifier(namespace)}.{cls._quote_identifier(table_name)}"

    @classmethod
    def _table_emptiness_connection(
        cls,
        database_connection: Any,
        namespace: str,
        table_names: Sequence[str],
    ) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for table_name in table_names:
            row = cls._one(
                cls._execute_rows(
                    database_connection,
                    (
                        "/* schemii_table_emptiness */ "
                        f"SELECT NOT EXISTS (SELECT 1 FROM {cls._qualified(namespace, table_name)} "
                        "LIMIT 1) AS is_empty"
                    ),
                )
            )
            if type(row.get("is_empty")) is not bool:
                raise PostgresCatalogValidationError()
            result[table_name] = row["is_empty"]
        return result

    def _bounded_rows(
        self,
        database_connection: Any,
        category: str,
        query: str,
        namespace: str,
        limit: int,
        text_budget: list[int],
        bounded_text_fields: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        def consume(row: dict[str, Any]) -> None:
            for field_name in bounded_text_fields:
                byte_count = row.pop(f"{field_name}_bytes", None)
                if byte_count is None:
                    if row.get(field_name) is not None:
                        raise PostgresCatalogValidationError()
                    continue
                if type(byte_count) is not int or byte_count < 0:
                    raise PostgresCatalogValidationError()
                if byte_count > self._limits.max_definition_bytes:
                    raise PostgresCatalogLimitError(
                        "catalog_text",
                        self._limits.max_definition_bytes,
                        byte_count,
                    )
            self._consume_text((row,), text_budget)

        rows = self._execute_rows(
            database_connection,
            query,
            (
                *(self._limits.max_definition_bytes for _ in bounded_text_fields),
                namespace,
                limit + 1,
            ),
            maximum=limit,
            row_consumer=consume,
        )
        if len(rows) > limit:
            raise PostgresCatalogLimitError(category, limit, len(rows))
        return rows

    def _consume_text(
        self,
        rows: Sequence[Mapping[str, Any]],
        text_budget: list[int],
    ) -> None:
        def text_bytes(value: Any) -> int:
            if isinstance(value, str):
                encoded = len(value.encode("utf-8"))
                if encoded > self._limits.max_definition_bytes:
                    raise PostgresCatalogLimitError(
                        "catalog_text",
                        self._limits.max_definition_bytes,
                        encoded,
                    )
                return encoded
            if isinstance(value, Mapping):
                return sum(text_bytes(item) for item in value.values())
            if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
                return sum(text_bytes(item) for item in value)
            return 0

        for row in rows:
            text_budget[0] += text_bytes(row)
            if text_budget[0] > self._limits.max_total_text_bytes:
                raise PostgresCatalogLimitError(
                    "catalog_text_total",
                    self._limits.max_total_text_bytes,
                    text_budget[0],
                )

    def _build_catalog(
        self,
        *,
        namespace: str,
        metadata: Mapping[str, Any],
        type_rows: list[dict[str, Any]],
        table_rows: list[dict[str, Any]],
        column_rows: list[dict[str, Any]],
        constraint_rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
        trigger_rows: list[dict[str, Any]],
        function_rows: list[dict[str, Any]],
        view_rows: list[dict[str, Any]],
    ) -> PostgresCatalog:
        types = tuple(
            PostgresType(
                namespace=namespace,
                name=row["type_name"],
                kind=row["type_kind"],
                definition=row["definition"],
            )
            for row in sorted(
                type_rows,
                key=lambda item: (item["type_name"], item["type_kind"]),
            )
        )

        relation_columns: dict[tuple[str, str], list[PostgresColumn]] = {}
        for row in sorted(
            column_rows,
            key=lambda item: (
                item["relation_kind"],
                item["relation_name"],
                item["ordinal"],
            ),
        ):
            key = (row["relation_kind"], row["relation_name"])
            relation_columns.setdefault(key, []).append(
                PostgresColumn(
                    name=row["column_name"],
                    ordinal=row["ordinal"],
                    data_type=row["data_type"],
                    nullable=row["nullable"],
                    default_expression=row.get("default_expression"),
                    identity=self._identity_kind(row.get("identity_kind")),
                    generated=self._generated_kind(row.get("generated_kind")),
                    collation_schema=row.get("collation_schema"),
                    collation_name=row.get("collation_name"),
                )
            )

        primary_keys: dict[str, PostgresPrimaryKey] = {}
        unique_constraints: dict[str, list[PostgresUniqueConstraint]] = {}
        checks: dict[str, list[PostgresCheckConstraint]] = {}
        not_null_constraints: dict[str, list[PostgresNotNullConstraint]] = {}
        exclusion_constraints: dict[str, list[PostgresExclusionConstraint]] = {}
        relationships: list[PostgresForeignKeyRelationship] = []
        for row in sorted(
            constraint_rows,
            key=lambda item: (
                item["table_name"],
                item["constraint_type"],
                item["constraint_name"],
            ),
        ):
            table_name = row["table_name"]
            columns = tuple(row.get("columns") or ())
            common = {
                "name": row["constraint_name"],
                "table": table_name,
                "columns": columns,
                "definition": row["definition"],
                "validated": row["validated"],
            }
            kind = row["constraint_type"]
            if kind == "p":
                if table_name in primary_keys:
                    raise PostgresCatalogValidationError()
                primary_keys[table_name] = PostgresPrimaryKey(
                    **common,
                    deferrable=row["deferrable"],
                    initially_deferred=row["initially_deferred"],
                )
            elif kind == "u":
                unique_constraints.setdefault(table_name, []).append(
                    PostgresUniqueConstraint(
                        **common,
                        deferrable=row["deferrable"],
                        initially_deferred=row["initially_deferred"],
                    )
                )
            elif kind == "c":
                checks.setdefault(table_name, []).append(
                    PostgresCheckConstraint(**common)
                )
            elif kind == "f":
                relationships.append(
                    PostgresForeignKeyRelationship(
                        name=row["constraint_name"],
                        source_namespace=namespace,
                        source_table=table_name,
                        source_columns=columns,
                        target_namespace=row["target_namespace"],
                        target_table=row["target_table"],
                        target_columns=tuple(row.get("target_columns") or ()),
                        definition=row["definition"],
                        on_update=self._foreign_key_action(row["update_action"]),
                        on_delete=self._foreign_key_action(row["delete_action"]),
                        match_type=self._foreign_key_match(row["match_type"]),
                        validated=row["validated"],
                        deferrable=row["deferrable"],
                        initially_deferred=row["initially_deferred"],
                    )
                )
            elif kind == "n":
                not_null_constraints.setdefault(table_name, []).append(
                    PostgresNotNullConstraint(**common)
                )
            elif kind == "x":
                exclusion_constraints.setdefault(table_name, []).append(
                    PostgresExclusionConstraint(
                        **common,
                        deferrable=row["deferrable"],
                        initially_deferred=row["initially_deferred"],
                    )
                )
            else:
                raise PostgresCatalogValidationError()

        indexes: dict[str, list[PostgresIndex]] = {}
        for row in sorted(index_rows, key=lambda item: (item["table_name"], item["index_name"])):
            table_name = row["table_name"]
            indexes.setdefault(table_name, []).append(
                PostgresIndex(
                    name=row["index_name"],
                    table=table_name,
                    definition=row["definition"],
                    method=row["method"],
                    unique=row["is_unique"],
                    valid=row["is_valid"],
                    predicate=row.get("predicate"),
                )
            )

        triggers: dict[str, list[PostgresTrigger]] = {}
        for row in sorted(trigger_rows, key=lambda item: (item["table_name"], item["trigger_name"])):
            table_name = row["table_name"]
            triggers.setdefault(table_name, []).append(
                PostgresTrigger(
                    name=row["trigger_name"],
                    table=table_name,
                    definition=row["definition"],
                    enabled=self._trigger_enabled(row["enabled"]),
                )
            )

        table_names = {row["table_name"] for row in table_rows}
        referenced_tables = (
            set(primary_keys)
            | set(unique_constraints)
            | set(checks)
            | set(not_null_constraints)
            | set(exclusion_constraints)
            | set(indexes)
            | set(triggers)
            | {relationship.source_table for relationship in relationships}
        )
        if not referenced_tables.issubset(table_names):
            raise PostgresCatalogValidationError()

        tables = tuple(
            PostgresTable(
                namespace=namespace,
                name=row["table_name"],
                kind=self._table_kind(row["relation_kind"]),
                is_partition=row["is_partition"],
                partition_key=row.get("partition_key"),
                columns=tuple(
                    relation_columns.pop(
                        (row["relation_kind"], row["table_name"]),
                        (),
                    )
                ),
                primary_key=primary_keys.get(row["table_name"]),
                unique_constraints=tuple(unique_constraints.get(row["table_name"], ())),
                checks=tuple(checks.get(row["table_name"], ())),
                not_null_constraints=tuple(
                    not_null_constraints.get(row["table_name"], ())
                ),
                exclusion_constraints=tuple(
                    exclusion_constraints.get(row["table_name"], ())
                ),
                indexes=tuple(indexes.get(row["table_name"], ())),
                triggers=tuple(triggers.get(row["table_name"], ())),
            )
            for row in sorted(table_rows, key=lambda item: (item["table_name"], item["relation_kind"]))
        )

        views: list[PostgresView] = []
        materialized_views: list[PostgresMaterializedView] = []
        for row in sorted(view_rows, key=lambda item: (item["view_name"], item["relation_kind"])):
            relation_kind = row["relation_kind"]
            columns = tuple(
                relation_columns.pop((relation_kind, row["view_name"]), ())
            )
            if relation_kind == "v":
                views.append(
                    PostgresView(
                        namespace=namespace,
                        name=row["view_name"],
                        columns=columns,
                        query_definition=row["query_definition"],
                    )
                )
            elif relation_kind == "m":
                materialized_views.append(
                    PostgresMaterializedView(
                        namespace=namespace,
                        name=row["view_name"],
                        columns=columns,
                        query_definition=row["query_definition"],
                        populated=row["populated"],
                    )
                )
            else:
                raise PostgresCatalogValidationError()
        if relation_columns:
            raise PostgresCatalogValidationError()

        functions = tuple(
            PostgresFunction(
                namespace=namespace,
                name=row["function_name"],
                kind=self._function_kind(row["function_kind"]),
                identity_arguments=row["identity_arguments"],
                arguments=row["arguments"],
                return_type=row.get("return_type"),
                language=row["language"],
                definition=row["definition"],
            )
            for row in sorted(
                function_rows,
                key=lambda item: (
                    item["function_name"],
                    item["identity_arguments"],
                    item["function_kind"],
                ),
            )
        )

        return build_postgres_catalog(
            database=metadata["database"],
            namespace=namespace,
            server_version=metadata["server_version"],
            server_version_num=metadata["server_version_num"],
            server_timezone=metadata["server_timezone"],
            types=types,
            tables=tables,
            relationships=tuple(
                sorted(
                    relationships,
                    key=lambda item: (item.source_table, item.name),
                )
            ),
            functions=functions,
            views=tuple(views),
            materialized_views=tuple(materialized_views),
            captured_at=self._clock(),
        )

    @staticmethod
    def _identity_kind(value: Any) -> str | None:
        mapping = {"": None, None: None, "a": "always", "d": "by_default"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _generated_kind(value: Any) -> str | None:
        mapping = {"": None, None: None, "s": "stored", "v": "virtual"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _table_kind(value: Any) -> str:
        mapping = {"r": "table", "p": "partitioned_table"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _function_kind(value: Any) -> str:
        mapping = {"f": "function", "p": "procedure"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _foreign_key_action(value: Any) -> str:
        mapping = {
            "a": "NO ACTION",
            "r": "RESTRICT",
            "c": "CASCADE",
            "n": "SET NULL",
            "d": "SET DEFAULT",
        }
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _foreign_key_match(value: Any) -> str:
        mapping = {"f": "FULL", "p": "PARTIAL", "s": "SIMPLE"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _trigger_enabled(value: Any) -> str:
        mapping = {"O": "origin", "D": "disabled", "R": "replica", "A": "always"}
        if value not in mapping:
            raise PostgresCatalogValidationError()
        return mapping[value]

    @staticmethod
    def _safe_close(resource: Any | None) -> None:
        if resource is None:
            return
        try:
            resource.close()
        except Exception:
            pass

    @classmethod
    def _cleanup(cls, database_connection: Any | None) -> None:
        if database_connection is None:
            return
        try:
            database_connection.rollback()
        except Exception:
            pass
        cls._safe_close(database_connection)
