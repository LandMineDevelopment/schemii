"""Strict domain models for PostgreSQL catalog snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


MAX_CATALOG_TEXT_BYTES = 1024 * 1024


def _validate_identifier(value: str) -> str:
    if "\x00" in value or len(value.encode("utf-8")) > 63:
        raise ValueError("value must be a valid PostgreSQL identifier")
    return value


def _validate_catalog_text(value: str) -> str:
    if "\x00" in value or len(value.encode("utf-8")) > MAX_CATALOG_TEXT_BYTES:
        raise ValueError("catalog text is invalid or too large")
    return value


PostgresIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1),
    AfterValidator(_validate_identifier),
]
CatalogText = Annotated[
    str,
    Field(strict=True),
    AfterValidator(_validate_catalog_text),
]


class _PostgresModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class PostgresConnectionTestResult(_PostgresModel):
    ok: Literal[True] = True
    database: PostgresIdentifier
    server_version: CatalogText


class PostgresColumn(_PostgresModel):
    name: PostgresIdentifier
    ordinal: Annotated[int, Field(ge=1, le=1600)]
    data_type: CatalogText
    nullable: bool
    default_expression: CatalogText | None = None
    identity: Literal["always", "by_default"] | None = None
    generated: Literal["stored", "virtual"] | None = None
    collation_schema: PostgresIdentifier | None = None
    collation_name: PostgresIdentifier | None = None

    @model_validator(mode="after")
    def require_complete_collation(self) -> "PostgresColumn":
        if (self.collation_schema is None) != (self.collation_name is None):
            raise ValueError("column collation must contain both schema and name")
        return self


class PostgresPrimaryKey(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    columns: Annotated[tuple[PostgresIdentifier, ...], Field(min_length=1, max_length=1600)]
    definition: CatalogText
    validated: bool
    deferrable: bool
    initially_deferred: bool


class PostgresUniqueConstraint(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    columns: Annotated[tuple[PostgresIdentifier, ...], Field(min_length=1, max_length=1600)]
    definition: CatalogText
    validated: bool
    deferrable: bool
    initially_deferred: bool


class PostgresCheckConstraint(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    columns: Annotated[tuple[PostgresIdentifier, ...], Field(max_length=1600)]
    definition: CatalogText
    validated: bool


class PostgresNotNullConstraint(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    columns: Annotated[tuple[PostgresIdentifier, ...], Field(min_length=1, max_length=1)]
    definition: CatalogText
    validated: bool


class PostgresExclusionConstraint(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    columns: Annotated[tuple[PostgresIdentifier, ...], Field(max_length=1600)]
    definition: CatalogText
    validated: bool
    deferrable: bool
    initially_deferred: bool


class PostgresIndex(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    definition: CatalogText
    method: PostgresIdentifier
    unique: bool
    valid: bool
    predicate: CatalogText | None = None


class PostgresTrigger(_PostgresModel):
    name: PostgresIdentifier
    table: PostgresIdentifier
    definition: CatalogText
    enabled: Literal["origin", "disabled", "replica", "always"]


class PostgresForeignKeyRelationship(_PostgresModel):
    name: PostgresIdentifier
    source_namespace: PostgresIdentifier
    source_table: PostgresIdentifier
    source_columns: Annotated[
        tuple[PostgresIdentifier, ...],
        Field(min_length=1, max_length=1600),
    ]
    target_namespace: PostgresIdentifier
    target_table: PostgresIdentifier
    target_columns: Annotated[
        tuple[PostgresIdentifier, ...],
        Field(min_length=1, max_length=1600),
    ]
    definition: CatalogText
    on_update: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]
    on_delete: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]
    match_type: Literal["FULL", "PARTIAL", "SIMPLE"]
    validated: bool
    deferrable: bool
    initially_deferred: bool

    @model_validator(mode="after")
    def require_matching_column_counts(self) -> "PostgresForeignKeyRelationship":
        if len(self.source_columns) != len(self.target_columns):
            raise ValueError("foreign-key column counts must match")
        return self


class PostgresFunction(_PostgresModel):
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    kind: Literal["function", "procedure"]
    identity_arguments: CatalogText
    arguments: CatalogText
    return_type: CatalogText | None = None
    language: PostgresIdentifier
    definition: CatalogText


class PostgresView(_PostgresModel):
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    columns: tuple[PostgresColumn, ...]
    query_definition: CatalogText


class PostgresMaterializedView(_PostgresModel):
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    columns: tuple[PostgresColumn, ...]
    query_definition: CatalogText
    populated: bool


class PostgresTable(_PostgresModel):
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    kind: Literal["table", "partitioned_table"]
    is_partition: bool
    partition_key: CatalogText | None = None
    columns: tuple[PostgresColumn, ...]
    primary_key: PostgresPrimaryKey | None = None
    unique_constraints: tuple[PostgresUniqueConstraint, ...] = ()
    checks: tuple[PostgresCheckConstraint, ...] = ()
    not_null_constraints: tuple[PostgresNotNullConstraint, ...] = ()
    exclusion_constraints: tuple[PostgresExclusionConstraint, ...] = ()
    indexes: tuple[PostgresIndex, ...] = ()
    triggers: tuple[PostgresTrigger, ...] = ()


class _PostgresCatalogContent(_PostgresModel):
    database: PostgresIdentifier
    namespace: PostgresIdentifier
    server_version: CatalogText
    server_version_num: Annotated[int, Field(ge=0)]
    server_timezone: CatalogText
    tables: tuple[PostgresTable, ...]
    relationships: tuple[PostgresForeignKeyRelationship, ...]
    functions: tuple[PostgresFunction, ...]
    views: tuple[PostgresView, ...]
    materialized_views: tuple[PostgresMaterializedView, ...]


def _catalog_digest(catalog: Any) -> str:
    payload = catalog.model_dump(
        mode="json",
        exclude={"captured_at", "fingerprint"},
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PostgresCatalog(_PostgresCatalogContent):
    captured_at: datetime
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("captured_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("captured_at must be UTC")
        return value

    @model_validator(mode="after")
    def require_matching_fingerprint(self) -> "PostgresCatalog":
        if self.fingerprint != _catalog_digest(self):
            raise ValueError("fingerprint does not match the catalog")
        return self


def compute_catalog_fingerprint(catalog: PostgresCatalog) -> str:
    """Return the canonical SHA-256 fingerprint for catalog authority fields."""

    return _catalog_digest(catalog)


def build_postgres_catalog(
    *,
    database: str,
    namespace: str,
    server_version: str,
    server_version_num: int,
    server_timezone: str,
    tables: tuple[PostgresTable, ...],
    relationships: tuple[PostgresForeignKeyRelationship, ...],
    functions: tuple[PostgresFunction, ...],
    views: tuple[PostgresView, ...],
    materialized_views: tuple[PostgresMaterializedView, ...],
    captured_at: datetime,
) -> PostgresCatalog:
    """Build a validated catalog and derive its content fingerprint."""

    content = _PostgresCatalogContent(
        database=database,
        namespace=namespace,
        server_version=server_version,
        server_version_num=server_version_num,
        server_timezone=server_timezone,
        tables=tables,
        relationships=relationships,
        functions=functions,
        views=views,
        materialized_views=materialized_views,
    )
    return PostgresCatalog(
        **content.model_dump(),
        captured_at=captured_at,
        fingerprint=_catalog_digest(content),
    )
