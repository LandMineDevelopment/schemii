"""Owner-scoped Pi provider discovery and encrypted API credentials."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SecretStr, Field, ConfigDict, model_validator, field_validator

from schemii.common.api.errors import ApiProblem
from schemii.common.auth.routes import admin
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.models import Principal, get_current_principal
from .models import AiStatusResponse

router = APIRouter(prefix="/api/v1/ai", tags=["ai-providers"])
admin_router = APIRouter(prefix="/api/v1/admin/ai/zen", tags=["ai-providers-admin"])


class ApiCredentialCreate(BaseModel):
    apiKey: SecretStr = Field(min_length=1, max_length=16384)

    @field_validator("apiKey")
    @classmethod
    def bounded_bytes(cls, value):
        if len(value.get_secret_value().encode("utf-8")) > 16384:
            raise ValueError("API key exceeds the maximum byte length")
        return value


class ZenGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: str = Field(min_length=1, max_length=128)
    product: str = Field(pattern="^(schemii|schemoo|schemer)$")
    connectionOwnerId: str | None = Field(default=None, max_length=128)
    connectionId: str | None = Field(default=None, pattern=r"^pg_[0-9a-f]{32}$")

    @model_validator(mode="after")
    def valid_scope(self):
        if (self.connectionOwnerId is None) != (self.connectionId is None):
            raise ValueError("Both connection identity fields are required")
        if self.connectionId is None and self.product != "schemii":
            raise ValueError("Only Schemii supports detached workspaces")
        return self


@router.get("/status", response_model=AiStatusResponse)
def status(request: Request, refresh: bool = False, product: str | None = None,
           resourceId: str | None = None, principal: Principal = Depends(get_current_principal)):
    service = request.app.state.ai_service
    if product is None and resourceId is None:
        result = service.status(principal.user_id, refresh=refresh)
    else:
        if product not in {"schemii", "schemoo", "schemer"} or not resourceId:
            raise ApiProblem(422, "ai_scope_invalid", "Select a valid app and saved resource.")
        runtime = service.runtime
        if runtime is None:
            result = service.status(principal.user_id, refresh=refresh)
        else:
            owner = principal.user_id
            if product == "schemii":
                scope = lambda: service._zen_scope(owner, resourceId)
            elif product == "schemoo":
                scope = lambda: request.app.state.schemoo_ai._zen_scope(owner, resourceId, None)
            else:
                scope = lambda: request.app.state.schemer_ai._zen_scope(owner, resourceId, request)
            result = runtime.status(owner, refresh=refresh, zen_scope=scope)
    return AiStatusResponse.model_validate(result)


@router.post("/credentials/{provider_id}")
def save_credential(provider_id: str, body: ApiCredentialCreate, request: Request,
                    principal: Principal = Depends(get_current_principal)):
    if provider_id != "openai":
        raise ApiProblem(422, "ai_provider_unsupported", "Only a personal OpenAI API key can be saved here. Zen is managed by an administrator.")
    runtime = request.app.state.ai_service.runtime
    if runtime is None:
        raise ApiProblem(503, "ai_runtime_unavailable", "The AI service is unavailable.")
    store = request.app.state.services.metadata.ai_credentials
    runtime.disconnect(principal.user_id, provider_id)
    record = store.begin_login(principal.user_id, provider_id, provider_id)
    if not store.save(principal.user_id, provider_id, provider_id,
                      {"type": "api_key", "key": body.apiKey.get_secret_value()}, record["generation"]):
        raise ApiProblem(409, "ai_credential_changed", "The credential changed while saving. Try again.")
    return {"saved": True}


@router.delete("/credentials/{provider_id}")
def disconnect(provider_id: str, request: Request,
               principal: Principal = Depends(get_current_principal)):
    ids = {"openai": "openai", "openai-codex": "codex-prototype"}
    if provider_id not in ids:
        raise ApiProblem(422, "ai_provider_unsupported", "This provider has no saved credentials.")
    credential_id = ids[provider_id]
    request.app.state.services.metadata.ai_credentials.delete(principal.user_id, credential_id)
    runtime = request.app.state.ai_service.runtime
    if runtime is not None:
        runtime.disconnect(principal.user_id, credential_id)
    return {"deleted": True}


def _zen_store(request):
    return request.app.state.services.metadata.ai_instance_providers


def _visible_connections(request, user_id, product):
    auth = request.app.state.auth
    services = request.app.state.services
    if not auth.user(user_id) or f"{product}:access" not in auth.capabilities(user_id):
        return []
    profiles = []
    if product != "schemer" or "schemer:author" in auth.capabilities(user_id):
        profiles.extend(services.connections.for_product(product).list(user_id))
    if product == "schemer":
        for grant in auth.dashboard_grants(user_id):
            try:
                profile = services.connections.get(grant["connection_owner_id"], grant["connection_id"])
            except ConnectionNotFoundError:
                continue
            profiles.append(profile)
    return [{"userId": user_id, "product": product,
             "connectionOwnerId": profile.owner_id or user_id,
             "connectionId": profile.id, "ownership": profile.ownership,
             "name": profile.name, "database": profile.database, "username": profile.username}
            for profile in profiles]


@admin_router.get("")
def admin_zen(request: Request, actor: str = Depends(admin)):
    store = _zen_store(request)
    with request.app.state.auth.store.transaction() as state:
        users = [user_id for user_id, user in state["users"].items() if not user["disabled"]]
    connections = {}
    for user_id in users:
        for product in ("schemii", "schemoo", "schemer"):
            for profile in _visible_connections(request, user_id, product):
                key = (profile["userId"], profile["product"], profile["connectionOwnerId"], profile["connectionId"])
                connections[key] = profile
    return {**store.status(), "grants": store.list_grants(), "connections": list(connections.values())}


@admin_router.put("/credential")
def admin_save_zen(body: ApiCredentialCreate, request: Request, actor: str = Depends(admin)):
    result = _zen_store(request).set_key(body.apiKey.get_secret_value())
    request.app.state.auth.audit(actor, "ai.zen.connect", "instance")
    return result


@admin_router.delete("/credential")
def admin_delete_zen(request: Request, actor: str = Depends(admin)):
    result = _zen_store(request).clear_key()
    request.app.state.auth.audit(actor, "ai.zen.disconnect", "instance")
    return result


@admin_router.put("/grants")
def admin_save_zen_grant(body: ZenGrant, request: Request, actor: str = Depends(admin)):
    auth = request.app.state.auth
    if not auth.user(body.userId) or f"{body.product}:access" not in auth.capabilities(body.userId):
        raise ApiProblem(422, "ai_grant_user_invalid", "The user needs access to the selected app.")
    if body.connectionId is not None and not any(
        item["connectionOwnerId"] == body.connectionOwnerId and item["connectionId"] == body.connectionId
        for item in _visible_connections(request, body.userId, body.product)
    ):
        raise ApiProblem(422, "ai_grant_database_invalid", "The user cannot access this database in the selected app.")
    result = _zen_store(request).upsert_grant(body.userId, body.product, body.connectionOwnerId, body.connectionId)
    auth.audit(actor, "ai.zen.grant", f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}")
    return result


@admin_router.delete("/grants")
def admin_delete_zen_grant(body: ZenGrant, request: Request, actor: str = Depends(admin)):
    removed = _zen_store(request).delete_grant(body.userId, body.product, body.connectionOwnerId, body.connectionId)
    request.app.state.auth.audit(actor, "ai.zen.revoke", f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}")
    return {"deleted": removed}
