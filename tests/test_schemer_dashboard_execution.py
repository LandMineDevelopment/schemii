"""Saved dashboard execution authority, SQL transparency and page boundaries."""
import json
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.console import ConsoleResultColumn
from test_schemer_routes import setup


@pytest.fixture
def planning_source(request, monkeypatch):
    """Real managed/shared planning adapters over counted, row-free transport."""
    from schemii.main import create_services
    from schemii.common.connections.models import PostgresConnectionCreate, SCHEMII_CONNECTION_OWNER_ID
    from schemii.common.postgres.models import PostgresColumn
    from schemii.schemer.dashboard_models import DashboardCreate
    from schemii.schemoo.models import ModelCreate
    from test_schemer_routes import Console, DEFINITION

    mode = request.param
    services = create_services()
    profile = services.connections.create_schemii_owned(PostgresConnectionCreate(
        name="Counted source", host="localhost", database="warehouse", username="reader",
        password="synthetic-counting-fixture"))
    author_profile = services.connections.create("author", PostgresConnectionCreate(
        name="Author source", host="localhost", database="warehouse", username="author"))
    owner = "viewer" if mode == "managed" else "author"
    model = services.models.create(owner, ModelCreate(
        name="People", connection_id=profile.id if mode == "managed" else author_profile.id,
        database="warehouse", namespace="public", definition=DEFINITION),
        connection_owner_id=SCHEMII_CONNECTION_OWNER_ID if mode == "managed" else owner)
    tile = {"id": "people", "title": "People", "kind": "detail",
            "detailFields": [{"table": "people", "column": "name"}], "limit": 3}
    dashboard = services.dashboards.create(owner, DashboardCreate(
        name="People", model_id=model.id, model_revision=1, tiles=[tile]))
    grant = {"role_id": "reader-role", "owner_id": owner, "dashboard_id": dashboard.id,
             "connection_owner_id": SCHEMII_CONNECTION_OWNER_ID, "connection_id": profile.id,
             "can_export": True, "can_drill": True}
    db_grant = {"role_id": "reader-role", "owner_id": SCHEMII_CONNECTION_OWNER_ID,
                "connection_id": profile.id, "allow_authoring": True}
    state = {"enabled": True, "granted": True,
             "product": True,
             "readable": {("people", "id"), ("people", "name")}}
    user = lambda: {"id": "viewer", "disabled": False} if state["enabled"] else None
    auth = SimpleNamespace(enabled=True, resolve=lambda token: user(), user=lambda actor: user(),
        capabilities=lambda actor: (["schemer:access", "schemer:author"] if mode == "managed"
                                     else ["schemer:access"]) if state["product"] else [],
        connection_access=lambda *args: deepcopy(db_grant) if state["granted"] else None,
        connection_grants=lambda actor: [deepcopy(db_grant)] if state["granted"] else [],
        dashboard_grants=lambda actor: [deepcopy(grant)] if state["granted"] else [], audit=lambda *args: None)
    services.connections.set_authority(auth)
    counts = Counter()
    original_get = services.models.get
    def model_get(*args):
        counts["model"] += 1
        return original_get(*args)
    monkeypatch.setattr(services.models, "get", model_get)
    columns = [PostgresColumn(name=name, ordinal=index + 1, data_type=kind, nullable=False)
               for index, (name, kind) in enumerate([("id", "uuid"), ("name", "text")])]
    live = SimpleNamespace(fingerprint="source-one", relationships=[], tables=[
        SimpleNamespace(name="people", columns=columns, primary_key=None, unique_constraints=[])])
    def introspect(*args):
        counts["introspect"] += 1
        return live
    def readable(*args):
        counts["readable"] += 1
        return set(state["readable"])
    console = Console()
    console.runs = []
    def run(owner, identifier, **kwargs):
        console.runs.append((owner, identifier))
        console.receipt.results = [SimpleNamespace(id=f"result-{i}")
                                   for i in range(len(console.target["statements"]))]
    console.run = run
    original_reserve = console.reserve_read_target
    def reserve(*args, **kwargs):
        kwargs.pop("connection_access", None)
        return original_reserve(*args, **kwargs)
    console.reserve_read_target = reserve
    services = replace(services, postgres=SimpleNamespace(introspect=introspect, readable_columns=readable),
                       console=console)
    route_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services, auth=auth)),
                                    cookies={"schemii_session": "test-viewer-session"})
    return SimpleNamespace(mode=mode, services=services, request=route_request, counts=counts,
                           state=state, model=model, dashboard=dashboard, tile=tile, owner=owner,
                           profile=profile, grant=grant, db_grant=db_grant, console=console)


