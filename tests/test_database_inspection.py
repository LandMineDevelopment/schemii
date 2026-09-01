from __future__ import annotations

import json

from fastapi.testclient import TestClient

from schemii.main import create_app


def test_developer_database_inspection_is_opt_in_and_hidden_from_openapi() -> None:
    disabled = TestClient(create_app(), base_url="http://localhost")
    enabled = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    assert disabled.get("/_developer/database").status_code == 404
    response = enabled.get("/_developer/database")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "/_developer/database" not in enabled.get("/openapi.json").json()["paths"]


def test_database_inspection_derives_the_runtime_contract_calls_and_queries() -> None:
    api = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    document = api.get("/_developer/database").json()

    assert document["schemaVersion"] == 1
    assert document["analysis"]["kind"] == "bounded-python-source"
    assert document["analysis"]["generation"] == "application-startup"
    assert document["analysis"]["callGraph"] == "recursive-first-party-calls"
    assert document["analysis"]["queryDiscovery"] == "referenced-static-query-constants"
    assert document["analysis"]["serviceBinding"] == "runtime-protocol-match"
    assert document["analysis"]["truncated"] == {
        "callables": False,
        "objects": False,
        "queries": False,
        "source": False,
    }

    objects = {item["id"]: item for item in document["objects"]}
    gateway = document["gateway"]
    assert gateway["serviceName"] == "postgres"
    assert objects[gateway["contractObjectId"]]["qualname"] == "PostgresGateway"
    assert (
        objects[gateway["implementationObjectId"]]["qualname"]
        == "PsycopgPostgresGateway"
    )

    operations = {item["name"]: item for item in document["operations"]}
    assert list(operations) == ["test_connection", "namespace_exists", "introspect"]
    assert operations["test_connection"]["returnAnnotation"] == (
        "PostgresConnectionTestResult"
    )
    assert operations["introspect"]["returnAnnotation"] == "PostgresCatalog"
    assert [
        item["name"] for item in operations["introspect"]["parameters"]
    ] == ["connection", "namespace"]
    assert all(
        len(operation["implementationDigest"]) == 64
        for operation in operations.values()
    )

    callables = {item["objectId"]: item for item in document["callables"]}
    introspect = callables[operations["introspect"]["implementationObjectId"]]
    call_names = [objects[item["objectId"]]["name"] for item in introspect["calls"]]
    assert call_names[:3] == ["_validated_namespace", "_connect", "_begin_read_only"]
    assert "_bounded_rows" in call_names
    assert call_names[-1] == "_cleanup"

    query_names = {item["name"] for item in document["queries"]}
    assert query_names == {
        "COLUMNS_QUERY",
        "CONNECTION_TEST_QUERY",
        "CONSTRAINTS_QUERY",
        "FUNCTIONS_QUERY",
        "INDEXES_QUERY",
        "METADATA_QUERY",
        "NAMESPACE_EXISTS_QUERY",
        "TABLES_QUERY",
        "TRIGGERS_QUERY",
        "VIEWS_QUERY",
    }
    introspection_query_ids = {
        query_id
        for call in introspect["calls"]
        for query_id in call["queryIds"]
    }
    assert {
        query["name"]
        for query in document["queries"]
        if query["id"] in introspection_query_ids
    } == query_names - {"CONNECTION_TEST_QUERY"}

    for query in document["queries"]:
        assert query["statement"] == "SELECT"
        assert query["location"]["path"] == "schemii/common/postgres/queries.py"
        assert query["location"]["definitionLine"] is not None
        assert query["marker"].startswith("schemii_")
        assert len(query["sha256"]) == 64
        assert query["truncated"] is False
    assert next(
        query
        for query in document["queries"]
        if query["name"] == "NAMESPACE_EXISTS_QUERY"
    )["placeholderCount"] == 1
    assert next(
        query
        for query in document["queries"]
        if query["name"] == "CONNECTION_TEST_QUERY"
    )["resultColumns"] == ["database", "server_version"]
    assert next(
        query
        for query in document["queries"]
        if query["name"] == "NAMESPACE_EXISTS_QUERY"
    )["resultColumns"] == ["namespace_exists"]
    assert all(query["resultColumns"] for query in document["queries"])
    assert "pg_catalog.pg_namespace" in next(
        query
        for query in document["queries"]
        if query["name"] == "NAMESPACE_EXISTS_QUERY"
    )["catalogObjects"]


def test_database_inspection_is_static_bounded_and_contains_no_runtime_values() -> None:
    document = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    ).get("/_developer/database").json()

    serialized = json.dumps(document)
    assert '"/home/' not in serialized
    assert '"/opt/' not in serialized
    assert "postgres.internal" not in serialized
    assert all(
        len(item["source"]["text"] or "") <= document["analysis"]["sourceLimit"]
        for item in document["objects"]
    )
    assert (
        sum(len(item["source"]["text"] or "") for item in document["objects"])
        <= document["analysis"]["totalSourceLimit"]
    )
    assert sum(len(item["sql"]) for item in document["queries"]) <= document[
        "analysis"
    ]["totalQuerySourceLimit"]
    assert {item["statement"] for item in document["inlineStatements"]} == {
        "BEGIN",
        "SET",
    }
    assert all(item["readOnly"] for item in document["inlineStatements"])
