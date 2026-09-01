"""Schemii workspace lifecycle routes and retained prototype adapters."""

from fastapi import APIRouter, Depends, Query, Request, Response, status

from schemii.common.api.errors import ApiProblem
from schemii.common.api.models import ApiModel
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import (
    ConnectionNotFoundError,
)
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.postgres.models import PostgresCatalog

from .models import (
    SchemiiWorkspace,
    SchemiiWorkspaceCreate,
    SchemiiWorkspaceLayoutUpdate,
    TablePosition,
)
from .store import (
    WorkspaceConflictError,
    WorkspaceLimitError,
    WorkspaceNotFoundError,
    WorkspaceRepository,
)


router = APIRouter(prefix="/workspaces", tags=["schemii-workspaces"])
legacy_design_router = APIRouter(
    prefix="/workspaces",
    tags=["schemii-schema-design"],
)
legacy_database_browser_router = APIRouter(
    prefix="/workspaces",
    tags=["schemii-database-browser"],
)


class WorkspaceListResponse(ApiModel):
    """Owner-visible Schemii workspaces."""

    workspaces: list[SchemiiWorkspace]


class WorkspaceCatalogResponse(ApiModel):
    """Live PostgreSQL catalog paired with a workspace's usable positions."""

    workspace: SchemiiWorkspace
    catalog: PostgresCatalog
    positions: list[TablePosition]


def _workspaces(request: Request) -> WorkspaceRepository:
    return request.app.state.services.workspaces


def _connections(request: Request) -> ConnectionService:
    return request.app.state.services.connections


def _workspace_not_found(error: WorkspaceNotFoundError) -> ApiProblem:
    return ApiProblem(404, "workspace_not_found", str(error))


def _connection_not_found(error: ConnectionNotFoundError) -> ApiProblem:
    return ApiProblem(404, "connection_not_found", str(error))


def _workspace_limit(error: WorkspaceLimitError) -> ApiProblem:
    return ApiProblem(
        409,
        "workspace_limit_reached",
        str(error),
        details={"category": error.category, "limit": error.limit},
    )


def _workspace_conflict(error: WorkspaceConflictError) -> ApiProblem:
    return ApiProblem(
        409,
        "workspace_conflict",
        str(error),
        details={"currentRevision": error.current_revision},
    )


@router.get("", response_model=WorkspaceListResponse)
def list_workspaces(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> WorkspaceListResponse:
    """List Schemii workspaces belonging to the current owner."""

    return WorkspaceListResponse(
        workspaces=_workspaces(request).list(principal.user_id)
    )


@router.post("", response_model=SchemiiWorkspace, status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: SchemiiWorkspaceCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Create a detached design or verify and attach its initial PostgreSQL target."""

    try:
        if body.connection_id is None:
            return _workspaces(request).create(principal.user_id, body)
        with _connections(request).use(
            principal.user_id,
            body.connection_id,
        ) as connection:
            if connection.database != body.database:
                raise ApiProblem(
                    409,
                    "workspace_database_mismatch",
                    "The connection does not target the requested workspace database",
                )
            namespace_exists = request.app.state.services.postgres.namespace_exists(
                connection,
                body.namespace,
            )
            if not namespace_exists:
                raise ApiProblem(
                    404,
                    "postgres_namespace_not_found",
                    "The requested PostgreSQL namespace was not found",
                )
            return _workspaces(request).create(principal.user_id, body)
    except ConnectionNotFoundError as error:
        raise _connection_not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    except WorkspaceLimitError as error:
        raise _workspace_limit(error) from error


@router.get("/{workspace_id}", response_model=SchemiiWorkspace)
def get_workspace(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Return one owner-scoped workspace and its optional target binding."""

    try:
        return _workspaces(request).get(principal.user_id, workspace_id)
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error


@legacy_design_router.put(
    "/{workspace_id}/layout",
    response_model=SchemiiWorkspace,
    deprecated=True,
)
def update_workspace_layout(
    workspace_id: str,
    body: SchemiiWorkspaceLayoutUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Validate revisions and live table names before saving layout positions."""

    try:
        workspace = _workspaces(request).get(principal.user_id, workspace_id)
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error
    if workspace.revision != body.expected_revision:
        raise _workspace_conflict(WorkspaceConflictError(workspace.revision))
    if workspace.connection_id is None:
        # TODO(schemii-detached-layout): Validate positions against persisted
        # desired-design object IDs once the design repository is implemented.
        raise ApiProblem(
            501,
            "planned_capability",
            "Detached workspace layout requires the planned design repository",
            details={"capability": "schemii.detached-layout", "status": "planned"},
        )
    try:
        with _connections(request).use(
            principal.user_id,
            workspace.connection_id,
        ) as connection:
            if connection.revision != body.expected_connection_revision:
                raise ApiProblem(
                    409,
                    "connection_conflict",
                    "The workspace connection changed before the layout could be saved",
                    details={"currentRevision": connection.revision},
                )
            if connection.database != workspace.database:
                raise ApiProblem(
                    409,
                    "workspace_target_changed",
                    "The workspace connection no longer targets its saved database",
                )
            catalog = request.app.state.services.postgres.introspect(
                connection,
                workspace.namespace,
            )
            live_tables = {table.name for table in catalog.tables}
            unknown_tables = sorted(
                table.name for table in body.tables if table.name not in live_tables
            )
            if unknown_tables:
                raise ApiProblem(
                    422,
                    "table_not_found",
                    "Layout positions may reference only live PostgreSQL tables",
                    details={"tables": unknown_tables},
                )
            return _workspaces(request).update_layout(
                principal.user_id,
                workspace_id,
                body,
            )
    except ConnectionNotFoundError as error:
        raise _connection_not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error
    except WorkspaceConflictError as error:
        raise _workspace_conflict(error) from error
    except WorkspaceLimitError as error:
        raise _workspace_limit(error) from error


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: str,
    request: Request,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Delete a workspace when its expected revision still matches."""

    try:
        _workspaces(request).delete(
            principal.user_id,
            workspace_id,
            expected_revision,
        )
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error
    except WorkspaceConflictError as error:
        raise _workspace_conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@legacy_database_browser_router.get(
    "/{workspace_id}/catalog",
    response_model=WorkspaceCatalogResponse,
)
def get_workspace_catalog(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> WorkspaceCatalogResponse:
    """Read the live PostgreSQL catalog and pair it with valid saved positions."""

    try:
        workspace = _workspaces(request).get(principal.user_id, workspace_id)
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error
    if workspace.connection_id is None:
        raise ApiProblem(
            409,
            "workspace_target_required",
            "Attach a PostgreSQL target before reading a live catalog",
        )
    try:
        with _connections(request).use(
            principal.user_id,
            workspace.connection_id,
        ) as connection:
            if connection.database != workspace.database:
                raise ApiProblem(
                    409,
                    "workspace_target_changed",
                    "The workspace connection no longer targets its saved database",
                )
            catalog = request.app.state.services.postgres.introspect(
                connection,
                workspace.namespace,
            )
    except ConnectionNotFoundError as error:
        raise _connection_not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    live_tables = {table.name for table in catalog.tables}
    positions = [
        position
        for position in workspace.tables
        if position.name in live_tables
    ]
    return WorkspaceCatalogResponse(
        workspace=workspace,
        catalog=catalog,
        positions=positions,
    )
