"""Real PostgreSQL values over complete synthetic report populations."""

from types import SimpleNamespace
from decimal import Decimal
import pytest
from schemii.schemoo.models import ModelDefinition
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemer import tile_queries as queries
from tests.integration.sql_oracles import execute as execute


def operand(column, aggregate="sum", scope="group"):
    return {
        "output": {"table": "people", "column": column, "aggregate": aggregate},
        "scope": scope,
    }


def prepared(monkeypatch, **patch):
    catalog = {
        "namespace": "public",
        "fingerprint": "",
        "tables": [
            {
                "name": "people",
                "primaryKey": ["id"],
                "columns": [
                    {"name": name, "dataType": kind}
                    for name, kind in [
                        ("id", "integer"),
                        ("grp", "text"),
                        ("amount", "numeric"),
                        ("units", "numeric"),
                        ("name", "text"),
                    ]
                ],
            }
        ],
        "relationships": [],
    }
    model = SimpleNamespace(
        definition=ModelDefinition.model_validate(
            {
                "root": "people",
                "nodes": [{"id": "people", "table": "people", "label": "People"}],
            }
        ),
        catalog_fingerprint="",
    )
    monkeypatch.setattr(queries, "load_model", lambda *args: model)
    monkeypatch.setattr(queries, "model_catalog", lambda *args, **kwargs: catalog)
    tile = DashboardTile.model_validate(
        {
            "id": "ratio",
            "title": "Weighted ratio",
            "kind": "aggregate",
            "dimensions": [{"table": "people", "column": "grp"}],
            "hiddenMeasures": [
                {"table": "people", "column": name, "aggregate": "sum"}
                for name in ("amount", "units")
            ],
            "calculations": [
                {
                    "id": "ratio",
                    "label": "Ratio",
                    "operation": "divide",
                    "left": operand("amount"),
                    "right": operand("units"),
                }
            ],
            "detailFields": [{"table": "people", "column": "id"}],
            "limit": 1,
            **patch,
        }
    )
    dashboard = SimpleNamespace(
        model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile]
    )
    return dashboard, tile


def statement(plan):
    return """WITH people(id,grp,amount,units,name) AS (VALUES
      (1,'A'::text,100::numeric,2::numeric,'same'::text),(2,'A',10,10,'other'),(3,'B',90,3,'same'),(4,'C',NULL,0,NULL))
    """ + plan["sql"].replace('"public"."people"', '"people"')


def test_weighted_ratio_is_complete_not_average_of_ratios_or_one_cached_row(
    monkeypatch, execute
):
    dashboard, tile = prepared(monkeypatch)
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    rows = execute(statement(plan))
    assert rows[0][0] == "A" and rows[0][1] == pytest.approx(
        Decimal(110) / Decimal(12), rel=Decimal("1e-12")
    )
    assert rows[0][1] != Decimal("25.5")
    assert rows[1] == ["B", Decimal(30)] and rows[2] == ["C", None]
    assert plan["rowLimit"] == 1 and "LIMIT" not in plan["sql"]


