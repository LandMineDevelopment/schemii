"""Safe domain exceptions raised by the shared PostgreSQL gateway."""

from __future__ import annotations


class PostgresGatewayError(RuntimeError):
    """Base error whose message is safe to return across product boundaries."""

    code = "postgres_gateway_error"


class PostgresDriverUnavailableError(PostgresGatewayError):
    code = "postgres_driver_unavailable"

    def __init__(self) -> None:
        super().__init__("PostgreSQL support is unavailable")


class PostgresConnectionError(PostgresGatewayError):
    code = "postgres_connection_failed"

    def __init__(self) -> None:
        super().__init__("PostgreSQL connection failed")


class PostgresConnectionCapacityError(PostgresGatewayError):
    code = "postgres_connection_capacity_reached"

    def __init__(self, limit_name: str, limit: int, observed: int) -> None:
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(
            "Schemii is using all configured PostgreSQL connection slots. "
            "Wait for an active operation to finish and retry."
        )


class PostgresQueryError(PostgresGatewayError):
    code = "postgres_query_failed"

    def __init__(self, *, sqlstate: str | None = None) -> None:
        self.sqlstate = sqlstate
        super().__init__("PostgreSQL catalog query failed")


class PostgresConsoleQueryError(PostgresGatewayError):
    code = "postgres_console_query_failed"

    def __init__(
        self,
        message: str,
        *,
        statement_index: int,
        sqlstate: str | None = None,
    ) -> None:
        self.statement_index = statement_index
        self.sqlstate = sqlstate
        super().__init__(message[:2048])


class PostgresConsoleLimitError(PostgresGatewayError):
    code = "postgres_console_result_limit"

    def __init__(
        self,
        message: str,
        *,
        statement_index: int,
        resource: str = "console_result",
        limit_name: str = "console.results.page_memory_bytes",
        limit: int | None = None,
        observed: int | None = None,
    ) -> None:
        self.statement_index = statement_index
        self.resource = resource
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(message)


class PostgresConsoleCancelledError(PostgresGatewayError):
    code = "postgres_console_cancelled"

    def __init__(self) -> None:
        super().__init__("Console execution was cancelled")


class PostgresDatabaseMismatchError(PostgresGatewayError):
    code = "postgres_database_mismatch"

    def __init__(self) -> None:
        super().__init__("The connected PostgreSQL database does not match the requested database")


class PostgresNamespaceNotFoundError(PostgresGatewayError):
    code = "postgres_namespace_not_found"

    def __init__(self) -> None:
        super().__init__("The requested PostgreSQL namespace was not found")


class PostgresInvalidNamespaceError(PostgresGatewayError):
    code = "postgres_invalid_namespace"

    def __init__(self) -> None:
        super().__init__("The PostgreSQL namespace is invalid")


class PostgresCatalogLimitError(PostgresGatewayError):
    code = "postgres_catalog_limit_exceeded"

    def __init__(self, category: str, limit: int, observed: int | None = None) -> None:
        self.category = category
        self.limit = limit
        self.observed = observed
        self.limit_name = {
            "catalog_text": "postgres.catalog.maximum_definition_bytes",
            "catalog_text_total": "postgres.catalog.maximum_total_text_bytes",
        }.get(category, f"postgres.catalog.maximum_{category}")
        super().__init__(
            f"The PostgreSQL catalog has more {category} than Schemii's configured limit of {limit}. "
            f"Reduce the inspected schema or ask the administrator to raise {self.limit_name}."
        )


class PostgresCatalogValidationError(PostgresGatewayError):
    code = "postgres_catalog_invalid"

    def __init__(self) -> None:
        super().__init__("PostgreSQL returned invalid catalog metadata")


class PostgresMigrationStaleError(PostgresGatewayError):
    code = "postgres_migration_stale"

    def __init__(self, current_fingerprint: str) -> None:
        self.current_fingerprint = current_fingerprint
        super().__init__("PostgreSQL changed after the migration was reviewed")


class PostgresMigrationPreconditionError(PostgresGatewayError):
    code = "postgres_migration_precondition_changed"

    def __init__(self, tables: tuple[str, ...]) -> None:
        self.tables = tables
        super().__init__(
            "A table that had to remain empty received rows after the migration was reviewed"
        )


class PostgresMigrationExecutionError(PostgresGatewayError):
    code = "postgres_migration_failed"

    def __init__(self, completed_step_count: int, *, sqlstate: str | None = None) -> None:
        self.completed_step_count = completed_step_count
        self.sqlstate = sqlstate
        detail = conversion_validation_message(sqlstate) if sqlstate else "PostgreSQL rejected the migration"
        super().__init__(detail + "; all migration statements were rolled back")


def conversion_validation_message(sqlstate: str | None) -> str:
    """Never propagate PostgreSQL diagnostics containing actual row values."""
    if sqlstate == "SC001":
        return "Strict conversion would alter or lose existing values. Choose a custom expression or revise the target type"
    if sqlstate == "57014":
        return "Conversion validation exceeded the PostgreSQL statement timeout. Ask the administrator to review the configured timeout"
    if sqlstate == "42501":
        return "The connection lacks permission to validate or convert this column"
    if sqlstate in {"42846", "42804", "42883", "42704"}:
        return "PostgreSQL cannot use this conversion or its default expression. Review the types and USING expression"
    if sqlstate and sqlstate.startswith("22"):
        return "Some values cannot be converted to the target type. Revise the type or provide an explicit custom expression"
    if sqlstate and sqlstate.startswith("23"):
        return "The conversion violates a required value, unique key, foreign key, or check constraint"
    return "PostgreSQL could not validate the conversion. Check the expression, connection permissions and target availability"


class PostgresCommitUncertainError(PostgresGatewayError):
    code = "postgres_migration_commit_uncertain"

    def __init__(self) -> None:
        super().__init__("The PostgreSQL connection ended while the migration commit outcome was unknown")


class PostgresTransactionStatusError(PostgresGatewayError):
    code = "postgres_transaction_status_unavailable"

    def __init__(self) -> None:
        super().__init__("PostgreSQL could not determine the migration transaction status")
