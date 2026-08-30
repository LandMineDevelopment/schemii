"""Application compatibility for persisted v1 Schemer snapshots.

These formatted-type rules preserve readability of records saved before catalog-derived
capabilities existed. They are not PostgreSQL authority and must not be used for live
structured-query execution or newly selected sources.
"""

from __future__ import annotations

import re


NUMERIC = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision)$", re.I)
SUM = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision|interval|money)$", re.I)
AVERAGE = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision|interval)$", re.I)
NON_ORDERABLE = re.compile(r"^(?:boolean|bit(?: varying)?(?:\([^)]*\))?|jsonb?|xml|box|circle|line|lseg|path|point|polygon)$", re.I)
NON_GROUPABLE = re.compile(r"^(?:json|xml|box|circle|line|lseg|path|point|polygon)$", re.I)
TEXT = re.compile(r"^(?:text|character varying(?:\([^)]*\))?|character(?:\([^)]*\))?|varchar(?:\([^)]*\))?|char(?:\([^)]*\))?|citext|name)$", re.I)
BOOLEAN = re.compile(r"^boolean$", re.I)
TEMPORAL = re.compile(r"^(?:date|time(?:stamp)?(?: with(?:out)? time zone)?|interval)$", re.I)
SERIES_TEMPORAL = re.compile(r"^(?:date|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?|timestamptz)$", re.I)


def supports(column_type: str, operation: str) -> bool:
    if operation == "count":
        return True
    if operation == "groupable" or operation == "distinct":
        return not NON_GROUPABLE.fullmatch(column_type)
    if operation == "sortable" or operation in {"minimum", "maximum"}:
        return not NON_ORDERABLE.fullmatch(column_type)
    if operation == "sum":
        return bool(SUM.fullmatch(column_type))
    if operation == "average":
        return bool(AVERAGE.fullmatch(column_type))
    if operation == "zeroable":
        return bool(NUMERIC.fullmatch(column_type))
    if operation == "temporal":
        return bool(SERIES_TEMPORAL.fullmatch(column_type))
    if operation in {"is_null", "is_not_null"}:
        return True
    if NON_GROUPABLE.fullmatch(column_type):
        return False
    if TEXT.fullmatch(column_type):
        return operation in {"eq", "neq", "in", "not_in", "like", "contains", "starts_with", "ends_with"}
    if BOOLEAN.fullmatch(column_type):
        return operation in {"eq", "neq"}
    if NUMERIC.fullmatch(column_type) or TEMPORAL.fullmatch(column_type):
        return operation in {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in"}
    return operation in {"eq", "neq", "in", "not_in"}
