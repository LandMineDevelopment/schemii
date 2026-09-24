"""Provisioner-only lifecycle for Schemii-owned PostgreSQL read-only identities."""

from fastapi import APIRouter, Depends, Query, Request, Response, status

from schemii.common.api.errors import ApiProblem
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.connections.models import (
    Password,
    PostgresConnectionCreate,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    SCHEMII_CONNECTION_OWNER_ID,
)
from schemii.common.connections.policy import ConnectionTargetForbiddenError
from schemii.common.connections.store import (
    ConnectionConflictError,
    ConnectionInUseError,
    ConnectionLimitError,
    ConnectionNotFoundError,
)
from schemii.common.postgres.errors import PostgresGatewayError

from .routes import admin


router = APIRouter(prefix="/api/v1/admin/schemii-connections", tags=["administration"])


class SchemiiConnectionCreate(PostgresConnectionCreate):
    password: Password


def _connections(request: Request):
    return request.app.state.services.connections


def _audit(request: Request, actor: str, action: str, connection_id: str) -> None:
    with request.app.state.auth.store.transaction(write=True) as state:
        state["audit"].append((actor, action, connection_id))


def _profile(request: Request, connection_id: str) -> PostgresConnectionProfile:
    try:
        return _connections(request).get_schemii_owned(connection_id)
    except ConnectionNotFoundError as error:
        raise ApiProblem(404, "connection_not_found", str(error)) from error


@router.get("")
def list_schemii_connections(request: Request, actor: str = Depends(admin)):
    """List only Schemii-owned profiles; never enumerate personal credentials."""
    return {"connections": _connections(request).list_schemii_owned()}


@router.post("", response_model=PostgresConnectionProfile, status_code=status.HTTP_201_CREATED)
def create_schemii_connection(
    body: SchemiiConnectionCreate, request: Request, actor: str = Depends(admin),
):
    try:
        profile = _connections(request).create_schemii_owned(body)
    except ConnectionTargetForbiddenError as error:
        raise ApiProblem(403, error.code, str(error)) from error
    except ConnectionLimitError as error:
        raise ApiProblem(409, "connection_limit_reached", str(error)) from error
    _audit(request, actor, "schemii_connection.create", profile.id)
    return profile


@router.get("/{connection_id}", response_model=PostgresConnectionProfile)
def get_schemii_connection(
    connection_id: str, request: Request, actor: str = Depends(admin),
):
    return _profile(request, connection_id)


@router.patch("/{connection_id}", response_model=PostgresConnectionProfile)
def update_schemii_connection(
    connection_id: str, body: PostgresConnectionUpdate,
    request: Request, actor: str = Depends(admin),
):
    try:
        profile = _connections(request).update_schemii_owned(connection_id, body)
    except ConnectionTargetForbiddenError as error:
        raise ApiProblem(403, error.code, str(error)) from error
    except ConnectionNotFoundError as error:
        raise ApiProblem(404, "connection_not_found", str(error)) from error
    except ConnectionConflictError as error:
        raise ApiProblem(409, "connection_conflict", str(error), details={"currentRevision": error.current_revision}) from error
    except ConnectionInUseError as error:
        raise ApiProblem(409, "connection_target_in_use", str(error), details={"dependencies": error.dependencies}) from error
    _audit(request, actor, "schemii_connection.update", profile.id)
    return profile


@router.post("/{connection_id}/test")
def test_schemii_connection(
    connection_id: str, request: Request, actor: str = Depends(admin),
):
    _profile(request, connection_id)
    try:
        with _connections(request).use(SCHEMII_CONNECTION_OWNER_ID, connection_id) as resolved:
            result = request.app.state.services.postgres.test_connection(resolved)
    except ConnectionTargetForbiddenError as error:
        raise ApiProblem(403, error.code, str(error)) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    return result


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schemii_connection(
    connection_id: str, request: Request,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    actor: str = Depends(admin),
):
    try:
        _connections(request).delete_schemii_owned(connection_id, expected_revision)
    except ConnectionNotFoundError as error:
        raise ApiProblem(404, "connection_not_found", str(error)) from error
    except ConnectionConflictError as error:
        raise ApiProblem(409, "connection_conflict", str(error), details={"currentRevision": error.current_revision}) from error
    except ConnectionInUseError as error:
        raise ApiProblem(409, "connection_in_use", str(error), details={"dependencies": error.dependencies}) from error
    _audit(request, actor, "schemii_connection.delete", connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
