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


class ModelEdge(Contract):
    id: Identifier
    relationshipId: Identifier | None = None
    kind: Literal["foreign_key", "logical"] = "foreign_key"
    sourceColumn: Identifier | None = None
    targetColumn: Identifier | None = None
    cardinality: Literal["many_to_many", "many_to_one", "one_to_many", "one_to_one"] = "many_to_many"
    source: Identifier
    target: Identifier
    enabled: bool = True

    @model_validator(mode="after")
    def relationship_definition(self):
        if self.kind == "logical":
            if self.relationshipId or not self.sourceColumn or not self.targetColumn:
                raise ValueError("Logical relationships require two columns and no foreign-key ID.")
        elif not self.relationshipId or self.sourceColumn or self.targetColumn:
            raise ValueError("Foreign-key relationships require a live relationship ID and cannot override columns.")
        return self


class SourceColumnContract(Contract):
    name: Identifier
    dataType: str = Field(default="", max_length=200)
    nullable: bool = True


class SourceTableContract(Contract):
    name: Identifier
    columns: list[SourceColumnContract] = Field(default_factory=list, max_length=1000)
    primaryKey: list[Identifier] = Field(default_factory=list, max_length=32)


class SourceRelationshipContract(Contract):
    id: Identifier
    name: str = Field(default="", max_length=200)
    sourceTable: Identifier
    sourceColumn: Identifier
    targetTable: Identifier
    targetColumn: Identifier


class SourceContract(Contract):
    tables: list[SourceTableContract] = Field(default_factory=list, max_length=100)
    relationships: list[SourceRelationshipContract] = Field(default_factory=list, max_length=200)


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
    domain: DomainValues | None = Field(default=None, description="Optional value-picker lookup only; it does not bind the condition. Always set table and column separately.")
    table: str = Field(default="", max_length=200, description="Stable model node ID whose column is filtered, including the exact alias ID. Not a physical table name unless that is also its node ID.")
    column: str = Field(default="", max_length=200, description="Source column name on the bound model node. Required for every usable condition, including parameter comparisons.")
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "range_contains_date", "in", "not_in", "is_null", "not_null"] = "eq"
    parameterId: str | None = Field(default=None, max_length=200)
    compareColumn: Identifier | None = Field(default=None, description="Compare against a physical column on the same table node/alias and row; cannot combine with a value, parameter, or domain.")
    value: FilterValue = None
    allowNull: bool = False

    @model_validator(mode="after")
    def column_comparison(self):
        if self.compareColumn is not None:
            if self.operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                raise ValueError("Column comparisons require equals, not equals, or an ordering comparison.")
            if self.parameterId is not None or self.value is not None or self.domain is not None:
                raise ValueError("Choose either a comparison column, a fixed value, or a parameter; column comparisons do not use domain values.")
        return self


class DerivedCondition(Condition):
    valueSource: Literal["literal", "today"] = "literal"

    @model_validator(mode="after")
    def supported_condition(self):
        if self.parameterId is not None:
            raise ValueError("Calculated-field conditions accept fixed values, columns, or Today; parameter bindings are not supported.")
        if self.valueSource == "today":
            if self.compareColumn is not None:
                raise ValueError("Choose either Today or a comparison column, not both.")
            if self.operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                raise ValueError("Today requires a single-value date comparison.")
            if self.value not in (None, ""):
                raise ValueError("Choose either Today or a fixed condition value, not both.")
            if self.domain is not None:
                raise ValueError("Today does not use a domain value list.")
        return self


class DerivedOutput(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=100)
    operation: Literal["add", "subtract", "multiply", "divide", "list", "count", "count_distinct", "sum", "avg", "min", "max"]
    nodeId: Identifier | None = None
    column: Identifier
    operand: Identifier | None = None
    distinct: bool = False
    delimiter: str = Field(default=", ", max_length=20)
    conditions: list[DerivedCondition] = Field(default_factory=list, max_length=16)


class SummaryConnectionColumn(Contract):
    source: Identifier
    target: Identifier


class SummaryConnection(Contract):
    target: Identifier
    columns: list[SummaryConnectionColumn] = Field(min_length=1, max_length=8)


