"""Planned, workspace-bound live database browser routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    RelationDetailResponse,
    RelationLineageResponse,
    RelationListResponse,
    RelationRef,
    RelationRowPage,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/relations",
    tags=["schemii-database-browser-planned"],
)


@router.get(
    "",
    response_model=RelationListResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def list_workspace_relations(
    workspace_id: str,
    cursor: str | None = Query(default=None, max_length=512),
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=250)] = 100,
    search: str | None = Query(default=None, max_length=256),
    principal: Principal = Depends(get_current_principal),
) -> RelationListResponse:
    """Page live relation summaries for an attached workspace target."""

    # TODO(postgres-relation-browser): Add one repeatable-read, bounded gateway
    # query and bind opaque continuation tokens to owner, target, and fingerprint.
    del workspace_id, cursor, page_size, search, principal
    planned_capability("schemii.catalog.relations")


@router.get(
    "/{relation_ref}",
    response_model=RelationDetailResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_workspace_relation(
    workspace_id: str,
    relation_ref: RelationRef,
    principal: Principal = Depends(get_current_principal),
) -> RelationDetailResponse:
    """Resolve an opaque relation reference into a fresh detailed descriptor."""

    # TODO(postgres-relation-detail): Decode and authorize the reference, then
    # inspect exactly one relation without materializing the full namespace.
    del workspace_id, relation_ref, principal
    planned_capability("schemii.catalog.relation-detail")


@router.get(
    "/{relation_ref}/lineage",
    response_model=RelationLineageResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_workspace_relation_lineage(
    workspace_id: str,
    relation_ref: RelationRef,
    principal: Principal = Depends(get_current_principal),
) -> RelationLineageResponse:
    """Return verified dependency and column-lineage evidence for one live relation."""

    # TODO(postgres-lineage): Read dependencies in the same catalog snapshot,
    # analyze bounded view SQL, and label every unsupported path as incomplete.
    del workspace_id, relation_ref, principal
    planned_capability("schemii.catalog.lineage")


@router.get(
    "/{relation_ref}/rows",
    response_model=RelationRowPage,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def preview_workspace_relation_rows(
    workspace_id: str,
    relation_ref: RelationRef,
    cursor: str | None = Query(default=None, max_length=512),
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=250)] = 100,
    principal: Principal = Depends(get_current_principal),
) -> RelationRowPage:
    """Read a bounded page without rerunning or broadening the selected relation."""

    # TODO(postgres-row-preview): Execute identifier-safe SELECT in a managed
    # read-only snapshot and make truncation, expiry, and cursor ownership explicit.
    del workspace_id, relation_ref, cursor, page_size, principal
    planned_capability("schemii.catalog.rows")
