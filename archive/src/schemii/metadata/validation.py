from __future__ import annotations

import json
import math
import re
from typing import Any

from .errors import MetadataStoreError


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_KEYS = {
    "credential", "credentials", "dsn", "password", "passphrase", "private_key",
    "secret", "token", "access_token", "refresh_token", "connection_string",
}
_CAMEL_CASE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def bounded_json(value: Any, field: str, max_bytes: int, *, require_object: bool = True) -> Any:
    if require_object and not isinstance(value, dict):
        raise MetadataStoreError("invalid_metadata", f"{field} must be an object", status=400)
    _inspect(value, field, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MetadataStoreError("invalid_metadata", f"{field} must be JSON-compatible", status=400) from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise MetadataStoreError("metadata_payload_too_large", f"{field} exceeds its size limit", status=413)
    return json.loads(encoded)


def _inspect(value: Any, field: str, depth: int) -> None:
    if depth > 32:
        raise MetadataStoreError("invalid_metadata", f"{field} is nested too deeply", status=400)
    if isinstance(value, dict):
        if len(value) > 1000:
            raise MetadataStoreError("invalid_metadata", f"{field} has too many members", status=400)
        for key, item in value.items():
            if not isinstance(key, str):
                raise MetadataStoreError("invalid_metadata", f"{field} has a non-string key", status=400)
            normalized = _CAMEL_CASE.sub("_", key.strip()).lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise MetadataStoreError("credentials_forbidden", f"{field} must not contain credentials", status=400)
            _inspect(item, field, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            raise MetadataStoreError("invalid_metadata", f"{field} has too many items", status=400)
        for item in value:
            _inspect(item, field, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise MetadataStoreError("invalid_metadata", f"{field} contains a non-finite number", status=400)
