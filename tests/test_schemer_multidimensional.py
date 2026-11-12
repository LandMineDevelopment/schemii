"""Multidimensional tiles keep their complete grouping across saved APIs."""
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlglot import exp, parse_one

from schemii.common.postgres.console import ConsoleResultColumn
from schemii.schemer.dashboard_models import DashboardTile
from test_schemer_routes import setup  # noqa: F401
import test_schemer_routes as report_fixtures


DIMENSIONS = [{"table": "people", "column": name} for name in ["name", "id"]]
MEASURE = {"table": "people", "column": "id", "aggregate": "count"}


@pytest.fixture
def multidimensional(request, monkeypatch):
    monkeypatch.setitem(report_fixtures.DEFINITION, "exposedFields", DIMENSIONS)
    client, model, console, fresh = request.getfixturevalue("setup")
    console.page = lambda *args: SimpleNamespace(
        columns=[ConsoleResultColumn(name=name, data_type=kind) for name, kind in
                 [("name", "text"), ("id", "uuid"), ("count", "int8")]],
        rows=[["Alice", "00000000-0000-0000-0000-000000000001", 2]],
        next_cursor=None, truncated=False)
    return client, model, console


@pytest.mark.parametrize("kind", ["bar", "line", "donut", "aggregate"])
def test_grouped_tiles_round_trip_plan_stream_export_and_drill(multidimensional, kind):
    client, model, console = multidimensional
    tile = {"id": "grouped", "title": "People groups", "kind": kind,
            "dimensions": DIMENSIONS, "measures": [MEASURE], "detailFields": DIMENSIONS}
    body = {"name": "Grouped", "modelId": model["modelId"], "modelRevision": 1, "tiles": [tile]}
    created = client.post("/api/v1/schemer/dashboards", json=body)
    assert created.status_code == 201, created.text
    url = "/api/v1/schemer/dashboards/" + created.json()["id"]
    saved = client.get(url).json()
    assert [field["column"] for field in saved["tiles"][0]["dimensions"]] == ["name", "id"]
    updated = client.put(url, json={**body, "name": "Renamed", "expectedRevision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert client.get(url).json()["tiles"] == updated.json()["tiles"]
    tile_url = url + "/tiles/grouped"
    payload = {"expectedRevision": 2}
    response = client.post(tile_url + "/plan", json=payload)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["outputLabels"][:2] == ["People.name", "People.id"]
    group = parse_one(plan["sql"], read="postgres").find(exp.Group)
    assert [field.name for field in group.expressions] == ["name", "id"]
    for path in [tile_url + "/executions/stream", url + "/executions/stream"]:
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.text
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[0]["tiles"][0]["plan"]["outputLabels"] == plan["outputLabels"]
        assert events[-1]["type"] == "end"
    exported = client.post(tile_url + "/export", data={"payload": json.dumps(payload)})
    assert exported.status_code == 200, exported.text
    assert exported.text.splitlines()[0].startswith("People.name,People.id,")
    assert "Alice,00000000-0000-0000-0000-000000000001,2" in exported.text
    selected = [{**DIMENSIONS[1], "value": "00000000-0000-0000-0000-000000000001"},
                {**DIMENSIONS[0], "value": None}]
    payload["selection"] = {"dimensions": selected, "measureIndex": 0}
    response = client.post(tile_url + "/plan", json=payload)
    assert response.status_code == 200, response.text
    assert '"contributors"."field_1" IS NULL' in response.json()["sql"]
    assert '"contributors"."field_2" = ' in response.json()["sql"]
    drill_sql = response.json()["sql"]
    drill = parse_one(drill_sql, read="postgres")
    contributors, = [node for node in drill.find_all(exp.Subquery)
                     if node.alias == "contributors"]
    # Detail rows retain their raw grain; aggregation belongs to parent eligibility.
    assert contributors.this.find(exp.Group) is None
    assert contributors.this.find(exp.AggFunc) is None
    detail = contributors.find_ancestor(exp.Select)
    assert detail.args.get("group") is None and detail.args.get("distinct") is None
    assert [(output.this.table, output.this.name) for output in detail.expressions] == [
        ("contributors", "field_1"), ("contributors", "field_2")]
    qualifying_parent, = [node for node in drill.find_all(exp.Subquery)
                         if node.alias == "qualifying_parent"]
    assert qualifying_parent.this == parse_one(plan["sql"], read="postgres")
    assert list(drill.find_all(exp.Group)) == list(qualifying_parent.find_all(exp.Group))
    membership = qualifying_parent.find_ancestor(exp.Exists)
    assert membership in list(detail.args["where"].this.flatten())
    parent_checks = list(membership.this.args["where"].find_all(exp.NullSafeEQ))
    assert len(parent_checks) == 2
    assert all(isinstance(check.expression, (exp.Null, exp.Literal, exp.ByteString))
               for check in parent_checks)
    assert {(check.this.table, check.this.name): check.expression.this for check in parent_checks} == {
        ("qualifying_parent", "output_1"): None,
        ("qualifying_parent", "output_2"): selected[0]["value"],
    }
    console.page = lambda *args: SimpleNamespace(
        columns=[ConsoleResultColumn(name="name", data_type="text"),
                 ConsoleResultColumn(name="id", data_type="uuid")],
        rows=[[None, selected[0]["value"]]], next_cursor=None, truncated=False)
    for action in ["executions/stream", "export"]:
        response = client.post(tile_url + "/" + action, json=payload)
        assert response.status_code == 200, response.text
        assert console.target["statements"] == [drill_sql]
    for invalid in [selected[:1], [selected[0], selected[0]],
                    [selected[0], {**selected[1], "column": "unknown"}], selected + selected[:1]]:
        payload["selection"]["dimensions"] = invalid
        for action in ["plan", "executions/stream", "export"]:
            assert client.post(tile_url + "/" + action, json=payload).status_code == 422


@pytest.mark.parametrize("kind", ["bar", "line", "donut"])
def test_charts_still_require_dimensions_and_unique_fields(kind):
    tile = {"id": "chart", "title": "Chart", "kind": kind, "measures": [MEASURE]}
    with pytest.raises(ValidationError, match="at least one dimension"):
        DashboardTile.model_validate(tile)
    with pytest.raises(ValidationError, match="Duplicate tile fields"):
        DashboardTile.model_validate({**tile, "dimensions": [DIMENSIONS[0]] * 2})
    dimensions = [{"table": "people", "column": f"dimension_{index}"} for index in range(64)]
    boundary = DashboardTile.model_validate({**tile, "dimensions": dimensions[:63]})
    assert len(boundary.dimensions) + len(boundary.measures) == 64
    with pytest.raises(ValidationError, match="at most 64 dimensions, base inputs and calculations"):
        DashboardTile.model_validate({**tile, "dimensions": dimensions})


@pytest.mark.parametrize("kind", ["bar", "line", "donut"])
@pytest.mark.parametrize("extra", [
    {"hiddenMeasures": [{**MEASURE, "aggregate": "count_distinct"}]},
    {"calculations": [{"id": "doubled", "label": "Doubled", "operation": "multiply",
                       "left": {"output": MEASURE}, "right": {"constant": 2}, "visible": False}]},
], ids=["hidden-base-input", "hidden-calculation"])
def test_chart_field_bound_includes_hidden_inputs_and_calculations(kind, extra):
    dimensions = [{"table": "people", "column": f"dimension_{index}"} for index in range(63)]
    tile = {"id": "chart", "title": "Chart", "kind": kind, "measures": [MEASURE], **extra}
    boundary = DashboardTile.model_validate({**tile, "dimensions": dimensions[:62]})
    assert (len(boundary.dimensions) + len(boundary.measures)
            + len(boundary.hidden_measures) + len(boundary.calculations)) == 64
    with pytest.raises(ValidationError, match="at most 64 dimensions, base inputs and calculations"):
        DashboardTile.model_validate({**tile, "dimensions": dimensions})


def test_multidimensional_support_retains_kpi_detail_and_donut_rules():
    tile = {"id": "chart", "title": "Chart", "dimensions": DIMENSIONS, "measures": [MEASURE]}
    for kind in ["kpi", "detail"]:
        with pytest.raises(ValidationError):
            DashboardTile.model_validate({**tile, "kind": kind, "detailFields": DIMENSIONS})
    with pytest.raises(ValidationError, match="exactly one measure"):
        DashboardTile.model_validate({**tile, "kind": "donut", "measures": [
            MEASURE, {**MEASURE, "aggregate": "count_distinct"}]})
