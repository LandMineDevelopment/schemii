"""Workspace-bound live database browser routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from schemii.common.api.errors import ApiProblem
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.schemii.workspaces.store import WorkspaceNotFoundError

from .models import (
    RelationDetailResponse,
    RelationLineageResponse,
    RelationListResponse,
    RelationRef,
    RelationRowPage,
)
from .service import RelationBrowserError, RelationBrowserService


router = APIRouter(
    prefix="/workspaces/{workspace_id}/relations",
    tags=["schemii-database-browser"],
)


def _service(request: Request) -> RelationBrowserService:
    services = request.app.state.services
    return RelationBrowserService(workspaces=services.workspaces, connections=services.connections, postgres=services.postgres)


def _problem(error: Exception) -> ApiProblem:
    if isinstance(error, WorkspaceNotFoundError):
        return ApiProblem(404, "workspace_not_found", str(error))
    if isinstance(error, ConnectionNotFoundError):
        return ApiProblem(404, "connection_not_found", str(error))
    if isinstance(error, RelationBrowserError):
        return ApiProblem(error.status, error.code, str(error))
    if isinstance(error, PostgresGatewayError):
        return postgres_api_problem(error)
    raise error


@router.get(
    "",
    response_model=RelationListResponse,
)
def list_workspace_relations(
    workspace_id: str,
    request: Request,
    cursor: str | None = Query(default=None, max_length=512),
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=250)] = 100,
    search: str | None = Query(default=None, max_length=256),
    principal: Principal = Depends(get_current_principal),
) -> RelationListResponse:
    """Page live relation summaries for an attached workspace target."""

    try:
        return _service(request).list(principal.user_id, workspace_id, cursor=cursor, page_size=page_size, search=search)
    except Exception as error:
        raise _problem(error) from error


@router.get(
    "/{relation_ref}",
    response_model=RelationDetailResponse,
)
def get_workspace_relation(
    workspace_id: str,
    relation_ref: RelationRef,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> RelationDetailResponse:
    """Resolve an opaque relation reference into a fresh detailed descriptor."""

    try:
        return _service(request).detail(principal.user_id, workspace_id, relation_ref)
    except Exception as error:
        raise _problem(error) from error


@router.get(
    "/{relation_ref}/lineage",
    response_model=RelationLineageResponse,
)
def get_workspace_relation_lineage(
    workspace_id: str,
    relation_ref: RelationRef,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> RelationLineageResponse:
    """Return verified dependency and column-lineage evidence for one live relation."""

    try:
        return _service(request).lineage(principal.user_id, workspace_id, relation_ref)
    except Exception as error:
        raise _problem(error) from error


@router.get(
    "/{relation_ref}/rows",
    response_model=RelationRowPage,
)
def preview_workspace_relation_rows(
    workspace_id: str,
    relation_ref: RelationRef,
    request: Request,
    cursor: str | None = Query(default=None, max_length=512),
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=250)] = 100,
    principal: Principal = Depends(get_current_principal),
) -> RelationRowPage:
    """Read a bounded page without rerunning or broadening the selected relation."""

    try:
        return _service(request).rows(principal.user_id, workspace_id, relation_ref, cursor=cursor, page_size=page_size)
    except Exception as error:
        raise _problem(error) from error
