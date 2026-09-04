"""Provider authentication and availability contracts shared by products."""

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator

from schemii.common.api.models import ApiModel


class AiProviderAuthOption(ApiModel):
    label: Annotated[str, Field(max_length=256)]
    value: Annotated[str, Field(max_length=256)]
    hint: Annotated[str, Field(max_length=256)] = ""


class AiProviderAuthPrompt(ApiModel):
    key: Annotated[str, Field(min_length=1, max_length=128)]
    message: Annotated[str, Field(min_length=1, max_length=512)]
    type: Literal["text", "select"]
    placeholder: Annotated[str, Field(max_length=256)] = ""
    options: list[AiProviderAuthOption] = Field(default_factory=list, max_length=50)


class AiProviderAuthMethod(ApiModel):
    id: Annotated[int, Field(strict=True, ge=0, le=100)]
    type: Literal["api", "oauth"]
    label: Annotated[str, Field(min_length=1, max_length=256)]
    prompts: list[AiProviderAuthPrompt] = Field(default_factory=list, max_length=20)


class AiProviderStatus(ApiModel):
    """Non-secret provider availability for the current owner."""

    id: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(min_length=1, max_length=256)]
    available: bool
    authenticated: bool
    privacy: str | None = None
    auth_methods: list[AiProviderAuthMethod] = Field(default_factory=list, max_length=20)
    models: list["AiModelStatus"] = Field(max_length=1000)


class AiModelStatus(ApiModel):
    """One provider-advertised model suitable for a chat session."""

    id: Annotated[str, Field(min_length=1, max_length=256)]
    name: Annotated[str, Field(min_length=1, max_length=256)]
    status: Literal["active", "deprecated", "unavailable"] = "active"


class AiStatusResponse(ApiModel):
    """Shared AI runtime health and owner-visible providers."""

    enabled: bool
    healthy: bool
    providers: list[AiProviderStatus]
    message: str | None = None


class AiProviderApiCredentialCreate(ApiModel):
    provider_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    key: SecretStr = Field(min_length=1, max_length=16_384)
    inputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20 or any(len(key) > 128 or len(item) > 4096 for key, item in value.items()):
            raise ValueError("provider inputs are too large")
        return value


class AiProviderOauthAuthorizeCreate(ApiModel):
    provider_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    method: Annotated[int, Field(strict=True, ge=0, le=100)]
    inputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, str]) -> dict[str, str]:
        return AiProviderApiCredentialCreate.validate_inputs(value)


class AiProviderOauthCallbackCreate(ApiModel):
    provider_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    method: Annotated[int, Field(strict=True, ge=0, le=100)]
    code: SecretStr | None = Field(default=None, max_length=16_384)


class AiProviderOauthAuthorization(ApiModel):
    url: Annotated[str, Field(min_length=1, max_length=8192)]
    method: Literal["auto", "code"]
    instructions: Annotated[str, Field(max_length=4096)] = ""


class AiProviderCredentialResult(ApiModel):
    saved: bool = False
    deleted: bool = False
    authenticated: bool = False
