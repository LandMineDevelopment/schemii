"""Shared result lifecycle; products authorize and compile their own executions.

There is intentionally no public arbitrary-SQL creation route here. A caller
must go through its product's source, model, and permission validation first.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.console import ConsoleExecution, ConsoleResultPage
from .errors import ConsoleServiceError


router = APIRouter(tags=["common-query-executions"])


def _service(request: Request, execution_id=None, *, read=False, owner_id=None):
    service = request.app.state.services.console
    if service is None:
        raise ApiProblem(503, "query_execution_unavailable", "Query execution is unavailable")
    if read and service.is_shared_report_execution(execution_id):
        raise ApiProblem(403, "report_result_private", "Shared report results are available only through their authorized report stream.")
    # Schemoo's owner-private result route is also used by models backed by a
    # role-managed credential. Recheck the role before exposing retained rows;
    # the original SQL may have run before access was revoked.
    if read and owner_id is not None and execution_id is not None:
        try:
            receipt = service.get_owned(owner_id, execution_id)
            connections = request.app.state.services.connections
            if hasattr(connections, "for_product"):
                service.authorize_managed_result(
                    owner_id, execution_id,
                    connections.for_product("schemoo" if receipt.workspace_id is None else "schemii"))
        except ConsoleServiceError as error:
            raise _problem(error) from error
    return service


def _problem(error: ConsoleServiceError) -> ApiProblem:
    return ApiProblem(
        error.status, error.code, str(error), details=error.details,
        retryable=error.retryable, limit_event=error.limit_event,
    )


@router.get("/query-executions/{execution_id}", response_model=ConsoleExecution)
def get_execution(
    execution_id: str, request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Read one owned execution receipt without rerunning its SQL."""
    try:
        return _service(request, execution_id, read=True, owner_id=principal.user_id).get_owned(principal.user_id, execution_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.delete("/query-executions/{execution_id}", response_model=ConsoleExecution)
def cancel_execution(
    execution_id: str, request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleExecution:
    """Cancel a retained execution using its original, owner-bound target."""
    service = _service(request)
    try:
        receipt = service.get_owned(principal.user_id, execution_id)
        return service.cancel(principal.user_id, receipt.workspace_id, execution_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.get(
    "/query-executions/{execution_id}/results/{result_id}",
    response_model=ConsoleResultPage,
)
def get_result_page(
    execution_id: str, result_id: str, request: Request,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    page_size: Annotated[int | None, Query(ge=1)] = None,
    principal: Principal = Depends(get_current_principal),
) -> ConsoleResultPage:
    """Page a transient result with the shared cursor and memory-limit policy."""
    service = _service(request, execution_id, read=True, owner_id=principal.user_id)
    try:
        receipt = service.get_owned(principal.user_id, execution_id)
        return service.page(
            principal.user_id, receipt.workspace_id, execution_id, result_id, cursor,
            **({"page_size": page_size} if page_size is not None else {}),
        )
    except ConsoleServiceError as error:
        raise _problem(error) from error


@router.delete("/query-executions/{execution_id}/results/{result_id}", status_code=204)
def close_result(
    execution_id: str, result_id: str, request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Release this result's retained database resources and transient rows."""
    service = _service(request)
    try:
        receipt = service.get_owned(principal.user_id, execution_id)
        service.close_result(principal.user_id, receipt.workspace_id, execution_id, result_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return Response(status_code=204)


@router.get("/query-executions/{execution_id}/results/{result_id}/export.csv")
def export_result(
    execution_id: str, result_id: str, request: Request,
    principal: Principal = Depends(get_current_principal),
) -> StreamingResponse:
    """Stream the result directly, without persisting its rows in metadata."""
    service = _service(request, execution_id, read=True, owner_id=principal.user_id)
    try:
        receipt = service.get_owned(principal.user_id, execution_id)
        rows = service.export_csv(principal.user_id, receipt.workspace_id, execution_id, result_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error
    return StreamingResponse(
        rows, media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{result_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/query-executions/{execution_id}/activity")
def get_execution_activity(
    execution_id: str, request: Request,
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Inspect only this owner's execution, using bounded independent monitoring."""
    try:
        return _service(request, execution_id, read=True, owner_id=principal.user_id).activity(principal.user_id, execution_id)
    except ConsoleServiceError as error:
        raise _problem(error) from error
