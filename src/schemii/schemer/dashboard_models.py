"""Dashboard configuration only: no query results are retained."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, model_validator
from schemii.schemoo.models import Contract, SelectedField, ScopeSelection, ReportFilter


class TimeAnalysis(Contract):
    table: str = Field(min_length=1, max_length=200)
    column: str = Field(min_length=1, max_length=200)
    granularity: Literal["day", "week", "month", "year"] = "month"
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    week_start: Literal["monday", "sunday"] = "monday"
    comparison: Literal["none", "previous_period", "prior_year"] = "none"
    running_total: bool = False

    @model_validator(mode="after")
    def valid_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone, such as UTC or America/New_York") from None
        return self


class DashboardTile(Contract):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=128)
    kind: Literal["detail", "aggregate", "bar", "line", "donut", "kpi"]
    dimensions: list[SelectedField] = Field(default_factory=list, max_length=64)
    measures: list[SelectedField] = Field(default_factory=list, max_length=64)
    detail_fields: list[SelectedField] = Field(default_factory=list, max_length=64)
    selections: dict[str, ScopeSelection] = Field(default_factory=dict, max_length=20)
    report_filters: list[ReportFilter] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=100, ge=1, le=100)
    time_analysis: TimeAnalysis | None = None

    @model_validator(mode="after")
    def valid_view(self):
        if not self.title.strip():
            raise ValueError("Tile title cannot be blank")
        if any(f.aggregate != "none" for f in self.dimensions + self.detail_fields):
            raise ValueError("Dimensions and detail columns must be raw fields")
        if len(self.dimensions) + len(self.measures) > 64:
            raise ValueError("A tile supports at most 64 output fields")
        for fields in (self.dimensions, self.measures, self.detail_fields):
            if len({(f.table, f.column, f.aggregate) for f in fields}) != len(fields):
                raise ValueError("Duplicate tile fields are not allowed")
        if self.kind == "detail":
            if not self.detail_fields or self.dimensions or self.measures:
                raise ValueError("Detail tiles require detail columns and no grouped fields")
        elif not self.measures:
            raise ValueError("Analytic tiles require at least one measure")
        if self.kind in {"bar", "line", "donut"} and not self.dimensions:
            raise ValueError("Charts require at least one dimension")
        if self.kind == "donut" and len(self.measures) != 1:
            raise ValueError("Donut charts require exactly one measure")
        if self.kind == "kpi" and (self.dimensions or len(self.measures) != 1):
            raise ValueError("KPI tiles require one measure and no dimensions")
        if self.time_analysis:
            if (self.time_analysis.table, self.time_analysis.column) not in {(f.table, f.column) for f in self.dimensions}:
                raise ValueError("The time field must be a selected dimension")
            if any(f.aggregate == "none" for f in self.measures):
                raise ValueError("Time analysis measures require an explicit aggregation")
        return self


class DashboardCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    model_id: str = Field(pattern=r"^model_[0-9a-f]{32}$")
    model_revision: int = Field(ge=1)
    optional_filters: list[str] = Field(default_factory=list, max_length=20)
    selections: dict[str, ScopeSelection] = Field(default_factory=dict, max_length=20)
    tiles: list[DashboardTile] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_dashboard(self):
        if not self.name.strip():
            raise ValueError("Dashboard name cannot be blank")
        if len({t.id for t in self.tiles}) != len(self.tiles):
            raise ValueError("Tile IDs must be unique within a dashboard")
        if len(set(self.optional_filters)) != len(self.optional_filters) or any(not item or len(item) > 200 for item in self.optional_filters):
            raise ValueError("Optional dashboard filters need unique model scope IDs")
        return self


class DashboardUpdate(DashboardCreate):
    expected_revision: int = Field(ge=1)


class Dashboard(DashboardCreate):
    id: str
    owner_id: str
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
