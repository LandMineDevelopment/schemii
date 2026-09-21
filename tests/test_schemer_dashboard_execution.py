"""Saved dashboard execution authority, SQL transparency and page boundaries."""
import json
from types import SimpleNamespace

import pytest
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.console import ConsoleResultColumn
from test_schemer_routes import setup


@pytest.fixture
def dashboard(setup):
    client, model, console, fresh_calls = setup
    response = client.post("/api/v1/schemer/dashboards", json={
        "name": "People dashboard", "modelId": model["modelId"], "modelRevision": 1,
        "tiles": [{"id": "people", "title": "People", "kind": "bar",
                   "dimensions": [{"table": "people", "column": "name"}],
                   "measures": [{"table": "people", "column": "name", "aggregate": "count_distinct"}],
                   "detailFields": [{"table": "people", "column": "name"}], "limit": 3}]})
    assert response.status_code == 201, response.text
    console.runs = []
    def run(owner, identifier):
        console.runs.append(identifier)
        console.receipt.results = [SimpleNamespace(id=f"result-{i}") for i in range(len(console.target["statements"]))]
    console.run = run
    dashboard = response.json()
    return client, f'/api/v1/schemer/dashboards/{dashboard["id"]}/tiles/people', console, fresh_calls


def test_tile_sql_route_uses_saved_configuration_without_execution(dashboard):
    client, url, console, _ = dashboard
    result = client.post(url + "/plan", json={"expectedRevision": 1})
    assert result.status_code == 200, result.text
    plan = result.json()
    assert 'COUNT(DISTINCT' in plan["sql"] and 'GROUP BY' in plan["sql"]
    assert "LIMIT" not in plan["sql"] and "OFFSET" not in plan["sql"]
    assert plan["rowLimit"] == 3
    assert not hasattr(console, "target")


def test_tile_execution_streams_and_releases_results(dashboard):
    client, url, console, fresh = dashboard
    result = client.post(url + "/executions", json={"expectedRevision": 1})
    assert result.status_code == 200, result.text
    events = [json.loads(line) for line in result.text.splitlines()]
    plan = events[0]["tiles"][0]["plan"]
    assert events[1]["executionId"] == console.receipt.id
    assert console.closed == ["result-0"] and console.pages == [None, "next"]
    assert events[-2]["rowCount"] == 4 and events[-1]["type"] == "end"
    assert console.runs == [console.receipt.id]
    assert fresh[-1] is True
    assert console.target["statements"] == [plan["sql"]]
    assert "LIMIT" not in plan["sql"] and "OFFSET" not in plan["sql"]


def test_offset_and_old_rerun_query_route_are_unavailable(dashboard):
    client, url, console, _ = dashboard
    assert client.post(url + "/executions", json={"expectedRevision": 1, "offset": 3}).status_code == 422
    assert client.post(url + "/query", json={"expectedRevision": 1}).status_code == 404
    assert console.runs == []


@pytest.mark.parametrize("action", ["plan", "executions"])
def test_dashboard_revision_and_owner_are_enforced_before_execution(dashboard, action):
    client, url, console, _ = dashboard
    result = client.post(url + "/" + action, json={"expectedRevision": 9})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "dashboard_revision_conflict"
    client.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="another-owner", authentication_source="local_prototype")
    assert client.post(url + "/" + action, json={"expectedRevision": 1}).status_code == 404
    assert not hasattr(console, "target")


@pytest.mark.parametrize("action", ["plan", "executions"])
def test_drill_selection_reaches_compiler_and_cannot_inject_fields(dashboard, action):
    client, url, _, _ = dashboard
    body = {"expectedRevision": 1, "selection": {"dimensions": [{"table": "people", "column": "name", "value": "Alice"}], "measureIndex": 0}}
    result = client.post(url + "/" + action, json=body)
    assert result.status_code == 200, result.text
    plan = result.json() if action == "plan" else json.loads(result.text.splitlines()[0])["tiles"][0]["plan"]
    assert plan["drill"] is True
    assert '"contributors"."People.name" = ' in plan["sql"]
    assert 'GROUP BY' not in plan["sql"]
    body["selection"]["dimensions"][0]["column"] = "id"
    assert client.post(url + "/" + action, json=body).status_code == 422
    assert client.post(url + "/" + action, json={"expectedRevision": 1, "sql": "SELECT 1"}).status_code == 422


def configure_tiles(client, tile_url, count):
    from copy import deepcopy
    url = tile_url.rsplit("/tiles/", 1)[0]
    saved = client.get(url).json()
    tile = saved["tiles"][0]
    tiles = [{**deepcopy(tile), "id": f"tile-{index}", "title": f"Tile {index}"} for index in range(count)]
    response = client.put(url, json={"expectedRevision": saved["revision"], "name": saved["name"],
        "modelId": saved["modelId"], "modelRevision": saved["modelRevision"], "selections": saved["selections"], "tiles": tiles})
    assert response.status_code == 200, response.text
    return url, response.json()["revision"]


def test_five_tiles_share_one_protected_retained_execution(dashboard):
    client, tile_url, console, _ = dashboard
    url, revision = configure_tiles(client, tile_url, 5)
    response = client.post(url + "/executions", json={"expectedRevision": revision})
    assert response.status_code == 200, response.text
    payload = json.loads(response.text.splitlines()[0])
    assert len(payload["tiles"]) == 5 and payload["tileErrors"] == []
    assert [tile["tileId"] for tile in payload["tiles"]] == [f"tile-{i}" for i in range(5)]
    assert console.runs == [console.receipt.id]
    assert len(console.target["statements"]) == 5
    assert console.target["protect_result"] is True
    assert len(console.closed) == 5 and len(console.pages) == 10