async def plan_dashboard(source, count, *, invalid=()):
    from schemii.schemer import routes
    from schemii.schemer.dashboard_models import DashboardUpdate
    dashboard = source.services.dashboards.update(source.owner, source.dashboard.id, DashboardUpdate(
        expected_revision=source.dashboard.revision, name="People", model_id=source.model.id,
        model_revision=source.model.revision,
        tiles=[{**deepcopy(source.tile), "id": f"tile-{index}",
                **({"detailFields": [{"table": "people", "column": "id"}]} if index in invalid else {})}
               for index in range(count)]))
    source.dashboard = dashboard
    response = await routes.stream_dashboard(dashboard.id,
        routes.DashboardExecutionRequest(expected_revision=dashboard.revision), source.request,
        Principal(user_id="viewer", authentication_source="local_prototype"))
    return [json.loads(item) async for item in response.body_iterator]


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
@pytest.mark.parametrize("count", [1, 5, 20])
def test_dashboard_planning_source_acquisition_is_bounded(planning_source, count):
    import asyncio
    source = planning_source
    events = asyncio.run(plan_dashboard(source, count))
    assert len(events[0]["tiles"]) == count and events[0]["tileErrors"] == []
    assert events[-1] == {"type": "end"}
    assert source.console.runs == [("viewer", source.console.receipt.id)]
    assert len(source.console.closed) == count
    assert source.counts["introspect"] == 1, dict(source.counts)
    assert source.counts["readable"] == (2 if source.mode == "shared" else 1), dict(source.counts)
    assert source.counts["model"] <= (3 if source.mode == "shared" else 2), dict(source.counts)


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
def test_request_projection_preserves_single_tile_sql_and_results(planning_source):
    import asyncio
    from schemii.schemer.access import prepare_dashboard
    from schemii.schemer.tile_queries import tile_plan
    source = planning_source
    dashboard, scoped, _ = prepare_dashboard(source.request, "viewer", source.dashboard.id)
    _, expected = tile_plan(scoped, "viewer", dashboard, "people", fresh=True)
    source.counts.clear()
    events = asyncio.run(plan_dashboard(source, 5))
    assert [tile["plan"] for tile in events[0]["tiles"]] == [expected] * 5
    for index in range(5):
        batches = [event["rows"] for event in events if event["type"] == "rows" and event["tileId"] == f"tile-{index}"]
        assert batches == [[["A"], ["B"]], [["C"], ["D"]]]
    assert source.counts["introspect"] == 1


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
@pytest.mark.parametrize("count,invalid", [(0, ()), (5, (0,)), (5, tuple(range(5)))])
def test_request_projection_preserves_empty_and_invalid_tiles(planning_source, count, invalid):
    import asyncio
    source = planning_source
    events = asyncio.run(plan_dashboard(source, count, invalid=invalid))
    start = events[0]
    assert [tile["tileId"] for tile in start["tiles"]] == [f"tile-{i}" for i in range(count) if i not in invalid]
    assert start["tileErrors"] == [{"tileId": f"tile-{i}", "code": "field_not_exposed",
        "message": "These preview fields or report filters are not exposed by this model: people.id. "
                   "Expose them in the model or remove them from the preview."} for i in invalid]
    assert source.counts["introspect"] == (1 if count else 0)
    assert len(source.console.runs) == (1 if count > len(invalid) else 0)
    assert events[-1] == {"type": "end"}


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
def test_request_projection_refreshes_column_privileges_between_requests(planning_source):
    import asyncio
    source = planning_source
    first = asyncio.run(plan_dashboard(source, 5))
    assert len(first[0]["tiles"]) == 5
    source.state["readable"] = {("people", "id")}
    second = asyncio.run(plan_dashboard(source, 5))
    assert second[0]["tiles"] == []
    assert [error["code"] for error in second[0]["tileErrors"]] == ["model_source_drift"] * 5
    assert source.counts["introspect"] == 2
    assert source.counts["readable"] == (4 if source.mode == "shared" else 2)
    assert len(source.console.runs) == 1


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
@pytest.mark.parametrize("change", ["session", "product", "grant", "model", "profile"])
def test_current_authority_and_revisions_are_checked_after_compilation(planning_source, monkeypatch, change):
    import asyncio
    from schemii.common.api.errors import ApiProblem
    from schemii.common.connections.models import PostgresConnectionUpdate, SCHEMII_CONNECTION_OWNER_ID
    from schemii.schemer import routes
    from schemii.schemoo.models import ModelUpdate
    source = planning_source
    original = routes.tile_plan
    def compile_tile(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3] == "tile-0":
            if change == "session":
                source.state["enabled"] = False
            elif change == "product":
                source.state["product"] = False
            elif change == "grant":
                source.state["granted"] = False
            elif change == "model":
                source.services.models.update(source.owner, source.model.id, ModelUpdate(
                    expected_revision=source.model.revision, name="Changed", definition=source.model.definition))
            else:
                source.services.connections.update(SCHEMII_CONNECTION_OWNER_ID, source.profile.id,
                    PostgresConnectionUpdate(expected_revision=source.profile.revision,
                                             password="synthetic-rotated-fixture"))
        return result
    monkeypatch.setattr(routes, "tile_plan", compile_tile)
    with pytest.raises(ApiProblem) as denied:
        asyncio.run(plan_dashboard(source, 5))
    assert denied.value.status_code == (404 if change == "grant" and source.mode == "managed"
                                       else 403 if change in {"session", "product", "grant"} else 409)
    assert source.console.runs == [] and not hasattr(source.console, "target")
    assert source.counts["introspect"] == 1


