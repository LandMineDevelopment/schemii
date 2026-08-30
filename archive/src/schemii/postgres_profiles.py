from __future__ import annotations

import re
from typing import Any

from .postgres_common import ValidationError


PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
PROFILE_FIELDS = {"name", "host", "port", "dbname", "user", "password", "sslmode", "timeout"}


def validate_profile_id(profile_id: Any) -> str:
    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise ValidationError("Profile ID must be 1-64 letters, numbers, underscores, or hyphens")
    return profile_id


def _text(payload: dict[str, Any], key: str, maximum: int, *, host: bool = False) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > maximum:
        raise ValidationError(f"{key} must be a non-empty trimmed string up to {maximum} characters")
    if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValidationError(f"{key} contains invalid characters")
    if host and any(char.isspace() for char in value):
        raise ValidationError("host must not contain whitespace")
    return value


def validate_profile(payload: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("Profile payload must be an object")
    unknown = set(payload) - PROFILE_FIELDS
    if unknown:
        raise ValidationError(f"Unknown profile field: {sorted(unknown)[0]}")
    merged = dict(existing or {})
    merged.update(payload)
    result = {
        "name": _text(merged, "name", 128),
        "host": _text(merged, "host", 255, host=True),
        "dbname": _text(merged, "dbname", 128),
        "user": _text(merged, "user", 128),
    }
    port = merged.get("port", 5432)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError("port must be an integer from 1 to 65535")
    result["port"] = port
    sslmode = merged.get("sslmode", "prefer")
    if not isinstance(sslmode, str) or sslmode not in SSL_MODES:
        raise ValidationError("sslmode is invalid")
    result["sslmode"] = sslmode
    timeout = merged.get("timeout", 10)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
        raise ValidationError("timeout must be an integer from 1 to 120 seconds")
    result["timeout"] = timeout
    password = merged.get("password", "")
    if not isinstance(password, str) or len(password) > 4096 or "\x00" in password:
        raise ValidationError("password is invalid")
    if existing is not None and payload.get("password") == "":
        password = existing.get("password", "")
    result["password"] = password
    return result


def validate_profile_document(document: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict) or set(document) != {"profiles"} or not isinstance(document["profiles"], dict):
        raise ValidationError("Profile store is invalid")
    return {
        validate_profile_id(profile_id): validate_profile(profile)
        for profile_id, profile in document["profiles"].items()
    }
