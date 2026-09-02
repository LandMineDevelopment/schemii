from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from schemii.common.api import inspection as route_inspection
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.metadata import MetadataRepositories
from schemii.common.postgres import PsycopgPostgresGateway
from schemii.common.postgres import inspection as database_inspection
from schemii.common import system_inspection
from schemii.common.system_inspection import build_developer_system_document
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


def test_developer_system_inspection_is_opt_in_and_hidden_from_openapi() -> None:
    disabled = TestClient(create_app(), base_url="http://localhost")
    enabled = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    assert disabled.get("/_developer/system").status_code == 404
    response = enabled.get("/_developer/system")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "/_developer/system" not in enabled.get("/openapi.json").json()["paths"]


def test_developer_documents_are_derived_once_for_each_application_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counters = {"routes": 0, "database": 0, "system": 0}

    def track(module: object, name: str, key: str) -> None:
        original = getattr(module, name)

        def tracked(application: object) -> dict[str, object]:
            counters[key] += 1
            return original(application)

        monkeypatch.setattr(module, name, tracked)

    track(route_inspection, "build_developer_route_document", "routes")
    track(database_inspection, "build_developer_database_document", "database")
    track(system_inspection, "build_developer_system_document", "system")

    first = TestClient(create_app(developer_inspection=True), base_url="http://localhost")
    assert counters == {"routes": 1, "database": 1, "system": 1}
    for path in ("/_developer/routes", "/_developer/database", "/_developer/system"):
        assert first.get(path).json()["analysis"]["generation"] == "application-startup"
        assert first.get(path).status_code == 200
    assert counters == {"routes": 1, "database": 1, "system": 1}

    create_app(developer_inspection=True)
    assert counters == {"routes": 2, "database": 2, "system": 2}


def test_system_inspection_preserves_application_route_registration_order() -> None:
    application = create_app()
    document = build_developer_system_document(application)
    expected_route_ids = [
        f"{method.lower()}:{route.path}"
        for route in system_inspection._public_route_contexts(application)
        if system_inspection.is_first_party(route.endpoint)
        for method in sorted(route.methods)
    ]

    assert [route["id"] for route in document["routes"]] == expected_route_ids


def test_system_inspection_joins_routes_services_repositories_and_gateway_calls() -> None:
    document = build_developer_system_document(create_app())

    assert document["schemaVersion"] == 1
    assert document["analysis"]["generation"] == "application-startup"
    assert document["analysis"]["callGraph"] == (
        "recursive-route-and-runtime-calls"
    )
    assert document["analysis"]["controlFlow"] == "static-source-regions"
    assert document["analysis"]["runtimeValues"] == "types-only"
    assert document["analysis"]["truncated"] == {
        "routes": False,
        "callables": False,
        "bindings": False,
        "objects": False,
        "source": False,
    }
    assert len(document["routes"]) == 71

    objects = {item["id"]: item for item in document["objects"]}
    callables = {item["objectId"]: item for item in document["callables"]}

    layout = next(
        route
        for route in document["routes"]
        if route["id"]
        == "put:/api/v1/schemii/workspaces/{workspace_id}/layout"
    )
    layout_calls = callables[layout["endpointObjectId"]]["calls"]
    layout_targets = {
        objects[call["objectId"]]["qualname"]: call for call in layout_calls
    }
    assert "ConnectionService.use" in layout_targets
    assert "PsycopgPostgresGateway.introspect" in layout_targets
    assert "InMemoryWorkspaceRepository.update_layout" in layout_targets
    assert "with" in {
        context["kind"]
        for context in layout_targets["ConnectionService.use"]["contexts"]
    }
    assert "finally" in {
        context["kind"]
        for call in callables[
            next(
                object_id
                for object_id, item in objects.items()
                if item["qualname"] == "PsycopgPostgresGateway.introspect"
            )
        ]["calls"]
        for context in call["contexts"]
    }
    assert len(layout["implementationDigest"]) == 64

    service_delete_id = next(
        object_id
        for object_id, item in objects.items()
        if item["qualname"] == "ConnectionService.delete"
    )
    delete_targets = {
        objects[call["objectId"]]["qualname"]
        for call in callables[service_delete_id]["calls"]
    }
    assert {
        "InMemoryConnectionRepository.get",
        "InMemoryWorkspaceRepository.count_for_connection",
        "ConnectionInUseError",
        "InMemoryConnectionRepository.delete",
    }.issubset(delete_targets)


