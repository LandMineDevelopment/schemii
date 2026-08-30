from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def encode_signed_json(secret: bytes, payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(encoded + signature).decode("ascii").rstrip("=")


def decode_signed_json(secret: bytes, token: Any, *, maximum_length: int = 4096) -> Any:
    if not isinstance(token, str) or not token or len(token) > maximum_length:
        raise ValueError("signed JSON token is malformed")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        encoded, signature = raw[:-32], raw[-32:]
        expected = hmac.new(secret, encoded, hashlib.sha256).digest()
        if len(raw) <= 32 or not hmac.compare_digest(signature, expected):
            raise ValueError("signed JSON token is malformed")
        return json.loads(encoded)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("signed JSON token is malformed") from error
