from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.postgres import (
    MAX_CONNECT_TIMEOUT_SECONDS,
    PostgresCatalogLimitError,
    PostgresCatalogLimits,
    PostgresConnectionError,
    PostgresDatabaseMismatchError,
    PostgresNamespaceNotFoundError,
    PsycopgPostgresGateway,
    compute_catalog_fingerprint,
)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rows: list[dict[str, Any]] = []
        self.offset = 0
        self.closed = False

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> None:
        self.connection.executed.append((query, parameters))
        self.rows = [dict(row) for row in self.connection.rows_for(query)]
        for row in self.rows:
            for field in (
                "partition_key",
                "default_expression",
                "definition",
                "predicate",
                "identity_arguments",
                "arguments",
                "return_type",
                "query_definition",
            ):
                bytes_field = f"{field}_bytes"
                if bytes_field in query and bytes_field not in row:
                    value = row.get(field)
                    row[bytes_field] = (
                        len(value.encode("utf-8")) if isinstance(value, str) else None
                    )
        self.offset = 0

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchmany(self, size: int) -> list[dict[str, Any]]:
        rows = self.rows[self.offset : self.offset + size]
        self.offset += len(rows)
        return rows

    def close(self) -> None:
        self.closed = True
        self.connection.closed_cursors += 1


class FakeConnection:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.closed_cursors = 0
        self.cursor_names: list[str | None] = []
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *, name: str | None = None) -> FakeCursor:
        self.cursor_names.append(name)
        return FakeCursor(self)

    def rows_for(self, query: str) -> list[dict[str, Any]]:
        for marker, rows in self.responses.items():
            if marker in query:
                return rows
        if query.startswith("BEGIN TRANSACTION") or query.startswith("SET LOCAL"):
            return []
        raise AssertionError("unexpected query")

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeConnectFactory:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.parameters: list[dict[str, Any]] = []
        self.connections: list[FakeConnection] = []

    def __call__(self, **parameters: Any) -> FakeConnection:
        self.parameters.append(parameters)
        connection = FakeConnection(self.responses)
        self.connections.append(connection)
        return connection


def resolved_connection(*, password: str | None = None) -> ResolvedPostgresConnection:
    return ResolvedPostgresConnection(
        id="pg_0123456789abcdef0123456789abcdef",
        revision=1,
        name="Reporting",
        host="postgres.internal",
        port=5432,
        database="analytics",
        username="reader",
        password=password,
        ssl_mode="require",
        connect_timeout=30,
    )


def metadata_responses() -> dict[str, list[dict[str, Any]]]:
    return {
        "schemii_catalog_metadata": [
            {
                "database": "analytics",
                "server_version": "17.2",
                "server_version_num": 170002,
                "server_timezone": "UTC",
            }
        ],
        "schemii_connection_test": [
            {"database": "analytics", "server_version": "17.2"}
        ],
        "schemii_namespace_exists": [{"namespace_exists": True}],
    }


