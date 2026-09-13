"""Human-reviewed write jobs. Deliberately absent from AI action adapters."""
from fastapi import APIRouter, Depends, Query, Request, Response
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.query_executions.errors import ConsoleServiceError
from schemii.common.api.errors import ApiProblem
from .models import JobCreate, JobCommand, ReconcileCommand, ResumeCommand
from .service import BulkJobService
from contextlib import contextmanager

router = APIRouter(prefix="/api/v1/schemii/workspaces/{workspace_id}/console/bulk-jobs", tags=["schemii-bulk-jobs"])


def service(request: Request) -> BulkJobService:
    return request.app.state.bulk_jobs


@contextmanager
def map_errors():
    try:
        yield
    except ConsoleServiceError as error:
        raise ApiProblem(error.status, error.code, str(error), details=error.details) from error


@router.get("")
def list_jobs(workspace_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with map_errors():
        return service(request).list(principal.user_id, workspace_id)


@router.post("", status_code=201)
def create_job(workspace_id: str, body: JobCreate, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    jobs = service(request)
    with map_errors():
        value = jobs.create_started(principal.user_id, workspace_id, body)
    return value


@router.get("/{job_id}")
def get_job(workspace_id: str, job_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with map_errors():
        return service(request).get(principal.user_id, workspace_id, job_id)


@router.post("/{job_id}/cancel")
def cancel_job(workspace_id: str, job_id: str, body: JobCommand, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with map_errors():
        return service(request).command(principal.user_id, workspace_id, job_id, body.expected_revision, "cancel")


@router.post("/{job_id}/resume")
def resume_job(workspace_id: str, job_id: str, body: ResumeCommand, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    jobs = service(request)
    with map_errors():
        value = jobs.resume_started(principal.user_id, workspace_id, job_id, body)
    return value


@router.post("/{job_id}/reconcile")
def reconcile_job(workspace_id: str, job_id: str, body: ReconcileCommand, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with map_errors():
        return service(request).command(principal.user_id, workspace_id, job_id, body.expected_revision, "reconcile", body.outcome)


@router.delete("/{job_id}", status_code=204)
def delete_job(workspace_id: str, job_id: str, request: Request, expected_revision: int = Query(alias="expectedRevision", ge=1), principal: Principal = Depends(get_current_principal)) -> Response:
    with map_errors():
        service(request).delete(principal.user_id, workspace_id, job_id, expected_revision)
    return Response(status_code=204)
