from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Union

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import create_model

from schemii.common.api import inspection as route_inspection
from schemii.common.api.inspection import build_developer_route_document
from schemii.main import create_app


def test_developer_route_inspection_is_opt_in_and_hidden_from_openapi() -> None:
    disabled = TestClient(create_app(), base_url="http://localhost")
    enabled = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    assert disabled.get("/_developer/routes").status_code == 404
    response = enabled.get("/_developer/routes")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "/_developer/routes" not in enabled.get("/openapi.json").json()["paths"]


def test_developer_route_inspection_derives_flow_objects_and_bounded_source() -> None:
    api = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    document = api.get("/_developer/routes").json()

    assert document["schemaVersion"] == 1
    assert document["analysis"]["generation"] == "application-startup"
    assert document["analysis"]["callGraph"] == "direct-first-party-calls"
    assert document["analysis"]["truncated"]["routes"] is False
    assert document["analysis"]["truncated"]["objects"] is False
    assert len(document["routes"]) == 70
    objects = {item["id"]: item for item in document["objects"]}
    layout = next(
        route
        for route in document["routes"]
        if route["id"]
        == "put:/api/v1/schemii/workspaces/{workspace_id}/layout"
    )

    endpoint = objects[layout["endpointId"]]
    assert endpoint["kind"] == "handler"
    assert endpoint["docstring"].startswith("Validate revisions")
    assert endpoint["location"]["path"] == "schemii/schemii/workspaces/routes.py"
    assert "def update_workspace_layout" in endpoint["source"]["text"]
    assert "".join(value for _, value in endpoint["source"]["tokens"]) == endpoint[
        "source"
    ]["text"]
    assert {kind for kind, _ in endpoint["source"]["tokens"]} >= {
        "keyword",
        "definition",
        "string",
    }
    assert len(layout["implementationDigest"]) == 64

    dependency_names = {
        objects[dependency["objectId"]]["qualname"]
        for dependency in layout["dependencies"]
    }
    assert dependency_names == {"get_current_principal"}
    ordered_call_names = [
        objects[call["objectId"]]["qualname"] for call in layout["calls"]
    ]
    call_names = set(ordered_call_names)
    assert "InMemoryWorkspaceRepository.get" in call_names
    assert "ConnectionService.use" in call_names
    assert "PsycopgPostgresGateway.introspect" in call_names
    assert "InMemoryWorkspaceRepository.update_layout" in call_names
    assert ordered_call_names.index("_workspaces") < ordered_call_names.index(
        "InMemoryWorkspaceRepository.get"
    )
    assert ordered_call_names.index("_connections") < ordered_call_names.index(
        "ConnectionService.use"
    )
    assert {
        objects[item]["qualname"] for item in layout["requestObjectIds"]
    } == {"SchemiiWorkspaceLayoutUpdate", "TablePosition"}
    assert {
        objects[item]["qualname"] for item in layout["responseObjectIds"]
    } == {"SchemiiWorkspace", "TablePosition"}

    serialized = json.dumps(document)
    assert '"/home/' not in serialized
    assert '"/opt/' not in serialized
    assert "schemii-local-test" not in serialized
    assert all(
        len(item["source"]["text"] or "") <= document["analysis"]["sourceLimit"]
        for item in objects.values()
    )
    assert (
        sum(len(item["source"]["text"] or "") for item in objects.values())
        <= document["analysis"]["totalSourceLimit"]
    )


def test_source_metadata_uses_static_docstrings_and_hashes_complete_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sample() -> None:
        pass

    sample.__doc__ = "runtime-secret-value"
    shared = (
        'def sample() -> None:\n    """Intent from source."""\n    # '
        + ("x" * 33_000)
    )
    first_source = f"{shared}a\n"
    second_source = f"{shared}b\n"

    monkeypatch.setattr(
        route_inspection.inspect,
        "getsourcelines",
        lambda subject: (first_source.splitlines(keepends=True), 10),
    )
    first = route_inspection._source_metadata(sample)
    monkeypatch.setattr(
        route_inspection.inspect,
        "getsourcelines",
        lambda subject: (second_source.splitlines(keepends=True), 10),
    )
    second = route_inspection._source_metadata(sample)

    assert first["docstring"] == "Intent from source."
    assert "runtime-secret-value" not in json.dumps(first)
    assert first["source"]["text"] == second["source"]["text"]
    assert "".join(value for _, value in first["source"]["tokens"]) == first[
        "source"
    ]["text"]
    assert first["source"]["sha256"] != second["source"]["sha256"]
    assert first["source"]["truncated"] is True


def test_route_inspection_walks_nested_dependencies() -> None:
    application = FastAPI()
    application.state.services = SimpleNamespace()

    def leaf_dependency() -> str:
        return "leaf"

    def parent_dependency(leaf: str = Depends(leaf_dependency)) -> str:
        return leaf

    def endpoint(parent: str = Depends(parent_dependency)) -> dict[str, str]:
        return {"value": parent}

    for subject in (leaf_dependency, parent_dependency, endpoint):
        subject.__module__ = "schemii.test_inspection"
    application.get("/nested", response_model=None)(endpoint)

    document = build_developer_route_document(application)
    objects = {item["id"]: item for item in document["objects"]}
    route = next(item for item in document["routes"] if item["id"] == "get:/nested")

    assert [objects[item["objectId"]]["name"] for item in route["dependencies"]] == [
        "parent_dependency",
        "leaf_dependency",
    ]
    assert route["truncated"]["dependencies"] is False


def test_model_discovery_enforces_its_limit_with_wide_unions() -> None:
    models = tuple(
        create_model(
            f"InspectionModel{index}",
            value=(str, ...),
            __module__="schemii.test_inspection",
        )
        for index in range(40)
    )

    discovered, truncated = route_inspection._model_tree([Union[models]])

    assert len(discovered) == 32
    assert truncated is True


@pytest.mark.parametrize(
    ("environment_value", "expected_status"),
    [(None, 404), ("0", 404), ("1", 200)],
)
def test_module_application_honors_developer_inspection_environment(
    environment_value: str | None,
    expected_status: int,
) -> None:
    environment = os.environ.copy()
    if environment_value is None:
        environment.pop("SCHEMII_DEVELOPER_INSPECTION", None)
    else:
        environment["SCHEMII_DEVELOPER_INSPECTION"] = environment_value
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from fastapi.testclient import TestClient; "
                "from schemii.main import app; "
                "print(TestClient(app, base_url='http://localhost')"
                ".get('/_developer/routes').status_code)"
            ),
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.stdout.strip() == str(expected_status)
