"""Pydantic contracts for Schemii's saved workspace state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from schemii.common.api.models import ApiModel
from schemii.common.connections.models import DatabaseName


NamespaceName = Annotated[str, Field(min_length=1, max_length=63)]
TableName = Annotated[str, Field(min_length=1, max_length=63)]
WorkspaceName = Annotated[str, Field(min_length=1, max_length=128)]
Coordinate = Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]


def _identifier(value: str) -> str:
    if not value or "\x00" in value or len(value.encode("utf-8")) > 63:
        raise ValueError("value must be a valid PostgreSQL identifier")
    return value


class TablePosition(ApiModel):
    """Saved canvas coordinates for one live PostgreSQL table."""

    name: TableName
    x: Coordinate
    y: Coordinate

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _identifier(value)


class SchemiiWorkspaceCreate(ApiModel):
    """Create a design workspace, optionally attached to one exact PostgreSQL target."""

    name: WorkspaceName = "Untitled schema"
    connection_id: str | None = Field(default=None, pattern=r"^pg_[0-9a-f]{32}$")
    database: DatabaseName | None = None
    namespace: NamespaceName | None = None

    @field_validator("database", "namespace")
    @classmethod
    def normalize_target(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def complete_optional_target(self) -> "SchemiiWorkspaceCreate":
        target = (self.connection_id, self.database, self.namespace)
        if any(value is not None for value in target) and not all(
            value is not None for value in target
        ):
            raise ValueError(
                "connectionId, database, and namespace must be supplied together"
            )
        return self


class SchemiiWorkspaceLayoutUpdate(ApiModel):
    """Optimistic revisions and unique live-table positions to persist."""

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
    """Owner-scoped design workspace with an optional PostgreSQL target."""

    id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    name: WorkspaceName
    connection_id: str | None = Field(default=None, pattern=r"^pg_[0-9a-f]{32}$")
    database: DatabaseName | None = None
    namespace: NamespaceName | None = None
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
        target = (self.connection_id, self.database, self.namespace)
        if any(value is not None for value in target) and not all(
            value is not None for value in target
        ):
            raise ValueError("workspace PostgreSQL target must be complete or absent")
        return self
