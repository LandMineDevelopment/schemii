"""Review contracts for mutable workspace metadata."""

from typing import Annotated

from pydantic import Field, field_validator

from schemii.common.api.models import ApiModel

from .models import WorkspaceName


class WorkspaceMetadataUpdate(ApiModel):
    """Rename a workspace without changing its design, layout, or target."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    name: WorkspaceName

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
