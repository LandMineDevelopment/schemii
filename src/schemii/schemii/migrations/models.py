"""Durable migration plan, execution, and reconciliation contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from schemii.common.api.models import ApiModel


MigrationPlanStatus = Literal["reviewable", "blocked", "expired", "claimed"]
MigrationExecutionStatus = Literal[
    "reserved",
    "applying",
    "succeeded",
    "failed",
    "uncertain",
    "reconciliation_required",
]


class MigrationPlanCreate(ApiModel):
    """Exact desired and live revisions used to derive a reviewable plan."""

    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_destructive: bool = False


class MigrationStep(ApiModel):
    """One ordered SQL change with explicit impact classification."""

    index: Annotated[int, Field(strict=True, ge=1)]
    object_kind: Literal["table", "column", "constraint", "index", "function", "view"]
    operation: Annotated[str, Field(min_length=1, max_length=128)]
    sql: Annotated[str, Field(min_length=1, max_length=1024 * 1024)]
    destructive: bool
    requires_lock: bool


class MigrationPlan(ApiModel):
    """Immutable reviewed difference between desired design and live catalog."""

    id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    status: MigrationPlanStatus
    design_revision: Annotated[int, Field(strict=True, ge=0)]
    design_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    complete: bool
    apply_capable: bool
    destructive: bool
    steps: list[MigrationStep] = Field(max_length=10_000)
    warnings: list[str] = Field(default_factory=list, max_length=1000)
    blocking_differences: list[str] = Field(default_factory=list, max_length=1000)
    created_at: datetime
    expires_at: datetime


class MigrationExecutionCreate(ApiModel):
    """One-use authorization to execute exactly the reviewed plan."""

    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_destructive: bool


class MigrationExecution(ApiModel):
    """Durable migration attempt and its PostgreSQL commit evidence."""

    id: str = Field(pattern=r"^mex_[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    status: MigrationExecutionStatus
    completed_step_count: Annotated[int, Field(strict=True, ge=0)]
    transaction_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=128)
    created_at: datetime
    updated_at: datetime


class MigrationReconciliationRequest(ApiModel):
    """Revision guard for checking durable PostgreSQL transaction evidence."""

    expected_execution_revision: Annotated[int, Field(strict=True, ge=1)]


class MigrationExecutionListResponse(ApiModel):
    """Recent owner-visible attempts for one workspace."""

    executions: list[MigrationExecution]
