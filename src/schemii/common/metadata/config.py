"""Validated configuration for durable application metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class MetadataStorageMode(str, Enum):
    """Explicit persistence mode selected by the runtime."""

    MEMORY = "memory"
    POSTGRESQL = "postgresql"


@dataclass(frozen=True)
class MetadataConfig:
    """Connection and credential-key inputs supplied by a deployment."""

    dsn: str
    password_file: str
    encryption_key_file: str
    connect_timeout: int = 5
    application_name: str = "schemii-metadata"
    target_host_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.dsn.strip():
            raise ValueError("metadata DSN must not be empty")
        if not Path(self.password_file).is_absolute():
            raise ValueError("metadata password file must be an absolute path")
        if not Path(self.encryption_key_file).is_absolute():
            raise ValueError("metadata encryption key file must be an absolute path")
        if isinstance(self.connect_timeout, bool) or not 1 <= self.connect_timeout <= 30:
            raise ValueError("metadata connect timeout must be between 1 and 30 seconds")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "MetadataConfig | None":
        values = os.environ if env is None else env
        raw_mode = values.get("SCHEMII_STORAGE_MODE", "").strip()
        try:
            mode = MetadataStorageMode(raw_mode)
        except ValueError as error:
            raise ValueError(
                "SCHEMII_STORAGE_MODE must be explicitly set to memory or postgresql"
            ) from error
        dsn = values.get("SCHEMII_METADATA_DSN", "").strip()
        if mode is MetadataStorageMode.MEMORY:
            if dsn:
                raise ValueError(
                    "SCHEMII_METADATA_DSN must not be set when SCHEMII_STORAGE_MODE is memory"
                )
            return None
        if not dsn:
            raise ValueError(
                "SCHEMII_METADATA_DSN is required when SCHEMII_STORAGE_MODE is postgresql"
            )
        try:
            connect_timeout = int(values.get("SCHEMII_METADATA_CONNECT_TIMEOUT", "5"))
        except (TypeError, ValueError) as error:
            raise ValueError("metadata connect timeout must be an integer") from error
        return cls(
            dsn=dsn,
            password_file=values.get("SCHEMII_METADATA_PASSWORD_FILE", ""),
            encryption_key_file=values.get(
                "SCHEMII_METADATA_ENCRYPTION_KEY_FILE",
                "",
            ),
            connect_timeout=connect_timeout,
            target_host_aliases=tuple(
                alias.strip()
                for alias in values.get(
                    "SCHEMII_METADATA_TARGET_HOST_ALIASES",
                    "",
                ).split(",")
                if alias.strip()
            ),
        )
