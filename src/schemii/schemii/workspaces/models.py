"""Pydantic contracts for Schemii's saved workspace state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from schemii.common.api.models import ApiModel
from schemii.common.connections.models import DatabaseName


NamespaceName = Annotated[str, Field(min_length=1, max_length=63)]
TableName = Annotated[str, Field(min_length=1, max_length=63)]
Coordinate = Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]


def _identifier(value: str) -> str:
    if not value or "\x00" in value or len(value.encode("utf-8")) > 63:
        raise ValueError("value must be a valid PostgreSQL identifier")
    return value


class TablePosition(ApiModel):
    name: TableName
    x: Coordinate
    y: Coordinate

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _identifier(value)


class SchemiiWorkspaceCreate(ApiModel):
    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    database: DatabaseName
    namespace: NamespaceName

    @field_validator("database", "namespace")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        return _identifier(value)


class SchemiiWorkspaceLayoutUpdate(ApiModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_connection_revision: Annotated[int, Field(strict=True, ge=1)]
    tables: list[TablePosition] = Field(max_length=10_000)

    @model_validator(mode="after")
    def unique_table_names(self) -> "SchemiiWorkspaceLayoutUpdate":
        names = [table.name for table in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("table positions must have unique names")
        return self


class SchemiiWorkspace(ApiModel):
    id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    database: DatabaseName
    namespace: NamespaceName
    tables: list[TablePosition]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("workspace timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_workspace(self) -> "SchemiiWorkspace":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        names = [table.name for table in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("workspace table positions must have unique names")
        return self
