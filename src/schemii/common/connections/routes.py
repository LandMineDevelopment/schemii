"""Shared FastAPI routes for owner-scoped PostgreSQL connections."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field

from schemii.common.api.errors import ApiProblem
from schemii.common.api.models import ApiModel
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.errors import PostgresGatewayError

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
)
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


class ConnectionDeletionImpactResponse(ApiModel):
    """Current owner-scoped resources preventing connection deletion."""

    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    connection_revision: Annotated[int, Field(strict=True, ge=1)]
    dependencies: dict[str, Annotated[int, Field(strict=True, ge=0)]]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


router = APIRouter(prefix="/api/v1/connections", tags=["connections"])


def _service(request: Request) -> ConnectionService:
    return request.app.state.services.connections


def _not_found(error: ConnectionNotFoundError) -> ApiProblem:
    return ApiProblem(404, "connection_not_found", str(error))


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
    except ConnectionLimitError as error:
        raise ApiProblem(
            409,
            "connection_limit_reached",
            str(error),
            details={"limit": error.limit},
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
    except ConnectionNotFoundError as error:
        raise _not_found(error) from error
    except ConnectionConflictError as error:
        raise ApiProblem(
            409,
            "connection_conflict",
            str(error),
            details={"currentRevision": error.current_revision},
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
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def list_connection_namespaces(
    connection_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionNamespaceListResponse:
    """List bounded namespaces visible to an owner-scoped connection."""

    # TODO(postgres-namespace-list): Add a bounded read-only gateway operation,
    # bind its result to the resolved connection revision, and exclude temp schemas.
    del connection_id, principal
    planned_capability("connections.namespaces")


@router.get(
    "/{connection_id}/deletion-impact",
    response_model=ConnectionDeletionImpactResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_connection_deletion_impact(
    connection_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ConnectionDeletionImpactResponse:
    """Preview exact product dependencies before a revision-checked deletion."""

    # TODO(connection-deletion-impact): Collect owner-scoped workspace, plan,
    # execution, Console, and AI references in one coherent metadata snapshot.
    del connection_id, principal
    planned_capability("connections.deletion-impact")


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