@pytest.mark.parametrize("planning_source", ["shared"], indirect=True)
def test_shared_request_rechecks_same_role_before_execution(planning_source, monkeypatch):
    import asyncio
    from schemii.common.api.errors import ApiProblem
    from schemii.schemer import routes
    source = planning_source
    original = routes.tile_plan
    def compile_tile(*args, **kwargs):
        result = original(*args, **kwargs)
        source.db_grant["role_id"] = "other-role"
        return result
    monkeypatch.setattr(routes, "tile_plan", compile_tile)
    with pytest.raises(ApiProblem) as denied:
        asyncio.run(plan_dashboard(source, 5))
    assert denied.value.status_code == 403 and denied.value.code == "report_access_revoked"
    assert source.console.runs == [] and not hasattr(source.console, "target")


@pytest.mark.parametrize("planning_source", ["managed", "shared"], indirect=True)
def test_catalog_projection_failure_stays_bounded_and_is_reported_per_tile(planning_source):
    import asyncio
    from schemii.common.api.errors import ApiProblem
    source = planning_source
    def unavailable(*args):
        source.counts["introspect"] += 1
        raise ApiProblem(422, "source_unavailable", "The source catalog is unavailable.")
    source.services.postgres.introspect = unavailable
    events = asyncio.run(plan_dashboard(source, 5))
    assert events[0]["tiles"] == []
    assert events[0]["tileErrors"] == [{"tileId": f"tile-{i}", "code": "source_unavailable",
        "message": "The source catalog is unavailable."} for i in range(5)]
    assert source.counts["introspect"] == 1
    assert source.console.runs == [] and not hasattr(source.console, "target")


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
    assert '"contributors"."field_1" = ' in plan["sql"]
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
