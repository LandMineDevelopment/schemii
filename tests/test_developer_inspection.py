import hashlib
import json

from fastapi.testclient import TestClient

from schemii.common.developer_inspection import (
    build_developer_inspection_snapshot,
)
from schemii.main import create_app


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


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
    assert first["documentDigests"] == {
        name: digest(document)
        for name, document in first["documents"].items()
    }
    assert first["snapshotId"] == digest(first["documentDigests"])
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


def test_snapshot_identity_changes_when_the_registered_application_changes() -> None:
    before = create_app()
    after = create_app()

    @after.get("/example-inspection-identity")
    def example() -> dict[str, bool]:
        return {"ok": True}

    first = build_developer_inspection_snapshot(before)
    second = build_developer_inspection_snapshot(after)

    assert first["snapshotId"] != second["snapshotId"]
    assert "/example-inspection-identity" not in first["documents"]["openapi"]["paths"]
    assert "/example-inspection-identity" in second["documents"]["openapi"]["paths"]


def test_canonical_inspection_is_opt_in_and_hidden_from_its_openapi_snapshot() -> None:
    disabled = TestClient(create_app(), base_url="http://localhost")
    enabled = TestClient(
        create_app(developer_inspection=True),
        base_url="http://localhost",
    )

    assert disabled.get("/_developer/inspection").status_code == 404
    response = enabled.get("/_developer/inspection")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert (
        "/_developer/inspection"
        not in response.json()["documents"]["openapi"]["paths"]
    )
