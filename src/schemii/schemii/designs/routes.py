"""Database-independent desired-schema routes."""

from fastapi import APIRouter, Depends, Request, status

from schemii.common.api.errors import ApiProblem
from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.query_analysis import QueryDefinitionError
from schemii.common.postgres.routine_analysis import (
    RoutineDefinitionError,
    analyze_routine_definition,
)
from schemii.common.postgres.trigger_analysis import (
    TriggerDefinitionError,
    analyze_trigger_definition,
)
from schemii.common.postgres.type_analysis import (
    TypeDefinitionError,
    analyze_type_definition,
)
from schemii.schemii.workspaces.store import WorkspaceNotFoundError, WorkspaceRepository

from .export import export_design
from .models import (
    SchemiiDesign,
    SchemiiDesignExport,
    SchemiiDesignExportRequest,
    SchemiiDesignImportRequest,
    SchemiiDesignLayout,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
    DesignRoutineAnalysis,
    DesignRoutineAnalysisRequest,
    DesignTriggerAnalysis,
    DesignTriggerAnalysisRequest,
    DesignTypeAnalysis,
    DesignTypeAnalysisRequest,
    DesignViewAnalysis,
    DesignViewAnalysisRequest,
)
from .store import (
    DesignConflictError,
    DesignLayoutConflictError,
    DesignRepository,
    DesignValidationError,
    DesignWorkspaceNotFoundError,
)
from .view_analysis import analyze_design_view


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["schemii-schema-design"],
)


def _designs(request: Request) -> DesignRepository:
    return request.app.state.services.designs


def _workspaces(request: Request) -> WorkspaceRepository:
    return request.app.state.services.workspaces


def _require_workspace(request: Request, owner_id: str, workspace_id: str) -> None:
    try:
        _workspaces(request).get(owner_id, workspace_id)
    except WorkspaceNotFoundError as error:
        raise ApiProblem(404, "workspace_not_found", str(error)) from error


def _design_not_found(error: DesignWorkspaceNotFoundError) -> ApiProblem:
    return ApiProblem(404, "workspace_not_found", str(error))


def _design_conflict(error: DesignConflictError) -> ApiProblem:
    return ApiProblem(
        409,
        "design_conflict",
        str(error),
        details={"currentRevision": error.current_revision},
    )


def _layout_conflict(error: DesignLayoutConflictError) -> ApiProblem:
    return ApiProblem(
        409,
        "design_layout_conflict",
        str(error),
        details={
            "currentLayoutRevision": error.current_layout_revision,
            "currentDesignRevision": error.current_design_revision,
        },
    )


def _invalid_design(error: DesignValidationError) -> ApiProblem:
    return ApiProblem(422, "invalid_design", str(error), details=error.details)


