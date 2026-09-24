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
from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.postgres.models import PostgresCatalog
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import SchemiiDesign, SchemiiDesignLayout
from schemii.schemii.designs.store import DesignRepository
from schemii.schemii.migrations.service import MigrationServiceError

from .models import (
    SchemiiWorkspace,
    SchemiiWorkspaceCreate,
    SchemiiPostgresWorkspaceOpen,
    SchemiiWorkspaceLayoutUpdate,
    TableColumnDisplayOrder,
    TablePosition,
    WorkspaceCreateRecord,
    WorkspaceMetadataUpdate,
)
from .store import (
    WorkspaceConflictError,
    WorkspaceLimitError,
    WorkspaceNotFoundError,
    WorkspaceRepository,
    WorkspaceMutationBlockedError,
    WorkspaceTargetExistsError,
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


class SchemiiPostgresWorkspaceResponse(ApiModel):
    """The one personal design for an exact saved PostgreSQL account and schema."""

    created: bool
    workspace: SchemiiWorkspace
    design: SchemiiDesign
    layout: SchemiiDesignLayout


def _workspaces(request: Request) -> WorkspaceRepository:
    return request.app.state.services.workspaces


def _connections(request: Request) -> ConnectionService:
    return request.app.state.services.connections.for_product("schemii")


def _designs(request: Request) -> DesignRepository:
    return request.app.state.services.designs


def _workspace_not_found(error: WorkspaceNotFoundError) -> ApiProblem:
    return ApiProblem(404, "workspace_not_found", str(error))


def _connection_not_found(error: ConnectionNotFoundError) -> ApiProblem:
    return ApiProblem(404, "connection_not_found", str(error))


def _workspace_limit(error: WorkspaceLimitError) -> ApiProblem:
    limit_name = {
        "workspace": "resources.maximum_workspaces_per_user",
        "table position": "workspace_layout.maximum_table_positions",
        "column display order": "workspace_layout.maximum_column_display_orders",
    }.get(error.category, f"workspace.{error.category}")
    return ApiProblem(
        409,
        "workspace_limit_reached",
        f"The {error.category} limit of {error.limit} has been reached. Remove unused {error.category} data or ask the administrator to raise {limit_name}.",
        details={"resource": error.category, "limitName": limit_name, "limit": error.limit, "observed": error.limit},
        limit_event=LimitEventNotice(
            resource=error.category.replace(" ", "_"),
            limit_name=limit_name,
            configured_limit=error.limit,
            observed_value=error.limit,
        ),
    )


def _workspace_conflict(error: WorkspaceConflictError) -> ApiProblem:
    return ApiProblem(
        409,
        "workspace_conflict",
        str(error),
        details={"currentRevision": error.current_revision},
    )


def _reconcile_column_orders(
    workspace: SchemiiWorkspace,
    catalog: PostgresCatalog,
) -> list[TableColumnDisplayOrder]:
    """Keep saved live names in preference order and append new columns by attnum."""

    saved = {order.name: order.columns for order in workspace.column_orders}
    reconciled: list[TableColumnDisplayOrder] = []
    for table in catalog.tables:
        preferred = saved.get(table.name)
        if preferred is None:
            continue
        physical = [
            column.name
            for column in sorted(table.columns, key=lambda item: item.ordinal)
        ]
        live = set(physical)
        ordered = [column for column in preferred if column in live]
        included = set(ordered)
        ordered.extend(column for column in physical if column not in included)
        reconciled.append(TableColumnDisplayOrder(name=table.name, columns=ordered))
    return reconciled


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
    """Create a database-independent editable design."""

    try:
        return _workspaces(request).create(
            principal.user_id,
            WorkspaceCreateRecord(name=body.name),
        )
    except WorkspaceLimitError as error:
        raise _workspace_limit(error) from error


@router.post(
    "/postgres",
    response_model=SchemiiPostgresWorkspaceResponse,
    responses={201: {"description": "A new personal target design was imported."}},
)
def open_postgres_workspace(
    body: SchemiiPostgresWorkspaceOpen,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiPostgresWorkspaceResponse:
    """Open an existing personal target design or import it exactly once."""

    try:
        with _connections(request).use(
            principal.user_id,
            body.connection_id,
        ) as connection:
            existing = _workspaces(request).find_by_target(
                principal.user_id,
                connection.id,
                connection.database,
                body.namespace,
            )
            if existing is not None:
                if (existing.connection_owner_id or principal.user_id) != (getattr(connection, "owner_id", None) or principal.user_id):
                    raise ApiProblem(409, "workspace_target_changed", "The saved workspace uses a different PostgreSQL identity")
                workspace = existing
                created = False
            else:
                catalog = request.app.state.services.postgres.introspect(
                    connection,
                    body.namespace,
                )
                imported = import_postgres_catalog(catalog)
                migrations = request.app.state.services.migrations
                assert migrations is not None
                try:
                    workspace = migrations.create_import_workspace(
                        principal.user_id,
                        WorkspaceCreateRecord(
                            name=f"{connection.database}.{body.namespace}",
                            connection_id=connection.id,
                            connection_owner_id=getattr(connection, "owner_id", None) or principal.user_id,
                            database=connection.database,
                            namespace=body.namespace,
                        ),
                        imported,
                        connection.revision,
                        catalog,
                    )
                    created = True
                    response.status_code = status.HTTP_201_CREATED
                except WorkspaceTargetExistsError as error:
                    workspace = error.workspace
                    created = False
        design = _designs(request).get(principal.user_id, workspace.id)
        layout = _designs(request).get_layout(principal.user_id, workspace.id)
        return SchemiiPostgresWorkspaceResponse(
            created=created,
            workspace=workspace,
            design=design,
            layout=layout,
        )
    except ConnectionNotFoundError as error:
        raise _connection_not_found(error) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    except WorkspaceLimitError as error:
        raise _workspace_limit(error) from error
    except MigrationServiceError as error:
        raise ApiProblem(
            error.status,
            error.code,
            str(error),
            details=error.details,
            retryable=error.retryable,
        ) from error


@router.patch("/{workspace_id}", response_model=SchemiiWorkspace)
def update_workspace_metadata(
    workspace_id: str,
    body: WorkspaceMetadataUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Rename an owner-scoped workspace without altering its database identity."""
    try:
        return _workspaces(request).rename(principal.user_id, workspace_id, body)
    except WorkspaceNotFoundError as error:
        raise _workspace_not_found(error) from error
    except WorkspaceConflictError as error:
        raise _workspace_conflict(error) from error


@router.get("/{workspace_id}", response_model=SchemiiWorkspace)
def get_workspace(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Return one workspace and the target identity fixed when it was created."""

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
        # TODO(schemii-local-layout): Validate positions against persisted
        # desired-design object IDs once the design repository is implemented.
        raise ApiProblem(
            501,
            "planned_capability",
            "Local workspace layout requires the planned design repository",
            details={"capability": "schemii.local-layout", "status": "planned"},
        )
    try:
        with _connections(request).use(
            principal.user_id,
            workspace.connection_id,
        ) as connection:
            if (getattr(connection, "owner_id", None) or principal.user_id) != (getattr(workspace, "connection_owner_id", None) or principal.user_id):
                raise ApiProblem(409, "workspace_target_changed", "The workspace connection identity changed")
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
            if body.column_orders is not None:
                live_columns = {
                    table.name: {column.name for column in table.columns}
                    for table in catalog.tables
                }
                mismatches = []
                for order in body.column_orders:
                    expected = live_columns.get(order.name)
                    if expected is None:
                        mismatches.append(
                            {
                                "table": order.name,
                                "missingColumns": [],
                                "unknownColumns": order.columns,
                            }
                        )
                        continue
                    supplied = set(order.columns)
                    missing = sorted(expected - supplied)
                    unknown = sorted(supplied - expected)
                    if missing or unknown:
                        mismatches.append(
                            {
                                "table": order.name,
                                "missingColumns": missing,
                                "unknownColumns": unknown,
                            }
                        )
                if mismatches:
                    raise ApiProblem(
                        422,
                        "column_order_mismatch",
                        "Custom display order must contain every live table column exactly once",
                        details={"tables": mismatches},
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
    except WorkspaceMutationBlockedError as error:
        raise ApiProblem(
            409,
            "workspace_mutation_blocked",
            str(error),
            details={"operation": error.operation},
        ) from error
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
            "Database catalogs are available only in PostgreSQL-backed workspaces",
        )
    try:
        with _connections(request).use(
            principal.user_id,
            workspace.connection_id,
        ) as connection:
            if (getattr(connection, "owner_id", None) or principal.user_id) != (getattr(workspace, "connection_owner_id", None) or principal.user_id):
                raise ApiProblem(409, "workspace_target_changed", "The workspace connection identity changed")
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
    workspace = workspace.model_copy(
        update={"column_orders": _reconcile_column_orders(workspace, catalog)},
        deep=True,
    )
    return WorkspaceCatalogResponse(
        workspace=workspace,
        catalog=catalog,
        positions=positions,
    )
