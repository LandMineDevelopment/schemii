"""Source-of-truth contracts for database-independent Schemii designs."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from schemii.common.api.models import ApiModel
from schemii.common.postgres.query_models import QueryAnalysis
from schemii.common.postgres.routine_analysis import analyze_routine_definition
from schemii.common.postgres.trigger_analysis import analyze_trigger_definition
from schemii.common.postgres.type_analysis import analyze_type_definition


DesignIdentifier = Annotated[str, Field(min_length=1, max_length=63)]
DesignObjectId = Annotated[str, Field(pattern=r"^[a-z]+_[0-9a-f]{32}$")]
DesignExpression = Annotated[str, Field(max_length=262_144)]
DesignRevision = Annotated[int, Field(strict=True, ge=0)]


class DesignDomainCheck(ApiModel):
    """One source-derived domain CHECK constraint."""

    name: DesignIdentifier | None = None
    expression: DesignExpression


class DesignType(ApiModel):
    """Desired enum or domain whose complete contract is derived from SQL."""

    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["enum", "domain"]
    enum_values: list[Annotated[str, Field(max_length=63)]] = Field(
        default_factory=list,
        max_length=10_000,
    )
    base_type: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    default_expression: DesignExpression | None = None
    not_null: bool = False
    checks: list[DesignDomainCheck] = Field(default_factory=list, max_length=10_000)
    collation: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    definition: DesignExpression

    @model_validator(mode="before")
    @classmethod
    def derive_contract_from_definition(cls, value: object) -> object:
        """Discard submitted type metadata and derive it from source."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        definition = normalized.get("definition")
        if not isinstance(definition, str):
            return normalized
        contract = analyze_type_definition(definition)
        for key in (
            "name",
            "kind",
            "enumValues",
            "enum_values",
            "baseType",
            "base_type",
            "defaultExpression",
            "default_expression",
            "notNull",
            "not_null",
            "checks",
            "collation",
        ):
            normalized.pop(key, None)
        normalized.update(
            {
                "name": contract.name,
                "kind": contract.kind,
                "enum_values": list(contract.enum_values),
                "base_type": contract.base_type,
                "default_expression": contract.default_expression,
                "not_null": contract.not_null,
                "checks": [
                    {"name": check.name, "expression": check.expression}
                    for check in contract.checks
                ],
                "collation": contract.collation,
            }
        )
        return normalized


class DesignTypeAnalysisRequest(ApiModel):
    definition: DesignExpression


class DesignTypeAnalysis(ApiModel):
    name: DesignIdentifier
    kind: Literal["enum", "domain"]
    enum_values: list[Annotated[str, Field(max_length=63)]]
    base_type: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    default_expression: DesignExpression | None = None
    not_null: bool
    checks: list[DesignDomainCheck]
    collation: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class DesignColumn(ApiModel):
    """One stable design column independent of any live PostgreSQL OID."""

    id: DesignObjectId
    name: DesignIdentifier
    data_type: Annotated[str, Field(min_length=1, max_length=512)]
    nullable: bool = True
    default_expression: DesignExpression | None = None
    identity: Literal["always", "by_default"] | None = None
    generated_expression: DesignExpression | None = None
    generated_source_column_ids: list[DesignObjectId] = Field(
        default_factory=list,
        max_length=1600,
    )


class DesignKeyConstraint(ApiModel):
    """Primary or unique key over stable column IDs."""

    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["primary", "unique"]
    column_ids: list[DesignObjectId] = Field(min_length=1, max_length=1600)


class DesignCheckConstraint(ApiModel):
    """Named table check with source-derived stable column dependencies."""

    id: DesignObjectId
    name: DesignIdentifier
    expression: DesignExpression
    column_ids: list[DesignObjectId] = Field(default_factory=list, max_length=1600)


class DesignIndex(ApiModel):
    """Desired index definition attached to one design table."""

    id: DesignObjectId
    name: DesignIdentifier
    method: DesignIdentifier = "btree"
    column_ids: list[DesignObjectId] = Field(default_factory=list, max_length=1600)
    expression: DesignExpression | None = None
    expression_source_column_ids: list[DesignObjectId] = Field(
        default_factory=list,
        max_length=1600,
    )
    predicate: DesignExpression | None = None
    predicate_column_ids: list[DesignObjectId] = Field(
        default_factory=list,
        max_length=1600,
    )
    unique: bool = False


