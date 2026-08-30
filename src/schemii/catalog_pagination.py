from __future__ import annotations

from typing import Any

from .postgres_common import PostgresServiceError, ValidationError
from .signed_json import decode_signed_json, encode_signed_json


DEFAULT_CATALOG_PAGE_SIZE = 100
MAX_CATALOG_PAGE_SIZE = 200


def catalog_page_size(value: Any) -> int:
    if value is None:
        return DEFAULT_CATALOG_PAGE_SIZE
    if isinstance(value, bool):
        raise ValidationError("pageSize must be an integer between 1 and 200")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("pageSize must be an integer between 1 and 200") from exc
    if str(parsed) != str(value) or not 1 <= parsed <= MAX_CATALOG_PAGE_SIZE:
        raise ValidationError("pageSize must be an integer between 1 and 200")
    return parsed


def encode_catalog_cursor(secret: bytes, context: dict[str, Any], after: list[str]) -> str:
    return encode_signed_json(secret, {"v": 1, "context": context, "after": after})


def decode_catalog_cursor(secret: bytes, cursor: Any, expected_context: dict[str, Any]) -> list[str] | None:
    if cursor is None:
        return None
    try:
        payload = decode_signed_json(secret, cursor)
    except ValueError:
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed") from None
    if not isinstance(payload, dict) or set(payload) != {"v", "context", "after"} or payload["v"] != 1:
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed")
    context = payload["context"]
    after = payload["after"]
    if not isinstance(context, dict) or set(context) != set(expected_context) or not isinstance(after, list) or not after or any(not isinstance(item, str) for item in after):
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed")
    comparison_context = {key: value for key, value in context.items() if key != "catalogFingerprint"}
    expected_comparison = {key: value for key, value in expected_context.items() if key != "catalogFingerprint"}
    if comparison_context != expected_comparison:
        raise PostgresServiceError(409, "catalog_cursor_mismatch", "The catalog cursor belongs to a different target, filter, sort, or page size")
    if context.get("catalogFingerprint") != expected_context.get("catalogFingerprint"):
        raise PostgresServiceError(409, "catalog_cursor_stale", "The PostgreSQL catalog changed; restart catalog paging")
    return after
