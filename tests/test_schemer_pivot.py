"""Pivot totals use complete qualifying semantic inputs, never displayed sums."""

from types import SimpleNamespace as NS
import sqlite3

import pytest
from pydantic import ValidationError
from sqlglot import exp, parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.models import ModelDefinition
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemer import tile_queries


DATA = [
    (1, "East", "A", "One", 10, "shared"),
    (2, "East", "A", "Two", 30, "shared"),
    (3, "East", "B", "One", 100, "shared"),
    (4, "West", "A", "One", 5, "shared"),
    (5, "West", "B", "One", None, "other"),
    (6, None, "A", "One", 20, "null-row"),
    (7, "Grand total", "A", "One", 70, "literal"),
]


def field(column, aggregate="none"):
    return {"table": "facts", "column": column, "aggregate": aggregate}


def fixture(monkeypatch, **changes):
    catalog = {
        "namespace": "public",
        "fingerprint": "",
        "tables": [
            {
                "name": "facts",
                "primaryKey": ["id"],
                "columns": [
                    {
                        "name": name,
                        "dataType": kind,
                        "nullable": name in {"amount", "region"},
                    }
                    for name, kind in [
                        ("id", "integer"),
                        ("region", "text"),
                        ("category", "text"),
                        ("team", "text"),
                        ("amount", "numeric"),
                        ("customer", "text"),
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
    tile = DashboardTile.model_validate(
        {
            "id": "pivot",
            "title": "Sales pivot",
            "kind": "pivot",
            "rowDimensions": [field("region")],
            "columnDimensions": [field("category")],
            "measures": [field("amount", "avg"), field("customer", "count_distinct")],
            "detailFields": [
                field("id"),
                field("region"),
                field("category"),
                field("amount"),
            ],
            **changes,
        }
    )
    dashboard = NS(
        model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile]
    )
    monkeypatch.setattr(tile_queries, "load_model", lambda *args: model)
    monkeypatch.setattr(tile_queries, "model_catalog", lambda *args, **kwargs: catalog)
    return dashboard, tile, model, catalog


def execute(statement, data=DATA):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(
            "ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.facts(id INTEGER,region TEXT,category TEXT,team TEXT,amount REAL,customer TEXT);"
        )
        connection.executemany("INSERT INTO public.facts VALUES(?,?,?,?,?,?)", data)
        sql = (
            parse_one(statement, read="postgres")
            .transform(
                lambda node: (
                    exp.Literal.string(node.this)
                    if isinstance(node, exp.ByteString)
                    else node
                )
            )
            .sql(dialect="sqlite")
        )
        return connection.execute(sql).fetchall()


def plan(context, selection=None, **options):
    dashboard, tile, _, _ = context
    return tile_queries.tile_plan(
        None, "owner", dashboard, tile.id, selection=selection, **options
    )[1]


def scope(kind, row_depth, column_depth, values=(), measure=0):
    return {
        "dimensions": [
            {"table": "facts", "column": name, "value": value} for name, value in values
        ],
        "measureIndex": measure,
        "pivot": {"kind": kind, "rowDepth": row_depth, "columnDepth": column_depth},
    }


def totals(rows, r, c):
    return [row for row in rows if row[4:6] == (r, c) and row[6] == "data"]


def test_average_and_distinct_totals_reaggregate_actual_inputs_and_null_is_not_total(
    monkeypatch,
):
    context = fixture(monkeypatch)
    result = execute(plan(context)["sql"])
    (grand,) = totals(result, 0, 0)
    assert grand[2] == pytest.approx(235 / 6)
    assert grand[3] == 4
    (east,) = [row for row in totals(result, 1, 0) if row[0] == "East"]
    assert east[2] == pytest.approx(
        140 / 3
    )  # Not AVG(20,100), and not SUM of averages.
    assert east[3] == 1  # Shared customer spans A and B.
    actual_null = [row for row in totals(result, 1, 1) if row[:2] == (None, "A")]
    literal_total = [
        row for row in totals(result, 1, 1) if row[:2] == ("Grand total", "A")
    ]
    assert actual_null[0][2] == 20 and literal_total[0][2] == 70
    assert grand[0] is None and grand[1] is None
    assert actual_null[0][4:6] != grand[4:6]


def test_total_population_is_qualified_before_top_n_and_independent_of_preview_batch(
    monkeypatch,
):
    context = fixture(
        monkeypatch,
        topN=1,
        limit=1,
        sort=[{**field("amount", "avg"), "direction": "desc"}],
        aggregateFilters=[{**field("amount", "avg"), "operator": "gt", "value": 15}],
    )
    compiled = plan(context)
    result = execute(compiled["sql"])
    assert len(totals(result, 1, 1)) == 1
    assert totals(result, 1, 1)[0][:3] == ("East", "B", 100)
    (grand,) = totals(result, 0, 0)
    assert grand[2] == 46 and grand[3] == 3
    assert "before Top N" in compiled["pivot"]["totalsPopulation"]
    assert compiled["rowLimit"] == 1


def test_subtotals_are_at_each_actual_prefix_grain(monkeypatch):
    context = fixture(monkeypatch, rowDimensions=[field("region"), field("team")])
    result = execute(plan(context)["sql"])
    # Offset increases by one row dimension; subtotal East/A averages 10 and 30.
    east_a = next(
        row for row in result if row[:3] == ("East", None, "A") and row[5:7] == (1, 1)
    )
    assert east_a[3] == 20
    assert any(row[5:7] == (2, 1) and row[0] == "East" for row in result)


def test_column_capacity_is_checked_in_sql_and_full_long_form_export_is_not_matrix_capped(
    monkeypatch,
):
    context = fixture(monkeypatch, pivotTotals={"maximumColumns": 1})
    result = execute(plan(context)["sql"])
    assert len(result) == 1 and result[0][6] == "column_capacity"
    assert result[0][7] == 2
    exported = plan(context, exporting=True)
    assert len(execute(exported["sql"])) > 1
    assert all(row[6] == "data" for row in execute(exported["sql"]))
    assert "grouping depth" in exported["outputLabels"][4].lower()


def test_row_cap_is_explicit_and_totals_do_not_use_only_visible_rows(monkeypatch):
    context = fixture(monkeypatch, pivotTotals={"maximumRows": 1})
    rows = execute(plan(context)["sql"])
    assert all(row[8] == 1 for row in rows)
    assert len({row[0] for row in totals(rows, 1, 1)}) == 1
    assert totals(rows, 0, 0)[0][2] == pytest.approx(235 / 6)
    assert (
        len(
            {
                row[0]
                for row in totals(execute(plan(context, exporting=True)["sql"]), 1, 1)
            }
        )
        == 4
    )


def test_empty_population_returns_real_zero_count_and_null_average(monkeypatch):
    context = fixture(monkeypatch)
    (grand,) = totals(execute(plan(context)["sql"], []), 0, 0)
    assert grand[2:4] == (None, 0) and grand[9] == 0


@pytest.mark.parametrize(
    "selected,ids",
    [
        (scope("cell", 1, 1, [("region", "East"), ("category", "A")]), {1, 2}),
        (scope("row", 1, 0, [("region", "East")]), {1, 2, 3}),
        (scope("column", 0, 1, [("category", "A")]), {1, 2, 4, 6, 7}),
        (scope("grand_total", 0, 0), {1, 2, 3, 4, 6, 7}),
        (scope("cell", 1, 1, [("region", None), ("category", "A")]), {6}),
    ],
)
def test_exact_scope_drills_and_raw_csv_plans_keep_only_actual_contributors(
    monkeypatch, selected, ids
):
    context = fixture(monkeypatch)
    compiled = plan(context, selected, exporting=True)
    assert {row[0] for row in execute(compiled["sql"])} == ids
    assert compiled["pivotScope"]["kind"] == selected["pivot"]["kind"]
    assert all(row[-4] == selected["pivot"]["kind"] for row in execute(compiled["sql"]))
    assert "LIMIT" not in compiled["sql"]


def test_total_drill_excludes_nonqualifying_cells_but_does_not_top_n_contributors(
    monkeypatch,
):
    context = fixture(
        monkeypatch,
        topN=1,
        sort=[{**field("amount", "avg"), "direction": "desc"}],
        aggregateFilters=[{**field("amount", "avg"), "operator": "gt", "value": 15}],
    )
    assert {
        row[0] for row in execute(plan(context, scope("grand_total", 0, 0))["sql"])
    } == {1, 2, 3, 6, 7}
    # A non-displayed leaf group cannot be forged into a cell drill.
    assert (
        execute(
            plan(context, scope("cell", 1, 1, [("region", "East"), ("category", "A")]))[
                "sql"
            ]
        )
        == []
    )


@pytest.mark.parametrize(
    "selected",
    [
        {"dimensions": [], "measureIndex": 0},
        scope("cell", 0, 0),
        scope("grand_total", True, 0),
        scope("row", 1, 0, [("category", "A")]),
        scope("grand_total", 0, 0, measure=99),
    ],
)
def test_forged_scope_fields_or_grains_fail_closed(monkeypatch, selected):
    context = fixture(monkeypatch)
    with pytest.raises(ApiProblem):
        plan(context, selected)


@pytest.mark.parametrize(
    "changes",
    [
        {"rowDimensions": []},
        {"dimensions": [field("region")]},
        {"columnDimensions": [field("region")]},
        {"measures": [field("amount")]},
        {"pivotTotals": {"maximumColumns": 25}},
        {"pivotTotals": {"maximumRows": 201}},
    ],
)
def test_invalid_pivot_and_ambiguous_summary_grains_are_actionable(
    monkeypatch, changes
):
    with pytest.raises(ValidationError):
        fixture(monkeypatch, **changes)


def test_legacy_tile_reads_do_not_acquire_pivot_axes_or_change_measure_semantics():
    saved = DashboardTile.model_validate(
        {
            "id": "legacy",
            "title": "Legacy",
            "kind": "aggregate",
            "dimensions": [field("region")],
            "measures": [field("amount", "avg")],
        }
    )
    assert saved.row_dimensions == saved.column_dimensions == []
    assert saved.measures[0].aggregate == "avg"


def test_subtotal_drill_is_exact_prefix_and_honors_configured_totals(monkeypatch):
    context = fixture(monkeypatch, rowDimensions=[field("region"), field("team")])
    selected = scope("subtotal", 1, 1, [("region", "East"), ("category", "A")])
    assert {row[0] for row in execute(plan(context, selected)["sql"])} == {1, 2}
    disabled = fixture(
        monkeypatch, pivotTotals={"rowTotals": False, "grandTotal": False}
    )
    rows = execute(plan(disabled)["sql"])
    assert all(row[5] != 0 for row in rows)
    with pytest.raises(ApiProblem, match="not configured"):
        plan(disabled, scope("grand_total", 0, 0))


def semantic_summary_context(
    monkeypatch,
):
    catalog = {
        "namespace": "public",
        "fingerprint": "",
        "tables": [
            {
                "name": "owners",
                "primaryKey": ["tenant", "id"],
                "columns": [
                    {"name": name, "dataType": kind, "nullable": False}
                    for name, kind in [
                        ("tenant", "text"),
                        ("id", "integer"),
                        ("region", "text"),
                        ("category", "text"),
                    ]
                ],
            },
            {
                "name": "items",
                "columns": [
                    {"name": name, "dataType": kind}
                    for name, kind in [
                        ("tenant", "text"),
                        ("owner", "integer"),
                        ("value", "numeric"),
                    ]
                ],
            },
        ],
        "relationships": [
            {
                "id": "owns",
                "sourceTable": "items",
                "targetTable": "owners",
                "columns": [
                    {"source": "tenant", "target": "tenant"},
                    {"source": "owner", "target": "id"},
                ],
            }
        ],
    }
    definition = ModelDefinition.model_validate(
        {
            "root": "owners",
            "nodes": [
                {"id": "owners", "table": "owners", "label": "Owners"},
                {"id": "items", "table": "items", "label": "Items"},
                {
                    "id": "summary",
                    "table": "owners",
                    "label": "Per owner",
                    "derivation": {
                        "kind": "aggregate",
                        "source": "owners",
                        "groupBy": ["tenant", "id"],
                        "outputs": [
                            {
                                "id": "average",
                                "label": "Average child value",
                                "operation": "avg",
                                "nodeId": "items",
                                "column": "value",
                            }
                        ],
                    },
                },
            ],
            "edges": [
                {
                    "id": "owns",
                    "relationshipId": "owns",
                    "source": "items",
                    "target": "owners",
                }
            ],
        }
    )
    model = NS(definition=definition, catalog_fingerprint="")
    tile = DashboardTile.model_validate(
        {
            "id": "pivot",
            "title": "Per-owner summary",
            "kind": "pivot",
            "rowDimensions": [{"table": "owners", "column": "region"}],
            "columnDimensions": [{"table": "owners", "column": "category"}],
            "measures": [{"table": "summary", "column": "average", "aggregate": "avg"}],
            "detailFields": [
                {"table": "owners", "column": "tenant"},
                {"table": "owners", "column": "id"},
                {"table": "summary", "column": "average"},
            ],
        }
    )
    dashboard = NS(
        model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile]
    )
    monkeypatch.setattr(tile_queries, "load_model", lambda *args: model)
    monkeypatch.setattr(tile_queries, "model_catalog", lambda *args, **kwargs: catalog)
    return dashboard, tile, model, catalog


def test_semantic_summary_totals_reaggregate_per_owner_and_keep_complete_composite_keys(
    monkeypatch,
):
    from schemii.schemoo.service import plan_query

    dashboard, tile, model, catalog = semantic_summary_context(monkeypatch)
    definition = model.definition
    compiled = plan((dashboard, tile, model, catalog))
    assert any(
        "per-owner" in warning and "not the original raw-child" in warning
        for warning in compiled["warnings"]
    )
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(
            "ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.owners(tenant TEXT,id INTEGER,region TEXT,category TEXT); CREATE TABLE public.items(tenant TEXT,owner INTEGER,value REAL); INSERT INTO public.owners VALUES('T1',1,'East','A'),('T2',1,'East','A'),('T1',2,'West','B'); INSERT INTO public.items VALUES('T1',1,10),('T1',1,30),('T2',1,100),('T1',2,5);"
        )

        def run(sql):
            return connection.execute(
                parse_one(sql, read="postgres")
                .transform(
                    lambda node: (
                        exp.Literal.string(node.this)
                        if isinstance(node, exp.ByteString)
                        else node
                    )
                )
                .sql(dialect="sqlite")
            ).fetchall()

        rows = run(compiled["sql"])
        grand = next(row for row in rows if row[3:5] == (0, 0))
        assert grand[2] == pytest.approx(
            125 / 3
        )  # AVG of owner averages (20,100,5), not raw-row AVG 145/4.
        selected = {
            "dimensions": [],
            "measureIndex": 0,
            "pivot": {"kind": "grand_total", "rowDepth": 0, "columnDepth": 0},
        }
        assert set(run(plan((dashboard, tile, model, catalog), selected)["sql"])) == {
            ("T1", 1, 20),
            ("T2", 1, 100),
            ("T1", 2, 5),
        }
    # Existing summary validation remains authoritative; pivot does not invent a coarser owner grain.
    invalid = definition.model_copy(deep=True)
    invalid.nodes[-1].derivation.groupBy = ["id"]
    with pytest.raises(ApiProblem, match="complete primary key"):
        plan_query(
            catalog,
            invalid,
            tile_queries.tile_report_query(dashboard, tile).explore,
            "",
            _bounded=False,
        )


@pytest.mark.parametrize("value", [{}, [], None, True])
def test_untyped_http_axis_references_are_rejected_before_membership_lookup(
    monkeypatch, value
):
    context = fixture(monkeypatch)
    selected = scope("cell", 1, 1, [("region", "East"), ("category", "A")])
    selected["dimensions"][0]["table"] = value
    with pytest.raises(ApiProblem) as denied:
        plan(context, selected)
    assert denied.value.status_code == 422


def test_persisted_pivot_rejects_detail_projection_beyond_supported_input_union(
    monkeypatch,
):
    with pytest.raises(ValidationError, match="64 semantic inputs"):
        fixture(
            monkeypatch, detailFields=[field(f"column_{index}") for index in range(64)]
        )


def test_source_filters_precede_group_filters_totals_and_top_n_contributors(
    monkeypatch,
):
    context = fixture(
        monkeypatch,
        topN=1,
        sort=[{**field("amount", "avg"), "direction": "desc"}],
        reportFilters=[
            {
                "mode": "rows",
                "conditions": [
                    {
                        "table": "facts",
                        "column": "amount",
                        "operator": "gt",
                        "value": 20,
                    }
                ],
            }
        ],
    )
    rows = execute(plan(context)["sql"])
    assert totals(rows, 0, 0)[0][2] == pytest.approx(200 / 3)
    assert len(totals(rows, 1, 1)) == 1
    assert {
        row[0] for row in execute(plan(context, scope("grand_total", 0, 0))["sql"])
    } == {2, 3, 7}


def test_distinct_count_drill_includes_its_own_nonnull_inputs_when_another_measure_is_null(
    monkeypatch,
):
    context = fixture(monkeypatch)
    compiled = plan(context, scope("grand_total", 0, 0, measure=1))
    assert {row[0] for row in execute(compiled["sql"])} == set(range(1, 8))
    assert any(
        "repeated values count once" in warning for warning in compiled["warnings"]
    )
