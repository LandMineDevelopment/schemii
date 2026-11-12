"""Real PostgreSQL pivot grain/type oracles over CTE-owned data; no app runtime."""

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from schemii.schemer import tile_queries
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemoo.models import ModelDefinition
from tests.integration.sql_oracles import execute as execute


@pytest.fixture
def compile_pivot(monkeypatch):
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
                        ("region", "text"),
                        ("category", "text"),
                        ("team", "text"),
                        ("amount", "numeric"),
                        ("customer", "text"),
                        ("occurred", "timestamp with time zone"),
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
    monkeypatch.setattr(tile_queries, "load_model", lambda *args: model)
    monkeypatch.setattr(tile_queries, "model_catalog", lambda *args, **kwargs: catalog)

    def compile(
        *,
        selection=None,
        exporting=False,
        source_kind="timestamp with time zone",
        **changes,
    ):
        catalog["tables"][0]["columns"][-1]["dataType"] = source_kind
        tile = DashboardTile.model_validate(
            {
                "id": "pivot",
                "title": "Sales",
                "kind": "pivot",
                "rowDimensions": [field("region")],
                "columnDimensions": [field("category")],
                "measures": [
                    field("amount", "avg"),
                    field("customer", "count_distinct"),
                ],
                "detailFields": [field("id"), field("amount")],
                "limit": 1,
                **changes,
            }
        )
        dashboard = NS(
            model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile]
        )
        _, plan = tile_queries.tile_plan(
            None, None, dashboard, tile.id, selection=selection, exporting=exporting
        )
        return {**plan, "sql": plan["sql"].replace('"public"."facts"', '"facts"')}

    return compile


def field(column, aggregate="none"):
    return {"table": "facts", "column": column, "aggregate": aggregate}


VALUES = """(1,'East'::text,'A'::text,'One'::text,10::numeric,'shared'::text,TIMESTAMPTZ '2024-03-10 06:30Z'),
(2,'East','A','Two',30,'shared',TIMESTAMPTZ '2024-03-10 07:30Z'),
(3,'East','B','One',100,'shared',TIMESTAMPTZ '2024-03-11 06:00Z'),
(4,'West','A','One',5,'shared',TIMESTAMPTZ '2024-03-10 06:30Z'),
(5,'West','B','One',NULL,'other',NULL),
(6,NULL,'A','One',20,'null-row',TIMESTAMPTZ '2024-03-10 06:30Z'),
(7,'Grand total','A','One',70,'literal',TIMESTAMPTZ '2024-03-10 06:30Z')"""


def run(execute, plan, values=VALUES):
    return execute(
        "WITH facts(id,region,category,team,amount,customer,occurred) AS (VALUES "
        + values
        + ") SELECT * FROM ("
        + plan["sql"].rstrip(";")
        + ") result"
    )


def test_weighted_average_distinct_totals_and_real_null_not_magic_total(
    execute, compile_pivot
):
    rows = run(execute, compile_pivot())
    grand = next(row for row in rows if row[4:6] == [0, 0])
    assert grand[2] == pytest.approx(Decimal(235) / 6) and grand[3] == 4
    east = next(row for row in rows if row[0] == "East" and row[4:6] == [1, 0])
    assert east[2] == pytest.approx(Decimal(140) / 3) and east[3] == 1
    assert any(
        row[:3] == [None, "A", Decimal(20)] and row[4:6] == [1, 1] for row in rows
    )
    assert any(row[:3] == ["Grand total", "A", Decimal(70)] for row in rows)
    assert grand[-1] == "grand_total"


def test_before_topn_totals_qualifying_membership_and_exact_raw_csv_scope(
    execute, compile_pivot
):
    changes = {
        "topN": 1,
        "sort": [{**field("amount", "avg"), "direction": "desc"}],
        "aggregateFilters": [{**field("amount", "avg"), "operator": "gt", "value": 15}],
    }
    rows = run(execute, compile_pivot(**changes))
    assert [row[:3] for row in rows if row[4:6] == [1, 1]] == [["East", "B", 100]]
    grand = next(row for row in rows if row[4:6] == [0, 0])
    assert grand[2:4] == [46, 3]
    selected = {
        "dimensions": [],
        "measureIndex": 0,
        "pivot": {"kind": "grand_total", "rowDepth": 0, "columnDepth": 0},
    }
    raw = run(execute, compile_pivot(selection=selected, exporting=True, **changes))
    assert {row[0] for row in raw} == {1, 2, 3, 6, 7}
    assert all(row[-4:-1] == ["grand_total", 0, 0] for row in raw)
    assert all('"kind": "grand_total"' in row[-1] for row in raw)


