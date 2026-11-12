"""Real PostgreSQL result-stage oracles over CTE-owned rows; no live app."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from schemii.schemer import tile_queries
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemoo.models import ModelDefinition
from tests.integration.sql_oracles import execute as execute


@pytest.fixture
def compile_report(monkeypatch):
    catalog = {
        "namespace": "public",
        "fingerprint": "",
        "tables": [
            {
                "name": "sales",
                "columns": [
                    {"name": name, "dataType": kind}
                    for name, kind in [
                        ("id", "integer"),
                        ("region", "text"),
                        ("amount", "numeric"),
                        ("occurred", "date"),
                    ]
                ],
            }
        ],
        "relationships": [],
    }
    definition = ModelDefinition.model_validate(
        {"root": "sale", "nodes": [{"id": "sale", "table": "sales", "label": "Sale"}]}
    )
    model = NS(definition=definition, catalog_fingerprint="")
    monkeypatch.setattr(tile_queries, "load_model", lambda *args: model)
    monkeypatch.setattr(tile_queries, "model_catalog", lambda *args, **kwargs: catalog)

    def compile(*, selection=None, time=False, **changes):
        def field(name, **extra):
            return {"table": "sale", "column": name, **extra}

        tile = DashboardTile.model_validate(
            {
                "id": "report",
                "title": "Sales",
                "kind": "aggregate",
                "dimensions": [field("occurred" if time else "region")],
                "measures": [field("amount", aggregate="sum")],
                "detailFields": [field("id"), field("region"), field("amount")],
                "limit": 1,
                **(
                    {
                        "timeAnalysis": {
                            "table": "sale",
                            "column": "occurred",
                            "granularity": "day",
                            "comparison": "previous_period",
                            "runningTotal": True,
                        }
                    }
                    if time
                    else {}
                ),
                **changes,
            }
        )
        dashboard = NS(
            model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile]
        )
        _, plan = tile_queries.tile_plan(
            None, None, dashboard, tile.id, selection=selection
        )
        return {**plan, "sql": plan["sql"].replace('"public"."sales"', '"sales"')}

    return compile


def run(execute, plan, values):
    return execute(
        "WITH sales(id,region,amount,occurred) AS (VALUES "
        + values
        + ") SELECT * FROM ("
        + plan["sql"].rstrip(";")
        + ") AS result"
    )


def measure(**extra):
    return {"table": "sale", "column": "amount", "aggregate": "sum", **extra}


def selected(value, *, time=False):
    return {
        "dimensions": [
            {
                "table": "sale",
                "column": "occurred" if time else "region",
                "value": value,
            }
        ],
        "measureIndex": 0,
    }


def test_ranked_winner_after_150_source_groups_is_not_lost_to_delivery_cap(
    execute, compile_report
):
    values = ",".join(
        f"({i},'G{i:03}',{i}::numeric,DATE '2024-01-01')" for i in range(150)
    )
    values += ",(999,'Z-winner',10000::numeric,DATE '2024-01-01')"
    plan = compile_report(sort=[measure(direction="desc")], topN=1)
    assert run(execute, plan, values) == [["Z-winner", Decimal(10000)]]
    assert plan["rowLimit"] == 1


def test_deterministic_ties_and_explicit_null_placement(execute, compile_report):
    values = "(1,'B',30::numeric,DATE '2024-01-01'),(2,'A',30,'2024-01-01'),(3,NULL,30,'2024-01-01'),(4,'C',NULL,'2024-01-01')"
    plan = compile_report(sort=[measure(direction="desc")], topN=3)
    assert run(execute, plan, values) == [["A", 30], ["B", 30], [None, 30]]
    first = compile_report(sort=[measure(direction="desc", nulls="first")], topN=1)
    assert run(execute, first, values) == [["C", None]]
    null_filter = compile_report(aggregateFilters=[measure(operator="is_null")])
    assert run(execute, null_filter, values) == [["C", None]]


def test_source_and_aggregate_filters_precede_topn_and_exclude_parent_drill(
    execute, compile_report
):
    values = "(1,'A',10::numeric,DATE '2024-01-01'),(2,'A',-50,'2024-01-01'),(3,'B',20,'2024-01-01'),(4,'C',30,'2024-01-01'),(5,'C',1,'2024-01-01')"
    controls = {
        "reportFilters": [
            {
                "mode": "rows",
                "conditions": [
                    {"table": "sale", "column": "amount", "operator": "gt", "value": 0}
                ],
            }
        ],
        "aggregateFilters": [
            measure(operator="gte", value=20),
            measure(operator="lt", value=35),
        ],
        "sort": [measure(direction="desc")],
        "topN": 1,
    }
    assert run(execute, compile_report(**controls), values) == [["C", 31]]
    assert (
        run(execute, compile_report(selection=selected("B"), **controls), values) == []
    )
    assert run(
        execute, compile_report(selection=selected("C"), **controls), values
    ) == [[4, "C", 30], [5, "C", 1]]


def test_null_group_and_dimensionless_kpi_qualification(execute, compile_report):
    values = "(1,NULL::text,10::numeric,DATE '2024-01-01'),(2,'A',5,'2024-01-01')"
    controls = {
        "sort": [{"table": "sale", "column": "region", "nulls": "first"}],
        "topN": 1,
    }
    assert run(
        execute, compile_report(selection=selected(None), **controls), values
    ) == [[1, None, 10]]
    kpi = {
        "kind": "kpi",
        "dimensions": [],
        "topN": 1,
        "aggregateFilters": [measure(operator="gt", value=20)],
    }
    assert (
        run(
            execute,
            compile_report(selection={"dimensions": [], "measureIndex": 0}, **kpi),
            values,
        )
        == []
    )
    kpi["aggregateFilters"] = [measure(operator="gte", value=15)]
    assert run(
        execute,
        compile_report(selection={"dimensions": [], "measureIndex": 0}, **kpi),
        values,
    ) == [[1, None, 10], [2, "A", 5]]


def test_time_history_is_complete_before_output_filters_rank_and_selected_parent_drill(
    execute, compile_report
):
    values = "(1,'A',10::numeric,DATE '2024-01-01'),(2,'A',20,'2024-01-02'),(3,'A',30,'2024-01-03')"
    controls = {
        "time": True,
        "sort": [measure(kind="running_total", direction="desc")],
        "topN": 1,
        "aggregateFilters": [measure(kind="running_total", operator="gte", value=25)],
    }
    assert run(execute, compile_report(**controls), values) == [
        [date(2024, 1, 3), 30, 20, 10, 50, 60]
    ]
    assert (
        run(
            execute,
            compile_report(selection=selected("2024-01-02", time=True), **controls),
            values,
        )
        == []
    )
    assert run(
        execute,
        compile_report(selection=selected("2024-01-03", time=True), **controls),
        values,
    ) == [[3, "A", 30]]


def test_refresh_and_export_contract_compiles_the_identical_final_relation(
    execute, compile_report
):
    controls = {
        "sort": [measure(direction="desc")],
        "aggregateFilters": [measure(operator="gte", value=10)],
        "topN": 2,
    }
    preview, refreshed, export = [compile_report(**controls) for _ in range(3)]
    assert preview["sql"] == refreshed["sql"] == export["sql"]
    values = "(1,'A',10::numeric,DATE '2024-01-01'),(2,'B',25,'2024-01-01'),(3,'C',20,'2024-01-01')"
    assert run(execute, preview, values) == [["B", 25], ["C", 20]]
