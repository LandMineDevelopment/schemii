"""Planned shared AI provider routes."""

from fastapi import APIRouter, Depends

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    AiOauthAuthorization,
    AiOauthAuthorizationCreate,
    AiOauthCallbackCreate,
    AiProviderCredentialUpdate,
    AiProviderStatus,
    AiStatusResponse,
)


router = APIRouter(prefix="/api/v1/ai", tags=["ai-providers-planned"])


@router.get(
    "/status",
    response_model=AiStatusResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_ai_status(
    principal: Principal = Depends(get_current_principal),
) -> AiStatusResponse:
    """Report provider availability without exposing credentials or model secrets."""

    # TODO(ai-provider-status): Query the pinned AI sidecar through a bounded
    # client and merge its capabilities with owner-scoped credential metadata.
    del principal
    planned_capability("ai.providers.status")


@router.put(
    "/providers/{provider_id}/credentials",
    response_model=AiProviderStatus,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def set_ai_provider_credential(
    provider_id: str,
    body: AiProviderCredentialUpdate,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderStatus:
    """Encrypt and replace one owner-scoped provider credential."""

    # TODO(ai-provider-credentials): Validate against the live provider manifest,
    # encrypt at the metadata boundary, verify once, and never return secret input.
    del provider_id, body, principal
    planned_capability("ai.providers.credentials.set")


@router.delete(
    "/providers/{provider_id}/credentials",
    response_model=AiProviderStatus,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def delete_ai_provider_credential(
    provider_id: str,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderStatus:
    """Remove one provider credential without deleting chats or audit records."""

    # TODO(ai-provider-credentials): Revoke local authority atomically and leave
    # historical chat/proposal evidence non-replayable but inspectable.
    del provider_id, principal
    planned_capability("ai.providers.credentials.delete")


@router.post(
    "/providers/{provider_id}/oauth/authorizations",
    response_model=AiOauthAuthorization,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def create_ai_oauth_authorization(
    provider_id: str,
    body: AiOauthAuthorizationCreate,
    principal: Principal = Depends(get_current_principal),
) -> AiOauthAuthorization:
    """Create a short-lived owner/provider-bound OAuth authorization."""

    # TODO(ai-provider-oauth): Persist hashed state with owner, provider, method,
    # redirect, and expiry before returning the provider authorization URL.
    del provider_id, body, principal
    planned_capability("ai.providers.oauth.authorize")


@router.post(
    "/providers/{provider_id}/oauth/callbacks",
    response_model=AiProviderStatus,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def complete_ai_oauth_callback(
    provider_id: str,
    body: AiOauthCallbackCreate,
    principal: Principal = Depends(get_current_principal),
) -> AiProviderStatus:
    """Consume one OAuth state and retain the resulting encrypted credential."""

    # TODO(ai-provider-oauth): Atomically consume state, exchange the code through
    # the bounded provider client, encrypt tokens, and reject all callback replay.
    del provider_id, body, principal
    planned_capability("ai.providers.oauth.callback")
