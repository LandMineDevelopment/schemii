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
from schemii.common.postgres.errors import PostgresNamespaceNotFoundError
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class FakePostgresGateway:
    def __init__(self) -> None:
        self.connections = []
        self.namespace_available = True
        self.columns = (
            PostgresColumn(
                name="id",
                ordinal=1,
                data_type="bigint",
                nullable=False,
            ),
            PostgresColumn(
                name="email",
                ordinal=2,
                data_type="text",
                nullable=False,
            ),
        )

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
        if not self.namespace_available or namespace != "public":
            raise PostgresNamespaceNotFoundError()
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
                    columns=self.columns,
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
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    postgres = FakePostgresGateway()
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connections),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
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
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connections),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=FakePostgresGateway(),
        workspaces=workspaces,
        designs=designs,
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
    assert workspace["mode"] == "live"
    assert workspace["importSummary"] is None
    assert workspace["tables"] == []
    assert workspace["columnOrders"] == []
    assert set(workspace) == {
        "id",
        "revision",
        "name",
        "mode",
        "connectionId",
        "database",
        "namespace",
        "tables",
        "columnOrders",
        "importSummary",
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

    incomplete_order = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 2,
            "tables": [],
            "columnOrders": [{"name": "customers", "columns": ["email"]}],
        },
    )
    assert incomplete_order.status_code == 422
    assert incomplete_order.json()["error"]["code"] == "column_order_mismatch"
    assert incomplete_order.json()["error"]["details"] == {
        "tables": [
            {
                "table": "customers",
                "missingColumns": ["id"],
                "unknownColumns": [],
            }
        ]
    }

    saved = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/layout",
        json={
            "expectedRevision": 1,
            "expectedConnectionRevision": 2,
            "tables": [{"name": "customers", "x": 10.5, "y": 20}],
            "columnOrders": [
                {"name": "customers", "columns": ["email", "id"]}
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["tables"] == [{"name": "customers", "x": 10.5, "y": 20.0}]
    assert saved.json()["columnOrders"] == [
        {"name": "customers", "columns": ["email", "id"]}
    ]

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

    postgres.columns = (
        *postgres.columns,
        PostgresColumn(
            name="created_at",
            ordinal=3,
            data_type="timestamp with time zone",
            nullable=False,
        ),
    )
    catalog = api.get(f"/api/v1/schemii/workspaces/{workspace['id']}/catalog")
    assert catalog.status_code == 200
    document = catalog.json()
    assert document["positions"] == [{"name": "customers", "x": 10.5, "y": 20.0}]
    assert document["workspace"]["columnOrders"] == [
        {"name": "customers", "columns": ["email", "id", "created_at"]}
    ]
    assert document["catalog"]["tables"][0]["columns"][0]["dataType"] == "bigint"
    assert len(document["catalog"]["fingerprint"]) == 64

    blocked = api.delete(
        f"/api/v1/connections/{connection['id']}?expectedRevision=1"
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "connection_in_use"


def test_postgres_import_creates_a_new_targeted_design_without_an_overwrite_route() -> None:
    api, postgres = client()
    connection = create_connection(api)

    response = api.post(
        "/api/v1/schemii/workspaces/imports",
        json={
            "name": "Imported customers",
            "connectionId": connection["id"],
            "database": "analytics",
            "namespace": "public",
        },
    )

    assert response.status_code == 201
    document = response.json()
    workspace = document["workspace"]
    assert workspace["name"] == "Imported customers"
    assert workspace["mode"] == "design"
    assert workspace["connectionId"] == connection["id"]
    assert workspace["importSummary"]["catalogFingerprint"]
    assert workspace["importSummary"]["complete"] is True
    assert workspace["importSummary"]["importedObjects"] == {
        "checks": 0,
        "columns": 2,
        "functions": 0,
        "indexes": 0,
        "keys": 0,
        "relationships": 0,
        "tables": 1,
        "triggers": 0,
        "types": 0,
        "views": 0,
    }
    assert document["design"]["revision"] == 1
    assert document["design"]["content"]["tables"][0]["name"] == "customers"
    assert document["layout"]["revision"] == 1
    assert document["layout"]["designRevision"] == 1
    assert postgres.connections[-1].password.get_secret_value() == "database secret"

    saved = api.get(f"/api/v1/schemii/workspaces/{workspace['id']}")
    assert saved.status_code == 200
    assert saved.json()["importSummary"] == workspace["importSummary"]
    design = api.get(f"/api/v1/schemii/workspaces/{workspace['id']}/design")
    assert design.json() == document["design"]

    overwrite = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/imports",
        json={},
    )
    assert overwrite.status_code == 404


def test_failed_postgres_import_does_not_create_a_workspace() -> None:
    api, postgres = client()
    connection = create_connection(api)
    postgres.namespace_available = False

    response = api.post(
        "/api/v1/schemii/workspaces/imports",
        json={
            "name": "Must not survive",
            "connectionId": connection["id"],
            "database": "analytics",
            "namespace": "missing",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "postgres_namespace_not_found"
    assert api.get("/api/v1/schemii/workspaces").json()["workspaces"] == []


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

    empty_design = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design"
    )
    assert empty_design.status_code == 200
    assert empty_design.json()["revision"] == 0
    assert empty_design.json()["content"] == {
        "types": [],
        "tables": [],
        "relationships": [],
        "functions": [],
        "views": [],
        "triggers": [],
    }

    table_id = "table_" + "a" * 32
    id_column = "column_" + "b" * 32
    name_column = "column_" + "c" * 32
    key_id = "key_" + "d" * 32
    saved = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design",
        json={
            "expectedDesignRevision": 0,
            "content": {
                "tables": [
                    {
                        "id": table_id,
                        "name": "inventory item",
                        "columns": [
                            {
                                "id": id_column,
                                "name": "id",
                                "dataType": "bigint",
                                "nullable": False,
                                "identity": "by_default",
                            },
                            {
                                "id": name_column,
                                "name": "display name",
                                "dataType": "text",
                                "nullable": False,
                            },
                        ],
                        "keys": [
                            {
                                "id": key_id,
                                "name": "inventory item_pkey",
                                "kind": "primary",
                                "columnIds": [id_column],
                            }
                        ],
                    }
                ]
            },
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert len(saved.json()["fingerprint"]) == 64

    impact = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/deletion-impact/{table_id}"
    )
    assert impact.status_code == 200
    assert impact.json()["designRevision"] == 1
    assert impact.json()["blocked"] is True
    assert [(item["kind"], item["objectId"]) for item in impact.json()["dependents"]] == [
        ("key", key_id)
    ]

    analyzed = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/view-analysis",
        json={
            "name": "inventory_names",
            "definition": 'SELECT id, "display name" FROM "inventory item"',
        },
    )
    assert analyzed.status_code == 200
    story = analyzed.json()
    assert story["sources"][0]["name"] == "inventory item"
    assert [output["name"] for output in story["outputs"]] == [
        "id",
        "display name",
    ]
    assert [output["dataType"] for output in story["outputs"]] == [
        "bigint",
        "text",
    ]

    routine_definition = """
        CREATE FUNCTION display_label(value text, fallback text DEFAULT 'Unknown')
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        AS $$ SELECT coalesce(value, fallback) $$
    """
    routine_analysis = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/routine-analysis",
        json={"definition": routine_definition},
    )
    assert routine_analysis.status_code == 200
    assert routine_analysis.json() == {
        "name": "display_label",
        "kind": "function",
        "arguments": "value text, fallback text DEFAULT 'Unknown'",
        "identityArguments": "text, text",
        "returnType": "text",
        "language": "sql",
    }

    trigger_definition = """
        CREATE TRIGGER inventory_name_changed
        AFTER UPDATE OF "display name" ON "inventory item"
        FOR EACH ROW EXECUTE FUNCTION audit_inventory()
    """
    trigger_analysis = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/trigger-analysis",
        json={"definition": trigger_definition},
    )
    assert trigger_analysis.status_code == 200
    assert trigger_analysis.json() == {
        "name": "inventory_name_changed",
        "relationName": "inventory item",
        "timing": "after",
        "events": ["update"],
        "orientation": "row",
        "functionName": "audit_inventory",
        "functionArguments": [],
        "updateColumns": ["display name"],
        "referencedColumns": ["display name"],
        "whenExpression": None,
        "transitionRelations": [],
        "constraint": False,
        "deferrable": False,
        "initiallyDeferred": False,
    }

    type_definition = "CREATE TYPE inventory_state AS ENUM ('draft', 'available', 'retired')"
    type_analysis = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/type-analysis",
        json={"definition": type_definition},
    )
    assert type_analysis.status_code == 200
    assert type_analysis.json() == {
        "name": "inventory_state",
        "kind": "enum",
        "enumValues": ["draft", "available", "retired"],
        "baseType": None,
        "defaultExpression": None,
        "notNull": False,
        "checks": [],
        "collation": None,
    }

    content_with_routine = saved.json()["content"]
    content_with_routine["types"] = [
        {
            "id": "type_" + "7" * 32,
            "definition": type_definition,
        }
    ]
    content_with_routine["functions"] = [
        {
            "id": "function_" + "e" * 32,
            "definition": routine_definition,
        }
    ]
    content_with_routine["triggers"] = [
        {
            "id": "trigger_" + "f" * 32,
            "definition": trigger_definition,
        }
    ]
    saved_routine = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design",
        json={"expectedDesignRevision": 1, "content": content_with_routine},
    )
    assert saved_routine.status_code == 200
    assert saved_routine.json()["content"]["functions"][0]["name"] == "display_label"
    assert saved_routine.json()["content"]["types"][0]["enumValues"] == [
        "draft",
        "available",
        "retired",
    ]
    assert saved_routine.json()["content"]["functions"][0]["identityArguments"] == "text, text"
    assert saved_routine.json()["content"]["triggers"][0]["relationName"] == "inventory item"
    assert postgres.connections == []

    stale = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design",
        json={"expectedDesignRevision": 0, "content": saved.json()["content"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"currentRevision": 2}

    layout = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/layout"
    )
    assert layout.status_code == 200
    assert layout.json()["revision"] == 2
    positioned = api.put(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/layout",
        json={
            "expectedLayoutRevision": 2,
            "expectedDesignRevision": 2,
            "content": {
                "objects": [
                    {
                        "objectId": table_id,
                        "layer": "tables",
                        "x": 120.0,
                        "y": 80.0,
                    }
                ]
            },
        },
    )
    assert positioned.status_code == 200
    assert positioned.json()["revision"] == 3

    exported = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/design/exports",
        json={"expectedDesignRevision": 2, "format": "postgresql_sql"},
    )
    assert exported.status_code == 200
    assert 'CREATE TABLE "inventory item"' in exported.json()["content"]
    assert exported.json()["content"].index("CREATE TYPE inventory_state") < exported.json()["content"].index('CREATE TABLE "inventory item"')
    assert '"display name" text NOT NULL' in exported.json()["content"]
    assert "CREATE FUNCTION display_label" in exported.json()["content"]
    assert "CREATE TRIGGER inventory_name_changed" in exported.json()["content"]
    assert postgres.connections == []


