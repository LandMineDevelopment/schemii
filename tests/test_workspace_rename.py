from fastapi.testclient import TestClient

from schemii.main import create_app


def test_workspace_rename_api_preserves_design_and_guards_stale_requests():
    with TestClient(create_app(), base_url="http://localhost") as api:
        base = "/api/v1/schemii/workspaces"
        original = api.post(base, json={"name": "Before"}).json()
        path = f"{base}/{original['id']}"
        design = api.get(f"{path}/design").json()
        body = {"name": "  After  ", "expectedRevision": original["revision"]}
        renamed = api.patch(path, json=body)
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "After"
        assert renamed.json()["revision"] == original["revision"] + 1
        assert api.get(f"{path}/design").json() == design
        assert api.patch(path, json=body).status_code == 409
        assert api.patch(path, json={**body, "name": " "}).status_code == 422
        assert api.patch(path, json={**body, "database": "changed"}).status_code == 422
        assert api.patch(f"{base}/ws_{'0' * 32}", json=body).status_code == 404
        operation = api.get("/openapi.json").json()["paths"][f"{base}/{{workspace_id}}"]["patch"]
        assert "x-schemii-status" not in operation
