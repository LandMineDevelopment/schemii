from fastapi import FastAPI

from schemii.main import COMMON_ROUTERS, PRODUCT_ROUTERS, app, create_app


def test_application_is_assembled_by_create_app() -> None:
    assert isinstance(app, FastAPI)
    assert isinstance(create_app(), FastAPI)
    assert app.title == "Schemii"


def test_common_and_product_namespaces_are_independent() -> None:
    assert [router.prefix for router in COMMON_ROUTERS] == [
        "/api/v1",
        "/api/v1/connections",
        "/api/v1/ai",
    ]
    assert [(router.prefix, router.tags) for router in PRODUCT_ROUTERS] == [
        ("/api/v1/schemii", ["schemii"]),
        ("/api/v1/schemoo", ["schemoo"]),
        ("/api/v1/schemer", ["schemer"]),
        ("/api/v1/schemer/dashboards", ["schemer"]),
    ]


def test_openapi_contains_current_prototype_routes() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/v1/session" in paths
    assert "/api/v1/connections/{connection_id}/test" in paths
    assert "/api/v1/schemii/workspaces/{workspace_id}/catalog" in paths
    assert "/api/v1/schemii/workspaces/{workspace_id}/target" not in paths
    workspace_create = schema["components"]["schemas"]["SchemiiWorkspaceCreate"]
    assert set(workspace_create["properties"]) == {"name"}
    assert "mode" not in schema["components"]["schemas"]["SchemiiWorkspace"]["properties"]
    assert "post" in paths["/api/v1/auth/change-password"]
    assert not any(
        "get" in operations and "password" in path.lower()
        for path, operations in paths.items()
    )
    connection_response = schema["components"]["schemas"]["PostgresConnectionProfile"]
    assert "password" not in connection_response["properties"]
    assert (
        paths["/api/v1/connections"]["post"]["responses"]["422"]["content"]
        ["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ApiErrorResponse"
    )


def test_openapi_exposes_typed_planned_routes_without_marking_them_implemented() -> None:
    schema = create_app().openapi()
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    planned = [
        operation
        for operation in operations
        if operation.get("x-schemii-status") == "planned"
    ]

    assert len(planned) < len(operations)
    for path, method in (
        ("/api/v1/schemii/workspaces/{workspace_id}", "patch"),
        ("/api/v1/schemii/console/settings", "put"),
    ):
        assert schema["paths"][path][method].get("x-schemii-status") is None
    assert {
        operation.get("x-schemii-status") for operation in operations
    } <= {None, "planned"}
    assert all("501" in operation["responses"] for operation in planned)
    assert schema["paths"]["/api/v1/schemii/ai/chats"]["get"]["tags"] == [
        "schemii",
        "schemii-assistant",
    ]
    assert "x-schemii-status" not in schema["paths"]["/api/v1/schemii/ai/chats"]["get"]
    assert schema["paths"]["/api/v1/schemii/workspaces/{workspace_id}/design"][
        "get"
    ]["tags"] == ["schemii", "schemii-schema-design"]
    assert schema["paths"][
        "/api/v1/schemii/workspaces/{workspace_id}/relations"
    ]["get"]["tags"] == ["schemii", "schemii-database-browser"]
    assert schema["paths"][
        "/api/v1/schemii/workspaces/{workspace_id}/relations"
    ]["get"].get("x-schemii-status") is None
    assert schema["paths"]["/api/v1/schemii/console/settings"]["get"]["tags"] == [
        "schemii",
        "schemii-sql-console",
    ]
    assert "x-schemii-status" not in schema["paths"][
        "/api/v1/schemii/console/settings"
    ]["get"]
    assert schema["paths"][
        "/api/v1/schemii/workspaces/{workspace_id}/layout"
    ]["put"]["tags"] == ["schemii", "schemii-schema-design"]
    assert schema["paths"][
        "/api/v1/schemii/workspaces/{workspace_id}/catalog"
    ]["get"]["tags"] == ["schemii", "schemii-database-browser"]
    export = schema["paths"][
        "/api/v1/schemii/workspaces/{workspace_id}/design/exports"
    ]["post"]
    assert export["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SchemiiDesignExportRequest"
    }
