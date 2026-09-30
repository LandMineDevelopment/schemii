import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from schemii.common.api import inspection as route_inspection
from schemii.common.developer_inspection import (
    build_developer_inspection_snapshot,
)
from schemii.main import create_app

pytest_plugins = ["inspection_fixtures"]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
            default=dict,
        ).encode("utf-8")
    ).hexdigest()


def test_inspection_snapshot_is_versioned_coherent_and_stable_for_one_app(
    inspection_baseline,
) -> None:
    from inspection_fixtures import small_inspection_app

    application = small_inspection_app()

    first = build_developer_inspection_snapshot(application)
    second = build_developer_inspection_snapshot(application)

    assert first == second
    for snapshot in (first, second, inspection_baseline.snapshot):
        assert snapshot["schemaVersion"] == 1
        assert snapshot["generation"] == "application-startup"
        assert len(snapshot["snapshotId"]) == 64
        assert set(snapshot["documentDigests"]) == {
            "routes",
            "database",
            "system",
            "openapi",
        }
        assert all(
            len(value) == 64 for value in snapshot["documentDigests"].values()
        )
        assert snapshot["documentDigests"] == {
            name: digest(document)
            for name, document in snapshot["documents"].items()
        }
        assert snapshot["snapshotId"] == digest(snapshot["documentDigests"])
        assert all(
            document["schemaVersion"] == 1
            for name, document in snapshot["documents"].items()
            if name != "openapi"
        )