class DesignTable(ApiModel):
    """Desired table shape and locally owned constraints."""

    id: DesignObjectId
    name: DesignIdentifier
    columns: list[DesignColumn] = Field(min_length=1, max_length=1600)
    keys: list[DesignKeyConstraint] = Field(default_factory=list, max_length=1600)
    checks: list[DesignCheckConstraint] = Field(default_factory=list, max_length=1600)
    indexes: list[DesignIndex] = Field(default_factory=list, max_length=1600)


class DesignRelationship(ApiModel):
    """Desired foreign key expressed with stable table and column IDs."""

    id: DesignObjectId
    name: DesignIdentifier
    source_table_id: DesignObjectId
    source_column_ids: list[DesignObjectId] = Field(min_length=1, max_length=1600)
    target_table_id: DesignObjectId
    target_column_ids: list[DesignObjectId] = Field(min_length=1, max_length=1600)
    on_update: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"
    on_delete: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"
    deferrable: bool = False
    initially_deferred: bool = False


class DesignFunction(ApiModel):
    """Desired routine whose presentation contract is always derived from SQL."""

    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["function", "procedure"]
    arguments: DesignExpression
    identity_arguments: DesignExpression
    return_type: DesignExpression | None = None
    language: DesignIdentifier
    definition: DesignExpression

    @model_validator(mode="before")
    @classmethod
    def derive_contract_from_definition(cls, value: object) -> object:
        """Never trust separately submitted routine metadata over its source."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        definition = normalized.get("definition")
        if not isinstance(definition, str):
            return normalized
        contract = analyze_routine_definition(definition)
        for key in (
            "name",
            "kind",
            "arguments",
            "identityArguments",
            "identity_arguments",
            "returnType",
            "return_type",
            "language",
        ):
            normalized.pop(key, None)
        normalized.update(
            {
                "name": contract.name,
                "kind": contract.kind,
                "arguments": contract.arguments,
                "identity_arguments": contract.identity_arguments,
                "return_type": contract.return_type,
                "language": contract.language,
            }
        )
        return normalized


class DesignRoutineAnalysisRequest(ApiModel):
    definition: DesignExpression


class DesignRoutineAnalysis(ApiModel):
    name: DesignIdentifier
    kind: Literal["function", "procedure"]
    arguments: DesignExpression
    identity_arguments: DesignExpression
    return_type: DesignExpression | None = None
    language: DesignIdentifier


class DesignTrigger(ApiModel):
    """Desired trigger whose complete presentation contract comes from SQL."""

    id: DesignObjectId
    name: DesignIdentifier
    relation_name: DesignIdentifier
    timing: Literal["before", "after", "instead_of"]
    events: list[Literal["insert", "update", "delete", "truncate"]] = Field(
        min_length=1,
        max_length=4,
    )
    orientation: Literal["row", "statement"]
    function_name: Annotated[str, Field(min_length=1, max_length=255)]
    function_arguments: list[DesignExpression] = Field(default_factory=list, max_length=100)
    update_columns: list[DesignIdentifier] = Field(default_factory=list, max_length=1600)
    referenced_columns: list[DesignIdentifier] = Field(default_factory=list, max_length=1600)
    when_expression: DesignExpression | None = None
    transition_relations: list[DesignExpression] = Field(default_factory=list, max_length=2)
    constraint: bool
    deferrable: bool
    initially_deferred: bool
    definition: DesignExpression

    @model_validator(mode="before")
    @classmethod
    def derive_contract_from_definition(cls, value: object) -> object:
        """Discard submitted trigger metadata and derive it from source."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        definition = normalized.get("definition")
        if not isinstance(definition, str):
            return normalized
        contract = analyze_trigger_definition(definition)
        for key in (
            "name",
            "relationName",
            "relation_name",
            "timing",
            "events",
            "orientation",
            "functionName",
            "function_name",
            "functionArguments",
            "function_arguments",
            "updateColumns",
            "update_columns",
            "referencedColumns",
            "referenced_columns",
            "whenExpression",
            "when_expression",
            "transitionRelations",
            "transition_relations",
            "constraint",
            "deferrable",
            "initiallyDeferred",
            "initially_deferred",
        ):
            normalized.pop(key, None)
        normalized.update(
            {
                "name": contract.name,
                "relation_name": contract.relation_name,
                "timing": contract.timing,
                "events": list(contract.events),
                "orientation": contract.orientation,
                "function_name": contract.function_name,
                "function_arguments": list(contract.function_arguments),
                "update_columns": list(contract.update_columns),
                "referenced_columns": list(contract.referenced_columns),
                "when_expression": contract.when_expression,
                "transition_relations": list(contract.transition_relations),
                "constraint": contract.constraint,
                "deferrable": contract.deferrable,
                "initially_deferred": contract.initially_deferred,
            }
        )
        return normalized


