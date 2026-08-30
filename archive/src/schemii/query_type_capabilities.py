from __future__ import annotations

import re
from typing import Any, Callable

from .postgres_common import canonical_fingerprint


CAPABILITY_VERSION = 1
FILTER_OPERATORS = (
    "eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in",
    "like", "contains", "starts_with", "ends_with", "is_null", "is_not_null",
)
AGGREGATES = ("count", "sum", "average", "minimum", "maximum")
OPERATOR_NAME_RE = re.compile(r"^[+\-*/<>=~!@#%^&|`?]{1,63}$")


class CapabilityValidationError(ValueError):
    pass


def _oid(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CapabilityValidationError(f"{label} must be a positive PostgreSQL OID")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 63 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CapabilityValidationError(f"{label} must be a PostgreSQL identifier")
    return value


def _identity(value: Any, label: str, *, operator: bool = False) -> dict[str, Any]:
    fields = {"oid", "namespace", "name", "inputTypeOid", "resultTypeOid", "catalogVersion"}
    if not isinstance(value, dict) or set(value) != fields:
        raise CapabilityValidationError(f"{label} identity is invalid")
    name = value.get("name")
    if operator:
        if not isinstance(name, str) or not OPERATOR_NAME_RE.fullmatch(name):
            raise CapabilityValidationError(f"{label} operator name is invalid")
    else:
        name = _identifier(name, f"{label} name")
    catalog_version = value.get("catalogVersion")
    if not isinstance(catalog_version, str) or not catalog_version or len(catalog_version) > 256:
        raise CapabilityValidationError(f"{label} catalog identity is invalid")
    return {
        "oid": _oid(value.get("oid"), f"{label} identity"),
        "namespace": _identifier(value.get("namespace"), f"{label} namespace"),
        "name": name,
        "inputTypeOid": _oid(value.get("inputTypeOid"), f"{label} input type"),
        "resultTypeOid": _oid(value.get("resultTypeOid"), f"{label} result type"),
        "catalogVersion": catalog_version,
    }


def _optional_catalog_identity(value: Any, label: str, fields: set[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != fields:
        raise CapabilityValidationError(f"{label} identity is invalid")
    result = {}
    for field in fields:
        item = value[field]
        if field.endswith("Oid") or field == "oid":
            result[field] = _oid(item, f"{label} {field}")
        elif field in {"namespace", "name"}:
            result[field] = _identifier(item, f"{label} {field}")
        elif field == "deterministic" and isinstance(item, bool):
            result[field] = item
        elif not isinstance(item, str) or len(item) > 256:
            raise CapabilityValidationError(f"{label} {field} is invalid")
        else:
            result[field] = item
    return result


def normalize_capabilities(value: Any) -> dict[str, Any]:
    fields = {
        "version", "declaredTypeOid", "baseTypeOid", "declaredType", "type", "collation", "array", "range",
        "groupable", "distinct", "sortable", "numeric", "filterOperators", "aggregates",
        "temporal", "capabilityFingerprint",
    }
    if not isinstance(value, dict) or set(value) != fields or value.get("version") != CAPABILITY_VERSION:
        raise CapabilityValidationError("column capabilities are invalid")
    type_value = value.get("type")
    declared_type_value = value.get("declaredType")
    type_fields = {"namespace", "name", "kind", "category", "catalogVersion"}
    if not isinstance(type_value, dict) or set(type_value) != type_fields or not isinstance(declared_type_value, dict) or set(declared_type_value) != type_fields:
        raise CapabilityValidationError("column type identity is invalid")
    kind = type_value.get("kind")
    category = type_value.get("category")
    if not isinstance(kind, str) or len(kind) != 1 or not isinstance(category, str) or len(category) != 1:
        raise CapabilityValidationError("column type classification is invalid")
    normalized_type = {
        "namespace": _identifier(type_value.get("namespace"), "type namespace"),
        "name": _identifier(type_value.get("name"), "type name"),
        "kind": kind,
        "category": category,
        "catalogVersion": str(type_value.get("catalogVersion")),
    }
    declared_kind = declared_type_value.get("kind")
    declared_category = declared_type_value.get("category")
    if not isinstance(declared_kind, str) or len(declared_kind) != 1 or not isinstance(declared_category, str) or len(declared_category) != 1:
        raise CapabilityValidationError("declared column type classification is invalid")
    normalized_declared_type = {
        "namespace": _identifier(declared_type_value.get("namespace"), "declared type namespace"),
        "name": _identifier(declared_type_value.get("name"), "declared type name"),
        "kind": declared_kind, "category": declared_category,
        "catalogVersion": str(declared_type_value.get("catalogVersion")),
    }
    booleans = ("groupable", "distinct", "sortable", "numeric")
    if any(not isinstance(value.get(field), bool) for field in booleans):
        raise CapabilityValidationError("column logical capabilities are invalid")
    temporal = value.get("temporal")
    if temporal not in {"none", "date", "timestamp", "timestamp_tz"}:
        raise CapabilityValidationError("column temporal classification is invalid")
    raw_filters = value.get("filterOperators")
    if not isinstance(raw_filters, list) or len(raw_filters) > len(FILTER_OPERATORS):
        raise CapabilityValidationError("column filter capabilities are invalid")
    filters = []
    seen = set()
    for item in raw_filters:
        if not isinstance(item, dict) or set(item) not in ({"name"}, {"name", "operator"}, {"name", "operators"}):
            raise CapabilityValidationError("column filter capability is invalid")
        name = item.get("name")
        if name not in FILTER_OPERATORS or name in seen:
            raise CapabilityValidationError("column filter capability is invalid")
        seen.add(name)
        normalized = {"name": name}
        if "operator" in item:
            normalized["operator"] = _identity(item["operator"], f"filter {name}", operator=True)
        if "operators" in item:
            operators = item["operators"]
            required = {"lower", "upper"}
            if not isinstance(operators, dict) or set(operators) != required:
                raise CapabilityValidationError("between filter identities are invalid")
            normalized["operators"] = {key: _identity(operators[key], f"filter {name} {key}", operator=True) for key in sorted(required)}
        if name not in {"is_null", "is_not_null"} and len(normalized) == 1:
            raise CapabilityValidationError("non-null filter requires a catalog operator")
        filters.append(normalized)
    if [item["name"] for item in filters] != [name for name in FILTER_OPERATORS if name in seen]:
        raise CapabilityValidationError("column filter capabilities must use stable logical order")
    raw_aggregates = value.get("aggregates")
    if not isinstance(raw_aggregates, list) or len(raw_aggregates) > len(AGGREGATES):
        raise CapabilityValidationError("column aggregate capabilities are invalid")
    aggregates = []
    seen = set()
    for item in raw_aggregates:
        if not isinstance(item, dict) or set(item) != {"name", "aggregate", "sortable", "zeroable"} or item.get("name") not in AGGREGATES or item["name"] in seen or not isinstance(item.get("sortable"), bool) or not isinstance(item.get("zeroable"), bool):
            raise CapabilityValidationError("column aggregate capability is invalid")
        seen.add(item["name"])
        aggregates.append({"name": item["name"], "aggregate": _identity(item["aggregate"], f"aggregate {item['name']}"), "sortable": item["sortable"], "zeroable": item["zeroable"]})
    if [item["name"] for item in aggregates] != [name for name in AGGREGATES if name in seen]:
        raise CapabilityValidationError("column aggregate capabilities must use stable logical order")
    fingerprint = value.get("capabilityFingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise CapabilityValidationError("column capability fingerprint is invalid")
    normalized = {
        "version": CAPABILITY_VERSION,
        "declaredTypeOid": _oid(value.get("declaredTypeOid"), "declared type"),
        "baseTypeOid": _oid(value.get("baseTypeOid"), "base type"),
        "declaredType": normalized_declared_type,
        "type": normalized_type,
        "collation": _optional_catalog_identity(value.get("collation"), "collation", {"oid", "namespace", "name", "provider", "deterministic", "version", "catalogVersion"}),
        "array": _optional_catalog_identity(value.get("array"), "array", {"typeOid", "elementTypeOid"}),
        "range": _optional_catalog_identity(value.get("range"), "range", {"typeOid", "subtypeOid", "multirangeTypeOid", "catalogVersion"}),
        **{field: value[field] for field in booleans},
        "filterOperators": filters,
        "aggregates": aggregates,
        "temporal": temporal,
        "capabilityFingerprint": fingerprint,
    }
    identity = {key: item for key, item in normalized.items() if key != "capabilityFingerprint"}
    if canonical_fingerprint(identity) != fingerprint:
        raise CapabilityValidationError("column capability fingerprint does not match its catalog identities")
    return normalized


def catalog_capabilities(column: dict[str, Any], operators: list[dict[str, Any]], aggregates: list[dict[str, Any]]) -> dict[str, Any]:
    """Project bounded logical operations from rows selected by catalog strategy/resolution queries."""
    by_name = {item["logical_name"]: item for item in operators}

    def operator_identity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "oid": int(row["operator_oid"]), "namespace": row["operator_namespace"], "name": row["operator_name"],
            "inputTypeOid": int(row["input_type_oid"]), "resultTypeOid": int(row["result_type_oid"]),
            "catalogVersion": str(row["catalog_version"]),
        }

    filters = []
    for logical in FILTER_OPERATORS:
        if logical in {"is_null", "is_not_null"}:
            filters.append({"name": logical})
        elif logical == "between":
            if "gte" in by_name and "lte" in by_name:
                filters.append({"name": logical, "operators": {"lower": operator_identity(by_name["gte"]), "upper": operator_identity(by_name["lte"])}})
        else:
            source_name = "like" if logical in {"like", "contains", "starts_with", "ends_with"} else "eq" if logical in {"in", "not_in"} else logical
            if source_name in by_name:
                filters.append({"name": logical, "operator": operator_identity(by_name[source_name])})
    aggregate_items = []
    for logical in AGGREGATES:
        row = next((item for item in aggregates if item["logical_name"] == logical), None)
        if row is not None:
            aggregate_items.append({
                "name": logical,
                "aggregate": {
                    "oid": int(row["aggregate_oid"]), "namespace": row["aggregate_namespace"], "name": row["aggregate_name"],
                    "inputTypeOid": int(row["input_type_oid"]), "resultTypeOid": int(row["result_type_oid"]),
                    "catalogVersion": str(row["catalog_version"]),
                },
                "sortable": bool(row.get("output_sortable")),
                "zeroable": bool(row.get("output_zeroable")),
            })
    base_namespace = column["base_type_namespace"]
    base_name = column["base_type_name"]
    temporal = "none"
    if base_namespace == "pg_catalog" and base_name == "date":
        temporal = "date"
    elif base_namespace == "pg_catalog" and base_name == "timestamp":
        temporal = "timestamp"
    elif base_namespace == "pg_catalog" and base_name == "timestamptz":
        temporal = "timestamp_tz"
    type_identity = {
        "namespace": base_namespace, "name": base_name, "kind": column["base_type_kind"],
        "category": column["base_type_category"], "catalogVersion": str(column["type_catalog_version"]),
    }
    declared_type_identity = {
        "namespace": column["declared_type_namespace"], "name": column["declared_type_name"],
        "kind": column["declared_type_kind"], "category": column["declared_type_category"],
        "catalogVersion": str(column["type_catalog_version"]),
    }
    value = {
        "version": CAPABILITY_VERSION,
        "declaredTypeOid": int(column["declared_type_oid"]), "baseTypeOid": int(column["base_type_oid"]),
        "declaredType": declared_type_identity, "type": type_identity, "collation": column.get("collation_identity"), "array": column.get("array_identity"),
        "range": column.get("range_identity"),
        "groupable": "eq" in by_name, "distinct": "eq" in by_name,
        "sortable": all(name in by_name for name in ("lt", "lte", "gt", "gte")),
        "numeric": column["base_type_category"] == "N", "filterOperators": filters,
        "aggregates": aggregate_items, "temporal": temporal,
    }
    value["capabilityFingerprint"] = canonical_fingerprint(value)
    return normalize_capabilities(value)


def snapshot_column(column: dict[str, Any]) -> dict[str, Any]:
    return {key: column[key] for key in ("name", "type", "nullable", "ordinal", "capabilities")}


def require_current_capabilities(columns: list[dict[str, Any]]) -> None:
    if any("capabilities" not in column for column in columns):
        raise CapabilityValidationError("The saved source uses a legacy column snapshot; reselect the source to use structured queries")


def operator_sql(identity: dict[str, Any], quote: Callable[[str], str]) -> str:
    normalized = _identity(identity, "operator", operator=True)
    return f"OPERATOR({quote(normalized['namespace'])}.{normalized['name']})"


def aggregate_sql(identity: dict[str, Any], quote: Callable[[str], str]) -> str:
    normalized = _identity(identity, "aggregate")
    return f"{quote(normalized['namespace'])}.{quote(normalized['name'])}"
