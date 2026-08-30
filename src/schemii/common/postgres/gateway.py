"""Shared, read-only PostgreSQL catalog gateway."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import secrets
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from schemii.common.connections.models import (
    MAX_CONNECT_TIMEOUT_SECONDS,
    ResolvedPostgresConnection,
)

from .errors import (
    PostgresCatalogLimitError,
    PostgresCatalogValidationError,
    PostgresConnectionError,
    PostgresDatabaseMismatchError,
    PostgresDriverUnavailableError,
    PostgresGatewayError,
    PostgresInvalidNamespaceError,
    PostgresNamespaceNotFoundError,
    PostgresQueryError,
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
    PostgresNotNullConstraint,
    PostgresPrimaryKey,
    PostgresTable,
    PostgresTrigger,
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
    NAMESPACE_EXISTS_QUERY,
    TABLES_QUERY,
    TRIGGERS_QUERY,
    VIEWS_QUERY,
)


STATEMENT_TIMEOUT_MS = 15_000
LOCK_TIMEOUT_MS = 5_000
IDLE_TRANSACTION_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class PostgresCatalogLimits:
    """Application-side limits that prevent unbounded catalog materialization."""

    max_tables: int = 2_000
    max_columns: int = 30_000
    max_constraints: int = 20_000
    max_indexes: int = 10_000
    max_triggers: int = 10_000
    max_functions: int = 5_000
    max_views: int = 5_000
    max_total_objects: int = 60_000
    max_definition_bytes: int = 256 * 1024
    max_total_text_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        hard_limits = {
            "max_tables": 10_000,
            "max_columns": 100_000,
            "max_constraints": 100_000,
            "max_indexes": 100_000,
            "max_triggers": 100_000,
            "max_functions": 50_000,
            "max_views": 50_000,
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

    def introspect(
        self,
        connection: ResolvedPostgresConnection,
        namespace: str,
    ) -> PostgresCatalog: ...


def _portable_dict_row(cursor: Any) -> Callable[[Sequence[Any]], dict[str, Any]]:
    """A psycopg-compatible row factory kept local for lazy driver loading."""

    names = [column.name for column in cursor.description]
    return lambda values: dict(zip(names, values))


class PsycopgPostgresGateway:
    """Read PostgreSQL authority through psycopg without retaining snapshots."""

    def __init__(
        self,
        *,
        connect_factory: Callable[..., Any] | None = None,
        limits: PostgresCatalogLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connect_factory = connect_factory
        self._limits = limits or PostgresCatalogLimits()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

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
            table_rows = self._bounded_rows(
                database_connection,
                "tables",
                TABLES_QUERY,
                namespace,
                self._limits.max_tables,
                text_budget,
                ("partition_key",),
            )
            column_rows = self._bounded_rows(
                database_connection,
                "columns",
                COLUMNS_QUERY,
                namespace,
                self._limits.max_columns,
                text_budget,
                ("default_expression",),
            )
            constraint_rows = self._bounded_rows(
                database_connection,
                "constraints",
                CONSTRAINTS_QUERY,
                namespace,
                self._limits.max_constraints,
                text_budget,
                ("definition",),
            )
            index_rows = self._bounded_rows(
                database_connection,
                "indexes",
                INDEXES_QUERY,
                namespace,
                self._limits.max_indexes,
                text_budget,
                ("definition", "predicate"),
            )
            trigger_rows = self._bounded_rows(
                database_connection,
                "triggers",
                TRIGGERS_QUERY,
                namespace,
                self._limits.max_triggers,
                text_budget,
                ("definition",),
            )
            function_rows = self._bounded_rows(
                database_connection,
                "functions",
                FUNCTIONS_QUERY,
                namespace,
                self._limits.max_functions,
                text_budget,
                ("identity_arguments", "arguments", "return_type", "definition"),
            )
            view_rows = self._bounded_rows(
                database_connection,
                "views",
                VIEWS_QUERY,
                namespace,
                self._limits.max_views,
                text_budget,
                ("query_definition",),
            )
            row_groups = (
                table_rows,
                column_rows,
                constraint_rows,
                index_rows,
                trigger_rows,
                function_rows,
                view_rows,
            )
            total_objects = sum(len(rows) for rows in row_groups)
            if total_objects > self._limits.max_total_objects:
                raise PostgresCatalogLimitError(
                    "total_objects",
                    self._limits.max_total_objects,
                )
            return self._build_catalog(
                namespace=namespace,
                metadata=metadata,
                table_rows=table_rows,
                column_rows=column_rows,
                constraint_rows=constraint_rows,
                index_rows=index_rows,
                trigger_rows=trigger_rows,
                function_rows=function_rows,
                view_rows=view_rows,
            )
        except PostgresGatewayError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError):
            raise PostgresCatalogValidationError() from None
        finally:
            self._cleanup(database_connection)

    def _connect(self, connection: ResolvedPostgresConnection) -> Any:
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
        try:
            return factory(**parameters)
        except PostgresGatewayError:
            raise
        except Exception:
            raise PostgresConnectionError() from None

    @staticmethod
    def _execute_statement(database_connection: Any, query: str) -> None:
        cursor: Any | None = None
        try:
            cursor = database_connection.cursor()
            cursor.execute(query)
        except Exception:
            raise PostgresQueryError() from None
        finally:
            PsycopgPostgresGateway._safe_close(cursor)

    @classmethod
    def _begin_read_only(
        cls,
        database_connection: Any,
        *,
        repeatable_read: bool = False,
    ) -> None:
        isolation = " ISOLATION LEVEL REPEATABLE READ" if repeatable_read else ""
        cls._execute_statement(
            database_connection,
            f"BEGIN TRANSACTION{isolation}, READ ONLY" if isolation else "BEGIN TRANSACTION READ ONLY",
        )
        cls._execute_statement(
            database_connection,
            f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}",
        )
        cls._execute_statement(
            database_connection,
            f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}",
        )
        cls._execute_statement(
            database_connection,
            f"SET LOCAL idle_in_transaction_session_timeout = {IDLE_TRANSACTION_TIMEOUT_MS}",
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
            raise PostgresCatalogLimitError(category, limit)
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
                )

    def _build_catalog(
        self,
        *,
        namespace: str,
        metadata: Mapping[str, Any],
        table_rows: list[dict[str, Any]],
        column_rows: list[dict[str, Any]],
        constraint_rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
        trigger_rows: list[dict[str, Any]],
        function_rows: list[dict[str, Any]],
        view_rows: list[dict[str, Any]],
    ) -> PostgresCatalog:
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