def test_system_inspection_derives_runtime_protocol_bindings_without_values() -> None:
    connections = InMemoryConnectionRepository()
    workspaces = InMemoryWorkspaceRepository()
    connections.create(
        "inspection-owner",
        PostgresConnectionCreate(
            name="runtime-only-name-9f70",
            host="runtime-only-host-9f70.internal",
            database="runtime_only_database_9f70",
            username="runtime_only_user_9f70",
            password=SecretStr("runtime-only-secret-9f70"),
        ),
    )
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connections),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=PsycopgPostgresGateway(),
        workspaces=workspaces,
        designs=InMemoryDesignRepository(),
    )

    document = build_developer_system_document(create_app(services))
    objects = {item["id"]: item for item in document["objects"]}
    bindings = {
        (
            objects[item["ownerObjectId"]]["name"],
            item["attribute"],
        ): (
            {objects[value]["name"] for value in item["contractObjectIds"]},
            {objects[value]["name"] for value in item["implementationObjectIds"]},
        )
        for item in document["bindings"]
    }

    assert bindings[("ConnectionService", "_repository")] == (
        {"ConnectionRepository"},
        {"InMemoryConnectionRepository"},
    )
    assert bindings[("ConnectionService", "_dependency_providers")] == (
        {"ConnectionDependencyProvider"},
        {"InMemoryWorkspaceRepository"},
    )
    assert bindings[("ApplicationServices", "postgres")] == (
        {"PostgresGateway"},
        {"PsycopgPostgresGateway"},
    )

    serialized = json.dumps(document)
    assert "runtime-only-name-9f70" not in serialized
    assert "runtime-only-host-9f70.internal" not in serialized
    assert "runtime_only_database_9f70" not in serialized
    assert "runtime_only_user_9f70" not in serialized
    assert "runtime-only-secret-9f70" not in serialized
    assert '"/home/' not in serialized


def test_every_route_journey_is_derived_from_live_source_relationships() -> None:
    document = build_developer_system_document(create_app())
    objects = {item["id"]: item for item in document["objects"]}

    assert document["analysis"]["journeyClassification"] == (
        "runtime-bindings-and-source-control-flow"
    )
    assert all(route["journey"]["status"] == "complete" for route in document["routes"])
    for route in document["routes"]:
        journey = route["journey"]
        assert journey["issues"] == []
        assert {node["key"] for node in journey["nodes"]}
        for node in journey["nodes"]:
            source_object = objects[node["objectId"]]
            assert node["provenance"] == "derived"
            assert node["evidence"]["kind"]
            assert source_object["location"]["path"].startswith("schemii/")
            assert source_object["location"]["definitionLine"] is not None

    create = next(
        route
        for route in document["routes"]
        if route["id"] == "post:/api/v1/connections"
    )
    assert [objects[item]["name"] for item in create["request"]["bodyObjectIds"]] == [
        "PostgresConnectionCreate"
    ]
    assert [objects[item]["name"] for item in create["response"]["objectIds"]] == [
        "PostgresConnectionProfile"
    ]
    assert [
        objects[item]["name"]
        for dependency in create["dependencies"]
        for item in dependency["resultObjectIds"]
    ] == ["Principal"]
    assert "database" not in {node["stage"] for node in create["journey"]["nodes"]}

    test_connection = next(
        route
        for route in document["routes"]
        if route["id"] == "post:/api/v1/connections/{connection_id}/test"
    )
    database_nodes = [
        node
        for node in test_connection["journey"]["nodes"]
        if node["stage"] == "database"
    ]
    assert database_nodes
    assert database_nodes[0]["role"] == "database-call"
    assert database_nodes[0]["evidence"]["kind"] == "installed-postgres-gateway"
    assert any(
        transition["fromStage"] == "api"
        and transition["toStage"] == "database"
        for transition in test_connection["journey"]["transitions"]
    )


def test_system_inspection_derives_data_shapes_and_call_argument_flow() -> None:
    document = build_developer_system_document(create_app())
    objects = {item["id"]: item for item in document["objects"]}
    callables = {item["objectId"]: item for item in document["callables"]}
    create = next(
        route
        for route in document["routes"]
        if route["id"] == "post:/api/v1/connections"
    )

    handler_signature = callables[create["endpointObjectId"]]["signature"]
    assert [
        (parameter["name"], parameter["annotation"])
        for parameter in handler_signature["parameters"]
    ] == [
        ("body", "PostgresConnectionCreate"),
        ("request", "Request"),
        ("principal", "Principal"),
    ]
    assert handler_signature["returnAnnotation"] == "PostgresConnectionProfile"
    assert [
        objects[object_id]["name"]
        for object_id in handler_signature["returnObjectIds"]
    ] == ["PostgresConnectionProfile"]

    service_call = next(
        call
        for call in callables[create["endpointObjectId"]]["calls"]
        if objects[call["objectId"]]["qualname"] == "ConnectionService.create"
    )
    assert [
        (
            argument["parameter"],
            argument["annotation"],
            argument["expression"],
        )
        for argument in service_call["arguments"]
    ] == [
        ("owner_id", "str", "principal.user_id"),
        ("request", "PostgresConnectionCreate", "body"),
    ]
    assert service_call["targetSignature"]["returnAnnotation"] == (
        "PostgresConnectionProfile"
    )
    assert service_call["endLine"] >= service_call["line"]

    shapes = {
        item["name"]: item["dataShape"]
        for item in document["objects"]
        if item["name"]
        in {"Principal", "PostgresConnectionCreate", "PostgresConnectionProfile"}
    }
    assert [field["name"] for field in shapes["Principal"]["fields"]] == [
        "user_id",
        "authentication_source",
    ]
    assert {field["name"] for field in shapes["PostgresConnectionCreate"]["fields"]} >= {
        "host",
        "database",
        "sslMode",
        "connectTimeout",
    }
    assert {
        field["name"] for field in shapes["PostgresConnectionProfile"]["fields"]
    } >= {"id", "revision", "credentialStored", "createdAt", "updatedAt"}

    assert " at 0x" not in json.dumps(document)
