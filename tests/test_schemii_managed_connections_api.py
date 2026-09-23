"""HTTP boundaries for administrator-owned and personal PostgreSQL profiles."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from schemii.common.auth.managed_connections import router as managed_router
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.auth.routes import router as auth_router
from schemii.common.auth.service import AuthService, COOKIE
from schemii.common.api.errors import install_api_error_handlers
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from schemii.common.connections.routes import router as connections_router
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository


@pytest.fixture
def client():
    app = FastAPI()
    auth = AuthService(enabled=True, setup_token="setup-secret")
    connections = ConnectionService(InMemoryConnectionRepository(), ())
    connections.set_authority(auth)
    tested = []

    def test_connection(target):
        tested.append((target.owner_id, target.id, target.password.get_secret_value()))
        return SimpleNamespace(ok=True, database=target.database, server_version="PostgreSQL")

    app.state.auth = auth
    app.state.services = SimpleNamespace(
        connections=connections,
        postgres=SimpleNamespace(test_connection=test_connection),
    )
    app.include_router(auth_router)
    app.include_router(managed_router)
    app.include_router(connections_router)
    install_api_error_handlers(app)
    app.add_middleware(AuthenticationMiddleware)
    http = TestClient(app, base_url="https://localhost:8001",
                      headers={"Origin": "https://localhost:8001"})
    assert http.post("/api/v1/auth/setup", json=dict(
        setup_token="setup-secret", username="admin", display_name="Admin",
        password="long-password-123")).status_code == 200
    return http, app, tested


def _profile(name, database, username, password):
    return dict(name=name, host="localhost", database=database,
                username=username, password=password)


def test_admin_manages_multiple_schemii_owned_credentials_without_disclosing_password(client):
    http, app, tested = client
    endpoint = "/api/v1/admin/schemii-connections"
    assert http.post(endpoint, json={**_profile("Missing", "east", "reader", "secret"),
                                     "password": None}).status_code == 422
    first = http.post(endpoint, json=_profile(
        "East rows", "organization", "east_reader", "east-secret"))
    second = http.post(endpoint, json=_profile(
        "West rows", "organization", "west_reader", "west-secret"))
    assert first.status_code == second.status_code == 201
    east, west = first.json(), second.json()
    assert east["id"] != west["id"]
    for response, profile in ((first, east), (second, west)):
        assert profile["ownerId"] == SCHEMII_CONNECTION_OWNER_ID
        assert profile["ownership"] == "schemii"
        assert profile["credentialStored"] is True
        assert "password" not in response.text
        assert "secret" not in response.text
    listed = http.get(endpoint)
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["connections"]} == {east["id"], west["id"]}
    assert http.get(f"{endpoint}/{east['id']}").json()["ownership"] == "schemii"

    tested_response = http.post(f"{endpoint}/{east['id']}/test")
    assert tested_response.status_code == 200
    assert tested == [(SCHEMII_CONNECTION_OWNER_ID, east["id"], "east-secret")]
    assert "east-secret" not in tested_response.text

    rotated = http.patch(f"{endpoint}/{east['id']}", json=dict(
        expectedRevision=east["revision"], password="rotated-secret"))
    assert rotated.status_code == 200
    assert rotated.json()["revision"] == east["revision"] + 1
    assert "rotated-secret" not in rotated.text
    assert http.patch(f"{endpoint}/{east['id']}", json=dict(
        expectedRevision=east["revision"], name="Stale")).status_code == 409
    assert http.post(f"{endpoint}/{east['id']}/test").status_code == 200
    assert tested[-1] == (SCHEMII_CONNECTION_OWNER_ID, east["id"], "rotated-secret")

    assert http.delete(f"{endpoint}/{west['id']}", params={"expectedRevision": 99}).status_code == 409
    assert http.delete(f"{endpoint}/{west['id']}", params={
        "expectedRevision": west["revision"]}).status_code == 204
    assert http.get(f"{endpoint}/{west['id']}").status_code == 404
    assert [entry[1] for entry in app.state.auth.store.state["audit"]
            if entry[1].startswith("schemii_connection.")] == [
                "schemii_connection.create", "schemii_connection.create",
                "schemii_connection.update", "schemii_connection.delete"]


def test_product_user_can_mix_personal_and_assigned_profiles_but_not_manage_shared_ones(client):
    http, app, _ = client
    endpoint = "/api/v1/admin/schemii-connections"
    shared = http.post(endpoint, json=_profile(
        "Organization reader", "organization", "report_reader", "shared-secret")).json()
    member = http.post("/api/v1/admin/accounts", json=dict(
        username="friend", display_name="Friend", password="friend-password-123")).json()
    role = http.post("/api/v1/admin/roles", json=dict(
        name="Schemii organization reader", capabilities=["schemii:access"],
        user_ids=[member["id"]], connections=[dict(
            owner_id=SCHEMII_CONNECTION_OWNER_ID, connection_id=shared["id"],
            allow_authoring=True)])).json()
    admin_cookie = http.cookies.get(COOKIE)

    assert http.post("/api/v1/auth/login", json=dict(
        username="friend", password="friend-password-123")).status_code == 200
    assert http.get(endpoint).status_code == 403
    assert http.get(f"{endpoint}/{shared['id']}").status_code == 403
    assert http.post(endpoint, json=_profile(
        "Forbidden", "organization", "reader", "secret")).status_code == 403
    assert http.patch(f"{endpoint}/{shared['id']}", json=dict(
        expectedRevision=1, name="Forbidden")).status_code == 403
    assert http.delete(f"{endpoint}/{shared['id']}", params={
        "expectedRevision": 1}).status_code == 403
    assert http.post(f"{endpoint}/{shared['id']}/test").status_code == 403

    personal = http.post("/api/v1/connections", json=_profile(
        "My credential", "organization", "friend_login", "personal-secret"))
    assert personal.status_code == 201
    own = personal.json()
    assert own["ownerId"] == member["id"]
    assert own["ownership"] == "user"
    listed = http.get("/api/v1/connections")
    assert listed.status_code == 200
    assert {(item["id"], item["ownership"]) for item in listed.json()["connections"]} == {
        (shared["id"], "schemii"), (own["id"], "user")}
    assert http.get(f"/api/v1/connections/{shared['id']}").status_code == 200
    assert http.get(f"/api/v1/connections/{own['id']}").status_code == 200
    assert http.patch(f"/api/v1/connections/{shared['id']}", json=dict(
        expectedRevision=1, name="Take over")).status_code == 404
    assert http.delete(f"/api/v1/connections/{shared['id']}", params={
        "expectedRevision": 1}).status_code == 404

    http.cookies.clear()
    http.cookies.set(COOKIE, admin_cookie)
    assert {item["id"] for item in http.get(endpoint).json()["connections"]} == {shared["id"]}
    assert http.put(f"/api/v1/admin/roles/{role['id']}", json=dict(
        name="Schemii organization reader", capabilities=["schemii:access"],
        user_ids=[member["id"]], connections=[])).status_code == 200
    assert http.delete(f"{endpoint}/{shared['id']}", params={
        "expectedRevision": shared["revision"]}).status_code == 204

    http.cookies.clear()
    assert http.post("/api/v1/auth/login", json=dict(
        username="friend", password="friend-password-123")).status_code == 200
    assert http.get(f"/api/v1/connections/{shared['id']}").status_code == 404
    assert http.get(f"/api/v1/connections/{own['id']}").status_code == 200
