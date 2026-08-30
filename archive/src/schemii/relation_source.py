from __future__ import annotations

import re
from typing import Any

from .query_type_capabilities import CapabilityValidationError, normalize_capabilities


PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELATION_KINDS = {"table", "partitioned_table", "view", "materialized_view", "foreign_table"}
IDENTITY_FIELDS = {"profileId", "database", "namespace", "relation", "kind", "fingerprint"}
LEGACY_COLUMN_FIELDS = {"name", "type", "nullable", "ordinal"}
CURRENT_COLUMN_FIELDS = LEGACY_COLUMN_FIELDS | {"capabilities"}


class RelationSourceValidationError(ValueError):
    pass


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 63
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RelationSourceValidationError(f"{label} must be a valid PostgreSQL identifier up to 63 bytes")
    return value


def normalize_relation_source(source: Any, *, expected_profile_id: str | None = None) -> dict[str, Any]:
    allowed_shapes = (IDENTITY_FIELDS, IDENTITY_FIELDS | {"columns"}, IDENTITY_FIELDS | {"snapshotVersion", "columns"})
    if not isinstance(source, dict) or set(source) not in allowed_shapes:
        raise RelationSourceValidationError("source must be one exact relation identity")
    profile_id = source.get("profileId")
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise RelationSourceValidationError("source profile is invalid")
    if expected_profile_id is not None and profile_id != expected_profile_id:
        raise RelationSourceValidationError("source must use the requested profile")
    kind = source.get("kind")
    if kind not in RELATION_KINDS:
        raise RelationSourceValidationError("source kind must be a supported read-only relation kind")
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise RelationSourceValidationError("source fingerprint must be a 64-character lowercase hexadecimal fingerprint")
    normalized = {
        "profileId": profile_id,
        "database": _identifier(source.get("database"), "source database"),
        "namespace": _identifier(source.get("namespace"), "source namespace"),
        "relation": _identifier(source.get("relation"), "source relation"),
        "kind": kind,
        "fingerprint": fingerprint,
    }
    if "columns" not in source:
        return normalized
    snapshot_version = source.get("snapshotVersion", 1)
    if isinstance(snapshot_version, bool) or snapshot_version not in {1, 2}:
        raise RelationSourceValidationError("source snapshot version is invalid")
    columns = source["columns"]
    if not isinstance(columns, list) or len(columns) > 1600:
        raise RelationSourceValidationError("source columns must be a bounded catalog snapshot")
    names: set[str] = set()
    ordinals: set[int] = set()
    normalized_columns = []
    for column in columns:
        expected_fields = CURRENT_COLUMN_FIELDS if snapshot_version == 2 else LEGACY_COLUMN_FIELDS
        if not isinstance(column, dict) or set(column) != expected_fields:
            raise RelationSourceValidationError("source column snapshot is invalid")
        name = _identifier(column.get("name"), "source column")
        column_type = column.get("type")
        ordinal = column.get("ordinal")
        if (
            name in names
            or ordinal in ordinals
            or not isinstance(column_type, str)
            or not column_type
            or column_type != column_type.strip()
            or len(column_type) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in column_type)
            or not isinstance(column.get("nullable"), bool)
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not 1 <= ordinal <= 1600
        ):
            raise RelationSourceValidationError("source column snapshot is invalid")
        names.add(name)
        ordinals.add(ordinal)
        normalized_column = {
            "name": name,
            "type": column_type,
            "nullable": column["nullable"],
            "ordinal": ordinal,
        }
        if snapshot_version == 2:
            try:
                normalized_column["capabilities"] = normalize_capabilities(column.get("capabilities"))
            except CapabilityValidationError as exc:
                raise RelationSourceValidationError(str(exc)) from exc
        normalized_columns.append(normalized_column)
    if [column["ordinal"] for column in normalized_columns] != sorted(ordinals):
        raise RelationSourceValidationError("source column snapshot must use ordinal order")
    if snapshot_version == 2:
        normalized["snapshotVersion"] = 2
    normalized["columns"] = normalized_columns
    return normalized