def test_snapshot_documents_describe_the_same_registered_operations(
    inspection_baseline,
) -> None:
    documents = inspection_baseline.documents
    openapi_operations = {
        f"{method}:{path}": operation
        for path, path_item in documents["openapi"]["paths"].items()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    route_document = documents["routes"]
    routes = {route["id"]: route for route in route_document["routes"]}
    system_routes = {
        route["id"]: route for route in documents["system"]["routes"]
    }

    assert routes.keys() == system_routes.keys() == openapi_operations.keys()
    assert all(
        routes[route_id]["operationId"]
        == system_routes[route_id]["operationId"]
        == operation["operationId"]
        for route_id, operation in openapi_operations.items()
    )

    objects = {item["id"]: item for item in route_document["objects"]}
    planned_from_source = {
        route_id
        for route_id, route in routes.items()
        if any(
            objects[call["objectId"]]["qualname"] == "planned_capability"
            for call in route["calls"]
        )
    }
    planned_from_openapi = {
        route_id
        for route_id, operation in openapi_operations.items()
        if operation.get("x-schemii-status") == "planned"
    }
    assert planned_from_source == planned_from_openapi


def test_canonical_snapshot_and_compatibility_routes_share_exact_documents(
    inspection_baseline,
) -> None:
    # These are real endpoint responses from a newly constructed installed app,
    # not documents injected into another app or requests on a shared client.
    responses = inspection_baseline.run.responses
    response = responses["/_developer/inspection"][0]

    assert response.status_code == 200
    assert response.cache_control == "no-store"
    snapshot = response.payload
    for name in ("routes", "database", "system"):
        compatibility = responses[f"/_developer/{name}"][0]
        assert compatibility.status_code == 200
        assert compatibility.cache_control == "no-store"
        assert compatibility.payload == snapshot["documents"][name]
    assert responses["/openapi.json"][0].payload == snapshot["documents"]["openapi"]
    assert not {
        "/_developer/inspection",
        "/_developer/routes",
        "/_developer/database",
        "/_developer/system",
    } & set(snapshot["documents"]["openapi"]["paths"])


def test_snapshot_identity_changes_when_the_registered_application_changes() -> None:
    from inspection_fixtures import small_inspection_app

    before = small_inspection_app()
    after = small_inspection_app()

    @after.get("/example-inspection-identity")
    def example() -> dict[str, bool]:
        return {"ok": True}

    example.__module__ = "schemii.test_inspection"
    first = build_developer_inspection_snapshot(before)
    second = build_developer_inspection_snapshot(after)

    assert first["snapshotId"] != second["snapshotId"]
    assert "/example-inspection-identity" not in first["documents"]["openapi"]["paths"]
    assert "/example-inspection-identity" in second["documents"]["openapi"]["paths"]
    for name in ("routes", "system"):
        route_id = "get:/example-inspection-identity"
        assert route_id not in {
            route["id"] for route in first["documents"][name]["routes"]
        }
        assert route_id in {
            route["id"] for route in second["documents"][name]["routes"]
        }


def test_canonical_inspection_is_opt_in_and_hidden_from_its_openapi_snapshot(
    inspection_http_documents,
) -> None:
    disabled = TestClient(create_app(), base_url="http://localhost")
    enabled = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    assert disabled.get("/_developer/inspection").status_code == 404
    response = enabled.get("/_developer/inspection")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == inspection_http_documents
    assert (
        "/_developer/inspection"
        not in response.json()["documents"]["openapi"]["paths"]
    )


def test_snapshot_identity_changes_when_endpoint_source_changes(monkeypatch) -> None:
    from inspection_fixtures import small_inspection_app

    application = small_inspection_app()
    endpoint = next(
        route.endpoint for route in application.routes if route.path == "/example"
    )
    original = route_inspection.inspect.getsourcelines

    def source_for(value):
        return lambda subject: (
            ([f"def _minimal_endpoint():\n    return {{'value': {value}}}\n"], 1)
            if subject is endpoint
            else original(subject)
        )

    monkeypatch.setattr(route_inspection.inspect, "getsourcelines", source_for(1))
    first = build_developer_inspection_snapshot(application)
    monkeypatch.setattr(route_inspection.inspect, "getsourcelines", source_for(2))
    second = build_developer_inspection_snapshot(application)

    assert first["documents"]["openapi"] == second["documents"]["openapi"]
    assert first["snapshotId"] != second["snapshotId"]
    for name in ("routes", "system"):
        before = first["documents"][name]["routes"][0]
        after = second["documents"][name]["routes"][0]
        assert before["id"] == after["id"] == "get:/example"
        assert before["implementationDigest"] != after["implementationDigest"]


def test_snapshot_identity_tracks_the_configured_gateway() -> None:
    from inspection_fixtures import InspectionGateway, small_inspection_app

    class AlternativeGateway(InspectionGateway):
        pass

    AlternativeGateway.__module__ = "schemii.test_inspection"
    first = build_developer_inspection_snapshot(small_inspection_app())
    second = build_developer_inspection_snapshot(
        small_inspection_app(AlternativeGateway())
    )

    assert first["documents"]["openapi"] == second["documents"]["openapi"]
    assert first["snapshotId"] != second["snapshotId"]
    before = first["documents"]["database"]["gateway"]
    after = second["documents"]["database"]["gateway"]
    assert before["contractObjectId"] == after["contractObjectId"]
    assert before["implementationObjectId"] != after["implementationObjectId"]
    assert after["implementationObjectId"].endswith("AlternativeGateway")
    assert second["documents"]["system"]["services"][0]["implementationObjectId"] == (
        after["implementationObjectId"]
    )


def test_baseline_documents_are_deeply_immutable_and_copies_are_independent(
    inspection_baseline,
) -> None:
    snapshot = inspection_baseline.snapshot
    routes = inspection_baseline.documents["routes"]["routes"]
    original_id = routes[0]["id"]

    with pytest.raises(TypeError):
        snapshot["schemaVersion"] = 999
    with pytest.raises(TypeError):
        routes[0]["id"] = "changed"
    with pytest.raises(TypeError):
        routes[0] = {}

    copy = json.loads(json.dumps(snapshot, default=dict))
    copy["documents"]["routes"]["routes"][0]["id"] = "changed"
    assert routes[0]["id"] == original_id
    assert digest(snapshot["documentDigests"]) == snapshot["snapshotId"]

    with pytest.raises(TypeError):
        inspection_baseline.run.construction_derivations["routes"] = 999
    with pytest.raises(TypeError):
        inspection_baseline.run.responses["/_developer/routes"][0].payload[
            "schemaVersion"
        ] = 999