@pytest.mark.parametrize(
    "kind,r,c,dimensions,ids",
    [
        ("cell", 1, 1, [("region", None), ("category", "A")], {6}),
        ("row", 1, 0, [("region", "East")], {1, 2, 3}),
        ("column", 0, 1, [("category", "A")], {1, 2, 4, 6, 7}),
    ],
)
def test_cell_row_column_contributors_are_not_preview_limited(
    execute, compile_pivot, kind, r, c, dimensions, ids
):
    selected = {
        "dimensions": [
            {"table": "facts", "column": name, "value": value}
            for name, value in dimensions
        ],
        "measureIndex": 0,
        "pivot": {"kind": kind, "rowDepth": r, "columnDepth": c},
    }
    assert {row[0] for row in run(execute, compile_pivot(selection=selected))} == ids


def test_prefix_subtotals_and_matrix_caps_leave_full_totals_and_csv_intact(
    execute, compile_pivot
):
    rows = run(execute, compile_pivot(rowDimensions=[field("region"), field("team")]))
    assert any(
        row[:4] == ["East", None, "A", 20] and row[5:7] == [1, 1] for row in rows
    )
    capped = run(execute, compile_pivot(pivotTotals={"maximumColumns": 1}))
    assert len(capped) == 1 and capped[0][6] == "column_capacity"
    full = run(
        execute, compile_pivot(pivotTotals={"maximumColumns": 1}, exporting=True)
    )
    assert all(row[6] == "data" for row in full)
    row_cap = run(execute, compile_pivot(pivotTotals={"maximumRows": 1}))
    assert all(row[8] for row in row_cap)
    assert next(row for row in row_cap if row[4:6] == [0, 0])[2] == pytest.approx(
        Decimal(235) / 6
    )


@pytest.mark.parametrize("calendar_axis", ["row", "column"])
@pytest.mark.parametrize("exporting", [False, True])
def test_zoned_calendar_pivot_totals_reaggregate_period_inputs_and_drill_bucket(
    execute, compile_pivot, calendar_axis, exporting
):
    row, column = (
        ("occurred", "category") if calendar_axis == "row" else ("category", "occurred")
    )
    changes = {
        "rowDimensions": [field(row)],
        "columnDimensions": [field(column)],
        "timeAnalysis": {
            "table": "facts",
            "column": "occurred",
            "granularity": "day",
            "timezone": "America/New_York",
        },
    }
    rows = run(execute, compile_pivot(exporting=exporting, **changes))
    assert next(row for row in rows if row[4:6] == [0, 0])[2] == pytest.approx(
        Decimal(235) / 6
    )
    assert next(row for row in rows if row[4:6] == [0, 0])[3] == 3
    selected = {
        "dimensions": [
            {
                "table": "facts",
                "column": name,
                "value": "2024-03-10" if name == "occurred" else "A",
            }
            for name in (row, column)
        ],
        "measureIndex": 0,
        "pivot": {"kind": "cell", "rowDepth": 1, "columnDepth": 1},
    }
    assert {
        row[0]
        for row in run(
            execute, compile_pivot(selection=selected, exporting=exporting, **changes)
        )
    } == {1, 2, 4, 6, 7}


def test_composite_owner_semantic_summary_has_deliberate_per_owner_total_and_exact_contributors(
    execute, monkeypatch
):
    from tests.test_schemer_pivot import semantic_summary_context

    dashboard, tile, _, _ = semantic_summary_context(monkeypatch)
    _, compiled = tile_queries.tile_plan(None, None, dashboard, tile.id)
    sources = "WITH owners(tenant,id,region,category) AS (VALUES ('T1'::text,1,'East'::text,'A'::text),('T2',1,'East','A'),('T1',2,'West','B')), items(tenant,owner,value) AS (VALUES ('T1'::text,1,10::numeric),('T1',1,30),('T2',1,100),('T1',2,5)) "

    def run(plan):
        sql = (
            plan["sql"]
            .replace('"public"."owners"', "owners")
            .replace('"public"."items"', "items")
            .rstrip(";")
        )
        return execute(sources + "SELECT * FROM (" + sql + ") result")

    grand = next(row for row in run(compiled) if row[3:5] == [0, 0])
    assert grand[2] == pytest.approx(Decimal(125) / 3)
    assert grand[2] != pytest.approx(
        Decimal(145) / 4
    )  # Explicit per-owner average, never presented as raw-row average.
    selected = {
        "dimensions": [],
        "measureIndex": 0,
        "pivot": {"kind": "grand_total", "rowDepth": 0, "columnDepth": 0},
    }
    _, raw = tile_queries.tile_plan(None, None, dashboard, tile.id, selection=selected)
    assert {tuple(row) for row in run(raw)} == {
        ("T1", 1, Decimal(20)),
        ("T2", 1, Decimal(100)),
        ("T1", 2, Decimal(5)),
    }
    assert any("per-owner" in warning for warning in compiled["warnings"])


