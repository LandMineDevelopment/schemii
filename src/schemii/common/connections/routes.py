"""Shared FastAPI routes for owner-scoped PostgreSQL connections."""

from fastapi import APIRouter, Depends, Query, Request, Response, status

from schemii.common.api.errors import ApiProblem
from schemii.common.api.models import ApiModel
from schemii.common.api.postgres import postgres_api_problem
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
    connections: list[PostgresConnectionProfile]


class ConnectionTestResponse(ApiModel):
    ok: bool
    database: str
    server_version: str


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
    return ConnectionListResponse(
        connections=_service(request).list(principal.user_id)
    )


@router.post("", response_model=PostgresConnectionProfile, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: PostgresConnectionCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PostgresConnectionProfile:
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


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: str,
    request: Request,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    principal: Principal = Depends(get_current_principal),
) -> Response:
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
