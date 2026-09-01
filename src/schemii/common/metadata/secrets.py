"""Strict file-backed secret loading for metadata infrastructure."""

from __future__ import annotations

import base64
from pathlib import Path


def read_secret_file(path: str, setting: str) -> str:
    """Read one non-empty line without silently normalizing secret bytes."""

    try:
        value = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{setting} could not be read") from error
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or value != value.strip():
        raise ValueError(f"{setting} must contain exactly one non-empty line")
    return value


def read_encryption_key(path: str) -> bytes:
    """Load one URL-safe or standard base64-encoded 256-bit key."""

    encoded = read_secret_file(path, "SCHEMII_METADATA_ENCRYPTION_KEY_FILE")
    try:
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError(
            "SCHEMII_METADATA_ENCRYPTION_KEY_FILE must contain a base64 key"
        ) from error
    if len(key) != 32:
        raise ValueError(
            "SCHEMII_METADATA_ENCRYPTION_KEY_FILE must contain a 256-bit key"
        )
    return key
