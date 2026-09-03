"""Conservative PostgreSQL column-type change classification.

Only changes that preserve every value by construction are authorized without a
reviewed ``USING`` expression.  PostgreSQL may support more assignment casts,
but accepting those here would make the migration result depend on the current
rows instead of the reviewed schema contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


TypeChangeDisposition = Literal["equivalent", "safe", "blocked"]


@dataclass(frozen=True, slots=True)
class TypeChangeDecision:
    disposition: TypeChangeDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class _ParsedType:
    family: str
    variant: str
    modifiers: tuple[int, ...] = ()


_INTEGER_RANKS = {
    "smallint": 0,
    "int2": 0,
    "integer": 1,
    "int": 1,
    "int4": 1,
    "bigint": 2,
    "int8": 2,
}
_FLOAT_RANKS = {"real": 0, "float4": 0, "double precision": 1, "float8": 1}


def _normalized(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _parse(value: str) -> _ParsedType | None:
    normalized = _normalized(value)
    if normalized in _INTEGER_RANKS:
        return _ParsedType("integer", str(_INTEGER_RANKS[normalized]))
    if normalized in _FLOAT_RANKS:
        return _ParsedType("float", str(_FLOAT_RANKS[normalized]))
    if normalized in {"boolean", "bool"}:
        return _ParsedType("boolean", "boolean")

    match = re.fullmatch(
        r"(?:character\s+varying|varchar)\s*(?:\(\s*(\d+)\s*\))?",
        normalized,
    )
    if match:
        length = match.group(1)
        return _ParsedType("string", "varchar", (() if length is None else (int(length),)))
    if normalized == "text":
        return _ParsedType("string", "text")

    match = re.fullmatch(
        r"(?:numeric|decimal)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?",
        normalized,
    )
    if match:
        precision = match.group(1)
        if precision is None:
            return _ParsedType("numeric", "numeric")
        return _ParsedType(
            "numeric",
            "numeric",
            (int(precision), int(match.group(2) or 0)),
        )

    timestamp = re.fullmatch(
        r"timestamp\s*(?:\(\s*(\d+)\s*\))?\s*(with(?:out)?\s+time\s+zone)?",
        normalized,
    )
    if timestamp:
        zone = timestamp.group(2) or "without time zone"
        variant = "with time zone" if zone == "with time zone" else "without time zone"
        return _ParsedType("timestamp", variant, (int(timestamp.group(1) or 6),))
    timestamp_alias = re.fullmatch(r"timestamptz\s*(?:\(\s*(\d+)\s*\))?", normalized)
    if timestamp_alias:
        return _ParsedType(
            "timestamp",
            "with time zone",
            (int(timestamp_alias.group(1) or 6),),
        )

    time = re.fullmatch(
        r"time\s*(?:\(\s*(\d+)\s*\))?\s*(with(?:out)?\s+time\s+zone)?",
        normalized,
    )
    if time:
        zone = time.group(2) or "without time zone"
        variant = "with time zone" if zone == "with time zone" else "without time zone"
        return _ParsedType("time", variant, (int(time.group(1) or 6),))
    time_alias = re.fullmatch(r"timetz\s*(?:\(\s*(\d+)\s*\))?", normalized)
    if time_alias:
        return _ParsedType("time", "with time zone", (int(time_alias.group(1) or 6),))
    return None


def classify_type_change(before: str, after: str) -> TypeChangeDecision:
    """Classify whether a direct ``ALTER COLUMN TYPE`` is value-preserving."""

    if _normalized(before) == _normalized(after):
        return TypeChangeDecision("equivalent", "The type declarations are identical")

    old = _parse(before)
    new = _parse(after)
    if old is None or new is None:
        return TypeChangeDecision(
            "blocked",
            "The conversion is not in Schemii's value-preserving type map",
        )
    if old == new:
        return TypeChangeDecision("equivalent", "The declarations are PostgreSQL aliases")

    if old.family == new.family == "integer":
        if int(new.variant) > int(old.variant):
            return TypeChangeDecision("safe", "The integer range only increases")
    elif old.family == new.family == "float":
        if int(new.variant) > int(old.variant):
            return TypeChangeDecision("safe", "The floating-point precision only increases")
    elif old.family == new.family == "string":
        if new.variant == "text":
            return TypeChangeDecision("safe", "The target has no length limit")
        if old.variant == "text":
            if not new.modifiers:
                return TypeChangeDecision("safe", "The target has no length limit")
        elif not new.modifiers:
            return TypeChangeDecision("safe", "The target removes the length limit")
        elif old.modifiers and new.modifiers[0] >= old.modifiers[0]:
            return TypeChangeDecision("safe", "The character limit only increases")
    elif old.family == new.family == "numeric":
        if not new.modifiers:
            return TypeChangeDecision("safe", "The target removes precision and scale limits")
        if old.modifiers:
            old_precision, old_scale = old.modifiers
            new_precision, new_scale = new.modifiers
            if new_scale >= old_scale and new_precision - new_scale >= old_precision - old_scale:
                return TypeChangeDecision(
                    "safe",
                    "The target preserves both integral digits and fractional scale",
                )
    elif old.family == new.family and old.family in {"timestamp", "time"}:
        if old.variant == new.variant and new.modifiers[0] > old.modifiers[0]:
            return TypeChangeDecision("safe", "The temporal precision only increases")

    return TypeChangeDecision(
        "blocked",
        "The conversion can narrow, reinterpret, or reject existing values",
    )