@router.get("/design", response_model=SchemiiDesign)
def get_workspace_design(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesign:
    """Return saved desired state without contacting an optional target database."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        return _designs(request).get(principal.user_id, workspace_id)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error


@router.put("/design", response_model=SchemiiDesign)
def replace_workspace_design(
    workspace_id: str,
    body: SchemiiDesignReplace,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesign:
    """Validate references and atomically replace one desired-design revision."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        return _designs(request).replace(principal.user_id, workspace_id, body)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error
    except DesignConflictError as error:
        raise _design_conflict(error) from error
    except DesignValidationError as error:
        raise _invalid_design(error) from error


@router.post("/design/type-analysis", response_model=DesignTypeAnalysis)
def analyze_workspace_type(
    workspace_id: str,
    body: DesignTypeAnalysisRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignTypeAnalysis:
    """Derive an enum or domain contract from SQL without contacting PostgreSQL."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        contract = analyze_type_definition(body.definition)
    except TypeDefinitionError as error:
        raise ApiProblem(
            422,
            "invalid_type_definition",
            str(error),
            details={"reason": error.code},
        ) from error
    return DesignTypeAnalysis.model_validate(
        {
            "name": contract.name,
            "kind": contract.kind,
            "enumValues": list(contract.enum_values),
            "baseType": contract.base_type,
            "defaultExpression": contract.default_expression,
            "notNull": contract.not_null,
            "checks": [
                {"name": check.name, "expression": check.expression}
                for check in contract.checks
            ],
            "collation": contract.collation,
        }
    )


@router.post("/design/view-analysis", response_model=DesignViewAnalysis)
def analyze_workspace_view(
    workspace_id: str,
    body: DesignViewAnalysisRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignViewAnalysis:
    """Derive a draft view story from SQL without saving or contacting PostgreSQL."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        design = _designs(request).get(principal.user_id, workspace_id)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error
    try:
        return analyze_design_view(design.content, body)
    except QueryDefinitionError as error:
        raise ApiProblem(
            422,
            "invalid_view_definition",
            str(error),
            details={"reason": error.code},
        ) from error


@router.post("/design/routine-analysis", response_model=DesignRoutineAnalysis)
def analyze_workspace_routine(
    workspace_id: str,
    body: DesignRoutineAnalysisRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignRoutineAnalysis:
    """Derive a routine signature from SQL without saving or contacting PostgreSQL."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        contract = analyze_routine_definition(body.definition)
    except RoutineDefinitionError as error:
        raise ApiProblem(
            422,
            "invalid_routine_definition",
            str(error),
            details={"reason": error.code},
        ) from error
    return DesignRoutineAnalysis.model_validate(
        {
            "name": contract.name,
            "kind": contract.kind,
            "arguments": contract.arguments,
            "identityArguments": contract.identity_arguments,
            "returnType": contract.return_type,
            "language": contract.language,
        }
    )


@router.post("/design/trigger-analysis", response_model=DesignTriggerAnalysis)
def analyze_workspace_trigger(
    workspace_id: str,
    body: DesignTriggerAnalysisRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignTriggerAnalysis:
    """Derive a trigger contract from SQL without saving or contacting PostgreSQL."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        contract = analyze_trigger_definition(body.definition)
    except TriggerDefinitionError as error:
        raise ApiProblem(
            422,
            "invalid_trigger_definition",
            str(error),
            details={"reason": error.code},
        ) from error
    return DesignTriggerAnalysis.model_validate(
        {
            "name": contract.name,
            "relationName": contract.relation_name,
            "timing": contract.timing,
            "events": list(contract.events),
            "orientation": contract.orientation,
            "functionName": contract.function_name,
            "functionArguments": list(contract.function_arguments),
            "updateColumns": list(contract.update_columns),
            "referencedColumns": list(contract.referenced_columns),
            "whenExpression": contract.when_expression,
            "transitionRelations": list(contract.transition_relations),
            "constraint": contract.constraint,
            "deferrable": contract.deferrable,
            "initiallyDeferred": contract.initially_deferred,
        }
    )


@router.get("/design/layout", response_model=SchemiiDesignLayout)
def get_workspace_design_layout(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignLayout:
    """Return stable desired-object positions; browser camera state stays local."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        return _designs(request).get_layout(principal.user_id, workspace_id)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error


@router.put("/design/layout", response_model=SchemiiDesignLayout)
def replace_workspace_design_layout(
    workspace_id: str,
    body: SchemiiDesignLayoutReplace,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignLayout:
    """Save positions against exact semantic and layout revisions."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        return _designs(request).replace_layout(principal.user_id, workspace_id, body)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error
    except DesignLayoutConflictError as error:
        raise _layout_conflict(error) from error
    except DesignValidationError as error:
        raise _invalid_design(error) from error


@router.post(
    "/design/imports",
    response_model=SchemiiDesign,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def import_attached_catalog(
    workspace_id: str,
    body: SchemiiDesignImportRequest,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesign:
    """Transform one fingerprint-bound live catalog into editable desired state."""

    # TODO(schemii-design-import): Reinspect the exact attached target, reject a
    # stale fingerprint, translate supported objects, and report omissions.
    del workspace_id, body, principal
    planned_capability("schemii.design.import")


@router.post("/design/exports", response_model=SchemiiDesignExport)
def export_workspace_design(
    workspace_id: str,
    body: SchemiiDesignExportRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignExport:
    """Render deterministic SQL or JSON from saved state without target I/O."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        design = _designs(request).get(principal.user_id, workspace_id)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error
    if design.revision != body.expected_design_revision:
        raise _design_conflict(DesignConflictError(design.revision))
    return export_design(design, body)
