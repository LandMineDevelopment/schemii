"""Source-of-truth contracts for database-independent Schemii designs."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from schemii.common.api.models import ApiModel


DesignIdentifier = Annotated[str, Field(min_length=1, max_length=63)]
DesignObjectId = Annotated[str, Field(pattern=r"^[a-z]+_[0-9a-f]{32}$")]
DesignExpression = Annotated[str, Field(max_length=262_144)]
DesignRevision = Annotated[int, Field(strict=True, ge=0)]


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
    """Desired function or procedure definition exportable without a live database."""

    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["function", "procedure"]
    arguments: DesignExpression
    return_type: Annotated[str, Field(max_length=512)] | None = None
    language: DesignIdentifier
    definition: DesignExpression


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


class ViewAnalysisSourceColumn(ApiModel):
    name: str
    data_type: str


class ViewAnalysisSource(ApiModel):
    namespace: str
    name: str
    kind: str
    resolved: bool
    aliases: list[str] = Field(default_factory=list)
    column_count: int = 0
    columns: list[ViewAnalysisSourceColumn] = Field(default_factory=list)


class ViewAnalysisInput(ApiModel):
    source: str | None = None
    column: str
    resolved: bool


class ViewAnalysisOutput(ApiModel):
    ordinal: int
    name: str | None = None
    data_type: str | None = None
    derivation: Literal["direct", "expression", "aggregate", "window", "constant"]
    expression: str | None = None
    inputs: list[ViewAnalysisInput] = Field(default_factory=list)


class ViewAnalysisTransformation(ApiModel):
    kind: Literal[
        "stages",
        "joins",
        "filters",
        "groups",
        "aggregates",
        "windows",
        "having",
        "distinct",
        "sets",
        "sorts",
        "limits",
    ]
    count: int
    items: list[str] = Field(default_factory=list)
    sql: str | None = None


class ViewAnalysisConsumer(ApiModel):
    id: DesignObjectId
    name: DesignIdentifier
    kind: Literal["view", "materialized_view"]


class DesignViewAnalysisRequest(ApiModel):
    view_id: DesignObjectId | None = None
    name: DesignIdentifier
    definition: DesignExpression


class DesignViewAnalysis(ApiModel):
    status: Literal["available", "partial"]
    sources: list[ViewAnalysisSource] = Field(default_factory=list)
    transformations: list[ViewAnalysisTransformation] = Field(default_factory=list)
    outputs: list[ViewAnalysisOutput] = Field(default_factory=list)
    consumers: list[ViewAnalysisConsumer] = Field(default_factory=list)
    stage_count: int = 0
    join_count: int = 0
    filter_count: int = 0
    grouping_count: int = 0
    aggregate_count: int = 0
    window_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class SchemiiDesignContent(ApiModel):
    """Database-independent desired schema authored by the user."""

    tables: list[DesignTable] = Field(default_factory=list, max_length=10_000)
    relationships: list[DesignRelationship] = Field(default_factory=list, max_length=20_000)
    functions: list[DesignFunction] = Field(default_factory=list, max_length=5_000)
    views: list[DesignView] = Field(default_factory=list, max_length=5_000)


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
