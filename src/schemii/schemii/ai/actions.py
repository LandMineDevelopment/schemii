"""Typed assistant adapters to the same application services used by the UI.

These adapters do not grant permission. The assistant proposal workflow binds
the owner, workspace and revisions and authorizes an exact action before calling
them. Existing services remain authoritative for validation and concurrency.

TODO: Add typed graphical layout actions
through their existing services. Do not substitute a generic API executor or
raw SQL for a missing structured action. Account/credential and AI permission
management remain user-controlled.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, create_model, model_validator
from psycopg import sql

from schemii.common.api.models import ApiModel
from schemii.schemii.catalog.models import RelationRef
from schemii.schemii.catalog.service import RelationBrowserError, RelationBrowserService
from schemii.schemii.designs.models import (
    DesignBaselineResetRequest,
    DesignHistoryTransitionRequest,
)
from schemii.schemii.migrations.errors import MigrationServiceError
from schemii.schemii.migrations.models import (
    MigrationExecutionCreate, MigrationPlanCreate, MigrationDriftResolutionRequest,
    MigrationReconciliationRequest,
)


Scalar = StrictStr | StrictInt | StrictFloat | StrictBool


class RelationFilter(ApiModel):
    column: str = Field(min_length=1, max_length=63)
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null", "is_not_null"] = "eq"
    value: Scalar | list[Scalar] | None = None

    @model_validator(mode="after")
    def valid_operand(self):
        if self.operator in {"is_null", "is_not_null"}:
            if self.value is not None:
                raise ValueError("null predicates do not accept a value")
        elif self.operator == "in":
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 100:
                raise ValueError("in requires between one and 100 values")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError("comparison requires a scalar value; use is_null for null")
        return self


class RelationOrder(ApiModel):
    column: str = Field(min_length=1, max_length=63)
    direction: Literal["asc", "desc"] = "asc"


class RelationListAction(ApiModel):
    search: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, max_length=512)
    page_size: int = Field(default=100, strict=True, ge=1, le=250)


class RelationRead(ApiModel):
    """Read selected live columns without constructing SQL or guessing names."""

    relation_ref: RelationRef
    columns: list[str] | None = Field(default=None, min_length=1, max_length=1600)
    filters: list[RelationFilter] = Field(default_factory=list, max_length=50)
    order_by: list[RelationOrder] = Field(default_factory=list, max_length=20)
    count_only: bool = False
    limit: int = Field(default=100, strict=True, ge=1, le=1000)


class DesignHistoryAction(ApiModel):
    direction: Literal["undo", "redo"]


class BaselineResetAction(DesignBaselineResetRequest):
    """The existing exact-reset review contract, never a broad reset grant."""


# Use the real migration option fields. Owner/workspace and optimistic revisions
# are supplied by the authorized proposal, not by model-controlled arguments.
MigrationPlanAction = create_model(
    "MigrationPlanAction",
    __base__=ApiModel,
    **{
        name: (field.annotation, deepcopy(field))
        for name, field in MigrationPlanCreate.model_fields.items()
        if name not in {"expected_workspace_revision", "expected_design_revision"}
    },
)


class MigrationApplyAction(MigrationExecutionCreate):
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")


class MigrationStatusAction(ApiModel):
    execution_id: str = Field(pattern=r"^mex_[0-9a-f]{32}$")


class MigrationPlanReference(ApiModel):
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")


class MigrationResolveAction(MigrationDriftResolutionRequest):
    plan_id: str = Field(pattern=r"^mpl_[0-9a-f]{32}$")


class MigrationReconcileAction(MigrationReconciliationRequest):
    execution_id: str = Field(pattern=r"^mex_[0-9a-f]{32}$")


def relation_browser(services: Any) -> RelationBrowserService:
    return RelationBrowserService(
        workspaces=services.workspaces,
        connections=services.connections,
        postgres=services.postgres,
    )


def relation_read_query(
    services: Any, owner: str, workspace: str, action: RelationRead,
) -> dict[str, str]:
    """Resolve fresh live facts, then feed the ordinary batched-read pipeline.

    This returns SQL only: execution, approval, result sampling, replay and row
    privacy all stay with the existing Console/AI read workflow.
    """

    relation = relation_browser(services).detail(owner, workspace, action.relation_ref).relation
    names = {column.name for column in relation.columns}
    selected = action.columns if action.columns is not None else [column.name for column in relation.columns]
    if not selected or len(selected) != len(set(selected)) or any(name not in names for name in selected):
        raise RelationBrowserError(422, "invalid_relation_columns", "Choose distinct column names from the current live relation")

    if any(item.column not in names for item in [*action.filters, *action.order_by]):
        raise RelationBrowserError(422, "invalid_relation_columns", "Filter and order columns must exist in the current live relation")
    projection = sql.SQL('COUNT(*) AS "row_count"') if action.count_only else sql.SQL(", ").join(sql.Identifier(name) for name in selected)
    statement = sql.SQL("SELECT {} FROM {}.{}").format(projection, sql.Identifier(relation.namespace), sql.Identifier(relation.name))
    predicates = []
    operators = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
    for item in action.filters:
        identifier = sql.Identifier(item.column)
        if item.operator in {"is_null", "is_not_null"}:
            predicate = sql.SQL("{} IS {}NULL").format(identifier, sql.SQL("NOT " if item.operator == "is_not_null" else ""))
        elif item.operator == "in":
            predicate = sql.SQL("{} IN ({})").format(identifier, sql.SQL(", ").join(sql.Literal(value) for value in item.value))
        else:
            predicate = sql.SQL("{} {} {}").format(identifier, sql.SQL(operators[item.operator]), sql.Literal(item.value))
        predicates.append(predicate)
    if predicates:
        statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(predicates)
    if action.order_by and not action.count_only:
        statement += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
            sql.SQL("{} {}").format(sql.Identifier(item.column), sql.SQL(item.direction.upper())) for item in action.order_by
        )
    statement += sql.SQL(" LIMIT {} ").format(sql.Literal(action.limit))
    return {"label": f"{'Count' if action.count_only else 'Rows'} from {relation.name}"[:200], "sql": statement.as_string()}


def list_relations(services: Any, owner: str, workspace: str, action: RelationListAction) -> Any:
    return relation_browser(services).list(owner, workspace, cursor=action.cursor, page_size=action.page_size, search=action.search)


def execute_history(
    services: Any, owner: str, workspace: str, expected_design_revision: int,
    action: DesignHistoryAction,
) -> Any:
    method = services.migrations.undo_design if action.direction == "undo" else services.migrations.redo_design
    return method(owner, workspace, DesignHistoryTransitionRequest(expected_design_revision=expected_design_revision))


def preview_baseline_reset(services: Any, owner: str, workspace: str) -> Any:
    return services.migrations.preview_baseline_reset(owner, workspace)


def execute_baseline_reset(services: Any, owner: str, workspace: str, action: BaselineResetAction) -> Any:
    return services.migrations.reset_design_to_baseline(owner, workspace, action)


def create_migration_plan(
    services: Any, owner: str, workspace: str,
    expected_workspace_revision: int, expected_design_revision: int,
    action: MigrationPlanAction,
) -> Any:
    request = MigrationPlanCreate(
        **action.model_dump(), expected_workspace_revision=expected_workspace_revision,
        expected_design_revision=expected_design_revision,
    )
    return services.migrations.create_plan(owner, workspace, request)


def execute_migration(services: Any, owner: str, workspace: str, action: MigrationApplyAction) -> Any:
    plan = services.migrations.get_plan(owner, action.plan_id)
    if plan.workspace_id != workspace:
        raise MigrationServiceError(409, "migration_workspace_mismatch", "The migration plan belongs to a different workspace")
    # The coordinator verifies digest, expiry, catalog drift, risk confirmations
    # and single-use execution. Do not infer confirmations from plan flags.
    return services.migrations.create_execution(
        owner, action.plan_id,
        MigrationExecutionCreate(**action.model_dump(exclude={"plan_id"})),
    )


def migration_status(services: Any, owner: str, workspace: str, action: MigrationStatusAction) -> Any:
    execution = services.migrations.get_execution(owner, action.execution_id)
    if execution.workspace_id != workspace:
        raise MigrationServiceError(409, "migration_workspace_mismatch", "The migration execution belongs to a different workspace")
    return execution


def migration_plan(services, owner, workspace, action):
    plan = services.migrations.get_plan(owner, action.plan_id)
    if plan.workspace_id != workspace:
        raise MigrationServiceError(409, "migration_workspace_mismatch", "The migration plan belongs to a different workspace")
    return plan


def drift_review_context(services, owner, workspace, action):
    """Bind readable object paths to the exact review, not model-supplied labels."""
    plan = migration_plan(services, owner, workspace, action)
    if plan.review_digest != action.review_digest:
        raise MigrationServiceError(409, "migration_review_changed", "Read the current migration plan before resolving its conflicts")
    return [{"id": conflict.id, "path": conflict.object_path, "reason": conflict.summary}
            for conflict in plan.conflicts]


def resolve_migration(services, owner, workspace, action):
    migration_plan(services, owner, workspace, action)
    return services.migrations.resolve_drift(owner, action.plan_id,
        MigrationDriftResolutionRequest(**action.model_dump(exclude={"plan_id"})))


def reconcile_migration(services, owner, workspace, action):
    migration_status(services, owner, workspace, action)
    return services.migrations.reconcile_execution(owner, action.execution_id,
        MigrationReconciliationRequest(**action.model_dump(exclude={"execution_id"})))