def test_empty_qualified_population_keeps_zero_distinct_null_average_and_no_contributors(
    execute, compile_pivot
):
    changes = {
        "aggregateFilters": [
            {**field("amount", "avg"), "operator": "gt", "value": 10000}
        ]
    }
    rows = run(execute, compile_pivot(**changes))
    assert len(rows) == 1 and rows[0][2:6] == [None, 0, 0, 0]
    assert rows[0][9] == 0 and rows[0][-1] == "grand_total"
    selected = {
        "dimensions": [],
        "measureIndex": 0,
        "pivot": {"kind": "grand_total", "rowDepth": 0, "columnDepth": 0},
    }
    assert run(execute, compile_pivot(selection=selected, **changes)) == []


@pytest.mark.parametrize(
    "source_kind,values,expected_min,expected_max,bucket",
    [
        (
            "date",
            "(1,'East'::text,'A'::text,'One'::text,10::numeric,'same'::text,DATE '2024-01-15'),(2,'East','A','One',20,'same',DATE '2024-01-16')",
            "2024-01-15",
            "2024-01-16",
            "2024-01-01",
        ),
        (
            "timestamp with time zone",
            "(1,'East'::text,'A'::text,'One'::text,10::numeric,'same'::text,TIMESTAMPTZ '2024-01-31 23:30Z'),(2,'East','A','One',20,'same',TIMESTAMPTZ '2024-02-01 00:30Z')",
            "2024-01-31T23:30:00+00:00",
            "2024-02-01T00:30:00+00:00",
            "2024-01-01",
        ),
    ],
)
def test_calendar_axis_same_raw_measure_count_distinct_min_max_at_every_grain_and_csv(
    execute, compile_pivot, source_kind, values, expected_min, expected_max, bucket
):
    from datetime import date, datetime

    minimum = (
        date.fromisoformat(expected_min)
        if source_kind == "date"
        else datetime.fromisoformat(expected_min)
    )
    maximum = (
        date.fromisoformat(expected_max)
        if source_kind == "date"
        else datetime.fromisoformat(expected_max)
    )
    changes = {
        "source_kind": source_kind,
        "columnDimensions": [field("occurred")],
        "measures": [
            field("occurred", aggregate)
            for aggregate in ("count_distinct", "min", "max")
        ],
        "detailFields": [field("id"), field("occurred")],
        "topN": 1,
        "timeAnalysis": {
            "table": "facts",
            "column": "occurred",
            "granularity": "month",
            "timezone": "America/New_York",
        },
    }
    for exporting in [False, True]:
        rows = run(execute, compile_pivot(exporting=exporting, **changes), values)
        assert {tuple(row[5:7]) for row in rows} == {(1, 1), (1, 0), (0, 1), (0, 0)}
        assert all(row[2:5] == [2, minimum, maximum] for row in rows)
        assert all(
            row[1] is None or row[1] == date.fromisoformat(bucket) for row in rows
        )
    for measure in range(3):
        selected = {
            "dimensions": [
                {"table": "facts", "column": "region", "value": "East"},
                {"table": "facts", "column": "occurred", "value": bucket},
            ],
            "measureIndex": measure,
            "pivot": {"kind": "cell", "rowDepth": 1, "columnDepth": 1},
        }
        rows = run(
            execute,
            compile_pivot(selection=selected, exporting=True, **changes),
            values,
        )
        assert {tuple(row[:2]) for row in rows} == {(1, minimum), (2, maximum)}
        assert all(row[-4:-1] == ["cell", 1, 1] for row in rows)
