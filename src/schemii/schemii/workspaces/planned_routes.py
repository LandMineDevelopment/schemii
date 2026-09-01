"""Planned workspace routes registered so their contracts can be reviewed."""

from fastapi import APIRouter, Depends, Query

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import SchemiiWorkspace
from .planned_models import WorkspaceMetadataUpdate, WorkspaceTargetUpdate


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


@router.put(
    "/{workspace_id}/target",
    response_model=SchemiiWorkspace,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def attach_workspace_target(
    workspace_id: str,
    body: WorkspaceTargetUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Verify and attach a PostgreSQL target without replacing the saved design."""

    # TODO(schemii-workspace-target): Lock the workspace and connection,
    # verify database/namespace identity, then persist the exact target binding.
    del workspace_id, body, principal
    planned_capability("schemii.workspace-target.attach")


@router.delete(
    "/{workspace_id}/target",
    response_model=SchemiiWorkspace,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def detach_workspace_target(
    workspace_id: str,
    expected_revision: int = Query(alias="expectedRevision", ge=1),
    principal: Principal = Depends(get_current_principal),
) -> SchemiiWorkspace:
    """Detach PostgreSQL while preserving the workspace design, layout, and exports."""

    # TODO(schemii-workspace-target): Reject active plans, Console resources,
    # and operations before atomically clearing only the target binding.
    del workspace_id, expected_revision, principal
    planned_capability("schemii.workspace-target.detach")
