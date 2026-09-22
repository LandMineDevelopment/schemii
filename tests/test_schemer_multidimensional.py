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
    assert '"contributors"."People.name" IS NULL' in response.json()["sql"]
    assert '"contributors"."People.id" = ' in response.json()["sql"]
    assert "GROUP BY" not in response.json()["sql"]
    drill_sql = response.json()["sql"]
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
    with pytest.raises(ValidationError, match="at most 64 output fields"):
        DashboardTile.model_validate({**tile, "dimensions": [
            {"table": "people", "column": f"dimension_{index}"} for index in range(64)]})


def test_multidimensional_support_retains_kpi_detail_and_donut_rules():
    tile = {"id": "chart", "title": "Chart", "dimensions": DIMENSIONS, "measures": [MEASURE]}
    for kind in ["kpi", "detail"]:
        with pytest.raises(ValidationError):
            DashboardTile.model_validate({**tile, "kind": kind, "detailFields": DIMENSIONS})
    with pytest.raises(ValidationError, match="exactly one measure"):
        DashboardTile.model_validate({**tile, "kind": "donut", "measures": [
            MEASURE, {**MEASURE, "aggregate": "count_distinct"}]})
