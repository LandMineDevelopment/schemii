from datetime import datetime, timezone

from fastapi.testclient import TestClient

from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.connections.service import ConnectionService
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresConnectionTestResult,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.main import ApplicationServices, create_app
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class FakePostgresGateway:
    def __init__(self) -> None:
        self.connections = []
        self.namespace_available = True

    def test_connection(self, connection):
        self.connections.append(connection)
        return PostgresConnectionTestResult(
            database=connection.database,
            server_version="17.2",
        )

    def namespace_exists(self, connection, namespace):
        self.connections.append(connection)
        return self.namespace_available and namespace == "public"

    def introspect(self, connection, namespace):
        self.connections.append(connection)
        return build_postgres_catalog(
            database=connection.database,
            namespace=namespace,
            server_version="17.2",
            server_version_num=170002,
            server_timezone="UTC",
            tables=(
                PostgresTable(
                    namespace=namespace,
                    name="customers",
                    kind="table",
                    is_partition=False,
                    columns=(
                        PostgresColumn(
                            name="id",
                            ordinal=1,
                            data_type="bigint",
                            nullable=False,
                        ),
                    ),
                ),
            ),
            relationships=(),
            functions=(),
            views=(),
            materialized_views=(),
            captured_at=datetime.now(timezone.utc),
        )


def client() -> tuple[TestClient, FakePostgresGateway]:
    connections = InMemoryConnectionRepository()
    workspaces = InMemoryWorkspaceRepository()
    postgres = FakePostgresGateway()
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connections),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=postgres,
        workspaces=workspaces,
    )
    return TestClient(create_app(services), base_url="http://localhost"), postgres