def catalog_responses() -> dict[str, list[dict[str, Any]]]:
    responses = metadata_responses()
    responses.update(
        {
            "schemii_catalog_tables": [
                {
                    "table_name": "orders",
                    "relation_kind": "r",
                    "is_partition": False,
                    "partition_key": None,
                },
                {
                    "table_name": "users",
                    "relation_kind": "r",
                    "is_partition": False,
                    "partition_key": None,
                },
            ],
            "schemii_catalog_columns": [
                {
                    "relation_name": "orders",
                    "relation_kind": "r",
                    "column_name": "id",
                    "ordinal": 1,
                    "data_type": "bigint",
                    "nullable": False,
                    "default_expression": "nextval('orders_id_seq'::regclass)",
                    "identity_kind": "",
                    "generated_kind": "",
                    "collation_schema": None,
                    "collation_name": None,
                },
                {
                    "relation_name": "orders",
                    "relation_kind": "r",
                    "column_name": "user_id",
                    "ordinal": 2,
                    "data_type": "bigint",
                    "nullable": False,
                    "default_expression": None,
                    "identity_kind": "",
                    "generated_kind": "",
                    "collation_schema": None,
                    "collation_name": None,
                },
                {
                    "relation_name": "users",
                    "relation_kind": "r",
                    "column_name": "id",
                    "ordinal": 1,
                    "data_type": "bigint",
                    "nullable": False,
                    "default_expression": None,
                    "identity_kind": "a",
                    "generated_kind": "",
                    "collation_schema": None,
                    "collation_name": None,
                },
                {
                    "relation_name": "order_totals",
                    "relation_kind": "v",
                    "column_name": "user_id",
                    "ordinal": 1,
                    "data_type": "bigint",
                    "nullable": True,
                    "default_expression": None,
                    "identity_kind": "",
                    "generated_kind": "",
                    "collation_schema": None,
                    "collation_name": None,
                },
                {
                    "relation_name": "daily_orders",
                    "relation_kind": "m",
                    "column_name": "day",
                    "ordinal": 1,
                    "data_type": "date",
                    "nullable": True,
                    "default_expression": None,
                    "identity_kind": "",
                    "generated_kind": "",
                    "collation_schema": None,
                    "collation_name": None,
                },
            ],
            "schemii_catalog_constraints": [
                {
                    "constraint_name": "orders_pkey",
                    "table_name": "orders",
                    "constraint_type": "p",
                    "columns": ["id"],
                    "target_namespace": None,
                    "target_table": None,
                    "target_columns": [],
                    "update_action": " ",
                    "delete_action": " ",
                    "match_type": " ",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "PRIMARY KEY (id)",
                },
                {
                    "constraint_name": "orders_user_unique",
                    "table_name": "orders",
                    "constraint_type": "u",
                    "columns": ["user_id"],
                    "target_namespace": None,
                    "target_table": None,
                    "target_columns": [],
                    "update_action": " ",
                    "delete_action": " ",
                    "match_type": " ",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "UNIQUE (user_id)",
                },
                {
                    "constraint_name": "orders_id_check",
                    "table_name": "orders",
                    "constraint_type": "c",
                    "columns": ["id"],
                    "target_namespace": None,
                    "target_table": None,
                    "target_columns": [],
                    "update_action": " ",
                    "delete_action": " ",
                    "match_type": " ",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "CHECK ((id > 0))",
                },
                {
                    "constraint_name": "orders_user_fkey",
                    "table_name": "orders",
                    "constraint_type": "f",
                    "columns": ["user_id"],
                    "target_namespace": "public",
                    "target_table": "users",
                    "target_columns": ["id"],
                    "update_action": "c",
                    "delete_action": "r",
                    "match_type": "s",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "FOREIGN KEY (user_id) REFERENCES users(id)",
                },
                {
                    "constraint_name": "orders_id_exclusion",
                    "table_name": "orders",
                    "constraint_type": "x",
                    "columns": ["id"],
                    "target_namespace": None,
                    "target_table": None,
                    "target_columns": [],
                    "update_action": " ",
                    "delete_action": " ",
                    "match_type": " ",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "EXCLUDE USING gist (id WITH =)",
                },
                {
                    "constraint_name": "orders_user_id_not_null",
                    "table_name": "orders",
                    "constraint_type": "n",
                    "columns": ["user_id"],
                    "target_namespace": None,
                    "target_table": None,
                    "target_columns": [],
                    "update_action": " ",
                    "delete_action": " ",
                    "match_type": " ",
                    "validated": True,
                    "deferrable": False,
                    "initially_deferred": False,
                    "definition": "NOT NULL user_id",
                },
            ],
            "schemii_catalog_indexes": [
                {
                    "table_name": "orders",
                    "index_name": "orders_id_desc_idx",
                    "definition": "CREATE INDEX orders_id_desc_idx ON public.orders USING btree (id DESC)",
                    "method": "btree",
                    "is_unique": False,
                    "is_valid": True,
                    "predicate": None,
                }
            ],
            "schemii_catalog_triggers": [
                {
                    "table_name": "orders",
                    "trigger_name": "orders_audit",
                    "definition": "CREATE TRIGGER orders_audit BEFORE UPDATE ON orders EXECUTE FUNCTION audit_order()",
                    "enabled": "O",
                }
            ],
            "schemii_catalog_functions": [
                {
                    "function_name": "audit_order",
                    "function_kind": "f",
                    "identity_arguments": "",
                    "arguments": "",
                    "return_type": "trigger",
                    "language": "plpgsql",
                    "definition": "CREATE FUNCTION public.audit_order() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                }
            ],
            "schemii_catalog_views": [
                {
                    "view_name": "daily_orders",
                    "relation_kind": "m",
                    "query_definition": "SELECT current_date AS day;",
                    "populated": True,
                },
                {
                    "view_name": "order_totals",
                    "relation_kind": "v",
                    "query_definition": "SELECT user_id FROM orders;",
                    "populated": True,
                },
            ],
        }
    )
    return responses


def test_connection_uses_safe_bounded_parameters_and_omits_missing_password() -> None:
    factory = FakeConnectFactory(metadata_responses())
    gateway = PsycopgPostgresGateway(connect_factory=factory)

    result = gateway.test_connection(resolved_connection())

    assert result.ok is True
    assert result.database == "analytics"
    assert factory.parameters[0]["application_name"] == "schemii"
    assert factory.parameters[0]["connect_timeout"] == MAX_CONNECT_TIMEOUT_SECONDS
    assert factory.parameters[0]["autocommit"] is False
    assert callable(factory.parameters[0]["row_factory"])
    assert "password" not in factory.parameters[0]
    connection = factory.connections[0]
    assert connection.rollbacks == 1
    assert connection.closed is True
    assert connection.closed_cursors == len(connection.executed)
    assert any("statement_timeout" in query for query, _ in connection.executed)


def test_driver_failures_are_mapped_without_leaking_credentials_or_driver_text() -> None:
    secret = "this-password-must-not-leak"

    def fail_connect(**parameters: Any) -> Any:
        assert parameters["password"] == secret
        raise RuntimeError(f"dsn with password={secret}; SELECT private_data")

    gateway = PsycopgPostgresGateway(connect_factory=fail_connect)

    with pytest.raises(PostgresConnectionError) as caught:
        gateway.test_connection(resolved_connection(password=secret))

    rendered = str(caught.value)
    assert secret not in rendered
    assert "dsn" not in rendered.lower()
    assert "select" not in rendered.lower()


def test_namespace_check_is_parameterized_and_rejects_database_drift() -> None:
    responses = metadata_responses()
    responses["schemii_catalog_metadata"][0]["database"] = "other_database"
    factory = FakeConnectFactory(responses)
    gateway = PsycopgPostgresGateway(connect_factory=factory)

    with pytest.raises(PostgresDatabaseMismatchError):
        gateway.namespace_exists(resolved_connection(), "custom schema")

    connection = factory.connections[0]
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_introspection_maps_all_domains_in_one_read_only_snapshot() -> None:
    captured_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    factory = FakeConnectFactory(catalog_responses())
    gateway = PsycopgPostgresGateway(
        connect_factory=factory,
        clock=lambda: captured_at,
    )

    catalog = gateway.introspect(resolved_connection(), "public")

    assert catalog.captured_at == captured_at
    assert catalog.fingerprint == compute_catalog_fingerprint(catalog)
    assert [table.name for table in catalog.tables] == ["orders", "users"]
    orders = catalog.tables[0]
    assert orders.primary_key is not None
    assert orders.primary_key.columns == ("id",)
    assert orders.unique_constraints[0].name == "orders_user_unique"
    assert orders.checks[0].name == "orders_id_check"
    assert orders.not_null_constraints[0].name == "orders_user_id_not_null"
    assert orders.exclusion_constraints[0].name == "orders_id_exclusion"
    assert orders.indexes[0].method == "btree"
    assert orders.triggers[0].enabled == "origin"
    assert catalog.relationships[0].on_update == "CASCADE"
    assert catalog.relationships[0].on_delete == "RESTRICT"
    assert catalog.functions[0].name == "audit_order"
    assert catalog.views[0].columns[0].name == "user_id"
    assert catalog.materialized_views[0].name == "daily_orders"
    assert catalog.materialized_views[0].populated is True

    connection = factory.connections[0]
    begin_statements = [
        query for query, _ in connection.executed if query.startswith("BEGIN TRANSACTION")
    ]
    assert begin_statements == [
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ]
    for query, parameters in connection.executed:
        if "schemii_catalog_" in query and "metadata" not in query:
            assert parameters[-2] == "public"
    assert sum(name is not None for name in connection.cursor_names) == 7
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_fingerprint_excludes_capture_time() -> None:
    first = PsycopgPostgresGateway(
        connect_factory=FakeConnectFactory(catalog_responses()),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).introspect(resolved_connection(), "public")
    second = PsycopgPostgresGateway(
        connect_factory=FakeConnectFactory(catalog_responses()),
        clock=lambda: datetime(2027, 1, 1, tzinfo=timezone.utc),
    ).introspect(resolved_connection(), "public")

    assert first.captured_at != second.captured_at
    assert first.fingerprint == second.fingerprint


def test_missing_namespace_has_no_fallback() -> None:
    responses = catalog_responses()
    responses["schemii_namespace_exists"] = [{"namespace_exists": False}]
    factory = FakeConnectFactory(responses)

    with pytest.raises(PostgresNamespaceNotFoundError):
        PsycopgPostgresGateway(connect_factory=factory).introspect(
            resolved_connection(),
            "missing",
        )

    assert not any(
        "schemii_catalog_tables" in query
        for query, _ in factory.connections[0].executed
    )


def test_catalog_object_limits_reject_instead_of_returning_partial_authority() -> None:
    responses = catalog_responses()
    factory = FakeConnectFactory(responses)
    limits = PostgresCatalogLimits(max_tables=1)

    with pytest.raises(PostgresCatalogLimitError) as caught:
        PsycopgPostgresGateway(
            connect_factory=factory,
            limits=limits,
        ).introspect(resolved_connection(), "public")

    assert caught.value.category == "tables"
    assert caught.value.limit == 1
    assert factory.connections[0].rollbacks == 1
    assert factory.connections[0].closed is True


def test_catalog_text_is_rejected_before_an_oversized_value_is_materialized() -> None:
    responses = catalog_responses()
    responses["schemii_catalog_functions"][0]["definition"] = "x" * 256
    factory = FakeConnectFactory(responses)

    with pytest.raises(PostgresCatalogLimitError) as caught:
        PsycopgPostgresGateway(
            connect_factory=factory,
            limits=PostgresCatalogLimits(max_definition_bytes=128),
        ).introspect(resolved_connection(), "public")

    assert caught.value.category == "catalog_text"
    function_query = next(
        (query, parameters)
        for query, parameters in factory.connections[0].executed
        if "schemii_catalog_functions" in query
    )
    assert function_query[1][:4] == (128, 128, 128, 128)
