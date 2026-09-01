"""Bounded relation, lineage, and row-preview contracts."""

from typing import Annotated, Any, Literal

from pydantic import Field

from schemii.common.api.models import ApiModel
from schemii.common.postgres.models import (
    PostgresMaterializedView,
    PostgresTable,
    PostgresView,
)


RelationRef = Annotated[str, Field(pattern=r"^rel_[A-Za-z0-9_-]{16,256}$")]


class RelationSummary(ApiModel):
    """Small relation projection suitable for a pageable browser."""

    ref: RelationRef
    name: Annotated[str, Field(min_length=1, max_length=63)]
    kind: Literal["table", "partitioned_table", "view", "materialized_view", "foreign_table"]
    column_count: Annotated[int, Field(strict=True, ge=0, le=1600)]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RelationListResponse(ApiModel):
    """One fingerprint-bound page of live PostgreSQL relations."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    relations: list[RelationSummary]
    next_cursor: str | None = Field(default=None, max_length=512)


class RelationDetailResponse(ApiModel):
    """Exact live relation descriptor selected through an opaque reference."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    relation_ref: RelationRef
    relation: PostgresTable | PostgresView | PostgresMaterializedView


class RelationLineageNode(ApiModel):
    """Verified or explicitly partial relation/column lineage node."""

    id: Annotated[str, Field(min_length=1, max_length=256)]
    kind: Literal["relation", "column", "expression", "consumer"]
    label: Annotated[str, Field(min_length=1, max_length=512)]
    relation_ref: RelationRef | None = None


class RelationLineageEdge(ApiModel):
    """Directed contribution between two lineage nodes."""

    source_id: Annotated[str, Field(min_length=1, max_length=256)]
    target_id: Annotated[str, Field(min_length=1, max_length=256)]
    classification: Literal["direct", "expression", "aggregate", "window", "dependency"]
    verified: bool


class RelationLineageResponse(ApiModel):
    """Bounded lineage derived from one live relation fingerprint."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    relation_ref: RelationRef
    relation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    warnings: list[str] = Field(default_factory=list, max_length=100)
    nodes: list[RelationLineageNode] = Field(max_length=10_000)
    edges: list[RelationLineageEdge] = Field(max_length=20_000)


class RelationRowPage(ApiModel):
    """Bounded read-only rows with explicit continuation and truncation state."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    relation_ref: RelationRef
    relation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    columns: list[str] = Field(max_length=1600)
    rows: list[dict[str, Any]] = Field(max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=512)
    truncated: bool
