"""Server-authoritative migration planning and execution routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    MigrationDriftResolution,
    MigrationDriftResolutionRequest,
    MigrationExecution,
    MigrationExecutionCreate,
    MigrationExecutionListResponse,
    MigrationPlan,
    MigrationPlanCreate,
    MigrationReconciliationRequest,
)
from .service import MigrationService, MigrationServiceError


router = APIRouter(tags=["schemii-migrations"])


def _service(request: Request) -> MigrationService:
    service = request.app.state.services.migrations
    assert service is not None
    return service


def _problem(error: MigrationServiceError) -> ApiProblem:
    return ApiProblem(
        error.status,
        error.code,
        str(error),
        details=error.details,
        retryable=error.retryable,
    )


@router.post(
    "/workspaces/{workspace_id}/migration-plans",
    response_model=MigrationPlan,
    status_code=status.HTTP_201_CREATED,
)
def create_migration_plan(
    workspace_id: str,
    body: MigrationPlanCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationPlan:
    """Derive and retain a three-way review from server-owned state."""

    try:
        return _service(request).create_plan(principal.user_id, workspace_id, body)
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.get("/migration-plans/{plan_id}", response_model=MigrationPlan)
def get_migration_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationPlan:
    """Return one immutable owner-scoped migration review."""

    try:
        return _service(request).get_plan(principal.user_id, plan_id)
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.post(
    "/migration-plans/{plan_id}/drift-resolutions",
    response_model=MigrationDriftResolution,
    status_code=status.HTTP_201_CREATED,
)
def resolve_migration_drift(
    plan_id: str,
    body: MigrationDriftResolutionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationDriftResolution:
    """Pull or acknowledge every server-derived external-change conflict."""

    try:
        return _service(request).resolve_drift(principal.user_id, plan_id, body)
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.post(
    "/migration-plans/{plan_id}/executions",
    response_model=MigrationExecution,
    status_code=status.HTTP_201_CREATED,
)
def execute_migration_plan(
    plan_id: str,
    body: MigrationExecutionCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Execute exactly one claimed server-owned reviewed plan."""

    try:
        return _service(request).create_execution(principal.user_id, plan_id, body)
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.get(
    "/migration-executions/{execution_id}",
    response_model=MigrationExecution,
)
def get_migration_execution(
    execution_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Read durable progress and terminal commit state without replay."""

    try:
        return _service(request).get_execution(principal.user_id, execution_id)
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.post(
    "/migration-executions/{execution_id}/reconciliation",
    response_model=MigrationExecution,
)
def reconcile_migration_execution(
    execution_id: str,
    body: MigrationReconciliationRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Resolve an uncertain target transaction without replaying its SQL."""

    try:
        return _service(request).reconcile_execution(
            principal.user_id,
            execution_id,
            body,
        )
    except MigrationServiceError as error:
        raise _problem(error) from error


@router.get(
    "/workspaces/{workspace_id}/migration-executions",
    response_model=MigrationExecutionListResponse,
)
def list_workspace_migration_executions(
    workspace_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecutionListResponse:
    """List recent owner-scoped migration attempts for one workspace."""

    try:
        return MigrationExecutionListResponse(
            executions=_service(request).list_executions(
                principal.user_id,
                workspace_id,
                limit,
            )
        )
    except MigrationServiceError as error:
        raise _problem(error) from error
