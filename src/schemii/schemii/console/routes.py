"""Schemii policy routes for bounded SQL Console execution."""

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.api.errors import ApiProblem
from schemii.common.postgres.console import (
    ConsoleExecution,
    ConsoleExecutionCreate,
    ConsoleResultPage,
    ConsoleSettings,
    ConsoleSettingsUpdate,
    ConsoleTransaction,
    ConsoleTransactionCommand,
    ConsoleTransactionCreate,
    ConsoleTransactionExecutionCreate,
)
from .service import ConsoleService, ConsoleServiceError


router = APIRouter(tags=["schemii-sql-console"])


def _service(request: Request) -> ConsoleService:
    service = request.app.state.services.console
    assert service is not None
    return service


def _problem(error: ConsoleServiceError) -> ApiProblem:
    return ApiProblem(
        error.status,
        error.code,
        str(error),
        details=error.details,
        retryable=error.retryable,
    )


@router.get(
    "/console/settings",
    response_model=ConsoleSettings,
)
def get_console_settings(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSettings:
    """Read Schemii's human SQL Console defaults and durable write intent."""
    return _service(request).settings(principal.user_id)


@router.put(
    "/console/settings",
    response_model=ConsoleSettings,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def update_console_settings(
    body: ConsoleSettingsUpdate,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSettings:
    """Replace bounded SQL Console settings after an optimistic revision check."""

    # TODO(console-settings): Persist application-scoped settings and audit every
    # write-intent transition independently from statement execution.
    del body, principal
    planned_capability("schemii.console.settings.update")


@router.post(
    "/workspaces/{workspace_id}/console/executions",
    response_model=ConsoleExecution,
    status_code=status.HTTP_201_CREATED,
)
def create_console_execution(
    workspace_id: str,
    body: ConsoleExecutionCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Reserve and run a reviewed script against the workspace's fixed target."""
    try:
        execution = _service(request).reserve(principal.user_id, workspace_id, body)
    except ConsoleServiceError as error:
        raise _problem(error) from error
    background_tasks.add_task(
        _service(request).run,
        principal.user_id,
        execution.id,
    )
    return execution


@router.get(
    "/workspaces/{workspace_id}/console/executions/{execution_id}",
    response_model=ConsoleExecution,
)
def get_console_execution(
    workspace_id: str,
    execution_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Read exact execution state without rerunning SQL."""
    try:
        return _service(request).get(principal.user_id, workspace_id, execution_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.delete(
    "/workspaces/{workspace_id}/console/executions/{execution_id}",
    response_model=ConsoleExecution,
)
def cancel_console_execution(
    workspace_id: str,
    execution_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Request cancellation and return the resulting execution receipt."""
    try:
        return _service(request).cancel(principal.user_id, workspace_id, execution_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.get(
    "/workspaces/{workspace_id}/console/executions/{execution_id}/results/{result_id}",
    response_model=ConsoleResultPage,
)
def get_console_result_page(
    workspace_id: str,
    execution_id: str,
    result_id: str,
    request: Request,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    principal: Principal = Depends(get_current_principal),
) -> ConsoleResultPage:
    """Advance one owner-bound result cursor within its original snapshot or spool."""
    try:
        return _service(request).page(
            principal.user_id, workspace_id, execution_id, result_id, cursor
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.delete(
    "/workspaces/{workspace_id}/console/executions/{execution_id}/results/{result_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def close_console_result(
    workspace_id: str,
    execution_id: str,
    result_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Release a retained result snapshot or bounded spool deterministically."""
    try:
        _service(request).close_result(
            principal.user_id, workspace_id, execution_id, result_id
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/console/transactions",
    response_model=ConsoleTransaction,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def create_console_transaction(
    workspace_id: str,
    body: ConsoleTransactionCreate,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Open one capacity- and lifetime-bounded explicit PostgreSQL transaction."""

    # TODO(console-transaction): Reserve target capacity, open the connection,
    # bind owner/workspace/settings, and publish only after transaction setup.
    del workspace_id, body, principal
    planned_capability("schemii.console.transaction.open")


@router.get(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}",
    response_model=ConsoleTransaction,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def get_console_transaction(
    workspace_id: str,
    transaction_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Read explicit transaction state and expiry without extending its lifetime."""

    # TODO(console-transaction): Resolve process-local state through a durable
    # ownership receipt and expire abandoned transactions with rollback.
    del workspace_id, transaction_id, principal
    planned_capability("schemii.console.transaction-status")


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/executions",
    response_model=ConsoleExecution,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def execute_console_transaction_statements(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionExecutionCreate,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Run reviewed statements sequentially inside one owned explicit transaction."""

    # TODO(console-transaction): Serialize commands per transaction, retain result
    # resources, and move failed transactions into PostgreSQL's aborted state.
    del workspace_id, transaction_id, body, principal
    planned_capability("schemii.console.transaction-execute")


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/commit",
    response_model=ConsoleTransaction,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def commit_console_transaction(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionCommand,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Close retained results and commit exactly one explicit transaction."""

    # TODO(console-transaction): Persist command intent, close results, commit once,
    # and report lost acknowledgement as uncertain instead of retrying.
    del workspace_id, transaction_id, body, principal
    planned_capability("schemii.console.transaction-commit")


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/rollback",
    response_model=ConsoleTransaction,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
    tags=["schemii-sql-console-planned"],
)
def rollback_console_transaction(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionCommand,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Close retained results and roll back exactly one explicit transaction."""

    # TODO(console-transaction): Persist command intent, close results, roll back,
    # and release target capacity through one idempotent state transition.
    del workspace_id, transaction_id, body, principal
    planned_capability("schemii.console.transaction-rollback")
