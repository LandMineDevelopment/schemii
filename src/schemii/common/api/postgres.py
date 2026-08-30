"""Map safe PostgreSQL domain errors into API problems."""

from schemii.common.postgres.errors import (
    PostgresCatalogLimitError,
    PostgresConnectionError,
    PostgresDatabaseMismatchError,
    PostgresDriverUnavailableError,
    PostgresGatewayError,
    PostgresInvalidNamespaceError,
    PostgresNamespaceNotFoundError,
)

from .errors import ApiProblem


def postgres_api_problem(error: PostgresGatewayError) -> ApiProblem:
    status = 502
    retryable = isinstance(error, (PostgresConnectionError,))
    details: dict[str, object] = {}
    if isinstance(error, PostgresDriverUnavailableError):
        status = 503
    elif isinstance(error, PostgresDatabaseMismatchError):
        status = 409
    elif isinstance(error, PostgresNamespaceNotFoundError):
        status = 404
    elif isinstance(error, PostgresInvalidNamespaceError):
        status = 422
    elif isinstance(error, PostgresCatalogLimitError):
        status = 422
        details = {"category": error.category, "limit": error.limit}
    return ApiProblem(
        status,
        error.code,
        str(error),
        retryable=retryable,
        details=details,
    )