def test_design_history_and_baseline_reset_are_server_authoritative() -> None:
    api, _ = client()
    workspace = api.post(
        "/api/v1/schemii/workspaces",
        json={"name": "History test"},
    ).json()
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/design"
    table_id = "table_" + "1" * 32
    column_id = "column_" + "2" * 32

    initial = api.get(f"{path}/history")
    assert initial.status_code == 200
    assert initial.json()["canUndo"] is False
    assert initial.json()["baseline"]["kind"] == "workspace_start"

    saved = api.put(
        path,
        json={
            "expectedDesignRevision": 0,
            "content": {
                "tables": [
                    {
                        "id": table_id,
                        "name": "orders",
                        "columns": [
                            {
                                "id": column_id,
                                "name": "id",
                                "dataType": "bigint",
                                "nullable": False,
                            }
                        ],
                    }
                ]
            },
        },
    )
    assert saved.status_code == 200
    history = api.get(f"{path}/history").json()
    assert history["undo"]["delta"] == [
        {"operation": "remove", "path": ["tables", 0], "value": None}
    ]
    layout = api.get(f"{path}/layout").json()
    positioned = api.put(
        f"{path}/layout",
        json={
            "expectedLayoutRevision": layout["revision"],
            "expectedDesignRevision": 1,
            "content": {
                "objects": [
                    {
                        "objectId": table_id,
                        "layer": "tables",
                        "x": 320.0,
                        "y": 200.0,
                    }
                ]
            },
        },
    )
    assert positioned.status_code == 200

    preview = api.get(f"{path}/baseline-reset")
    assert preview.status_code == 200
    review = preview.json()
    assert review["summary"]["changeCount"] == 2
    reset = api.post(
        f"{path}/baseline-reset",
        json={
            "expectedDesignRevision": review["designRevision"],
            "baselineId": review["baseline"]["id"],
            "baselineRevision": review["baseline"]["revision"],
            "reviewDigest": review["reviewDigest"],
        },
    )
    assert reset.status_code == 200
    assert reset.json()["design"]["content"]["tables"] == []

    undone = api.post(
        f"{path}/undo",
        json={"expectedDesignRevision": reset.json()["design"]["revision"]},
    )
    assert undone.status_code == 200
    assert undone.json()["design"]["content"]["tables"][0]["name"] == "orders"
    assert undone.json()["layout"]["content"]["objects"][0] == {
        "objectId": table_id,
        "layer": "tables",
        "x": 320.0,
        "y": 200.0,
    }

    redone = api.post(
        f"{path}/redo",
        json={"expectedDesignRevision": undone.json()["design"]["revision"]},
    )
    assert redone.status_code == 200
    assert redone.json()["design"]["content"]["tables"] == []


def test_workspace_rejects_an_incomplete_optional_target() -> None:
    api, _ = client()

    response = api.post(
        "/api/v1/schemii/workspaces",
        json={"name": "Incomplete", "database": "analytics"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