class Derivation(Contract):
    kind: Literal["row", "aggregate"]
    source: Identifier
    groupBy: list[Identifier] = Field(default_factory=list, max_length=8)
    connection: SummaryConnection | None = None
    outputs: list[DerivedOutput] = Field(min_length=1, max_length=32)


class ModelNode(Contract):
    id: Identifier
    table: Identifier
    label: str = Field(min_length=1, max_length=100)
    derivation: Derivation | None = None


class ScopeAlternative(Contract):
    id: Identifier
    label: str = Field(default="", max_length=200)
    inputs: list[ParameterInput] = Field(default_factory=list, max_length=16)
    conditions: list[Condition] = Field(default_factory=list, max_length=32)


class ModelScope(Contract):
    id: Identifier
    label: str = Field(default="", max_length=200)
    kind: Literal["required", "conditional"]
    requirement: Literal["required", "optional"] = "required"
    rowBehavior: Literal["keep_unmatched", "require_matching"] | None = None
    alternatives: list[ScopeAlternative] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_activation(cls, value):
        """Read drafts written before requirement replaced model-time activation."""
        if isinstance(value, dict) and "requirement" not in value and "activation" in value:
            value = dict(value)
            activation = value.pop("activation")
            value["requirement"] = {"automatic": "required", "optional": "optional"}.get(
                activation, activation)
        return value


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
    sourceContract: SourceContract | None = None

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
    active: bool = False


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


class ModelDuplicate(Contract):
    """Copy one consistent saved model snapshot, including its saved previews."""
    name: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    expected_layout_revision: int = Field(ge=1)
    expected_explore_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def trim_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter a model name")
        return self


class ModelUpdate(Contract):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    definition: ModelDefinition
    catalog_fingerprint: str = Field(default="", max_length=128)


class NodeChanges(Contract):
    upsert: list[ModelNode] = Field(default_factory=list, max_length=100)
    remove: list[Identifier] = Field(default_factory=list, max_length=100)


class EdgeChanges(Contract):
    upsert: list[ModelEdge] = Field(default_factory=list, max_length=200)
    remove: list[Identifier] = Field(default_factory=list, max_length=200)


class ScopeChanges(Contract):
    upsert: list[ModelScope] = Field(default_factory=list, max_length=20)
    remove: list[Identifier] = Field(default_factory=list, max_length=20)


class ModelPatch(Contract):
    """Atomic edits by stable model IDs; omitted records remain unchanged."""

    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    root: Identifier | None = None
    nodes: NodeChanges = Field(default_factory=NodeChanges)
    edges: EdgeChanges = Field(default_factory=EdgeChanges)
    scopes: ScopeChanges = Field(default_factory=ScopeChanges)
    expose: list[SelectedField] = Field(default_factory=list, max_length=1000)
    hide: list[SelectedField] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unambiguous_changes(self):
        changed = self.name is not None or self.root is not None or self.expose or self.hide
        for label in ("nodes", "edges", "scopes"):
            changes = getattr(self, label)
            ids = [item.id for item in changes.upsert]
            if len(set(ids)) != len(ids) or len(set(changes.remove)) != len(changes.remove):
                raise ValueError(f"Duplicate {label} patch IDs are not allowed")
            if set(ids) & set(changes.remove):
                raise ValueError(f"Cannot upsert and remove the same {label} in one patch")
            changed = changed or changes.upsert or changes.remove
        if {(f.table, f.column) for f in self.expose} & {(f.table, f.column) for f in self.hide}:
            raise ValueError("Cannot expose and hide the same field in one patch")
        if not changed:
            raise ValueError("Supply at least one model change")
        return self


class LayoutUpdate(Contract):
    expected_revision: int = Field(ge=1)
    layout: ModelLayout


class ExploreUpdate(Contract):
    expected_revision: int = Field(ge=1)
    explore: ExploreState


class PreviewCreate(Contract):
    """Named test inputs only; never result rows or copied model definitions."""
    name: str = Field(min_length=1, max_length=128)
    explore: ExploreState

    @model_validator(mode="after")
    def trim_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter a preview name")
        return self


class PreviewUpdate(PreviewCreate):
    expected_revision: int = Field(ge=1)


class SavedPreview(PreviewCreate):
    id: str = Field(pattern=r"^preview_[0-9a-f]{32}$")
    model_id: str = Field(pattern=r"^model_[0-9a-f]{32}$")
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


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
