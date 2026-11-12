"""Saved formats are presentation metadata with exact, bounded semantic bindings."""

from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from schemii.schemer.dashboard_models import DashboardCreate, DashboardTile, NumberRule
from schemii.schemer.dashboard_routes import validate_dashboard
from schemii.schemoo.models import ModelDefinition


def output(column="amount", aggregate="sum", **extra):
    return {
        "table": "facts",
        "column": column,
        "aggregate": aggregate,
        "kind": "value",
        **extra,
    }


def setting(**changes):
    return {
        "output": output(),
        "style": "currency",
        "currency": "USD",
        "precision": 2,
        "unit": "",
        "percentScale": "auto",
        "rules": [
            {
                "operator": "gt",
                "value": "9007199254740993.000000001",
                "label": "Above target",
                "tone": "positive",
                "symbol": "up",
            }
        ],
        **changes,
    }


def tile(**changes):
    body = {
        "id": "tile",
        "title": "Revenue",
        "kind": "aggregate",
        "dimensions": [],
        "measures": [output()],
        "detailFields": [output("id", "none")],
        **changes,
    }
    for name in [
        "dimensions",
        "measures",
        "detailFields",
        "rowDimensions",
        "columnDimensions",
    ]:
        if name in body:
            body[name] = [
                {key: value for key, value in field.items() if key != "kind"}
                for field in body[name]
            ]
    return DashboardTile.model_validate(body)


def test_formats_round_trip_exact_thresholds_and_legacy_defaults():
    configured = tile(numberFormats=[setting()], formatLocale="de-DE")
    wire = configured.model_dump(mode="json", by_alias=True)
    assert wire["numberFormats"][0]["rules"][0]["value"] == "9007199254740993.000000001"
    assert wire["formatLocale"] == "de-DE"
    assert tile().number_formats == [] and tile().format_locale is None
    assert tile(numberFormats=[setting()]).measures == tile().measures


@pytest.mark.parametrize(
    "change",
    [
        {"precision": True},
        {"precision": 13},
        {"precision": -1},
        {"precision": "2"},
        {"style": "html"},
        {"style": "currency", "currency": None},
        {"currency": "usd"},
        {"unit": "<script>"},
        {"percentScale": 100},
        {"rules": [setting()["rules"][0]] * 9},
        {"rules": [{"operator": "eval", "label": "X"}]},
        {"output": {**output(), "sql": "SELECT 1"}},
        {"css": "background:red"},
    ],
)
def test_invalid_format_shapes_fail_before_save(change):
    with pytest.raises(ValidationError):
        tile(numberFormats=[setting(**change)])


@pytest.mark.parametrize(
    "value",
    [
        "NaN",
        "Infinity",
        "",
        "1e999999999999999999999999",
        "1e-4097",
        "1; SELECT 1",
        True,
        9007199254740993,
    ],
)
def test_thresholds_are_bounded_exact_text(value):
    with pytest.raises(ValidationError):
        NumberRule(operator="gte", value=value, label="Target")


def test_rule_order_nulls_ranges_duplicate_refs_and_locale_are_explicit():
    for rule in [
        dict(operator="between", value="2", upper="1"),
        dict(operator="is_null", value="0"),
        dict(operator="gt", value="1", upper="2"),
    ]:
        with pytest.raises(ValidationError):
            NumberRule(label="Invalid", **rule)
    with pytest.raises(ValidationError):
        tile(numberFormats=[setting(), setting()])
    with pytest.raises(ValidationError):
        tile(formatLocale="not-a-locale")
    rules = [
        dict(operator="is_null", label="Missing", symbol="warning"),
        dict(operator="gte", value="0", label="Present"),
    ]
    saved = tile(numberFormats=[setting(rules=rules)])
    assert [rule.label for rule in saved.number_formats[0].rules] == [
        "Missing",
        "Present",
    ]


@pytest.fixture
def validate(monkeypatch):
    from schemii.schemer import dashboard_routes

    catalog = {
        "namespace": "public",
        "fingerprint": "",
        "tables": [
            {
                "name": "facts",
                "columns": [
                    {"name": name, "dataType": kind}
                    for name, kind in [
                        ("id", "integer"),
                        ("amount", "numeric"),
                        ("other", "numeric"),
                        ("label", "text"),
                        ("at", "date"),
                    ]
                ],
            }
        ],
        "relationships": [],
    }
    model = NS(
        definition=ModelDefinition(
            root="facts", nodes=[{"id": "facts", "table": "facts", "label": "Facts"}]
        ),
        catalog_fingerprint="",
    )
    monkeypatch.setattr(dashboard_routes, "load_model", lambda *args: model)
    monkeypatch.setattr(dashboard_routes, "model_catalog", lambda *args: catalog)

    def run(configured):
        body = DashboardCreate(
            name="Formats",
            model_id="model_" + "a" * 32,
            model_revision=1,
            tiles=[configured],
        )
        validate_dashboard(
            NS(admin_config=NS(console=NS(maximum_statements_per_run=20))),
            "owner",
            body,
        )

    return run


def test_numeric_reference_validation_and_stale_read_repair(validate):
    validate(tile(numberFormats=[setting()]))
    stale = tile(measures=[output("other")], numberFormats=[setting()])
    assert (
        stale.number_formats[0].output.column == "amount"
    )  # Read retains stale binding.
    with pytest.raises(ValueError, match="removed or hidden"):
        validate(stale)
    with pytest.raises(ValueError, match="numeric"):
        validate(
            tile(
                dimensions=[output("label", "none")],
                numberFormats=[setting(output=output("label", "none"))],
            )
        )
    validate(
        tile(
            dimensions=[output("id", "none")],
            numberFormats=[setting(output=output("id", "none"))],
        )
    )


def test_time_calculated_pivot_and_kpi_formats_bind_stable_output_kinds(validate):
    validate(
        tile(
            dimensions=[output("at", "none")],
            timeAnalysis={
                "table": "facts",
                "column": "at",
                "comparison": "previous_period",
            },
            numberFormats=[
                setting(output=output(kind="percent_change"), style="percent")
            ],
        )
    )
    calculation = {
        "id": "share",
        "label": "Share",
        "operation": "percent_of",
        "left": {"output": output()},
        "right": {"constant": "100"},
    }
    validate(
        tile(
            calculations=[calculation],
            numberFormats=[
                setting(
                    output={
                        "table": "@report",
                        "column": "share",
                        "aggregate": "none",
                        "kind": "calculated",
                    },
                    style="percent",
                )
            ],
        )
    )
    validate(
        tile(
            kind="pivot",
            rowDimensions=[output("id", "none")],
            columnDimensions=[output("at", "none")],
            numberFormats=[setting()],
        )
    )
    validate(
        tile(
            kind="kpi",
            kpiComparison={"baseline": {"constant": "100"}, "favorable": "higher"},
            numberFormats=[
                setting(
                    output={
                        "table": "@kpi",
                        "column": "percent",
                        "aggregate": "none",
                        "kind": "kpi_percent",
                    },
                    style="percent",
                )
            ],
        )
    )
    with pytest.raises(ValueError, match="KPI comparison"):
        validate(
            tile(
                numberFormats=[
                    setting(
                        output={
                            "table": "@kpi",
                            "column": "percent",
                            "aggregate": "none",
                            "kind": "kpi_percent",
                        }
                    )
                ]
            )
        )
