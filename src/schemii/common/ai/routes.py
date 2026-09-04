"""Deployment-configured AI provider discovery."""

from fastapi import APIRouter, Depends, Request

from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal

from .opencode import OpenCodeError
from .models import (
    AiProviderApiCredentialCreate,
    AiProviderCredentialResult,
    AiProviderOauthAuthorization,
    AiProviderOauthAuthorizeCreate,
    AiProviderOauthCallbackCreate,
    AiStatusResponse,
)


router = APIRouter(prefix="/api/v1/ai", tags=["ai-providers"])


def _call(function, *args):
    try:
        return function(*args)
    except OpenCodeError as error:
        raise ApiProblem(
            503 if error.code == "ai_runtime_unavailable" else 502,
            error.code,
            str(error),
        ) from error


@router.get("/status", response_model=AiStatusResponse)
def status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AiStatusResponse:
    del principal
    return AiStatusResponse.model_validate(request.app.state.ai_service.status())


@router.post("/auth/api", response_model=AiProviderCredentialResult)
def set_api_credential(
    body: AiProviderApiCredentialCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderCredentialResult:
    return _call(
        request.app.state.ai_service.set_api_credential, principal.user_id, body
    )


@router.post("/auth/oauth/authorize", response_model=AiProviderOauthAuthorization)
def authorize_oauth(
    body: AiProviderOauthAuthorizeCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderOauthAuthorization:
    return _call(
        request.app.state.ai_service.authorize_oauth, principal.user_id, body
    )


@router.post("/auth/oauth/callback", response_model=AiProviderCredentialResult)
def complete_oauth(
    body: AiProviderOauthCallbackCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderCredentialResult:
    return _call(
        request.app.state.ai_service.complete_oauth, principal.user_id, body
    )


@router.delete("/auth/{provider_id}", response_model=AiProviderCredentialResult)
def remove_provider_credential(
    provider_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderCredentialResult:
    return _call(
        request.app.state.ai_service.remove_provider_credential,
        principal.user_id,
        provider_id,
    )
