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


class PostgresQueryError(PostgresGatewayError):
    code = "postgres_query_failed"

    def __init__(self) -> None:
        super().__init__("PostgreSQL catalog query failed")


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

    def __init__(self, category: str, limit: int) -> None:
        self.category = category
        self.limit = limit
        super().__init__("The PostgreSQL catalog exceeds the configured introspection limit")


class PostgresCatalogValidationError(PostgresGatewayError):
    code = "postgres_catalog_invalid"

    def __init__(self) -> None:
        super().__init__("PostgreSQL returned invalid catalog metadata")
