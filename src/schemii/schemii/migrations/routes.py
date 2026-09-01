"""Planned migration resource routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    MigrationExecution,
    MigrationExecutionCreate,
    MigrationExecutionListResponse,
    MigrationPlan,
    MigrationPlanCreate,
    MigrationReconciliationRequest,
)


router = APIRouter(tags=["schemii-migrations-planned"])


@router.post(
    "/workspaces/{workspace_id}/migration-plans",
    response_model=MigrationPlan,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def create_migration_plan(
    workspace_id: str,
    body: MigrationPlanCreate,
    principal: Principal = Depends(get_current_principal),
) -> MigrationPlan:
    """Compare saved desired state with one repeatable-read live catalog snapshot."""

    # TODO(schemii-migration-plan): Require an attached target, derive complete
    # differences, persist private proofs, and mark incomplete reviews non-executable.
    del workspace_id, body, principal
    planned_capability("schemii.migrations.plan")


@router.get(
    "/migration-plans/{plan_id}",
    response_model=MigrationPlan,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_migration_plan(
    plan_id: str,
    principal: Principal = Depends(get_current_principal),
) -> MigrationPlan:
    """Return the immutable public review document for one owner-scoped plan."""

    # TODO(schemii-migration-plan): Read durable metadata, enforce ownership and
    # expiry, and never disclose private authority or reconstruction evidence.
    del plan_id, principal
    planned_capability("schemii.migrations.plan-status")


@router.post(
    "/migration-plans/{plan_id}/executions",
    response_model=MigrationExecution,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def execute_migration_plan(
    plan_id: str,
    body: MigrationExecutionCreate,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Claim one reviewed plan and create its sole durable execution attempt."""

    # TODO(schemii-migration-execution): Persist confirmation before target I/O,
    # revalidate all bindings under a namespace lock, and execute without CASCADE.
    del plan_id, body, principal
    planned_capability("schemii.migrations.execute")


@router.get(
    "/migration-executions/{execution_id}",
    response_model=MigrationExecution,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_migration_execution(
    execution_id: str,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Read durable progress and terminal commit state without replaying work."""

    # TODO(schemii-migration-execution): Return the authoritative metadata state
    # and explicit reconciliation requirement for interrupted applying attempts.
    del execution_id, principal
    planned_capability("schemii.migrations.execution-status")


@router.post(
    "/migration-executions/{execution_id}/reconciliation",
    response_model=MigrationExecution,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def reconcile_migration_execution(
    execution_id: str,
    body: MigrationReconciliationRequest,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecution:
    """Resolve an uncertain attempt from persisted PostgreSQL transaction evidence."""

    # TODO(schemii-migration-reconcile): Query pg_xact_status for the recorded XID,
    # synchronize saved design only after proven commit, and never execute SQL again.
    del execution_id, body, principal
    planned_capability("schemii.migrations.reconcile")


@router.get(
    "/workspaces/{workspace_id}/migration-executions",
    response_model=MigrationExecutionListResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def list_workspace_migration_executions(
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    principal: Principal = Depends(get_current_principal),
) -> MigrationExecutionListResponse:
    """List recent migration attempts belonging to one workspace."""

    # TODO(schemii-migration-history): Page durable owner/workspace execution
    # summaries without loading private plan payloads or redacted terminal details.
    del workspace_id, limit, principal
    planned_capability("schemii.migrations.history")
