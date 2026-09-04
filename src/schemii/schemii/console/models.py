"""Schemii-owned durable SQL Console library and history contracts."""

from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from schemii.common.api.models import ApiModel


QueryName = Annotated[str, Field(min_length=1, max_length=80)]
QuerySql = Annotated[str, Field(min_length=1, max_length=1024 * 1024)]


class ConsoleSavedQuery(ApiModel):
    """One named query owned by a user inside an exact workspace target."""

    id: str = Field(pattern=r"^sq_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    name: QueryName
    sql: QuerySql
    starter: bool = False
    created_at: datetime
    updated_at: datetime


class ConsoleSavedQueryCreate(ApiModel):
    """Create a durable named query in the current user's metadata library."""

    name: QueryName
    sql: QuerySql
    starter: bool = False

    @field_validator("name", "sql", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ConsoleSavedQueryUpdate(ApiModel):
    """Optimistically update one durable named query."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    name: QueryName | None = None
    sql: QuerySql | None = None

    @field_validator("name", "sql", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> "ConsoleSavedQueryUpdate":
        if self.name is None and self.sql is None:
            raise ValueError("name or sql is required")
        return self


class ConsoleSavedQueryList(ApiModel):
    """Owner-visible saved queries for one workspace."""

    queries: list[ConsoleSavedQuery]


class ConsoleHistoryEntry(ApiModel):
    """One replayable SQL script, without execution or result telemetry."""

    sql: QuerySql
    ran_at: datetime


class ConsoleHistoryList(ApiModel):
    """Newest-first bounded query history for one workspace."""

    queries: list[ConsoleHistoryEntry]
