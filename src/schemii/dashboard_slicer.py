from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .widget_query import QueryValidationError, normalize_query


MAX_SLICERS = 16
MAX_SLICER_BINDINGS = 100
MAX_FILTER_GROUPS = 32
MAX_FILTER_CONDITIONS = 64


class SlicerValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_dashboard_slicer", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not all(character.isascii() and (character.isalnum() or character in "_-") for character in value)
    ):
        raise SlicerValidationError(f"{label} is invalid")
    return value


def _text(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SlicerValidationError(f"{label} must be a trimmed string up to {maximum} characters")
    return value


def _date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SlicerValidationError(f"{label} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SlicerValidationError(f"{label} must be an ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise SlicerValidationError(f"{label} must be a canonical ISO calendar date")
    return value


def _temporal_column(widget: dict[str, Any], source_column: Any) -> tuple[str, dict[str, Any]]:
    widget_id = widget["id"]
    configuration = widget.get("configuration", {})
    source = configuration.get("source")
    if not isinstance(source, dict) or source.get("snapshotVersion") != 2 or not isinstance(configuration.get("query"), dict):
        raise SlicerValidationError(
            f"Slicer binding for widget {widget_id} requires an executable widget with a current source capability snapshot"
        )
    if not isinstance(source_column, str):
        raise SlicerValidationError(f"Slicer binding for widget {widget_id} has an invalid source column")
    column = next((item for item in source.get("columns", []) if item.get("name") == source_column), None)
    capabilities = column.get("capabilities") if isinstance(column, dict) else None
    temporal = capabilities.get("temporal") if isinstance(capabilities, dict) else None
    operators = {
        item.get("name") for item in capabilities.get("filterOperators", [])
        if isinstance(item, dict)
    } if isinstance(capabilities, dict) else set()
    if temporal not in {"date", "timestamp", "timestamp_tz"} or not {"gte", "lt"} <= operators:
        raise SlicerValidationError(
            f"Slicer binding for widget {widget_id} must name an exact saved temporal source column with range capabilities"
        )
    return temporal, column


def normalize_dashboard_slicers(value: Any, widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_SLICERS:
        raise SlicerValidationError(f"Dashboard slicers must contain at most {MAX_SLICERS} items")
    widgets_by_id = {widget["id"]: widget for widget in widgets}
    slicer_ids: set[str] = set()
    bound_sources: set[tuple[str, str]] = set()
    binding_count = 0
    normalized = []
    for slicer in value:
        if not isinstance(slicer, dict) or set(slicer) != {"id", "kind", "title", "range", "bindings"}:
            raise SlicerValidationError("Date slicer fields are invalid")
        slicer_id = _identifier(slicer.get("id"), "Slicer ID")
        if slicer_id in slicer_ids:
            raise SlicerValidationError("Slicer ID is duplicated")
        slicer_ids.add(slicer_id)
        if slicer.get("kind") != "date_range":
            raise SlicerValidationError("Only explicit date-range slicers are supported")
        range_value = slicer.get("range")
        if not isinstance(range_value, dict) or set(range_value) != {"start", "endExclusive"}:
            raise SlicerValidationError("Date slicer range fields are invalid")
        start = _date(range_value.get("start"), "Date slicer start")
        end_exclusive = _date(range_value.get("endExclusive"), "Date slicer endExclusive")
        if date.fromisoformat(end_exclusive) <= date.fromisoformat(start):
            raise SlicerValidationError("Date slicer endExclusive must be after its inclusive start")
        bindings = slicer.get("bindings")
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= MAX_SLICER_BINDINGS:
            raise SlicerValidationError("Date slicer bindings must contain from 1 to 100 explicit widget bindings")
        normalized_bindings = []
        for binding in bindings:
            binding_count += 1
            if binding_count > MAX_SLICER_BINDINGS:
                raise SlicerValidationError(f"Dashboard slicers may contain at most {MAX_SLICER_BINDINGS} bindings")
            if not isinstance(binding, dict) or set(binding) not in (
                {"widgetId", "sourceColumn"},
                {"widgetId", "sourceColumn", "sourceTimeZone"},
            ):
                raise SlicerValidationError("Date slicer binding fields are invalid")
            widget_id = _identifier(binding.get("widgetId"), "Slicer widget ID")
            widget = widgets_by_id.get(widget_id)
            if widget is None:
                raise SlicerValidationError(f"Slicer binding references missing widget {widget_id}")
            source_column = binding.get("sourceColumn")
            temporal, _column = _temporal_column(widget, source_column)
            identity = (widget_id, source_column)
            if identity in bound_sources:
                raise SlicerValidationError("A widget source column cannot be bound to more than one date slicer")
            bound_sources.add(identity)
            source_time_zone = binding.get("sourceTimeZone")
            if temporal == "timestamp":
                source_time_zone = _text(source_time_zone, "sourceTimeZone", 128)
                try:
                    ZoneInfo(source_time_zone)
                except (ZoneInfoNotFoundError, ValueError) as exc:
                    raise SlicerValidationError("sourceTimeZone must name an available IANA time zone") from exc
                normalized_binding = {
                    "widgetId": widget_id,
                    "sourceColumn": source_column,
                    "sourceTimeZone": source_time_zone,
                }
            else:
                if "sourceTimeZone" in binding:
                    raise SlicerValidationError(
                        "sourceTimeZone is allowed only for timestamp-without-time-zone source semantics"
                    )
                normalized_binding = {"widgetId": widget_id, "sourceColumn": source_column}
            normalized_bindings.append(normalized_binding)
        normalized.append({
            "id": slicer_id,
            "kind": "date_range",
            "title": _text(slicer.get("title"), "Date slicer title", 128),
            "range": {"start": start, "endExclusive": end_exclusive},
            "bindings": normalized_bindings,
        })
    return normalized


def _generated_id(prefix: str, identity: tuple[Any, ...], used_ids: set[str]) -> str:
    attempt = 0
    while True:
        payload = json.dumps([*identity, attempt], ensure_ascii=True, separators=(",", ":"))
        candidate = f"{prefix}_{hashlib.sha256(payload.encode('ascii')).hexdigest()[:24]}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        attempt += 1


def _range_values(temporal: str, start: str, end_exclusive: str) -> tuple[str, str]:
    if temporal == "date":
        return start, end_exclusive
    if temporal == "timestamp":
        return f"{start}T00:00:00", f"{end_exclusive}T00:00:00"
    return f"{start}T00:00:00Z", f"{end_exclusive}T00:00:00Z"


def compose_dashboard_slicers(
    widgets: list[dict[str, Any]],
    slicers: list[dict[str, Any]],
    widget_id: str,
    query: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    widget = next((item for item in widgets if item["id"] == widget_id), None)
    if widget is None:
        raise SlicerValidationError("The slicer execution widget is missing", code="saved_widget_changed")
    source = widget.get("configuration", {}).get("source", {})
    source_columns = source.get("columns") if isinstance(source, dict) else None
    try:
        normalized_query = normalize_query(query, source_columns)
    except QueryValidationError as exc:
        raise SlicerValidationError(str(exc), code="invalid_widget_query") from exc
    matching = []
    for slicer in slicers:
        for binding in slicer["bindings"]:
            if binding["widgetId"] == widget_id:
                matching.append((slicer, binding))
    if not matching:
        return normalized_query, []

    groups = json.loads(json.dumps(normalized_query["filters"]))
    used_ids = {
        item["id"] for item in normalized_query["dimensions"] + normalized_query["measures"]
    }
    for group in groups:
        used_ids.add(group["id"])
        used_ids.update(item["id"] for item in group["conditions"])
    if not groups:
        groups = [{
            "id": _generated_id("slicer_group", (widget_id,), used_ids),
            "conditions": [],
        }]
    if len(groups) > MAX_FILTER_GROUPS:
        raise SlicerValidationError(
            "Dashboard slicers would exceed the structured query filter-group limit",
            code="slicer_query_limit",
            details={"limit": MAX_FILTER_GROUPS, "kind": "filterGroups"},
        )
    existing_conditions = sum(len(group["conditions"]) for group in groups)
    added_conditions = len(groups) * len(matching) * 2
    if existing_conditions + added_conditions > MAX_FILTER_CONDITIONS:
        raise SlicerValidationError(
            "Dashboard slicers would exceed the structured query condition limit",
            code="slicer_query_limit",
            details={
                "limit": MAX_FILTER_CONDITIONS,
                "existing": existing_conditions,
                "added": added_conditions,
                "kind": "filterConditions",
            },
        )

    lineage = []
    source_by_name = {column["name"]: column for column in source_columns}
    for slicer, binding in matching:
        temporal = source_by_name[binding["sourceColumn"]]["capabilities"]["temporal"]
        start, end_exclusive = _range_values(
            temporal, slicer["range"]["start"], slicer["range"]["endExclusive"]
        )
        condition_lineage = []
        for group in groups:
            start_id = _generated_id(
                "slicer_start", (slicer["id"], widget_id, binding["sourceColumn"], group["id"]), used_ids,
            )
            end_id = _generated_id(
                "slicer_end", (slicer["id"], widget_id, binding["sourceColumn"], group["id"]), used_ids,
            )
            group["conditions"].extend((
                {"id": start_id, "column": binding["sourceColumn"], "operator": "gte", "values": [start]},
                {"id": end_id, "column": binding["sourceColumn"], "operator": "lt", "values": [end_exclusive]},
            ))
            condition_lineage.append({
                "filterGroupId": group["id"],
                "startInclusiveConditionId": start_id,
                "endExclusiveConditionId": end_id,
            })
        lineage.append({
            "slicerId": slicer["id"],
            "widgetId": widget_id,
            "sourceColumn": binding["sourceColumn"],
            "temporalKind": temporal,
            "range": {"startInclusive": start, "endExclusive": end_exclusive},
            **({"sourceTimeZone": binding["sourceTimeZone"]} if "sourceTimeZone" in binding else {}),
            "conditions": condition_lineage,
        })
    effective = {**normalized_query, "filters": groups}
    try:
        effective = normalize_query(effective, source_columns)
    except QueryValidationError as exc:
        raise SlicerValidationError(str(exc), code="slicer_query_limit") from exc
    return effective, lineage


def bound_widget_ids(slicers: list[dict[str, Any]]) -> set[str]:
    return {
        binding["widgetId"]
        for slicer in slicers
        for binding in slicer["bindings"]
    }


def remove_widget_bindings(slicers: Any, widget_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(slicers, list):
        return slicers
    result = []
    for slicer in slicers:
        if not isinstance(slicer, dict) or not isinstance(slicer.get("bindings"), list):
            result.append(slicer)
            continue
        retained = [binding for binding in slicer["bindings"] if binding.get("widgetId") not in widget_ids]
        if retained:
            result.append({**slicer, "bindings": retained})
    return result
