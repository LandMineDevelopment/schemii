from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.ai.instance_provider_store import MemoryInstanceAiProviderStore
from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.ai.routes import admin_router, router, shared_codex_router
from schemii.common.api.errors import install_api_error_handlers
from schemii.common.metadata.models import Principal, get_current_principal


class Auth:
    enabled = True

    def __init__(self):
        self.store = self
        self.events = []
        self.admin_id = "admin"

    def is_admin(self, user_id):
        return user_id == self.admin_id

    def user(self, user_id):
        return {"id": user_id} if user_id in {"admin", "alice"} else None

    def capabilities(self, user_id):
        return ["schemii:access"] if user_id == "alice" else []

    def audit(self, *event):
        self.events.append(event)

    @contextmanager
    def transaction(self):
        yield {"users": {"alice": {"disabled": False}}}


def test_zen_key_is_admin_managed_and_never_returned_to_client():
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    install_api_error_handlers(app)
    store = MemoryInstanceAiProviderStore()
    app.state.auth = Auth()
    app.state.services = SimpleNamespace(
        metadata=SimpleNamespace(ai_instance_providers=store),
        connections=SimpleNamespace(for_product=lambda product: SimpleNamespace(list=lambda user: [])))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="admin", authentication_source="local_prototype")
    client = TestClient(app)

    assert client.post("/api/v1/ai/credentials/opencode", json={"apiKey": "secret-zen-key"}).status_code == 422
    response = client.put("/api/v1/admin/ai/zen/credential", json={"apiKey": "secret-zen-key"})
    assert response.status_code == 200 and response.json()["connected"] is True
    assert "secret-zen-key" not in response.text
    grant = {"userId": "alice", "product": "schemii", "connectionOwnerId": None, "connectionId": None}
    assert client.put("/api/v1/admin/ai/zen/grants", json=grant).status_code == 200
    listing = client.get("/api/v1/admin/ai/zen")
    assert listing.status_code == 200
    assert listing.json()["grants"] == [grant]
    assert "secret-zen-key" not in listing.text
    assert store.resolve("alice", "schemii")["credential"] == "secret-zen-key"

    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="alice", authentication_source="local_prototype")
    assert client.get("/api/v1/admin/ai/zen").status_code == 403
    assert client.put("/api/v1/admin/ai/zen/credential", json={"apiKey": "replacement"}).status_code == 403
    assert client.delete("/api/v1/admin/ai/zen/credential").status_code == 403
    assert store.resolve("alice", "schemii")["credential"] == "secret-zen-key"

    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="admin", authentication_source="local_prototype")
    assert client.request("DELETE", "/api/v1/admin/ai/zen/grants", json=grant).json() == {"deleted": True}
    assert store.resolve("alice", "schemii") is None
    assert client.delete("/api/v1/admin/ai/zen/credential").json()["connected"] is False


def test_provider_status_passes_server_resolved_product_scope():
    app = FastAPI()
    app.include_router(router)
    install_api_error_handlers(app)
    calls = []
    def runtime_status(owner, *, refresh=False, zen_scope=None):
        calls.append((owner, refresh, zen_scope()))
        return {"enabled": True, "healthy": True, "providers": []}
    app.state.ai_service = SimpleNamespace(runtime=SimpleNamespace(status=runtime_status))
    app.state.schemoo_ai = SimpleNamespace(
        _zen_scope=lambda owner, resource, request: ("schemoo", owner, "pg_" + "a" * 32))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="alice", authentication_source="local_prototype")
    client = TestClient(app)
    response = client.get("/api/v1/ai/status?product=schemoo&resourceId=model_123&refresh=true")
    assert response.status_code == 200
    assert calls == [("alice", True, ("schemoo", "alice", "pg_" + "a" * 32))]
    assert client.get("/api/v1/ai/status?product=schemoo").status_code == 422


def test_shared_codex_admin_controls_connection_model_reasoning_and_exact_grant():
    app = FastAPI()
    app.include_router(shared_codex_router)
    install_api_error_handlers(app)
    credentials = MemoryAiCredentialStore()
    credentials.begin_login("admin", "codex-prototype", "openai-codex")
    credentials.save("admin", "codex-prototype", "openai-codex",
                     {"type": "oauth", "refresh": "private-refresh-token"}, 1)
    instance = MemoryInstanceAiProviderStore()
    app.state.auth = Auth()
    app.state.services = SimpleNamespace(
        metadata=SimpleNamespace(ai_instance_providers=instance, ai_credentials=credentials),
        connections=SimpleNamespace(for_product=lambda product: SimpleNamespace(list=lambda user: [])))
    app.state.ai_service = SimpleNamespace(runtime=SimpleNamespace(
        _supported_models=lambda: [{"providerId": "openai-codex", "id": "gpt-6-luna",
                                    "name": "GPT-6 Luna", "reasoningLevels": ["default", "high"]}],
        instance_codex_catalog=lambda: {"verifiedModels": [], "catalogCheckedAt": None},
        test_instance_codex=lambda: {"connected": True, "checkedAt": "now", "models": [
            {"providerId": "openai-codex", "id": "gpt-6-luna"}]}))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="admin", authentication_source="local_prototype")
    client = TestClient(app)
    assert client.get("/api/v1/admin/ai/shared-codex").json()["sourceConnected"] is True
    connected = client.put("/api/v1/admin/ai/shared-codex/credential")
    assert connected.status_code == 200 and connected.json()["connected"] is True
    grant = {"userId": "alice", "product": "schemii", "connectionOwnerId": None,
             "connectionId": None, "modelId": "gpt-6-luna", "reasoningEffort": "high"}
    assert client.put("/api/v1/admin/ai/shared-codex/grants", json=grant).status_code == 200
    listing = client.get("/api/v1/admin/ai/shared-codex")
    assert listing.status_code == 200
    assert listing.json()["grants"][0].items() >= grant.items()
    assert "private-refresh-token" not in listing.text + connected.text
    assert client.post("/api/v1/admin/ai/shared-codex/test").json()["models"][0]["id"] == "gpt-6-luna"
    credentials.delete("admin", "codex-prototype")
    detached_source = client.get("/api/v1/admin/ai/shared-codex").json()
    assert detached_source["sourceConnected"] is False and detached_source["connected"] is True
    invalid = client.put("/api/v1/admin/ai/shared-codex/grants",
                         json={**grant, "reasoningEffort": "max"})
    assert invalid.status_code == 422 and instance.get_grant("alice", "schemii", provider_id="openai-codex")["reasoningEffort"] == "high"
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="alice", authentication_source="local_prototype")
    assert client.get("/api/v1/admin/ai/shared-codex").status_code == 403
    assert client.put("/api/v1/admin/ai/shared-codex/credential").status_code == 403
    assert client.post("/api/v1/admin/ai/shared-codex/test").status_code == 403
