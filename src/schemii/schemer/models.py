"""Schemer consumes saved model rules; clients supply only report inputs."""

from pydantic import Field
from schemii.schemoo.models import Contract, ExploreState


class ReportQuery(Contract):
    model_id: str = Field(pattern=r"^model_[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)
    explore: ExploreState
