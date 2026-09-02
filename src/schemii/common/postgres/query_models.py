"""Reusable contracts for source-derived PostgreSQL query analysis."""

from typing import Literal

from pydantic import Field

from schemii.common.api.models import ApiModel


class QueryAnalysisSourceColumn(ApiModel):
    name: str
    data_type: str
    uses: list[str] = Field(default_factory=list)


class QueryAnalysisSource(ApiModel):
    namespace: str
    name: str
    kind: str
    resolved: bool
    aliases: list[str] = Field(default_factory=list)
    column_count: int = 0
    columns: list[QueryAnalysisSourceColumn] = Field(default_factory=list)


class QueryAnalysisInput(ApiModel):
    source: str | None = None
    column: str
    resolved: bool


class QueryAnalysisExpression(ApiModel):
    expression: str
    inputs: list[QueryAnalysisInput] = Field(default_factory=list)
    scope: str | None = None


class QueryAnalysisJoin(ApiModel):
    join_type: str
    target: str
    alias: str | None = None
    expression: str | None = None
    inputs: list[QueryAnalysisInput] = Field(default_factory=list)
    scope: str | None = None


class QueryAnalysisOutput(ApiModel):
    ordinal: int
    name: str | None = None
    data_type: str | None = None
    derivation: Literal["direct", "expression", "aggregate", "window", "constant"]
    expression: str | None = None
    inputs: list[QueryAnalysisInput] = Field(default_factory=list)


class QueryAnalysisParticipantColumn(ApiModel):
    name: str
    data_type: str | None = None
    roles: list[str] = Field(default_factory=list)
    filter_only: bool = False


class QueryAnalysisParticipant(ApiModel):
    reference: str
    namespace: str | None = None
    name: str
    kind: str
    resolved: bool
    columns: list[QueryAnalysisParticipantColumn] = Field(default_factory=list)


class QueryAnalysisStep(ApiModel):
    ordinal: int
    kind: Literal[
        "cte",
        "derived_table",
        "subquery",
        "set_branch",
        "table_function",
        "final",
    ]
    result_name: str
    participants: list[QueryAnalysisParticipant] = Field(default_factory=list)
    joins: list[QueryAnalysisJoin] = Field(default_factory=list)
    row_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    aggregate_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    grouping: list[QueryAnalysisExpression] = Field(default_factory=list)
    group_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    ordering: list[QueryAnalysisExpression] = Field(default_factory=list)
    distinct: bool = False
    limit: str | None = None
    outputs: list[QueryAnalysisOutput] = Field(default_factory=list)


class QueryAnalysisTransformation(ApiModel):
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


class QueryAnalysis(ApiModel):
    status: Literal["available", "partial"]
    sources: list[QueryAnalysisSource] = Field(default_factory=list)
    transformations: list[QueryAnalysisTransformation] = Field(default_factory=list)
    outputs: list[QueryAnalysisOutput] = Field(default_factory=list)
    formatted_sql: str
    stages: list[str] = Field(default_factory=list)
    joins: list[QueryAnalysisJoin] = Field(default_factory=list)
    row_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    aggregate_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    grouping: list[QueryAnalysisExpression] = Field(default_factory=list)
    group_filters: list[QueryAnalysisExpression] = Field(default_factory=list)
    ordering: list[QueryAnalysisExpression] = Field(default_factory=list)
    distinct: bool = False
    limit: str | None = None
    set_operations: list[str] = Field(default_factory=list)
    query_steps: list[QueryAnalysisStep] = Field(default_factory=list)
    stage_count: int = 0
    join_count: int = 0
    filter_count: int = 0
    grouping_count: int = 0
    aggregate_count: int = 0
    window_count: int = 0
    warnings: list[str] = Field(default_factory=list)
