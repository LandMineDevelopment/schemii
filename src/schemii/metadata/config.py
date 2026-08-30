from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_APPLICATION_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_APPLICATION_ID = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

METADATA_OWNER_ROLE = "schemii_metadata_owner"
METADATA_ADMIN_OWNER_ROLE = "schemii_metadata_bootstrap"
METADATA_RUNTIME_ROLES = {
    "schemii": "schemii_metadata_schemii",
    "schemer": "schemii_metadata_schemer",
}


@dataclass(frozen=True)
class MetadataConfig:
    dsn: str
    application_name: str = "schemii-metadata"
    connect_timeout: int = 5
    max_json_bytes: int = 1024 * 1024
    password_file: str = ""
    expected_application: str = ""
    expected_role: str = ""
    expected_owner: str = ""
    expected_admin_owner: str = ""

    def __post_init__(self) -> None:
        dsn = self.dsn.strip() if isinstance(self.dsn, str) else ""
        if not dsn:
            raise ValueError("metadata dsn is required")
        if not (dsn.startswith("postgresql://") or dsn.startswith("postgres://") or "=" in dsn):
            raise ValueError("metadata dsn must be a PostgreSQL connection string")
        if not _APPLICATION_NAME.fullmatch(self.application_name):
            raise ValueError("metadata application_name is invalid")
        if isinstance(self.connect_timeout, bool) or not 1 <= self.connect_timeout <= 60:
            raise ValueError("metadata connect_timeout must be between 1 and 60")
        if isinstance(self.max_json_bytes, bool) or not 1024 <= self.max_json_bytes <= 1024 * 1024:
            raise ValueError("metadata max_json_bytes must be between 1024 and 1048576")
        object.__setattr__(self, "dsn", dsn)
        if self.password_file and not Path(self.password_file).is_absolute():
            raise ValueError("metadata password_file must be absolute")
        if self.expected_application and not _APPLICATION_ID.fullmatch(self.expected_application):
            raise ValueError("metadata expected_application is invalid")
        if self.expected_role and not _ROLE_NAME.fullmatch(self.expected_role):
            raise ValueError("metadata expected_role is invalid")
        if self.expected_owner and not _ROLE_NAME.fullmatch(self.expected_owner):
            raise ValueError("metadata expected_owner is invalid")
        if self.expected_admin_owner and not _ROLE_NAME.fullmatch(self.expected_admin_owner):
            raise ValueError("metadata expected_admin_owner is invalid")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MetadataConfig":
        values = os.environ if env is None else env
        dsn = values.get("SCHEMII_METADATA_DSN", "")
        try:
            timeout = int(values.get("SCHEMII_METADATA_CONNECT_TIMEOUT", "5"))
            max_json = int(values.get("SCHEMII_METADATA_MAX_JSON_BYTES", str(1024 * 1024)))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata numeric environment settings must be integers") from exc
        return cls(
            dsn=dsn,
            password_file=values.get("SCHEMII_METADATA_PASSWORD_FILE", ""),
            application_name=values.get("SCHEMII_METADATA_APPLICATION_NAME", "schemii-metadata"),
            connect_timeout=timeout,
            max_json_bytes=max_json,
            expected_application=values.get("SCHEMII_METADATA_EXPECTED_APPLICATION", ""),
            expected_role=values.get("SCHEMII_METADATA_EXPECTED_ROLE", ""),
            expected_owner=values.get("SCHEMII_METADATA_EXPECTED_OWNER", ""),
            expected_admin_owner=values.get("SCHEMII_METADATA_EXPECTED_ADMIN_OWNER", ""),
        )

    @classmethod
    def from_runtime_env(
        cls, application: str, env: Mapping[str, str] | None = None,
    ) -> "MetadataConfig":
        role = METADATA_RUNTIME_ROLES.get(application)
        if role is None:
            raise ValueError("metadata runtime application is invalid")
        configured = cls.from_env(env)
        return cls(
            dsn=configured.dsn,
            application_name=application,
            connect_timeout=configured.connect_timeout,
            max_json_bytes=configured.max_json_bytes,
            password_file=configured.password_file,
            expected_application=application,
            expected_role=role,
            expected_owner=METADATA_OWNER_ROLE,
            expected_admin_owner=METADATA_ADMIN_OWNER_ROLE,
        )
