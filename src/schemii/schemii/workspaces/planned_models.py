"""Review contracts for workspace metadata and optional target lifecycle."""

from typing import Annotated

from pydantic import Field, field_validator

from schemii.common.api.models import ApiModel
from schemii.common.connections.models import DatabaseName

from .models import NamespaceName, WorkspaceName, _identifier


class WorkspaceMetadataUpdate(ApiModel):
    """Rename a workspace without changing its design, layout, or target."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    name: WorkspaceName

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class WorkspaceTargetUpdate(ApiModel):
    """Attach or replace one exact PostgreSQL target without changing the design."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    connection_id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    database: DatabaseName
    namespace: NamespaceName

    @field_validator("database", "namespace")
    @classmethod
    def validate_target_identifier(cls, value: str) -> str:
        return _identifier(value)
