"""Schemer's saved-model authority and short-lived report resource boundary."""

import asyncio
import threading
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from schemii.main import create_app, create_services
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.console import ConsoleResultColumn
from schemii.schemer.service import _materialize

CATALOG = {"namespace": "public", "database": "warehouse", "fingerprint": "one",
           "tables": [{"name": "people", "columns": [{"name": "id", "dataType": "uuid"}, {"name": "name", "dataType": "text"}]}],
           "relationships": [], "positions": []}
DEFINITION = {"root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}],
              "edges": [], "scopes": [], "exposedFields": [{"table": "people", "column": "name"}]}
EXPLORE = {"root": "people", "fields": [{"table": "people", "column": "name"}], "limit": 3}


class Console:
    def __init__(self):
        self.closed = []
        self.cancelled = False
        self.pages = []
        self.receipt = SimpleNamespace(id="execution", status="succeeded", error_code=None,
                                       error_message=None, results=[SimpleNamespace(id="result")])

    def reserve_read_target(self, owner, **kwargs):
        self.target = kwargs
        return self.receipt

    def run(self, owner, identifier):
        pass

    def get_owned(self, owner, identifier):
        return self.receipt

    def page(self, owner, workspace, execution, result, cursor):
        self.pages.append(cursor)
        return SimpleNamespace(columns=[ConsoleResultColumn(name="name", data_type="text")],
                               rows=[["A"], ["B"]] if cursor is None else [["C"], ["D"]],
                               next_cursor="next" if cursor is None else None, truncated=False)

    def close_result(self, owner, workspace, execution, result):
        self.closed.append(result)

    def cancel(self, owner, workspace, execution):
        self.cancelled = True


@pytest.fixture
def setup():
    services = create_services()
    fresh_calls = []
    def catalog(services, owner, connection_id, namespace, fresh=False):
        profile = services.connections.get(owner, connection_id)
        fresh_calls.append(fresh)
        return {**deepcopy(CATALOG), "connectionId": connection_id, "database": profile.database}
    console = Console()
    services = replace(services, model_catalogs=SimpleNamespace(get=catalog), console=console)
    client = TestClient(create_app(services=services), base_url="http://localhost")
    connection = client.post("/api/v1/connections", json={"name": "Warehouse", "host": "localhost", "database": "warehouse", "username": "reader"}).json()
    response = client.post("/api/v1/schemoo/models", json={"name": "People", "connectionId": connection["id"],
                           "namespace": "public", "definition": DEFINITION, "explore": EXPLORE})
    assert response.status_code == 201, response.text
    model = response.json()
    yield client, {"modelId": model["id"], "expectedRevision": 1, "explore": deepcopy(EXPLORE)}, console, fresh_calls
    client.close()


def test_plan_and_bounded_report_release_results(setup):
    client, body, console, fresh = setup
    plan = client.post("/api/v1/schemer/plan", json=body)
    assert plan.status_code == 200, plan.text
    assert "LIMIT 3" in plan.json()["sql"]
    response = client.post("/api/v1/schemer/query", json=body)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["rows"] == [["A"], ["B"], ["C"]]
    assert report["columns"] == [{"name": "name", "dataType": "text"}]
    assert report["rowLimit"] == 3 and report["limitReached"]
    assert report["elapsedMs"] >= 0
    assert console.closed == ["result"]
    assert console.pages == [None, "next"]
    assert fresh[-1] is True
    assert console.target["statements"] == [report["plan"]["sql"]]


@pytest.mark.parametrize("path", ["plan", "query"])
def test_fixed_root_exposure_revision_and_limit(setup, path):
    client, body, _, _ = setup
    url = f"/api/v1/schemer/{path}"
    changed = deepcopy(body)
    changed["explore"]["root"] = "unrelated"
    response = client.post(url, json=changed)
    assert response.status_code == 422 and response.json()["error"]["code"] == "report_root_changed"
    changed = deepcopy(body)
    changed["explore"]["fields"][0]["column"] = "id"
    assert client.post(url, json=changed).status_code == 422
    changed = deepcopy(body)
    changed["explore"]["limit"] = 101
    assert client.post(url, json=changed).status_code == 422
    changed = {**body, "expectedRevision": 99}
    assert client.post(url, json=changed).status_code == 409
    assert client.post(url, json={**body, "sql": "SELECT 1"}).status_code == 422
    client.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="someone-else", authentication_source="local_prototype")
    assert client.post(url, json=body).status_code == 404


def test_fetch_failure_releases_all_results():
    console = Console()
    def fail(*args):
        raise RuntimeError("fetch failed")
    console.page = fail
    with pytest.raises(RuntimeError, match="fetch failed"):
        _materialize(console, "owner", console.receipt, {}, 3, threading.Event())
    assert console.closed == ["result"]


def test_failed_execution_releases_partial_results():
    console = Console()
    console.receipt.status = "failed"
    console.receipt.results.append(SimpleNamespace(id="second"))
    with pytest.raises(ApiProblem):
        _materialize(console, "owner", console.receipt, {}, 3, threading.Event())
    assert console.closed == ["result", "second"]


def test_cancellation_releases_results():
    console = Console()
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(ApiProblem):
        _materialize(console, "owner", console.receipt, {}, 3, stopped)
    assert console.cancelled and console.closed == ["result"]


@pytest.mark.parametrize("cancel_task", [False, True])
def test_disconnect_and_task_cancellation_wait_for_cleanup(monkeypatch, cancel_task):
    from schemii.schemer import service
    console = Console()
    started, released = threading.Event(), threading.Event()
    def run(*args):
        started.set()
        assert released.wait(2)
    def cancel(*args):
        console.cancelled = True
        released.set()
    console.run = run
    console.cancel = cancel
    model = SimpleNamespace(connection_id="connection", database="warehouse", namespace="public")
    monkeypatch.setattr(service, "report_plan", lambda *args, **kwargs: (model, {"sql": "SELECT name LIMIT 3"}))
    class Request:
        async def is_disconnected(self):
            return started.is_set() and not cancel_task
    async def scenario():
        work = asyncio.create_task(service.query_report(SimpleNamespace(console=console), "owner",
            SimpleNamespace(explore=SimpleNamespace(limit=3)), Request()))
        if cancel_task:
            while not started.is_set():
                await asyncio.sleep(0.001)
            work.cancel()
        with pytest.raises(asyncio.CancelledError if cancel_task else ApiProblem):
            await work
    asyncio.run(scenario())
    assert console.cancelled
    assert console.closed == ["result"]


def test_cancel_during_reservation_releases_admitted_lease(monkeypatch):
    from schemii.schemer import service
    console = Console()
    entered, released = threading.Event(), threading.Event()
    def reserve(*args, **kwargs):
        entered.set()
        assert released.wait(2)
        return console.receipt
    def run(*args):
        pytest.fail("A cancelled reservation must never execute")
    console.reserve_read_target = reserve
    console.run = run
    model = SimpleNamespace(connection_id="connection", database="warehouse", namespace="public")
    monkeypatch.setattr(service, "report_plan", lambda *args, **kwargs: (model, {"sql": "SELECT name LIMIT 3"}))
    async def scenario():
        work = asyncio.create_task(service.query_report(SimpleNamespace(console=console), "owner",
            SimpleNamespace(explore=SimpleNamespace(limit=3)), None))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        work.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await work
    asyncio.run(scenario())
    assert console.cancelled
