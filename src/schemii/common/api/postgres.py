"""Map safe PostgreSQL domain errors into API problems."""

from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.postgres.errors import (
    PostgresCatalogLimitError,
    PostgresConnectionCapacityError,
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
    retryable = isinstance(
        error,
        (PostgresConnectionError, PostgresConnectionCapacityError),
    )
    details: dict[str, object] = {}
    limit_event = None
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
        limit_name = error.limit_name
        details = {
            "resource": error.category,
            "limitName": limit_name,
            "limit": error.limit,
            "observed": error.observed,
        }
        limit_event = LimitEventNotice(
            resource=f"postgres_catalog_{error.category}",
            limit_name=limit_name,
            configured_limit=error.limit,
            observed_value=error.observed,
        )
    elif isinstance(error, PostgresConnectionCapacityError):
        status = 503
        details = {
            "resource": "postgres_connections",
            "limitName": error.limit_name,
            "limit": error.limit,
            "observed": error.observed,
        }
        limit_event = LimitEventNotice(
            resource="postgres_connections",
            limit_name=error.limit_name,
            configured_limit=error.limit,
            observed_value=error.observed,
        )
    return ApiProblem(
        status,
        error.code,
        str(error),
        retryable=retryable,
        details=details,
        limit_event=limit_event,
    )
