"""Execute time-analysis plans over synthetic data via the launcher-managed stack.

SCHEMOO_LIVE_TESTS=1 /home/kasey/projects/schema_foundry/.venv/bin/python -m pytest
 tests/test_schemer_time_sql_live.py
No persistent source objects or warehouse rows are used.
"""
import os
import time
from uuid import uuid4
from types import SimpleNamespace as NS

import pytest

from schemii.schemoo.models import ModelDefinition
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemer import tile_queries
from test_schemoo_sql_live import api

pytestmark = pytest.mark.skipif(os.getenv("SCHEMOO_LIVE_TESTS") != "1", reason="Requires launcher-managed HTTPS stack")


@pytest.fixture(scope="module")
def execute():
    workspaces = api("/api/v1/schemii/workspaces")["workspaces"]
    workspace = next((item for item in workspaces if item.get("database") == "schemii_test" and item.get("namespace") == "bookstore"), None)
    assert workspace, "The existing schemii_test.bookstore workspace must be available."
    settings = api("/api/v1/schemii/console/settings")
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions"

    def run_sql(sql):
        created = api(path, {"consoleId": "con_" + uuid4().hex, "expectedWorkspaceRevision": workspace["revision"], "expectedSettingsRevision": settings["revision"], "mode": "managed_read", "statements": [sql]})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            receipt = api(f"{path}/{created['id']}")
            if receipt["status"] in ("succeeded", "failed", "cancelled", "expired"):
                break
            time.sleep(0.1)
        assert receipt["status"] == "succeeded", receipt
        assert len(receipt["results"]) == 1
        page = api(f"{path}/{created['id']}/results/{receipt['results'][0]['id']}")
        assert page["nextCursor"] is None, "Synthetic fixture must fit a single page."
        return page["rows"]

    return run_sql


@pytest.fixture
def compile_time(monkeypatch):
    def compile(*, kind="date", partition=False, analysis=None, filters=None, selection=None):
        columns = [("occurred", kind), ("amount", "numeric"), ("team", "text")]
        catalog = {"fingerprint": "", "namespace": "public", "tables": [{"name": "events", "columns": [
            {"name": name, "dataType": data_type} for name, data_type in columns]}], "relationships": []}
        definition = ModelDefinition.model_validate({"root": "events", "nodes": [{"id": "events", "table": "events", "label": "Events"}]})
        model = NS(definition=definition, catalog_fingerprint="")
        field = lambda column, **extra: {"table": "events", "column": column, **extra}
        tile = DashboardTile.model_validate({"id": "time", "title": "Time", "kind": "aggregate",
            "dimensions": [field("occurred"), *([field("team")] if partition else [])],
            "measures": [field("amount", aggregate="sum")], "detailFields": [field("occurred"), field("amount")],
            "limit": 1, "reportFilters": filters or [],
            "timeAnalysis": {"table": "events", "column": "occurred", "granularity": "day", **(analysis or {})}})
        dashboard = NS(model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile])
        monkeypatch.setattr(tile_queries, "load_model", lambda *args: model)
        monkeypatch.setattr(tile_queries, "model_catalog", lambda *args, **kwargs: catalog)
        _, plan = tile_queries.tile_plan(None, None, dashboard, tile.id, selection=selection)
        plan["sql"] = plan["sql"].replace('"public"."events"', '"events"')
        return plan
    return compile


def run(execute, plan, values):
    # Wrapping allows generated WITH clauses while keeping all synthetic rows local.
    return execute("WITH events(occurred, amount, team) AS (VALUES " + values + ") SELECT * FROM (" + plan["sql"].rstrip(";") + ") AS fixture")


def numeric(row):
    return [None if value is None else float(value) for value in row]


def test_missing_calendar_period_zero_and_full_running_window(execute, compile_time):
    plan = compile_time(analysis={"comparison": "previous_period", "runningTotal": True})
    rows = run(execute, plan, "(DATE '2024-01-01',0::numeric,'A'::text),('2024-01-02',5,'A'),('2024-01-04',7,'A')")
    assert len(rows) == 3  # Browser preview of one row cannot truncate SQL analysis.
    assert numeric(rows[1][1:]) == [5, 0, 5, None, 5]
    assert numeric(rows[2][1:]) == [7, None, None, None, 12]


def test_leap_day_prior_year_clamps_and_running_partition_nulls(execute, compile_time):
    plan = compile_time(partition=True, analysis={"comparison": "prior_year", "runningTotal": True})
    rows = run(execute, plan, "(DATE '2023-02-28',10::numeric,NULL::text),('2024-02-29',15,NULL),('2024-02-29',100,'B')")
    leap_null = next(row for row in rows if str(row[0]).startswith("2024-02-29") and row[1] is None)
    leap_b = next(row for row in rows if row[1] == "B")
    assert numeric(leap_null[2:]) == [15, 10, 5, 50, 25]
    assert numeric(leap_b[2:]) == [100, None, None, None, 100]


@pytest.mark.parametrize("week_start,expected", [("monday", ["2024-03-04", "2024-03-11"]), ("sunday", ["2024-03-10"])])
def test_week_boundary_is_explicit(execute, compile_time, week_start, expected):
    plan = compile_time(analysis={"granularity": "week", "weekStart": week_start})
    rows = run(execute, plan, "(DATE '2024-03-10',2::numeric,'A'::text),('2024-03-11',3,'A')")
    assert [str(row[0])[:10] for row in rows] == expected
    assert sum(float(row[1]) for row in rows) == 5


