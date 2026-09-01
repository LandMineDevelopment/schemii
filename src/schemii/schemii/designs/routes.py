"""Planned database-independent schema design routes."""

from fastapi import APIRouter, Depends, status

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    SchemiiDesign,
    SchemiiDesignExport,
    SchemiiDesignExportRequest,
    SchemiiDesignImportRequest,
    SchemiiDesignLayout,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["schemii-schema-design-planned"],
)


@router.get(
    "/design",
    response_model=SchemiiDesign,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_workspace_design(
    workspace_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesign:
    """Return user-authored desired state whether or not PostgreSQL is attached."""

    # TODO(schemii-design-store): Read the owner/workspace design aggregate
    # from metadata storage without contacting the optional target database.
    del workspace_id, principal
    planned_capability("schemii.design.read")


@router.put(
    "/design",
    response_model=SchemiiDesign,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def replace_workspace_design(
    workspace_id: str,
    body: SchemiiDesignReplace,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesign:
    """Validate stable object references and replace one desired-design revision."""

    # TODO(schemii-design-store): Validate unique names, relationship endpoints,
    # and function/view bounds before one optimistic metadata transaction.
    del workspace_id, body, principal
    planned_capability("schemii.design.replace")


@router.get(
    "/design/layout",
    response_model=SchemiiDesignLayout,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_workspace_design_layout(
    workspace_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignLayout:
    """Return target-independent positions and cameras for stable design objects."""

    # TODO(schemii-design-layout): Read layout separately from semantic design so
    # frequent drag saves do not create false desired-state revision conflicts.
    del workspace_id, principal
    planned_capability("schemii.design.layout.read")


@router.put(
    "/design/layout",
    response_model=SchemiiDesignLayout,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def replace_workspace_design_layout(
    workspace_id: str,
    body: SchemiiDesignLayoutReplace,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignLayout:
    """Save visual state after validating every object against the design revision."""

    # TODO(schemii-design-layout): Reject unknown/duplicate object IDs and commit
    # one optimistic layout revision without modifying semantic desired state.
    del workspace_id, body, principal
    planned_capability("schemii.design.layout.replace")


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


@router.post(
    "/design/exports",
    response_model=SchemiiDesignExport,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def export_workspace_design(
    workspace_id: str,
    body: SchemiiDesignExportRequest,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiDesignExport:
    """Render deterministic SQL or JSON from saved design state with no database call."""

    # TODO(schemii-design-export): Compile names and dependencies in a stable
    # order, quote PostgreSQL identifiers, and hash the exact returned bytes.
    del workspace_id, body, principal
    planned_capability("schemii.design.export")
