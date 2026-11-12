"""Dashboard configuration only: no query results are retained."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, model_validator, field_validator
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


class ReportOutput(SelectedField):
    """A semantic selected output, independent of position and display label."""

    kind: Literal["value", "comparison", "change", "percent_change", "running_total", "calculated"] = "value"


class MeasureOperand(Contract):
    output: ReportOutput | None = None
    constant: str | int | float | None = None
    scope: Literal["group", "source_population", "qualifying_groups"] = "group"

    @field_validator("constant",mode="before")
    @classmethod
    def numeric_constant(cls,value):
        if isinstance(value,bool):
            raise ValueError("A numeric constant cannot be true or false")
        return value

    @model_validator(mode="after")
    def valid_operand(self):
        if (self.output is None) == (self.constant is None):
            raise ValueError("Choose exactly one measure output or numeric constant")
        if self.constant is not None:
            try:
                value = Decimal(str(self.constant))
                if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",str(self.constant).strip()) or len(str(self.constant)) > 200 or not value.is_finite() or abs(value.adjusted()) > 4096:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise ValueError("Use a finite numeric constant of at most 200 characters and bounded magnitude") from None
            if self.scope != "group":
                raise ValueError("Numeric constants have no population scope")
        return self


class CalculatedMeasure(Contract):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=128)
    operation: Literal["add", "subtract", "multiply", "divide", "percent_of"]
    left: MeasureOperand
    right: MeasureOperand
    visible: bool = True

    @model_validator(mode="after")
    def named(self):
        if not self.label.strip():
            raise ValueError("Name the calculated measure")
        return self


class KPIComparison(Contract):
    label: str = Field(default="Target", min_length=1, max_length=100)
    baseline: MeasureOperand
    favorable: Literal["higher", "lower", "equal"]


def base_measures(tile):
    return [*tile.measures, *getattr(tile, "hidden_measures", [])]


def visible_measure_count(tile):
    return len(tile.measures) + sum(item.visible for item in getattr(tile, "calculations", []))


class ReportSort(ReportOutput):
    direction: Literal["asc", "desc"] = "asc"
    nulls: Literal["first", "last"] = "last"


class AggregateFilter(ReportOutput):
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "not_null"]
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.operator in {"is_null", "not_null"}:
            if self.value is not None:
                raise ValueError("NULL checks do not take a comparison value")
        elif self.value is None:
            raise ValueError("Choose a comparison value or an explicit NULL check")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Aggregate filter numbers must be finite")
        if isinstance(self.value, str) and (len(self.value) > 4000 or "\x00" in self.value):
            raise ValueError("Aggregate filter text must contain at most 4000 characters and no null bytes")
        return self


FORMAT_LOCALES = ("en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "pt-BR", "ja-JP", "zh-CN", "hi-IN", "ar-EG")


def format_decimal(value):
    if not isinstance(value, str) or len(value) > 200 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        raise ValueError("Enter an exact finite decimal of at most 200 characters")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Enter a bounded finite decimal threshold") from None
    if not number.is_finite() or abs(number.adjusted()) > 4096:
        raise ValueError("The threshold magnitude must be between 1e-4096 and 1e4096")
    return number


class FormatOutput(ReportOutput):
    kind: Literal["value", "comparison", "change", "percent_change", "running_total", "calculated", "kpi_baseline", "kpi_absolute", "kpi_percent"] = "value"


class NumberRule(Contract):
    operator: Literal["lt", "lte", "eq", "ne", "gte", "gt", "between", "is_null", "not_null"]
    value: str | None = Field(default=None, max_length=200)
    upper: str | None = Field(default=None, max_length=200)
    tone: Literal["neutral", "positive", "caution", "negative"] = "neutral"
    symbol: Literal["dot", "up", "down", "check", "warning"] = "dot"
    label: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def valid_rule(self):
        if not self.label.strip() or any(ord(c) < 32 for c in self.label):
            raise ValueError("Name the condition with plain readable text")
        if self.operator in {"is_null", "not_null"}:
            if self.value is not None or self.upper is not None:
                raise ValueError("NULL rules do not take thresholds")
        else:
            lower = format_decimal(self.value)
            if self.operator == "between":
                if lower > format_decimal(self.upper):
                    raise ValueError("The lower threshold must not exceed the upper threshold")
            elif self.upper is not None:
                raise ValueError("Only between rules take two thresholds")
        return self


class OutputNumberFormat(Contract):
    output: FormatOutput
    style: Literal["number", "currency", "percent"] = "number"
    precision: int | None = Field(default=None, strict=True, ge=0, le=12)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    percent_scale: Literal["auto", "fraction", "whole"] = "auto"
    unit: str = Field(default="", max_length=24, pattern=r"^[\w °µ²³/·%.-]*$")
    rules: list[NumberRule] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_style(self):
        if self.style == "currency" and self.currency is None:
            raise ValueError("Choose a three-letter currency code")
        return self


class PivotTotals(Contract):
    row_subtotals: bool = True
    column_subtotals: bool = True
    row_totals: bool = True
    column_totals: bool = True
    grand_total: bool = True
    maximum_columns: int = Field(default=12, strict=True, ge=1, le=24)
    maximum_rows: int = Field(default=100, strict=True, ge=1, le=200)


def tile_dimensions(tile):
    return [*tile.row_dimensions, *tile.column_dimensions] if tile.kind == "pivot" else tile.dimensions


class DashboardTile(Contract):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=128)
    kind: Literal["detail", "aggregate", "bar", "line", "donut", "kpi", "pivot"]
    dimensions: list[SelectedField] = Field(default_factory=list, max_length=64)
    row_dimensions: list[SelectedField] = Field(default_factory=list, max_length=4)
    column_dimensions: list[SelectedField] = Field(default_factory=list, max_length=3)
    pivot_totals: PivotTotals = Field(default_factory=PivotTotals)
    measures: list[SelectedField] = Field(default_factory=list, max_length=64)
    hidden_measures: list[SelectedField] = Field(default_factory=list, max_length=64)
    calculations: list[CalculatedMeasure] = Field(default_factory=list, max_length=32)
    kpi_comparison: KPIComparison | None = None
    detail_fields: list[SelectedField] = Field(default_factory=list, max_length=64)
    selections: dict[str, ScopeSelection] = Field(default_factory=dict, max_length=20)
    report_filters: list[ReportFilter] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=100, ge=1, le=100)
    time_analysis: TimeAnalysis | None = None
    sort: list[ReportSort] = Field(default_factory=list, max_length=32)
    top_n: int | None = Field(default=None, strict=True, ge=1, le=2147483647)
    aggregate_filters: list[AggregateFilter] = Field(default_factory=list, max_length=32)
    number_formats: list[OutputNumberFormat] = Field(default_factory=list, max_length=64)
    format_locale: Literal["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "pt-BR", "ja-JP", "zh-CN", "hi-IN", "ar-EG"] | None = None

    @model_validator(mode="after")
    def valid_view(self):
        axes = tile_dimensions(self)
        format_keys = [(item.output.table, item.output.column, item.output.aggregate, item.output.kind) for item in self.number_formats]
        if len(set(format_keys)) != len(format_keys):
            raise ValueError("Choose only one number format per semantic output")
        count = visible_measure_count(self)
        if len({item.id for item in self.calculations}) != len(self.calculations):
            raise ValueError("Calculated measure IDs must be unique")
        if len(axes) + len(base_measures(self)) + len(self.calculations) > 64:
            raise ValueError("A report supports at most 64 dimensions, base inputs and calculations")
        if len({(f.table,f.column,f.aggregate) for f in base_measures(self)}) != len(base_measures(self)):
            raise ValueError("Base inputs must be unique across visible and hidden measures")
        if self.kpi_comparison and self.kind != "kpi":
            raise ValueError("Target/baseline comparisons belong to KPI reports")
        if not self.title.strip():
            raise ValueError("Tile title cannot be blank")
        if any(f.aggregate != "none" for f in axes + self.detail_fields):
            raise ValueError("Dimensions and detail columns must be raw fields")
        if len(axes) + len(self.measures) > 64:
            raise ValueError("A tile supports at most 64 output fields")
        for fields in (axes, self.measures, self.detail_fields):
            if len({(f.table, f.column, f.aggregate) for f in fields}) != len(fields):
                raise ValueError("Duplicate tile fields are not allowed")
        if self.kind == "pivot":
            if len({(f.table, f.column) for f in [*axes, *self.measures, *self.detail_fields]}) > 64:
                raise ValueError("Pivot and detail columns together support at most 64 semantic inputs")
            if self.dimensions or not self.row_dimensions or not self.column_dimensions:
                raise ValueError("Pivot reports require separate row and column dimensions, with no legacy dimensions")
            if len(base_measures(self)) + len(self.calculations) > 8 or any(field.aggregate == "none" for field in base_measures(self)):
                raise ValueError("Pivot measures need an explicit aggregation; at most eight measures are supported. Model summary values are not raw-row totals")
            if self.time_analysis and (self.time_analysis.comparison != "none" or self.time_analysis.running_total):
                raise ValueError("Pivot calendar grouping supports period values. Use a report or chart for comparison and running-total windows")
        elif self.row_dimensions or self.column_dimensions:
            raise ValueError("Row and column dimensions belong to pivot reports")
        if self.kind == "detail":
            if not self.detail_fields or self.dimensions or base_measures(self) or self.calculations or self.kpi_comparison:
                raise ValueError("Detail tiles require detail columns and no grouped fields")
        elif not count or not base_measures(self):
            raise ValueError("Analytic tiles require at least one measure")
        if self.kind in {"bar", "line", "donut"} and not self.dimensions:
            raise ValueError("Charts require at least one dimension")
        if self.kind == "donut" and count != 1:
            raise ValueError("Donut charts require exactly one measure")
        if self.kind == "kpi" and (self.dimensions or count != 1):
            raise ValueError("KPI tiles require one measure and no dimensions")
        if self.time_analysis:
            if (self.time_analysis.table, self.time_analysis.column) not in {(f.table, f.column) for f in axes}:
                raise ValueError("The time field must be a selected dimension")
            if any(f.aggregate == "none" for f in base_measures(self)):
                raise ValueError("Time analysis measures require an explicit aggregation")
        if len({(item.table, item.column, item.aggregate, item.kind) for item in self.sort}) != len(self.sort):
            raise ValueError("Sort outputs must be unique")
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
