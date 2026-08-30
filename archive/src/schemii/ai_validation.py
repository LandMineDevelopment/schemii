from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Callable


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,63}$")


def exact(value: dict[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise ValueError("action fields are invalid")


def exact_optional(value: dict[str, Any], required: set[str], optional: set[str]) -> None:
    if not required <= set(value) or set(value) - required - optional:
        raise ValueError("action fields are invalid")


def confirmation(action: dict[str, Any]) -> None:
    if action.get("requiresConfirmation") is not True:
        raise ValueError("confirmation is required")


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("identifier is invalid")
    return value


def postgres_name(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not NAME.fullmatch(value) or len(value.encode("utf-8")) > 63:
        raise ValueError("PostgreSQL name is invalid")
    return value


def text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("text is invalid")
    return copy.deepcopy(value)


def sql(value: Any, maximum: int = 10_000) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value.encode("utf-8")) > maximum or "\x00" in value:
        raise ValueError("SQL is invalid")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("SQL is invalid")
    return value


def revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("revision is invalid")
    return value


def normalized_list(value: Any, minimum: int, maximum: int, normalize: Callable[[Any], Any]) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("list size is invalid")
    return [normalize(item) for item in value]


def json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("row values must be finite JSON values")
    if value is None or isinstance(value, (str, bool, int, float)):
        return copy.deepcopy(value)
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: json_value(item) for key, item in value.items()}
    raise ValueError("row values must be JSON values")


def insert_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("rows must contain 1 to 100 items")
    normalized = []
    columns = None
    for row in value:
        if not isinstance(row, dict) or not 1 <= len(row) <= 50:
            raise ValueError("each row must contain 1 to 50 columns")
        current = tuple(row)
        if columns is None:
            columns = current
        elif set(current) != set(columns):
            raise ValueError("all rows must use the same columns")
        normalized.append({postgres_name(name): json_value(row[name]) for name in columns})
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("row values must be finite JSON values") from exc
    if len(encoded) > 24 * 1024:
        raise ValueError("row values are too large")
    return normalized
