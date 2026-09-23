"""Owner-scoped Pi provider discovery and encrypted API credentials."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SecretStr, Field, ConfigDict, model_validator, field_validator
from typing import Literal

from schemii.common.api.errors import ApiProblem
from schemii.common.auth.routes import admin
from schemii.common.auth.service import PROVISIONER_ROLE_ID
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from schemii.common.metadata.models import Principal, get_current_principal
from .models import AiStatusResponse
from .pi import PiError, role_has_instance_scope

router = APIRouter(prefix="/api/v1/ai", tags=["ai-providers"])
admin_router = APIRouter(prefix="/api/v1/admin/ai/zen", tags=["ai-providers-admin"])
shared_codex_router = APIRouter(prefix="/api/v1/admin/ai/shared-codex", tags=["ai-providers-admin"])


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


class SharedCodexGrant(ZenGrant):
    modelId: str = Field(default="gpt-6-luna", min_length=1, max_length=128)
    reasoningEffort: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"] = "default"


class SharedCodexExactGrant(ZenGrant):
    modelId: str = Field(min_length=1, max_length=128)
    reasoningEffort: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"]


class RoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roleId: str = Field(min_length=1, max_length=128)
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


class SharedCodexRoleGrant(RoleGrant):
    modelId: str = Field(min_length=1, max_length=128)
    reasoningEffort: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"]


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


def _instance_store(request):
    return request.app.state.services.metadata.ai_instance_providers


def _zen_store(request):
    return _instance_store(request)


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


def _admin_instance_state(request, provider_id):
    store = _instance_store(request)
    with request.app.state.auth.store.transaction() as state:
        users = [user_id for user_id, user in state["users"].items() if not user["disabled"]]
    connections = {}
    for user_id in users:
        for product in ("schemii", "schemoo", "schemer"):
            for profile in _visible_connections(request, user_id, product):
                key = (profile["userId"], profile["product"], profile["connectionOwnerId"], profile["connectionId"])
                connections[key] = profile
    return {**store.status(provider_id), "grants": store.list_grants(provider_id),
            "roleGrants": _role_grants_state(request, provider_id),
            "connections": list(connections.values())}


def _validate_instance_grant(body, request):
    auth = request.app.state.auth
    if not auth.user(body.userId) or f"{body.product}:access" not in auth.capabilities(body.userId):
        raise ApiProblem(422, "ai_grant_user_invalid", "The user needs access to the selected app.")
    if body.connectionId is not None and not any(
        item["connectionOwnerId"] == body.connectionOwnerId and item["connectionId"] == body.connectionId
        for item in _visible_connections(request, body.userId, body.product)
    ):
        raise ApiProblem(422, "ai_grant_database_invalid", "The user cannot access this database in the selected app.")


def _validate_role_grant(body, request):
    if body.roleId == PROVISIONER_ROLE_ID or body.roleId.startswith("role_personal_"):
        raise ApiProblem(422, "ai_role_scope_invalid", "Choose a managed role for role AI access.")
    auth = request.app.state.auth
    with auth.store.transaction() as state:
        role = state["roles"].get(body.roleId)
    if role is None or not role_has_instance_scope(role, body.product,
            body.connectionOwnerId, body.connectionId):
        raise ApiProblem(422, "ai_role_scope_invalid",
                         "Give this role access to the app and exact managed database before enabling AI.")
    if body.connectionId is not None:
        if body.connectionOwnerId != SCHEMII_CONNECTION_OWNER_ID:
            raise ApiProblem(422, "ai_role_scope_invalid", "Role AI grants require a managed database.")
        try:
            profile = request.app.state.services.connections.get(body.connectionOwnerId, body.connectionId)
        except ConnectionNotFoundError:
            raise ApiProblem(422, "ai_role_scope_invalid", "The managed database is unavailable.") from None
        if profile.ownership != "schemii":
            raise ApiProblem(422, "ai_role_scope_invalid", "Role AI grants require a managed database.")


def _role_grants_state(request, provider_id):
    grants = _instance_store(request).list_role_grants(provider_id)
    for grant in grants:
        body = RoleGrant(**{key: grant[key] for key in
                            ("roleId", "product", "connectionOwnerId", "connectionId")})
        try:
            _validate_role_grant(body, request)
        except ApiProblem as problem:
            grant.update(active=False, issue=problem.message)
        else:
            grant["active"] = True
    return grants


@admin_router.get("")
def admin_zen(request: Request, actor: str = Depends(admin)):
    return _admin_instance_state(request, "opencode")


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
    _validate_instance_grant(body, request)
    result = _zen_store(request).upsert_grant(body.userId, body.product, body.connectionOwnerId, body.connectionId)
    auth.audit(actor, "ai.zen.grant", f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}")
    return result


@admin_router.delete("/grants")
def admin_delete_zen_grant(body: ZenGrant, request: Request, actor: str = Depends(admin)):
    removed = _zen_store(request).delete_grant(body.userId, body.product, body.connectionOwnerId, body.connectionId)
    request.app.state.auth.audit(actor, "ai.zen.revoke", f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}")
    return {"deleted": removed}


@admin_router.get("/role-grants")
def admin_zen_role_grants(request: Request, actor: str = Depends(admin)):
    return {"grants": _role_grants_state(request, "opencode")}


@admin_router.put("/role-grants")
def admin_save_zen_role_grant(body: RoleGrant, request: Request, actor: str = Depends(admin)):
    _validate_role_grant(body, request)
    result = _instance_store(request).upsert_role_grant(
        body.roleId, body.product, body.connectionOwnerId, body.connectionId)
    request.app.state.auth.audit(actor, "ai.zen.role_grant", body.roleId)
    return result


@admin_router.delete("/role-grants")
def admin_delete_zen_role_grant(body: RoleGrant, request: Request, actor: str = Depends(admin)):
    removed = _instance_store(request).delete_role_grant(
        body.roleId, body.product, body.connectionOwnerId, body.connectionId)
    request.app.state.auth.audit(actor, "ai.zen.role_revoke", body.roleId)
    return {"deleted": removed}


def _shared_models(request):
    runtime = request.app.state.ai_service.runtime
    if runtime is None:
        raise ApiProblem(503, "ai_runtime_unavailable", "The AI service is unavailable.")
    try:
        catalog = runtime._supported_models()
    except Exception:
        raise ApiProblem(503, "ai_catalog_unavailable", "The AI model catalog is temporarily unavailable.") from None
    return [{"id": item["id"], "name": item.get("name", item["id"]),
             "reasoningLevels": item.get("reasoningLevels", ["default"])}
            for item in catalog if item.get("providerId") == "openai-codex"]


@shared_codex_router.get("")
def admin_shared_codex(request: Request, actor: str = Depends(admin)):
    state = _admin_instance_state(request, "openai-codex")
    runtime = request.app.state.ai_service.runtime
    state.update(runtime.instance_codex_catalog() if runtime else
                 {"verifiedModels": [], "catalogCheckedAt": None})
    personal = request.app.state.services.metadata.ai_credentials.list(actor)
    state["sourceConnected"] = any(row["credential_id"] == "codex-prototype" and row["provider_id"] == "openai-codex"
                                   for row in personal)
    try:
        state["models"] = _shared_models(request)
    except ApiProblem:
        state["models"] = []
        state["catalogError"] = "The model catalog is temporarily unavailable. Try again shortly."
    return state


@shared_codex_router.put("/credential")
def admin_save_shared_codex(request: Request, actor: str = Depends(admin)):
    source = request.app.state.services.metadata.ai_credentials.get(actor, "codex-prototype")
    if source is None or source["provider_id"] != "openai-codex" or source["credential"].get("type") != "oauth":
        raise ApiProblem(409, "ai_codex_source_missing", "Connect your personal ChatGPT Codex sign-in, then add it to this installation.")
    result = _instance_store(request).set_credential("openai-codex", source["credential"])
    request.app.state.auth.audit(actor, "ai.shared_codex.connect", "instance")
    return result


@shared_codex_router.delete("/credential")
def admin_delete_shared_codex(request: Request, actor: str = Depends(admin)):
    result = _instance_store(request).clear_credential("openai-codex")
    request.app.state.auth.audit(actor, "ai.shared_codex.disconnect", "instance")
    return result


@shared_codex_router.post("/test")
def admin_test_shared_codex(request: Request, actor: str = Depends(admin)):
    runtime = request.app.state.ai_service.runtime
    if runtime is None:
        raise ApiProblem(503, "ai_runtime_unavailable", "The AI service is unavailable.")
    try:
        result = runtime.test_instance_codex()
    except PiError as error:
        raise ApiProblem(error.status, error.code, str(error)) from error
    request.app.state.auth.audit(actor, "ai.shared_codex.test", "instance")
    return result


@shared_codex_router.put("/grants")
def admin_save_shared_codex_grant(body: SharedCodexGrant, request: Request, actor: str = Depends(admin)):
    _validate_instance_grant(body, request)
    model = next((item for item in _shared_models(request) if item["id"] == body.modelId), None)
    if model is None or body.reasoningEffort not in model["reasoningLevels"]:
        raise ApiProblem(422, "ai_grant_model_invalid", "Choose a supported ChatGPT Codex model and reasoning level.")
    result = _instance_store(request).upsert_grant(body.userId, body.product,
        body.connectionOwnerId, body.connectionId, provider_id="openai-codex",
        model_id=body.modelId, reasoning_effort=body.reasoningEffort)
    request.app.state.auth.audit(actor, "ai.shared_codex.grant",
        f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}:{body.modelId}:{body.reasoningEffort}")
    return result


@shared_codex_router.delete("/grants")
def admin_delete_shared_codex_grant(body: SharedCodexGrant, request: Request, actor: str = Depends(admin)):
    removed = _instance_store(request).delete_grant(body.userId, body.product,
        body.connectionOwnerId, body.connectionId, provider_id="openai-codex")
    request.app.state.auth.audit(actor, "ai.shared_codex.revoke",
        f"{body.userId}:{body.product}:{body.connectionOwnerId or 'detached'}:{body.connectionId or ''}")
    return {"deleted": removed}


@shared_codex_router.post("/grants")
def admin_add_shared_codex_grant(body: SharedCodexExactGrant, request: Request,
                                  actor: str = Depends(admin)):
    _validate_instance_grant(body, request)
    runtime = request.app.state.ai_service.runtime
    verified = runtime.instance_codex_catalog().get("verifiedModels", []) if runtime else []
    model = next((item for item in verified if item["id"] == body.modelId), None)
    if model is None or body.reasoningEffort not in model.get("reasoningLevels", ["default"]):
        raise ApiProblem(422, "ai_grant_model_invalid",
                         "Test this connection and choose a verified model and reasoning level.")
    result = _instance_store(request).add_grant(
        body.userId, body.product, body.connectionOwnerId, body.connectionId,
        model_id=body.modelId, reasoning_effort=body.reasoningEffort)
    request.app.state.auth.audit(actor, "ai.shared_codex.grant",
                                 f"{body.userId}:{body.product}:{body.modelId}:{body.reasoningEffort}")
    return result


@shared_codex_router.delete("/model-grants")
def admin_delete_shared_codex_model_grant(body: SharedCodexExactGrant, request: Request,
                                           actor: str = Depends(admin)):
    removed = _instance_store(request).delete_model_grant(
        body.userId, body.product, body.connectionOwnerId, body.connectionId,
        model_id=body.modelId, reasoning_effort=body.reasoningEffort)
    request.app.state.auth.audit(actor, "ai.shared_codex.revoke",
                                 f"{body.userId}:{body.product}:{body.modelId}:{body.reasoningEffort}")
    return {"deleted": removed}


@shared_codex_router.get("/role-grants")
def admin_shared_codex_role_grants(request: Request, actor: str = Depends(admin)):
    return {"grants": _role_grants_state(request, "openai-codex")}


@shared_codex_router.put("/role-grants")
def admin_save_shared_codex_role_grant(body: SharedCodexRoleGrant, request: Request,
                                        actor: str = Depends(admin)):
    _validate_role_grant(body, request)
    runtime = request.app.state.ai_service.runtime
    verified = runtime.instance_codex_catalog().get("verifiedModels", []) if runtime else []
    model = next((item for item in verified if item["id"] == body.modelId), None)
    if model is None or body.reasoningEffort not in model.get("reasoningLevels", ["default"]):
        raise ApiProblem(422, "ai_grant_model_invalid",
                         "Test this connection and choose a verified model and reasoning level.")
    result = _instance_store(request).upsert_role_grant(
        body.roleId, body.product, body.connectionOwnerId, body.connectionId,
        provider_id="openai-codex", model_id=body.modelId,
        reasoning_effort=body.reasoningEffort)
    request.app.state.auth.audit(actor, "ai.shared_codex.role_grant",
                                 f"{body.roleId}:{body.product}:{body.modelId}:{body.reasoningEffort}")
    return result


@shared_codex_router.delete("/role-grants")
def admin_delete_shared_codex_role_grant(body: SharedCodexRoleGrant, request: Request,
                                          actor: str = Depends(admin)):
    removed = _instance_store(request).delete_role_grant(
        body.roleId, body.product, body.connectionOwnerId, body.connectionId,
        provider_id="openai-codex", model_id=body.modelId,
        reasoning_effort=body.reasoningEffort)
    request.app.state.auth.audit(actor, "ai.shared_codex.role_revoke",
                                 f"{body.roleId}:{body.product}:{body.modelId}:{body.reasoningEffort}")
    return {"deleted": removed}
