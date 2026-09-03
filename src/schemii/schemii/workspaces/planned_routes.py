"""Planned workspace metadata routes registered for contract review."""

from fastapi import APIRouter, Depends

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import SchemiiWorkspace
from .planned_models import WorkspaceMetadataUpdate


router = APIRouter(prefix="/workspaces", tags=["schemii-workspaces-planned"])


@router.patch(
    "/{workspace_id}",
    response_model=SchemiiWorkspace,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def update_workspace_metadata(
    workspace_id: str,
    body: WorkspaceMetadataUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Rename an owner-scoped workspace while preserving every owned child resource."""

    # TODO(schemii-workspace-metadata): Persist the optimistic rename through
    # the metadata workspace repository and return the incremented revision.
    del workspace_id, body, principal
    planned_capability("schemii.workspace-metadata")
