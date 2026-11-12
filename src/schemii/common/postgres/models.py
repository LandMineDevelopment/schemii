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


class PostgresNamespace(_PostgresModel):
    """One schema visible to the PostgreSQL role behind a saved connection."""

    name: PostgresIdentifier
    system: bool


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


class PostgresType(_PostgresModel):
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    kind: Literal["enum", "domain"]
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


class PostgresObjectIdentity(_PostgresModel):
    kind: Literal["relation", "type", "routine", "schema"]
    namespace: PostgresIdentifier
    name: PostgresIdentifier
    signature: CatalogText = ""


class PostgresDependency(_PostgresModel):
    source: PostgresObjectIdentity
    target: PostgresObjectIdentity
    kind: CatalogText


class PostgresGrant(_PostgresModel):
    grantor: PostgresIdentifier
    grantee: PostgresIdentifier
    privilege: Literal["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
    grantable: bool


class PostgresPreservation(_PostgresModel):
    identity: PostgresObjectIdentity
    relation_kind: Literal["r", "p", "v", "m", "f", "S", "c"]
    owner: PostgresIdentifier
    actor: PostgresIdentifier
    acl: tuple[PostgresGrant, ...]
    reloptions: tuple[CatalogText, ...]
    tablespace: PostgresIdentifier | None
    access_method: PostgresIdentifier | None
    partition_bound: CatalogText | None
    partition_parents: tuple[PostgresObjectIdentity, ...]
    indexes: tuple[PostgresIndex, ...]
    default_grantees: tuple[PostgresIdentifier, ...]
    properties: CatalogText


class PostgresSafetyEvidence(_PostgresModel):
    version: Literal[1] = 1
    dependencies: tuple[PostgresDependency, ...]
    preservation: tuple[PostgresPreservation, ...]
    dependency_complete: bool
    unknown_reasons: tuple[CatalogText, ...]

    @field_validator("version", mode="before")
    @classmethod
    def strict_evidence_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unknown catalog evidence version")
        return value

    @model_validator(mode="after")
    def honest_completeness(self) -> "PostgresSafetyEvidence":
        if self.dependency_complete == bool(self.unknown_reasons):
            raise ValueError("dependency completeness must agree with unknown reasons")
        identities = [item.identity for item in self.preservation]
        if len(identities) != len(set(identities)):
            raise ValueError("preservation identities must be unique")
        return self


def dependency_closure(evidence: PostgresSafetyEvidence, seeds: set[PostgresObjectIdentity]) -> set[PostgresObjectIdentity]:
    """Conservative connected closure, including incoming/cross-namespace edges."""
    result = set(seeds)
    while True:
        size = len(result)
        for edge in evidence.dependencies:
            if edge.source in result or edge.target in result:
                result.update((edge.source, edge.target))
        if len(result) == size:
            return result


class _PostgresCatalogContent(_PostgresModel):
    fingerprint_version: Literal[1, 2] = 1
    safety: PostgresSafetyEvidence | None = None
    database: PostgresIdentifier
    namespace: PostgresIdentifier
    server_version: CatalogText
    server_version_num: Annotated[int, Field(ge=0)]
    server_timezone: CatalogText
    types: tuple[PostgresType, ...] = ()
    tables: tuple[PostgresTable, ...]
    relationships: tuple[PostgresForeignKeyRelationship, ...]
    functions: tuple[PostgresFunction, ...]
    views: tuple[PostgresView, ...]
    materialized_views: tuple[PostgresMaterializedView, ...]


# Every historical field is named, including nested defaulted fields. This is a
# wire-format contract, independent of future additions to the domain models.
_V1_COLUMN = {"name", "ordinal", "data_type", "nullable", "default_expression", "identity", "generated", "collation_schema", "collation_name"}
_V1_KEY = {"name", "table", "columns", "definition", "validated", "deferrable", "initially_deferred"}
_V1_CHECK = {"name", "table", "columns", "definition", "validated"}
_V1_INDEX = {"name", "table", "definition", "method", "unique", "valid", "predicate"}
_V1_TRIGGER = {"name", "table", "definition", "enabled"}
_V1_TABLE = {"namespace": True, "name": True, "kind": True, "is_partition": True, "partition_key": True,
    "columns": {"__all__": _V1_COLUMN}, "primary_key": _V1_KEY,
    "unique_constraints": {"__all__": _V1_KEY}, "checks": {"__all__": _V1_CHECK},
    "not_null_constraints": {"__all__": _V1_CHECK}, "exclusion_constraints": {"__all__": _V1_KEY},
    "indexes": {"__all__": _V1_INDEX}, "triggers": {"__all__": _V1_TRIGGER}}
_V1_VIEW = {"namespace": True, "name": True, "columns": {"__all__": _V1_COLUMN}, "query_definition": True}
_V1_PROJECTION = {"database": True, "namespace": True, "server_version": True, "server_version_num": True,
    "server_timezone": True, "types": {"__all__": {"namespace", "name", "kind", "definition"}},
    "tables": {"__all__": _V1_TABLE},
    "relationships": {"__all__": {"name", "source_namespace", "source_table", "source_columns", "target_namespace", "target_table", "target_columns", "definition", "on_update", "on_delete", "match_type", "validated", "deferrable", "initially_deferred"}},
    "functions": {"__all__": {"namespace", "name", "kind", "identity_arguments", "arguments", "return_type", "language", "definition"}},
    "views": {"__all__": _V1_VIEW}, "materialized_views": {"__all__": {**_V1_VIEW, "populated": True}}}


def _catalog_digest(catalog: Any) -> str:
    # Version1 is the frozen historical projection. Evidence is an explicit
    # new version; defaulted future fields must never reinterpret old authority.
    payload = (catalog.model_dump(mode="json", include=_V1_PROJECTION)
        if catalog.fingerprint_version == 1 else
        catalog.model_dump(mode="json", exclude={"captured_at", "fingerprint"}))
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

    @field_validator("fingerprint_version", mode="before")
    @classmethod
    def strict_fingerprint_version(cls, value: Any) -> int:
        if type(value) is not int or value not in {1,2}:
            raise ValueError("unknown catalog fingerprint version")
        return value

    @field_validator("captured_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("captured_at must be UTC")
        return value

    @model_validator(mode="after")
    def require_matching_fingerprint(self) -> "PostgresCatalog":
        if (self.fingerprint_version == 2) != (self.safety is not None):
            raise ValueError("version2 requires safety evidence; historical catalogs cannot acquire it")
        if self.safety is not None and self.safety.dependency_complete:
            expected = {PostgresObjectIdentity(kind="relation", namespace=item.namespace, name=item.name)
                for item in (*self.tables, *self.views, *self.materialized_views)}
            if not expected.issubset({item.identity for item in self.safety.preservation}):
                raise ValueError("complete safety evidence requires every selected relation descriptor")
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
    types: tuple[PostgresType, ...] = (),
    tables: tuple[PostgresTable, ...],
    relationships: tuple[PostgresForeignKeyRelationship, ...],
    functions: tuple[PostgresFunction, ...],
    views: tuple[PostgresView, ...],
    materialized_views: tuple[PostgresMaterializedView, ...],
    captured_at: datetime,
    safety: PostgresSafetyEvidence | None = None,
    fingerprint_version: Literal[1, 2] | None = None,
) -> PostgresCatalog:
    """Build a validated catalog and derive its content fingerprint."""

    selected_version = 2 if safety is not None else 1
    if fingerprint_version is not None and (type(fingerprint_version) is not int or fingerprint_version != selected_version):
        raise ValueError("catalog version and evidence must agree")
    content = _PostgresCatalogContent(
        fingerprint_version=selected_version,
        safety=safety,
        database=database,
        namespace=namespace,
        server_version=server_version,
        server_version_num=server_version_num,
        server_timezone=server_timezone,
        types=types,
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
