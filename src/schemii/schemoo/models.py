"""Bounded authored semantic metadata. Query rows never belong in these models."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator
from schemii.common.api.models import ApiModel

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Scalar = Annotated[str, Field(max_length=4000)] | int | float | bool | None
FilterValue = Scalar | Annotated[list[Annotated[str, Field(max_length=4000)] | int | float | bool], Field(max_length=500)]


class Contract(ApiModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelNode(Contract):
    id: Identifier
    table: Identifier
    label: str = Field(min_length=1, max_length=100)


class ModelEdge(Contract):
    id: Identifier
    relationshipId: Identifier
    source: Identifier
    target: Identifier
    enabled: bool = True


class DomainValues(Contract):
    nodeId: Identifier | None = None
    table: str = Field(default="", max_length=200)
    column: str = Field(default="", max_length=200)
    labelColumn: str = Field(default="", max_length=200)


class ParameterInput(Contract):
    id: Identifier
    label: str = Field(default="", max_length=200)
    type: Literal["source", "text", "uuid", "integer", "number", "boolean", "date"] = "text"
    defaultValue: FilterValue = ""
    domain: DomainValues | None = None


class Condition(Contract):
    domain: DomainValues | None = None
    table: str = Field(default="", max_length=200)
    column: str = Field(default="", max_length=200)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in", "is_null", "not_null"] = "eq"
    parameterId: str | None = Field(default=None, max_length=200)
    value: FilterValue = None
    allowNull: bool = False


class ScopeAlternative(Contract):
    id: Identifier
    label: str = Field(default="", max_length=200)
    inputs: list[ParameterInput] = Field(default_factory=list, max_length=16)
    conditions: list[Condition] = Field(default_factory=list, max_length=32)


class ModelScope(Contract):
    id: Identifier
    label: str = Field(default="", max_length=200)
    kind: Literal["required", "conditional"]
    alternatives: list[ScopeAlternative] = Field(default_factory=list, max_length=12)


class SelectedField(Contract):
    table: Identifier
    column: Identifier
    aggregate: Literal["none", "count", "count_distinct", "sum", "avg", "min", "max"] = "none"


class ModelDefinition(Contract):
    root: str = Field(default="", max_length=200)
    nodes: list[ModelNode] = Field(default_factory=list, max_length=100)
    edges: list[ModelEdge] = Field(default_factory=list, max_length=200)
    scopes: list[ModelScope] = Field(default_factory=list, max_length=20)
    exposedFields: list[SelectedField] | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def unique_identifiers(self):
        for label, items in (("node", self.nodes), ("edge", self.edges), ("scope", self.scopes)):
            if len({item.id for item in items}) != len(items):
                raise ValueError(f"Duplicate {label} IDs are not allowed")
        return self


class ModelPosition(Contract):
    id: Identifier
    x: float
    y: float


class ModelLayout(Contract):
    positions: list[ModelPosition] = Field(default_factory=list, max_length=100)


class ScopeSelection(Contract):
    alternativeId: str | None = Field(default=None, max_length=200)
    values: dict[str, FilterValue] = Field(default_factory=dict, max_length=32)


class ReportFilter(Contract):
    id: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=200)
    mode: Literal["rows", "exists", "not_exists"] = "rows"
    conditions: list[Condition] = Field(default_factory=list, max_length=32)


class ExploreState(Contract):
    root: str = Field(default="", max_length=200)
    fields: list[SelectedField] = Field(default_factory=list, max_length=64)
    selections: dict[str, ScopeSelection] = Field(default_factory=dict, max_length=20)
    reportFilters: list[ReportFilter] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=100, ge=1, le=100)


class ModelCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    database: str = Field(min_length=1, max_length=63)
    namespace: str = Field(min_length=1, max_length=63)
    definition: ModelDefinition = Field(default_factory=ModelDefinition)
    catalog_fingerprint: str = Field(default="", max_length=128)
    layout: ModelLayout = Field(default_factory=ModelLayout)
    explore: ExploreState = Field(default_factory=ExploreState)


class ModelUpdate(Contract):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    definition: ModelDefinition
    catalog_fingerprint: str = Field(default="", max_length=128)


class LayoutUpdate(Contract):
    expected_revision: int = Field(ge=1)
    layout: ModelLayout


class ExploreUpdate(Contract):
    expected_revision: int = Field(ge=1)
    explore: ExploreState


class ModelSummary(Contract):
    """Library/dependency projection, without potentially large JSON documents."""
    id: str
    owner_id: str
    name: str
    connection_id: str
    database: str
    namespace: str
    catalog_fingerprint: str = ""
    revision: int = Field(ge=1)
    layout_revision: int = Field(default=1, ge=1)
    explore_revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class SchemooModel(ModelCreate, ModelSummary):
    pass
