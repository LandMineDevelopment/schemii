"""Provider authentication and availability contracts shared by products."""

from typing import Annotated, Literal

from pydantic import Field, SecretStr

from schemii.common.api.models import ApiModel


class AiProviderStatus(ApiModel):
    """Non-secret provider availability for the current owner."""

    id: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(min_length=1, max_length=256)]
    available: bool
    authenticated: bool
    auth_methods: list[Literal["api_key", "oauth"]]
    models: list[str] = Field(max_length=1000)


class AiStatusResponse(ApiModel):
    """Shared AI runtime health and owner-visible providers."""

    enabled: bool
    healthy: bool
    providers: list[AiProviderStatus]


class AiProviderCredentialUpdate(ApiModel):
    """Replace one encrypted owner-scoped provider API credential."""

    key: SecretStr = Field(min_length=1, max_length=16_384)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=32)


class AiOauthAuthorizationCreate(ApiModel):
    """Begin an OAuth authorization using a provider-advertised method."""

    method: Annotated[str, Field(min_length=1, max_length=128)]
    redirect_uri: Annotated[str, Field(min_length=1, max_length=2048)]
    inputs: dict[str, str] = Field(default_factory=dict, max_length=32)


class AiOauthAuthorization(ApiModel):
    """Short-lived browser authorization URL and state binding."""

    provider_id: Annotated[str, Field(min_length=1, max_length=128)]
    authorization_url: Annotated[str, Field(min_length=1, max_length=4096)]
    state: Annotated[str, Field(min_length=32, max_length=512)]
    expires_in_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)]


class AiOauthCallbackCreate(ApiModel):
    """Complete OAuth with the exact state issued during authorization."""

    method: Annotated[str, Field(min_length=1, max_length=128)]
    state: Annotated[str, Field(min_length=32, max_length=512)]
    code: SecretStr = Field(min_length=1, max_length=16_384)
