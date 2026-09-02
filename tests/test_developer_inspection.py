from fastapi.testclient import TestClient

from schemii.common.developer_inspection import (
    build_developer_inspection_snapshot,
)
from schemii.main import create_app


def test_inspection_snapshot_is_versioned_coherent_and_stable_for_one_app() -> None:
    application = create_app()

    first = build_developer_inspection_snapshot(application)
    second = build_developer_inspection_snapshot(application)

    assert first == second
    assert first["schemaVersion"] == 1
    assert first["generation"] == "application-startup"
    assert len(first["snapshotId"]) == 64
    assert set(first["documentDigests"]) == {
        "routes",
        "database",
        "system",
        "openapi",
    }
    assert all(len(digest) == 64 for digest in first["documentDigests"].values())
    assert all(
        document["schemaVersion"] == 1
        for name, document in first["documents"].items()
        if name != "openapi"
    )


def test_canonical_snapshot_and_compatibility_routes_share_exact_documents() -> None:
    api = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    response = api.get("/_developer/inspection")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    snapshot = response.json()
    for name in ("routes", "database", "system"):
        assert api.get(f"/_developer/{name}").json() == snapshot["documents"][name]
    assert api.get("/openapi.json").json() == snapshot["documents"]["openapi"]
    assert not {
        "/_developer/inspection",
        "/_developer/routes",
        "/_developer/database",
        "/_developer/system",
    } & set(snapshot["documents"]["openapi"]["paths"])
