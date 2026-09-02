"""Server-authoritative migration planning, drift, and execution contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from schemii.common.api.models import ApiModel


MigrationPlanStatus = Literal[
    "reviewable",
    "blocked",
    "expired",
    "claimed",
    "resolved",
]
MigrationDriftStatus = Literal["none", "compatible", "conflicting"]
MigrationExecutionStatus = Literal[
    "reserved",
    "applying",
    "succeeded",
    "failed",
    "uncertain",
    "reconciliation_required",
]
MigrationObjectKind = Literal[
    "schema",
    "type",
    "table",
    "column",
    "constraint",
    "index",
    "relationship",
    "function",
    "procedure",
    "view",
    "materialized_view",
    "trigger",
]


class MigrationPlanCreate(ApiModel):
    """Optimistic UI context; every authoritative value is re-read server-side."""

    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_catalog_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    allow_destructive: bool = False


class MigrationWarning(ApiModel):
    """One source-derived operational or drift warning."""

    code: Annotated[str, Field(min_length=1, max_length=128)]
    message: Annotated[str, Field(min_length=1, max_length=2048)]
    object_path: str | None = Field(default=None, max_length=1024)


class MigrationExternalChange(ApiModel):
    """One compatible PostgreSQL change derived from baseline and live state."""

    id: str = Field(pattern=r"^mec_[0-9a-f]{32}$")
    object_kind: MigrationObjectKind
    object_path: Annotated[str, Field(min_length=1, max_length=1024)]
    operation: Literal["added", "removed", "changed", "renamed"]
    summary: Annotated[str, Field(min_length=1, max_length=2048)]
    baseline_value: Any = None
    live_value: Any = None


class MigrationConflict(ApiModel):
    """One server-proven collision between desired and external changes."""

    id: str = Field(pattern=r"^mcf_[0-9a-f]{32}$")
    category: Literal["direct", "structural", "dependency", "opaque"]
    object_kind: MigrationObjectKind
    object_path: Annotated[str, Field(min_length=1, max_length=1024)]
    summary: Annotated[str, Field(min_length=1, max_length=2048)]
    baseline_value: Any = None
    design_value: Any = None
    live_value: Any = None
    affected_objects: list[str] = Field(default_factory=list, max_length=10_000)
    allowed_resolutions: list[Literal["pull_live", "keep_design"]] = Field(
        default_factory=lambda: ["pull_live", "keep_design"],
        min_length=1,
        max_length=2,
    )


class MigrationStep(ApiModel):
    """One exact ordered SQL statement compiled and validated by the server."""

    index: Annotated[int, Field(strict=True, ge=1)]
    object_kind: MigrationObjectKind
    object_path: Annotated[str, Field(min_length=1, max_length=1024)]
    operation: Annotated[str, Field(min_length=1, max_length=128)]
    sql: Annotated[str, Field(min_length=1, max_length=1024 * 1024)]
    destructive: bool
    requires_lock: bool
    data_movement: bool = False


class MigrationPlan(ApiModel):
    """Immutable server-derived review of desired, baseline, and live state."""

    id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    status: MigrationPlanStatus
    workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    design_revision: Annotated[int, Field(strict=True, ge=0)]
    design_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_revision: Annotated[int, Field(strict=True, ge=1)]
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    merged_design_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    drift_status: MigrationDriftStatus
    complete: bool
    apply_capable: bool
    destructive: bool
    requires_external_change_acknowledgement: bool
    steps: list[MigrationStep] = Field(max_length=10_000)
    external_changes: list[MigrationExternalChange] = Field(
        default_factory=list,
        max_length=20_000,
    )
    conflicts: list[MigrationConflict] = Field(default_factory=list, max_length=20_000)
    warnings: list[MigrationWarning] = Field(default_factory=list, max_length=10_000)
    blocking_differences: list[MigrationWarning] = Field(
        default_factory=list,
        max_length=10_000,
    )
    created_at: datetime
    expires_at: datetime


class MigrationConflictResolution(ApiModel):
    """One explicit choice bound to a server-generated conflict identifier."""

    conflict_id: str = Field(pattern=r"^mcf_[0-9a-f]{32}$")
    resolution: Literal["pull_live", "keep_design"]


class MigrationDriftResolutionRequest(ApiModel):
    """Resolve every conflict in an immutable blocked review."""

    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolutions: list[MigrationConflictResolution] = Field(
        min_length=1,
        max_length=20_000,
    )

    @model_validator(mode="after")
    def unique_conflicts(self) -> "MigrationDriftResolutionRequest":
        identifiers = [item.conflict_id for item in self.resolutions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("each migration conflict may be resolved once")
        return self


class MigrationDriftResolution(ApiModel):
    """Audited metadata-only result; a fresh migration plan is required."""

    id: str = Field(pattern=r"^mdr_[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    design_revision: Annotated[int, Field(strict=True, ge=1)]
    design_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_revision: Annotated[int, Field(strict=True, ge=1)]
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    incorporated_external_changes: list[MigrationExternalChange]
    created_at: datetime


class MigrationExecutionCreate(ApiModel):
    """Single-use authorization for exactly one immutable reviewed plan."""

    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_destructive: bool = False
    confirm_external_changes: bool = False


class MigrationExecution(ApiModel):
    """Durable migration attempt and its PostgreSQL commit evidence."""

    id: str = Field(pattern=r"^mex_[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    status: MigrationExecutionStatus
    completed_step_count: Annotated[int, Field(strict=True, ge=0)]
    transaction_id: str | None = Field(default=None, max_length=128)
    commit_outcome: Literal["committed", "rolled_back", "uncertain"] | None = None
    sync_status: Literal["pending", "succeeded", "conflict", "failed"] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    reconcile_required: bool = False
    recovery_available_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MigrationReconciliationRequest(ApiModel):
    """Revision guard for checking durable PostgreSQL transaction evidence."""

    expected_execution_revision: Annotated[int, Field(strict=True, ge=1)]


class MigrationExecutionListResponse(ApiModel):
    """Recent owner-visible attempts for one workspace."""

    executions: list[MigrationExecution]
