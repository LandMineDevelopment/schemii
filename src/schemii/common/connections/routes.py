"""Shared FastAPI routes for owner-scoped PostgreSQL connections."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field

from schemii.common.api.errors import ApiProblem
from schemii.common.api.models import ApiModel
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.postgres.errors import PostgresGatewayError

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
)
from .policy import ConnectionTargetForbiddenError
from .service import ConnectionInUseError, ConnectionService
from .store import (
    ConnectionConflictError,
    ConnectionLimitError,
    ConnectionNotFoundError,
)


class ConnectionListResponse(ApiModel):
    """Owner-visible PostgreSQL connection profiles."""

    connections: list[PostgresConnectionProfile]


class ConnectionTestResponse(ApiModel):
    """Verified target identity returned by a PostgreSQL connection test."""

    ok: bool
    database: str
    server_version: str


class PostgresNamespaceSummary(ApiModel):
    """One namespace visible through the selected PostgreSQL role."""

    name: Annotated[str, Field(min_length=1, max_length=63)]
    system: bool


class ConnectionNamespaceListResponse(ApiModel):
    """Bounded namespaces read from one exact connection revision."""

    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    connection_revision: Annotated[int, Field(strict=True, ge=1)]
    namespaces: list[PostgresNamespaceSummary] = Field(max_length=10_000)


class ConnectionDeletionDependency(ApiModel):
    """One exact product resource retaining the selected connection."""

    provider: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Annotated[str, Field(min_length=1, max_length=64)]
    resource_id: Annotated[str, Field(min_length=1, max_length=128)]
    resource_revision: Annotated[int, Field(strict=True, ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    target: str | None = Field(default=None, max_length=255)
    deletion_blocked: bool
    blocking_reason: str | None = Field(default=None, max_length=1024)


class ConnectionDeletionImpactResponse(ApiModel):
    """Current owner-scoped resources preventing connection deletion."""

    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    connection_revision: Annotated[int, Field(strict=True, ge=1)]
    can_delete: bool
    dependencies: list[ConnectionDeletionDependency] = Field(max_length=10_000)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


router = APIRouter(prefix="/api/v1/connections", tags=["connections"])


def _service(request: Request) -> ConnectionService:
    return request.app.state.services.connections


def _not_found(error: ConnectionNotFoundError) -> ApiProblem:
    return ApiProblem(404, "connection_not_found", str(error))


def _forbidden_target(error: ConnectionTargetForbiddenError) -> ApiProblem:
    return ApiProblem(403, error.code, str(error))


@router.get("", response_model=ConnectionListResponse)
def list_connections(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionListResponse:
    """List the current owner's non-secret PostgreSQL connection profiles."""

    return ConnectionListResponse(
        connections=_service(request).list(principal.user_id)
    )


@router.post("", response_model=PostgresConnectionProfile, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: PostgresConnectionCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PostgresConnectionProfile:
    """Validate and retain a new owner-scoped PostgreSQL connection profile."""

    try:
        return _service(request).create(principal.user_id, body)
    except ConnectionTargetForbiddenError as error:
        raise _forbidden_target(error) from error
    except ConnectionLimitError as error:
        raise ApiProblem(
            409,
            "connection_limit_reached",
            f"This user already has {error.limit} saved connections. Delete an unused connection or ask the administrator to raise resources.maximum_connections_per_user.",
            details={"resource": "saved_connections", "limitName": "resources.maximum_connections_per_user", "limit": error.limit, "observed": error.limit},
            limit_event=LimitEventNotice(
                resource="saved_connections",
                limit_name="resources.maximum_connections_per_user",
                configured_limit=error.limit,
                observed_value=error.limit,
            ),
        ) from error


@router.get("/{connection_id}", response_model=PostgresConnectionProfile)
def get_connection(
    connection_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PostgresConnectionProfile:
    """Return one owner-scoped connection without exposing its credential."""

    try:
        return _service(request).get(principal.user_id, connection_id)
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error


@router.patch("/{connection_id}", response_model=PostgresConnectionProfile)
def update_connection(
    connection_id: str,
    body: PostgresConnectionUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PostgresConnectionProfile:
    """Apply an optimistic revision-checked update to connection metadata."""

    try:
        return _service(request).update(principal.user_id, connection_id, body)
    except ConnectionTargetForbiddenError as error:
        raise _forbidden_target(error) from error
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    except ConnectionConflictError as error:
        raise ApiProblem(
            409,
            "connection_conflict",
            str(error),
            details={"currentRevision": error.current_revision},
        ) from error
    except ConnectionInUseError as error:
        raise ApiProblem(
            409,
            "connection_target_in_use",
            "Host, port, database, and username cannot change while this connection has saved database workspaces",
            details={"dependencies": error.dependencies},
        ) from error


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
def test_connection(
    connection_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionTestResponse:
    """Resolve the stored credential and verify the exact PostgreSQL target."""

    try:
        with _service(request).use(principal.user_id, connection_id) as resolved:
            result = request.app.state.services.postgres.test_connection(resolved)
    except ConnectionTargetForbiddenError as error:
        raise _forbidden_target(error) from error
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    return ConnectionTestResponse(
        ok=result.ok,
        database=result.database,
        server_version=result.server_version,
    )


@router.get(
    "/{connection_id}/namespaces",
    response_model=ConnectionNamespaceListResponse,
)
def list_connection_namespaces(
    connection_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionNamespaceListResponse:
    """List bounded namespaces visible to an owner-scoped connection."""

    try:
        with _service(request).use(principal.user_id, connection_id) as resolved:
            namespaces = request.app.state.services.postgres.list_namespaces(resolved)
    except ConnectionTargetForbiddenError as error:
        raise _forbidden_target(error) from error
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    return ConnectionNamespaceListResponse(
        connection_id=resolved.id,
        connection_revision=resolved.revision,
        namespaces=[
            PostgresNamespaceSummary(name=item.name, system=item.system)
            for item in namespaces
        ],
    )


@router.get(
    "/{connection_id}/deletion-impact",
    response_model=ConnectionDeletionImpactResponse,
)
def get_connection_deletion_impact(
    connection_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionDeletionImpactResponse:
    """Preview exact product dependencies before a revision-checked deletion."""

    try:
        impact = _service(request).deletion_impact(
            principal.user_id,
            connection_id,
        )
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    return ConnectionDeletionImpactResponse(
        connection_id=impact.connection_id,
        connection_revision=impact.connection_revision,
        can_delete=not impact.dependencies,
        dependencies=[
            ConnectionDeletionDependency(
                provider=item.provider,
                kind=item.kind,
                resource_id=item.resource_id,
                resource_revision=item.revision,
                name=item.name,
                target=item.target,
                deletion_blocked=item.deletion_blocked,
                blocking_reason=item.blocking_reason,
            )
            for item in impact.dependencies
        ],
        fingerprint=impact.fingerprint,
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: str,
    request: Request,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Delete an unused connection when its expected revision still matches."""

    try:
        _service(request).delete(
            principal.user_id,
            connection_id,
            expected_revision,
        )
    except ConnectionInUseError as error:
        raise ApiProblem(
            409,
            "connection_in_use",
            str(error),
            details={"dependencies": error.dependencies},
        ) from error
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    except ConnectionConflictError as error:
        raise ApiProblem(
            409,
            "connection_conflict",
            str(error),
            details={"currentRevision": error.current_revision},
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