def test_dst_local_buckets_and_drill_are_identical(execute, compile_time):
    values = "(TIMESTAMPTZ '2024-03-10 04:59:59+00',1::numeric,'A'::text),('2024-03-10 05:00:00+00',2,'A'),('2024-03-11 03:59:59+00',3,'A'),('2024-03-11 04:00:00+00',4,'A')"
    config = {"timezone": "America/New_York"}
    plan = compile_time(kind="timestamp with time zone", analysis=config)
    rows = run(execute, plan, values)
    assert [(str(row[0])[:10], float(row[1])) for row in rows] == [("2024-03-09", 1), ("2024-03-10", 5), ("2024-03-11", 4)]
    drill = compile_time(kind="timestamp with time zone", analysis=config, selection={"dimensions": [{"table": "events", "column": "occurred", "value": "2024-03-10"}], "measureIndex": 0})
    assert sorted(float(row[1]) for row in run(execute, drill, values)) == [2, 3]


def test_filter_scope_caps_history_and_refreshed_data_recomputes(execute, compile_time):
    plan = compile_time(analysis={"comparison": "previous_period", "runningTotal": True}, filters=[{"mode": "rows", "conditions": [{"table": "events", "column": "occurred", "operator": "gte", "value": "2024-01-02"}]}])
    values = "(DATE '2024-01-01',100::numeric,'A'::text),('2024-01-02',5,'A'),('2024-01-03',7,'A')"
    rows = run(execute, plan, values)
    assert numeric(rows[0][1:]) == [5, None, None, None, 5]
    assert numeric(rows[1][1:]) == [7, 5, 2, 40, 12]
    refreshed = run(execute, plan, values + ",('2024-01-03',3,'A')")
    assert numeric(refreshed[1][1:]) == [10, 5, 5, 100, 15]


def test_large_numeric_values_do_not_overflow_comparison(execute, compile_time):
    plan = compile_time(analysis={"comparison": "previous_period", "runningTotal": True})
    rows = run(execute, plan, "(DATE '2024-01-01',100000000000000000000::numeric,'A'::text),('2024-01-02',200000000000000000000,'A')")
    assert numeric(rows[1][1:]) == [2e20, 1e20, 1e20, 100, 3e20]


def test_prior_year_week_rebuckets_to_same_week_boundary(execute, compile_time):
    plan = compile_time(analysis={"granularity": "week", "comparison": "prior_year"})
    rows = run(execute, plan, "(DATE '2023-03-06',10::numeric,'A'::text),('2023-03-13',90,'A'),('2024-03-11',15,'A')")
    assert numeric(rows[-1][1:]) == [15, 10, 5, 50]


def test_month_groups_partial_periods_as_filtered(execute, compile_time):
    plan = compile_time(analysis={"granularity": "month", "comparison": "previous_period"})
    rows = run(execute, plan, "(DATE '2024-01-31',20::numeric,'A'::text),('2024-02-01',10,'A'),('2024-02-29',20,'A')")
    assert [str(row[0])[:10] for row in rows] == ["2024-01-01", "2024-02-01"]
    assert numeric(rows[1][1:]) == [30, 20, 10, 50]


@pytest.mark.parametrize('comparison', ['previous_period', 'prior_year'])
def test_year_groups_leap_days_and_compares_calendar_years(execute, compile_time, comparison):
    plan = compile_time(analysis={'granularity': 'year', 'comparison': comparison, 'runningTotal': True})
    rows = run(execute, plan, "(DATE '2023-12-31',20::numeric,'A'::text),('2024-01-01',10,'A'),('2024-02-29',20,'A'),('2026-01-01',5,'A')")
    assert [str(row[0])[:10] for row in rows] == ['2023-01-01', '2024-01-01', '2026-01-01']
    assert numeric(rows[1][1:]) == [30, 20, 10, 50, 50]
    assert numeric(rows[2][1:]) == [5, None, None, None, 55]


def test_year_timezone_boundary_and_drill_match(execute, compile_time):
    values = "(TIMESTAMPTZ '2024-01-01 04:59:59+00',1::numeric,'A'::text),('2024-01-01 05:00:00+00',2,'A'),('2025-01-01 04:59:59+00',3,'A'),('2025-01-01 05:00:00+00',4,'A')"
    config = {'granularity': 'year', 'timezone': 'America/New_York'}
    plan = compile_time(kind='timestamp with time zone', analysis=config)
    rows = run(execute, plan, values)
    assert [(str(row[0])[:10], float(row[1])) for row in rows] == [('2023-01-01', 1), ('2024-01-01', 5), ('2025-01-01', 4)]
    drill = compile_time(kind='timestamp with time zone', analysis=config, selection={
        'dimensions': [{'table': 'events', 'column': 'occurred', 'value': '2024-01-01'}], 'measureIndex': 0})
    assert sorted(float(row[1]) for row in run(execute, drill, values)) == [2, 3]


def test_timezone_free_timestamp_retains_calendar_date(execute, compile_time):
    plan = compile_time(kind="timestamp without time zone", analysis={"timezone": "Pacific/Honolulu"})
    rows = run(execute, plan, "(TIMESTAMP '2024-03-10 00:01',2::numeric,'A'::text),('2024-03-10 23:59',3,'A'),(NULL,100,'A')")
    assert [(str(row[0])[:10], float(row[1])) for row in rows] == [("2024-03-10", 5)]