def create_connection(api: TestClient, password="database secret") -> dict:
    response = api.post(
        "/api/v1/connections",
        json={
            "name": "Reporting",
            "host": "localhost",
            "database": "analytics",
            "username": "reader",
            "password": password,
            "sslMode": "require",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_workspace(api: TestClient, connection_id: str) -> dict:
    response = api.post(
        "/api/v1/schemii/workspaces",
        json={
            "connectionId": connection_id,
            "database": "analytics",
            "namespace": "public",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_runtime_and_error_envelopes_are_ready_for_ui_consumers() -> None:
    api, _ = client()

    session = api.get("/api/v1/session")
    assert session.json() == {
        "userId": "user_local_prototype",
        "authenticationSource": "local_prototype",
        "ephemeral": True,
    }
    assert session.headers["cache-control"] == "no-store"
    assert len(session.headers["x-request-id"]) == 32

    invalid = api.post("/api/v1/connections", json={"name": "missing fields"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.json()["error"]["requestId"] == invalid.headers["x-request-id"]

    missing = api.get("/api/v1/not-a-route")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    wrong_method = api.post("/api/v1/session")
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"


def test_unexpected_errors_keep_safe_runtime_headers() -> None:
    connections = InMemoryConnectionRepository()
    workspaces = InMemoryWorkspaceRepository()
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connections),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=FakePostgresGateway(),
        workspaces=workspaces,
    )
    application = create_app(services)

    @application.get("/test-only-failure")
    def failure():
        raise RuntimeError("private failure details")

    api = TestClient(
        application,
        base_url="http://localhost",
        raise_server_exceptions=False,
    )
    response = api.get("/test-only-failure")

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["requestId"] == response.headers["x-request-id"]
    assert "private failure details" not in response.text


def test_connection_api_redacts_credentials_and_tests_internal_resolution() -> None:
    api, postgres = client()
    created = create_connection(api)

    assert created["revision"] == 1
    assert created["credentialStored"] is True
    assert "password" not in created
    assert "database secret" not in api.get("/api/v1/connections").text

    tested = api.post(f"/api/v1/connections/{created['id']}/test")
    assert tested.status_code == 200
    assert tested.json() == {
        "ok": True,
        "database": "analytics",
        "serverVersion": "17.2",
    }
    assert postgres.connections[-1].password.get_secret_value() == "database secret"

    updated = api.patch(
        f"/api/v1/connections/{created['id']}",
        json={"expectedRevision": 1, "password": None},
    )
    assert updated.status_code == 200
    assert updated.json()["credentialStored"] is False

    stale = api.patch(
        f"/api/v1/connections/{created['id']}",
        json={"expectedRevision": 1, "name": "Stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"currentRevision": 2}


def test_workspace_api_stores_only_target_and_live_table_positions() -> None:
    api, postgres = client()
    connection = create_connection(api)
    workspace = create_workspace(api, connection["id"])

    assert workspace["revision"] == 1
    assert workspace["tables"] == []
    assert set(workspace) == {
        "id",
        "revision",
        "name",
        "connectionId",
        "database",
        "namespace",
        "tables",
        "createdAt",
        "updatedAt",
    }

    revised_connection = api.patch(
        f"/api/v1/connections/{connection['id']}",
        json={"expectedRevision": 1, "name": "Reporting revised"},
    )
    assert revised_connection.status_code == 200
    calls_before_connection_conflict = len(postgres.connections)
    connection_conflict = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 1,
            "tables": [],
        },
    )
    assert connection_conflict.status_code == 409
    assert connection_conflict.json()["error"] == {
        "code": "connection_conflict",
        "message": "The workspace connection changed before the layout could be saved",
        "retryable": False,
        "requestId": connection_conflict.headers["x-request-id"],
        "details": {"currentRevision": 2},
    }
    assert len(postgres.connections) == calls_before_connection_conflict

    unknown = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 2,
            "tables": [{"name": "not_live", "x": 10, "y": 20}],
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "table_not_found"

    saved = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 2,
            "tables": [{"name": "customers", "x": 10.5, "y": 20}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["tables"] == [{"name": "customers", "x": 10.5, "y": 20.0}]

    calls_before_stale_write = len(postgres.connections)
    stale = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 2,
            "tables": [{"name": "not_live", "x": 0, "y": 0}],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"currentRevision": 2}
    assert len(postgres.connections) == calls_before_stale_write

    catalog = api.get(f"/api/v1/schemii/workspaces/{workspace['id']}/catalog")
    assert catalog.status_code == 200
    document = catalog.json()
    assert document["positions"] == [{"name": "customers", "x": 10.5, "y": 20.0}]
    assert document["catalog"]["tables"][0]["columns"][0]["dataType"] == "bigint"
    assert len(document["catalog"]["fingerprint"]) == 64

    blocked = api.delete(
        f"/api/v1/connections/{connection['id']}?expectedRevision=1"
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "connection_in_use"


def test_workspace_creation_requires_a_live_namespace() -> None:
    api, postgres = client()
    connection = create_connection(api)
    postgres.namespace_available = False

    response = api.post(
        "/api/v1/schemii/workspaces",
        json={
            "connectionId": connection["id"],
            "database": "analytics",
            "namespace": "missing",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "postgres_namespace_not_found"


def test_workspace_can_start_detached_for_database_independent_design() -> None:
    api, postgres = client()

    response = api.post(
        "/api/v1/schemii/workspaces",
        json={"name": "Future inventory"},
    )

    assert response.status_code == 201
    workspace = response.json()
    assert workspace["name"] == "Future inventory"
    assert workspace["connectionId"] is None
    assert workspace["database"] is None
    assert workspace["namespace"] is None
    assert postgres.connections == []

    catalog = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/catalog"
    )
    assert catalog.status_code == 409
    assert catalog.json()["error"]["code"] == "workspace_target_required"

    planned_design = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design"
    )
    assert planned_design.status_code == 501
    assert planned_design.json()["error"]["details"] == {
        "capability": "schemii.design.read",
        "status": "planned",
    }


def test_workspace_rejects_an_incomplete_optional_target() -> None:
    api, _ = client()

    response = api.post(
        "/api/v1/schemii/workspaces",
        json={"name": "Incomplete", "database": "analytics"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
