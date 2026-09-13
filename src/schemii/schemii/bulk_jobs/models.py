from typing import Literal
from pydantic import Field, model_validator, field_validator
from schemii.common.api.models import ApiModel


class BatchCreate(ApiModel):
    sql: str = Field(min_length=1, max_length=262144)


class JobCreate(ApiModel):
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    expected_workspace_revision: int = Field(strict=True, ge=1)
    expected_settings_revision: int = Field(strict=True, ge=1)
    name: str = Field(min_length=1, max_length=120)
    batches: list[BatchCreate] = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trimmed_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def bounded_sql(self):
        if sum(len(batch.sql.encode()) for batch in self.batches) > 1048576:
            raise ValueError("A bulk job may contain at most 1 MiB of SQL")
        return self


class JobCommand(ApiModel):
    expected_revision: int = Field(strict=True, ge=1)


class ReconcileCommand(JobCommand):
    outcome: Literal["committed", "rolled_back"]


class ResumeCommand(JobCommand):
    expected_workspace_revision: int = Field(strict=True, ge=1)
    expected_settings_revision: int = Field(strict=True, ge=1)
