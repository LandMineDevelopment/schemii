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
    ]
    assert [(router.prefix, router.tags) for router in PRODUCT_ROUTERS] == [
        ("/api/v1/schemii", ["schemii"]),
        ("/api/v1/schemoo", ["schemoo"]),
        ("/api/v1/schemer", ["schemer"]),
    ]


def test_openapi_contains_current_prototype_routes() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/v1/session" in paths
    assert "/api/v1/connections/{connection_id}/test" in paths
    assert "/api/v1/schemii/workspaces/{workspace_id}/catalog" in paths
    assert not any("password" in path.lower() for path in paths)
    assert (
        paths["/api/v1/connections"]["post"]["responses"]["422"]["content"]
        ["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ApiErrorResponse"
    )
