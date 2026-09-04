"""Schemii policy routes for bounded SQL Console execution."""

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

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
from .models import (
    ConsoleHistoryList,
    ConsoleSavedQuery,
    ConsoleSavedQueryCreate,
    ConsoleSavedQueryList,
    ConsoleSavedQueryUpdate,
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
        limit_event=error.limit_event,
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


@router.get(
    "/workspaces/{workspace_id}/console/history",
    response_model=ConsoleHistoryList,
)
def list_console_history(
    workspace_id: str,
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=1000),
    principal: Principal = Depends(get_current_principal),
) -> ConsoleHistoryList:
    """List bounded replay-only SQL history for one user-owned workspace."""

    try:
        queries = _service(request).history(
            principal.user_id,
            workspace_id,
            limit,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return ConsoleHistoryList(queries=queries)


@router.get(
    "/workspaces/{workspace_id}/console/saved-queries",
    response_model=ConsoleSavedQueryList,
)
def list_console_saved_queries(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSavedQueryList:
    """List named SQL saved by the current user for an exact workspace target."""

    try:
        queries = _service(request).saved_queries(principal.user_id, workspace_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return ConsoleSavedQueryList(queries=queries)


@router.post(
    "/workspaces/{workspace_id}/console/saved-queries",
    response_model=ConsoleSavedQuery,
    status_code=status.HTTP_201_CREATED,
)
def create_console_saved_query(
    workspace_id: str,
    body: ConsoleSavedQueryCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSavedQuery:
    """Save named SQL in Schemii metadata without modifying the target database."""

    try:
        return _service(request).create_saved_query(
            principal.user_id,
            workspace_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.patch(
    "/workspaces/{workspace_id}/console/saved-queries/{query_id}",
    response_model=ConsoleSavedQuery,
)
def update_console_saved_query(
    workspace_id: str,
    query_id: str,
    body: ConsoleSavedQueryUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSavedQuery:
    """Update named SQL after an optimistic metadata revision check."""

    try:
        return _service(request).update_saved_query(
            principal.user_id,
            workspace_id,
            query_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.delete(
    "/workspaces/{workspace_id}/console/saved-queries/{query_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_console_saved_query(
    workspace_id: str,
    query_id: str,
    request: Request,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Delete one named query after an optimistic metadata revision check."""

    try:
        _service(request).delete_saved_query(
            principal.user_id,
            workspace_id,
            query_id,
            expected_revision,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get(
    "/workspaces/{workspace_id}/console/executions/{execution_id}/results/{result_id}/export.csv",
    response_class=StreamingResponse,
)
def export_console_result_csv(
    workspace_id: str,
    execution_id: str,
    result_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> StreamingResponse:
    """Stream every currently retained result row directly from PostgreSQL."""

    try:
        rows = _service(request).export_csv(
            principal.user_id, workspace_id, execution_id, result_id
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return StreamingResponse(
        rows,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{result_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


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
)
def create_console_transaction(
    workspace_id: str,
    body: ConsoleTransactionCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Open one capacity- and lifetime-bounded explicit PostgreSQL transaction."""
    try:
        return _service(request).create_transaction(
            principal.user_id,
            workspace_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.get(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}",
    response_model=ConsoleTransaction,
)
def get_console_transaction(
    workspace_id: str,
    transaction_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Read explicit transaction state and expiry without extending its lifetime."""
    try:
        return _service(request).get_transaction(
            principal.user_id,
            workspace_id,
            transaction_id,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/executions",
    response_model=ConsoleExecution,
    status_code=status.HTTP_201_CREATED,
)
def execute_console_transaction_statements(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionExecutionCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Run reviewed statements sequentially inside one owned explicit transaction."""
    try:
        execution = _service(request).reserve_transaction_execution(
            principal.user_id,
            workspace_id,
            transaction_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
    background_tasks.add_task(
        _service(request).run_transaction,
        principal.user_id,
        execution.id,
    )
    return execution


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/commit",
    response_model=ConsoleTransaction,
)
def commit_console_transaction(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionCommand,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Close retained results and commit exactly one explicit transaction."""
    try:
        return _service(request).commit_transaction(
            principal.user_id,
            workspace_id,
            transaction_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.post(
    "/workspaces/{workspace_id}/console/transactions/{transaction_id}/rollback",
    response_model=ConsoleTransaction,
)
def rollback_console_transaction(
    workspace_id: str,
    transaction_id: str,
    body: ConsoleTransactionCommand,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleTransaction:
    """Close retained results and roll back exactly one explicit transaction."""
    try:
        return _service(request).rollback_transaction(
            principal.user_id,
            workspace_id,
            transaction_id,
            body,
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error