def test_compile_failure_does_not_prevent_other_tiles_running(dashboard, monkeypatch):
    from schemii.schemer import routes
    from schemii.common.api.errors import ApiProblem
    client, tile_url, console, _ = dashboard
    url, revision = configure_tiles(client, tile_url, 5)
    original = routes.tile_plan
    def compile_tile(*args, **kwargs):
        if args[3] == "tile-1":
            raise ApiProblem(422, "invalid_model_query", "Choose a required filter value.")
        return original(*args, **kwargs)
    monkeypatch.setattr(routes, "tile_plan", compile_tile)
    response = client.post(url + "/executions", json={"expectedRevision": revision})
    assert response.status_code == 200, response.text
    payload = json.loads(response.text.splitlines()[0])
    assert [tile["tileId"] for tile in payload["tiles"]] == ["tile-0", "tile-2", "tile-3", "tile-4"]
    assert payload["tileErrors"] == [{"tileId": "tile-1", "code": "invalid_model_query", "message": "Choose a required filter value."}]
    assert len(console.target["statements"]) == 4


def test_group_owner_revision_and_capacity_are_enforced(dashboard):
    client, tile_url, console, _ = dashboard
    url, revision = configure_tiles(client, tile_url, 5)
    assert client.post(url + "/executions", json={"expectedRevision": revision + 1}).status_code == 409
    client.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="another-owner", authentication_source="local_prototype")
    assert client.post(url + "/executions", json={"expectedRevision": revision}).status_code == 404
    client.app.dependency_overrides.clear()
    from dataclasses import replace
    services = client.app.state.services
    config = replace(services.admin_config, console=replace(services.admin_config.console, maximum_statements_per_run=4))
    client.app.state.services = replace(services, admin_config=config)
    response = client.post(url + "/executions", json={"expectedRevision": revision})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dashboard_tile_execution_limit"
    assert console.runs == []


def test_cannot_save_dashboard_above_grouped_execution_limit(dashboard):
    from copy import deepcopy
    client, tile_url, console, _ = dashboard
    url = tile_url.rsplit("/tiles/", 1)[0]
    saved = client.get(url).json()
    tile = saved["tiles"][0]
    body = {"expectedRevision": saved["revision"], "name": saved["name"],
            "modelId": saved["modelId"], "modelRevision": saved["modelRevision"],
            "tiles": [{**deepcopy(tile), "id": f"tile-{i}"} for i in range(21)]}
    response = client.put(url, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dashboard_tile_execution_limit"
    assert response.json()["error"]["details"]["maximumTiles"] == 20
    assert len(client.get(url).json()["tiles"]) == 1
    assert console.runs == []


def test_cancel_during_group_reservation_releases_protected_execution(monkeypatch):
    import asyncio
    import threading
    from fastapi import BackgroundTasks
    from schemii.schemer import routes
    entered, released = threading.Event(), threading.Event()
    cancelled = []
    receipt = SimpleNamespace(id="retained")
    def reserve(*args, **kwargs):
        assert kwargs["protect_result"] is True
        entered.set()
        assert released.wait(2)
        return receipt
    model = SimpleNamespace(connection_id="connection", database="warehouse", namespace="public")
    dashboard = SimpleNamespace(tiles=[SimpleNamespace(id="tile")])
    monkeypatch.setattr(routes, "_dashboard", lambda *args: dashboard)
    monkeypatch.setattr(routes, "tile_plan", lambda *args, **kwargs: (model, {"sql": "SELECT 1", "rowLimit": 3}))
    console = SimpleNamespace(reserve_read_target=reserve,
        cancel=lambda *args: cancelled.append(args), run=lambda *args: pytest.fail("Cancelled admission cannot execute"))
    services = SimpleNamespace(console=console, admin_config=SimpleNamespace(console=SimpleNamespace(maximum_statements_per_run=20)))
    from schemii.schemer.streaming import reserve as reserve_stream
    async def scenario():
        work = asyncio.create_task(reserve_stream(services, "owner", model, [{"plan": {"sql": "SELECT 1"}}]))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        work.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await work
    asyncio.run(scenario())
    assert cancelled == [("owner", None, "retained")]


def test_native_form_export_streams_fresh_full_csv(dashboard):
    client, url, console, fresh = dashboard
    response = client.post(url + "/export", data={"payload": json.dumps({"expectedRevision": 1})})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-schemer-snapshot"] == "fresh"
    assert "attachment" in response.headers["content-disposition"]
    assert "People.name" in response.text.splitlines()[0]
    assert response.text.splitlines()[1:] == ["A", "B", "C", "D"]
    assert console.closed == ["result-0"]
    assert fresh[-1] is True
    assert "LIMIT" not in console.target["statements"][0]


def test_stream_and_export_revision_checks(dashboard):
    client, url, console, _ = dashboard
    for action in ["executions/stream", "export"]:
        response = client.post(url + "/" + action, json={"expectedRevision": 99})
        assert response.status_code == 409
    assert not hasattr(console, "target")
