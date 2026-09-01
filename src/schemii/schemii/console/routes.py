"""Planned Schemii policy layer over the future shared SQL Console engine."""

from fastapi import APIRouter, Depends, Query, Response, status

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal
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


router = APIRouter(tags=["schemii-sql-console-planned"])


@router.get(
    "/console/settings",
    response_model=ConsoleSettings,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_console_settings(
    principal: Principal = Depends(get_current_principal),
) -> ConsoleSettings:
    """Read Schemii's human SQL Console defaults and durable write intent."""

    # TODO(console-settings): Store one optimistic settings record per owner and
    # application without sharing write intent with Schemer or AI policies.
    del principal
    planned_capability("schemii.console.settings.read")


@router.put(
    "/console/settings",
    response_model=ConsoleSettings,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
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
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def create_console_execution(
    workspace_id: str,
    body: ConsoleExecutionCreate,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Reserve and run a reviewed script against the attached workspace target."""

    # TODO(console-execution): Build the shared admission/execution service,
    # bind target and settings fingerprints, and persist pre-dispatch reservations.
    del workspace_id, body, principal
    planned_capability("schemii.console.execute")


@router.get(
    "/workspaces/{workspace_id}/console/executions/{execution_id}",
    response_model=ConsoleExecution,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_console_execution(
    workspace_id: str,
    execution_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Read exact execution state without rerunning SQL."""

    # TODO(console-execution): Resolve one owner/workspace receipt and report
    # explicit partial-commit or uncertain outcomes without optimistic promotion.
    del workspace_id, execution_id, principal
    planned_capability("schemii.console.execution-status")


@router.delete(
    "/workspaces/{workspace_id}/console/executions/{execution_id}",
    response_model=ConsoleExecution,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def cancel_console_execution(
    workspace_id: str,
    execution_id: str,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Request cancellation and return the resulting execution receipt."""

    # TODO(console-cancellation): Signal the exact backend PID when safe, retain
    # the durable receipt, and distinguish cancelled from already committed work.
    del workspace_id, execution_id, principal
    planned_capability("schemii.console.cancel")


@router.get(
    "/workspaces/{workspace_id}/console/executions/{execution_id}/results/{result_id}",
    response_model=ConsoleResultPage,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_console_result_page(
    workspace_id: str,
    execution_id: str,
    result_id: str,
    cursor: str = Query(min_length=1, max_length=512),
    principal: Principal = Depends(get_current_principal),
) -> ConsoleResultPage:
    """Advance one owner-bound result cursor within its original snapshot or spool."""

    # TODO(console-results): Enforce single-advance opaque cursors, resource TTL,
    # page/byte limits, and no query replay after the retained result is closed.
    del workspace_id, execution_id, result_id, cursor, principal
    planned_capability("schemii.console.results.page")


@router.delete(
    "/workspaces/{workspace_id}/console/executions/{execution_id}/results/{result_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def close_console_result(
    workspace_id: str,
    execution_id: str,
    result_id: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Release a retained result snapshot or bounded spool deterministically."""

    # TODO(console-results): Authorize the complete ownership binding, close the
    # cursor/spool exactly once, and make later reads return a stable gone error.
    del workspace_id, execution_id, result_id, principal
    planned_capability("schemii.console.results.close")


@router.post(
    "/workspaces/{workspace_id}/console/transactions",
    response_model=ConsoleTransaction,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
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
