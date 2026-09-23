"""Owner-scoped Pi provider discovery and encrypted API credentials."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SecretStr, Field

from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal
from .models import AiStatusResponse

router = APIRouter(prefix="/api/v1/ai", tags=["ai-providers"])


class ApiCredentialCreate(BaseModel):
    apiKey: SecretStr = Field(min_length=1, max_length=16384)


@router.get("/status", response_model=AiStatusResponse)
def status(request: Request, refresh: bool = False, principal: Principal = Depends(get_current_principal)):
    service = request.app.state.ai_service
    result = service.status(principal.user_id, refresh=True) if refresh else service.status(principal.user_id)
    return AiStatusResponse.model_validate(result)


@router.post("/credentials/{provider_id}")
def save_credential(provider_id: str, body: ApiCredentialCreate, request: Request,
                    principal: Principal = Depends(get_current_principal)):
    if provider_id != "openai":
        raise ApiProblem(422, "ai_provider_unsupported", "Only OpenAI API keys can be saved here. Use device sign-in for Codex.")
    runtime = request.app.state.ai_service.runtime
    if runtime is None:
        raise ApiProblem(503, "ai_runtime_unavailable", "The AI service is unavailable.")
    store = request.app.state.services.metadata.ai_credentials
    runtime.disconnect(principal.user_id, "openai")
    record = store.begin_login(principal.user_id, "openai", "openai")
    if not store.save(principal.user_id, "openai", "openai",
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
