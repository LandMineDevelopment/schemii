"""Validated models for shared PostgreSQL connections."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


ConnectionName = Annotated[str, Field(min_length=1, max_length=128)]
Host = Annotated[str, Field(min_length=1, max_length=255)]
MAX_CONNECT_TIMEOUT_SECONDS = 30
def _postgres_identifier(value: str) -> str:
    if "\x00" in value or len(value.encode("utf-8")) > 63:
        raise ValueError("value must be a valid PostgreSQL identifier")
    return value


DatabaseName = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(_postgres_identifier),
]
Username = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(_postgres_identifier),
]
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
ConnectTimeout = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_CONNECT_TIMEOUT_SECONDS),
]
Password = Annotated[SecretStr, Field(min_length=1, max_length=4096)]


class PostgresSslMode(str, Enum):
    DISABLE = "disable"
    ALLOW = "allow"
    PREFER = "prefer"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


class _ConnectionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class PostgresConnectionMetadata(_ConnectionModel):
    """Non-secret PostgreSQL connection fields."""

    name: ConnectionName
    host: Host
    port: Port = 5432
    database: DatabaseName
    username: Username
    ssl_mode: PostgresSslMode = PostgresSslMode.VERIFY_FULL
    connect_timeout: ConnectTimeout = 10

    @field_validator("name", "host", mode="before")
    @classmethod
    def normalize_metadata_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if "," in value or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "host must identify one server and contain no whitespace or control characters"
            )
        return value


class PostgresConnectionCreate(PostgresConnectionMetadata):
    password: Password | None = None


class PostgresConnectionUpdate(_ConnectionModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    name: ConnectionName | None = None
    host: Host | None = None
    port: Port | None = None
    database: DatabaseName | None = None
    username: Username | None = None
    ssl_mode: PostgresSslMode | None = None
    connect_timeout: ConnectTimeout | None = None
    password: Password | None = None

    @field_validator("name", "host", mode="before")
    @classmethod
    def normalize_metadata_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        if value is not None and (
            "," in value
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in value
            )
        ):
            raise ValueError(
                "host must identify one server and contain no whitespace or control characters"
            )
        return value

    @model_validator(mode="after")
    def validate_changes(self) -> "PostgresConnectionUpdate":
        changed = self.model_fields_set - {"expected_revision"}
        if not changed:
            raise ValueError("at least one connection field must be provided")
        if any(field != "password" and getattr(self, field) is None for field in changed):
            raise ValueError("connection fields cannot be null")
        return self


class PostgresConnectionProfile(PostgresConnectionMetadata):
    """Public connection metadata; credentials are intentionally absent."""

    id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    credential_stored: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("connection timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_chronology(self) -> "PostgresConnectionProfile":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class ResolvedPostgresConnection(PostgresConnectionMetadata):
    """Internal target resolved only when opening PostgreSQL."""

    id: str = Field(pattern=r"^pg_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    password: SecretStr | None = None
