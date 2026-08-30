from __future__ import annotations

import hashlib


_LOCK_DOMAIN = b"schemii:namespace-mutation:v1\0"


def namespace_lock_keys(database: str, namespace: str) -> tuple[int, int]:
    """Return stable signed int32 keys for PostgreSQL's two-key advisory lock."""
    payload = _LOCK_DOMAIN + database.encode("utf-8") + b"\0" + namespace.encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return (
        int.from_bytes(digest[:4], "big", signed=True),
        int.from_bytes(digest[4:8], "big", signed=True),
    )