class DesignTriggerAnalysisRequest(ApiModel):
    definition: DesignExpression


class DesignTriggerAnalysis(ApiModel):
    name: DesignIdentifier
    relation_name: DesignIdentifier
    timing: Literal["before", "after", "instead_of"]
    events: list[Literal["insert", "update", "delete", "truncate"]]
    orientation: Literal["row", "statement"]
    function_name: Annotated[str, Field(min_length=1, max_length=255)]
    function_arguments: list[DesignExpression]
    update_columns: list[DesignIdentifier]
    referenced_columns: list[DesignIdentifier]
    when_expression: DesignExpression | None = None
    transition_relations: list[DesignExpression]
    constraint: bool
    deferrable: bool
    initially_deferred: bool


class DesignView(ApiModel):
    """Desired ordinary or materialized view definition."""

    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["view", "materialized_view"]
    definition: DesignExpression
    populate_on_create: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_population_intent(cls, value: object) -> object:
        """Read the former ambiguous field without retaining it in new designs."""

        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy = migrated.pop("populated", None)
        if "populateOnCreate" not in migrated and "populate_on_create" not in migrated:
            if legacy is not None:
                migrated["populateOnCreate"] = legacy
        return migrated

    @model_validator(mode="after")
    def valid_population_intent(self) -> "DesignView":
        if self.kind == "view" and self.populate_on_create is not None:
            raise ValueError("ordinary views cannot have materialized population intent")
        if self.kind == "materialized_view" and self.populate_on_create is None:
            self.populate_on_create = True
        return self


class ViewAnalysisConsumer(ApiModel):
    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["view", "materialized_view"]


class DesignViewAnalysisRequest(ApiModel):
    view_id: DesignObjectId | None = None
    name: DesignIdentifier
    definition: DesignExpression


class DesignViewAnalysis(QueryAnalysis):
    consumers: list[ViewAnalysisConsumer] = Field(default_factory=list)


class SchemiiDesignContent(ApiModel):
    """Database-independent desired schema authored by the user."""

    types: list[DesignType] = Field(default_factory=list, max_length=5_000)
    tables: list[DesignTable] = Field(default_factory=list, max_length=10_000)
    relationships: list[DesignRelationship] = Field(default_factory=list, max_length=20_000)
    functions: list[DesignFunction] = Field(default_factory=list, max_length=5_000)
    views: list[DesignView] = Field(default_factory=list, max_length=5_000)
    triggers: list[DesignTrigger] = Field(default_factory=list, max_length=10_000)


class SchemiiDesign(ApiModel):
    """Versioned desired state; live PostgreSQL state is never stored here."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: DesignRevision
    content: SchemiiDesignContent
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SchemiiDesignReplace(ApiModel):
    """Optimistic complete replacement of user-authored desired state."""

    expected_design_revision: DesignRevision
    content: SchemiiDesignContent


class DesignObjectPosition(ApiModel):
    """Canvas position keyed by a stable desired-design object ID."""

    object_id: DesignObjectId
    layer: Literal["tables", "views"]
    x: Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]
    y: Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]


class SchemiiDesignLayoutContent(ApiModel):
    """Target-independent placement; cameras remain browser-owned."""

    objects: list[DesignObjectPosition] = Field(default_factory=list, max_length=20_000)

    @model_validator(mode="after")
    def unique_objects(self) -> "SchemiiDesignLayoutContent":
        object_ids = [position.object_id for position in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("design layout object IDs must be unique")
        return self


class SchemiiDesignLayout(ApiModel):
    """Versioned visual state validated against one desired-design revision."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=0)]
    design_revision: DesignRevision
    content: SchemiiDesignLayoutContent


class SchemiiDesignLayoutReplace(ApiModel):
    """Optimistic complete replacement of target-independent visual state."""

    expected_layout_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_design_revision: DesignRevision
    content: SchemiiDesignLayoutContent


class SchemiiDesignImportRequest(ApiModel):
    """Import an attached live catalog into desired state under an explicit strategy."""

    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: DesignRevision
    expected_catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: Literal["require_empty", "replace", "merge"] = "require_empty"


class SchemiiDesignExportRequest(ApiModel):
    """Render a design without requiring an attached PostgreSQL target."""

    expected_design_revision: DesignRevision
    format: Literal["postgresql_sql", "schemii_json"] = "postgresql_sql"
    include_drop_statements: bool = False


class SchemiiDesignExport(ApiModel):
    """Deterministic downloadable representation of one design revision."""

    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    design_revision: DesignRevision
    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: Literal["application/sql", "application/json"]
    content: Annotated[str, Field(max_length=16 * 1024 * 1024)]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
