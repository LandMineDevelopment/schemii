"""Database-independent desired-schema routes."""

from fastapi import APIRouter, Depends, Request

from schemii.common.api.errors import ApiProblem
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
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError

from .export import export_design
from .deletion_impact import DesignObjectNotFoundError, design_deletion_impact
from .models import (
    DesignDeletionImpact,
    DesignBaselineResetPreview,
    DesignBaselineResetRequest,
    DesignHistoryMutation,
    DesignHistoryState,
    DesignHistoryTransitionRequest,
    SchemiiDesign,
    SchemiiDesignExport,
    SchemiiDesignExportRequest,
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
    DesignMutationBlockedError,
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


def _migrations(request: Request) -> MigrationService:
    return request.app.state.services.migrations


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


def _migration_problem(error: MigrationServiceError) -> ApiProblem:
    return ApiProblem(
        error.status,
        error.code,
        str(error),
        details=error.details,
        retryable=error.retryable,
    )


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


@router.get("/design/deletion-impact/{object_id}", response_model=DesignDeletionImpact)
def get_design_deletion_impact(
    workspace_id: str,
    object_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignDeletionImpact:
    """Derive every current-design object affected by one explicit deletion."""

    _require_workspace(request, principal.user_id, workspace_id)
    try:
        design = _designs(request).get(principal.user_id, workspace_id)
        return design_deletion_impact(design.content, design.revision, object_id)
    except DesignWorkspaceNotFoundError as error:
        raise _design_not_found(error) from error
    except DesignObjectNotFoundError as error:
        raise ApiProblem(
            404,
            "design_object_not_found",
            "The selected object is no longer in this design",
            details={"objectId": str(error)},
        ) from error


@router.get("/design/history", response_model=DesignHistoryState)
def get_design_history(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignHistoryState:
    """Return the server-owned semantic cursor and current reset destination."""

    try:
        return _migrations(request).design_history_state(principal.user_id, workspace_id)
    except MigrationServiceError as error:
        raise _migration_problem(error) from error


@router.post("/design/undo", response_model=DesignHistoryMutation)
def undo_design_history(
    workspace_id: str,
    body: DesignHistoryTransitionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignHistoryMutation:
    """Move one durable semantic action backward without contacting PostgreSQL."""

    try:
        return _migrations(request).undo_design(principal.user_id, workspace_id, body)
    except MigrationServiceError as error:
        raise _migration_problem(error) from error


@router.post("/design/redo", response_model=DesignHistoryMutation)
def redo_design_history(
    workspace_id: str,
    body: DesignHistoryTransitionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignHistoryMutation:
    """Move one durable semantic action forward without contacting PostgreSQL."""

    try:
        return _migrations(request).redo_design(principal.user_id, workspace_id, body)
    except MigrationServiceError as error:
        raise _migration_problem(error) from error


@router.get("/design/baseline-reset", response_model=DesignBaselineResetPreview)
def preview_design_baseline_reset(
    workspace_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignBaselineResetPreview:
    """Derive the exact metadata-only reset for explicit review."""

    try:
        return _migrations(request).preview_baseline_reset(principal.user_id, workspace_id)
    except MigrationServiceError as error:
        raise _migration_problem(error) from error


@router.post("/design/baseline-reset", response_model=DesignHistoryMutation)
def reset_design_to_baseline(
    workspace_id: str,
    body: DesignBaselineResetRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> DesignHistoryMutation:
    """Restore desired metadata to its reviewed baseline without applying DDL."""

    try:
        return _migrations(request).reset_design_to_baseline(
            principal.user_id, workspace_id, body
        )
    except MigrationServiceError as error:
        raise _migration_problem(error) from error


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
    except DesignMutationBlockedError as error:
        raise ApiProblem(409, "design_mutation_blocked", str(error)) from error
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