@pytest.mark.parametrize(
    "scope,expected", [("source_population", 55), ("qualifying_groups", 100)]
)
def test_population_scope_reaggregates_inputs_before_top_n(
    monkeypatch, execute, scope, expected
):
    dashboard, tile = prepared(
        monkeypatch,
        calculations=[
            {
                "id": "share",
                "label": "Share",
                "operation": "percent_of",
                "left": operand("amount"),
                "right": operand("amount", scope=scope),
            }
        ],
        aggregateFilters=[
            {
                "table": "people",
                "column": "units",
                "aggregate": "sum",
                "operator": "gt",
                "value": 5,
            }
        ],
        topN=1,
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    rows = execute(statement(plan))
    assert len(rows) == 1 and rows[0][0] == "A"
    assert rows[0][1] == pytest.approx(Decimal(expected), rel=Decimal("1e-12"))
    assert (
        plan["calculatedMeasures"][0]["unit"] == "percent"
        and plan["calculatedMeasures"][0]["scale"] == 100
    )


@pytest.mark.parametrize(
    "aggregate,column,first,second",
    [
        ("avg", "amount", Decimal("0.825"), Decimal("1.35")),
        ("count_distinct", "name", Decimal(1), Decimal("0.5")),
    ],
)
def test_nonadditive_population_reaggregates_raw_values(
    monkeypatch, execute, aggregate, column, first, second
):
    dashboard, tile = prepared(
        monkeypatch,
        hiddenMeasures=[{"table": "people", "column": column, "aggregate": aggregate}],
        calculations=[
            {
                "id": "ratio",
                "label": "Ratio",
                "operation": "divide",
                "left": operand(column, aggregate),
                "right": operand(column, aggregate, "source_population"),
            }
        ],
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    rows = execute(statement(plan))
    assert rows[0][0] == "A" and rows[0][1] == pytest.approx(
        first, rel=Decimal("1e-12")
    )
    assert rows[1][0] == "B" and rows[1][1] == pytest.approx(
        second, rel=Decimal("1e-12")
    )


def test_composite_denominator_drill_uses_its_full_population(monkeypatch, execute):
    dashboard, tile = prepared(
        monkeypatch,
        calculations=[
            {
                "id": "ratio",
                "label": "Ratio",
                "operation": "divide",
                "left": operand("amount"),
                "right": operand("units", scope="source_population"),
            }
        ],
    )
    selection = {
        "dimensions": [{"table": "people", "column": "grp", "value": "A"}],
        "measureIndex": 0,
        "constituent": operand("units", scope="source_population"),
    }
    _, plan = queries.tile_plan(
        None, None, dashboard, tile.id, selection=selection, exporting=True
    )
    rows = execute(statement(plan))
    assert [row[0] for row in rows] == [1, 2, 3, 4]
    assert plan["calculationScope"]["constituent"]["scope"] == "source_population"
    assert plan["outputLabels"][-1] == "Calculation selection"


def test_kpi_baseline_is_exact_and_zero_has_null_percentage(monkeypatch, execute):
    dashboard, tile = prepared(
        monkeypatch,
        kind="kpi",
        dimensions=[],
        kpiComparison={
            "label": "Target",
            "baseline": {"constant": "0.000000000000000000000"},
            "favorable": "higher",
        },
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    row = execute(statement(plan))[0]
    assert (
        row[0] == pytest.approx(Decimal(200) / Decimal(15), rel=Decimal("1e-12"))
        and row[1] == 0
        and row[3] is None
        and row[4] == "favorable"
    )
    assert plan["kpiComparison"]["percentScale"] == 100


def test_pivot_ratio_totals_use_raw_sums_before_arithmetic(monkeypatch, execute):
    dashboard, tile = prepared(
        monkeypatch,
        kind="pivot",
        dimensions=[],
        rowDimensions=[{"table": "people", "column": "grp"}],
        columnDimensions=[{"table": "people", "column": "name"}],
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    rows = execute(statement(plan))
    total = next(row for row in rows if row[-7:-5] == [0, 0])
    assert total[2] == pytest.approx(Decimal(200) / Decimal(15), rel=Decimal("1e-12"))
    assert plan["pivot"]["measureCount"] == 1 and plan["pivot"]["metadataOffset"] == 3


def test_qualifying_denominator_includes_groups_omitted_only_by_top_n(
    monkeypatch, execute
):
    dashboard, tile = prepared(
        monkeypatch,
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        calculations=[
            {
                "id": "share",
                "label": "Share",
                "operation": "percent_of",
                "left": operand("amount"),
                "right": operand("amount", scope="qualifying_groups"),
            }
        ],
        topN=1,
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    rows = execute(statement(plan))
    assert (
        len(rows) == 1
        and rows[0][0] == "A"
        and rows[0][1] == pytest.approx(Decimal(55), rel=Decimal("1e-12"))
    )


def test_calendar_population_excludes_undated_rows_before_aggregate_filters(
    monkeypatch, execute
):
    dashboard, tile = prepared(
        monkeypatch,
        dimensions=[{"table": "people", "column": "created_at"}],
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        timeAnalysis={
            "table": "people",
            "column": "created_at",
            "granularity": "month",
        },
        calculations=[
            {
                "id": "share",
                "label": "Share",
                "operation": "percent_of",
                "left": operand("amount"),
                "right": operand("amount", scope="source_population"),
            }
        ],
    )
    queries.model_catalog(None, None, None)["tables"][0]["columns"].append(
        {"name": "created_at", "dataType": "date"}
    )
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    sql = """WITH people(id,grp,amount,units,name,created_at) AS (VALUES
        (1,'x'::text,100::numeric,1::numeric,'x'::text,DATE '2024-01-01'),
        (2,'x',200,1,'x',DATE '2024-02-01'),(3,'x',1000,1,'x',NULL::date))
    """ + plan["sql"].replace('"public"."people"', '"people"')
    rows = execute(sql)
    assert len(rows) == 2
    assert rows[0][1] == pytest.approx(
        Decimal(100) / Decimal(300) * 100, rel=Decimal("1e-12")
    )
    assert rows[1][1] == pytest.approx(
        Decimal(200) / Decimal(300) * 100, rel=Decimal("1e-12")
    )
