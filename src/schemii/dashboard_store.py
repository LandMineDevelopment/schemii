from __future__ import annotations

import json
import os
import re
import secrets
import hashlib
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_json import remove_file, write_json
from .dashboard_slicer import (
    SlicerValidationError,
    bound_widget_ids,
    normalize_dashboard_slicers,
    remove_widget_bindings,
)
from .file_lock import RefCountedKeyedFileGuard
from .http_limits import MAX_BODY_SIZE
from .relation_source import RelationSourceValidationError, normalize_relation_source
from .signed_json import decode_signed_json, encode_signed_json
from .widget_query import QueryValidationError, normalize_number_format, normalize_query


MAX_DASHBOARD_ID_LENGTH = 128
DASHBOARD_ID_PATTERN = re.compile(rf"^[A-Za-z0-9_-]{{1,{MAX_DASHBOARD_ID_LENGTH}}}$")
DASHBOARD_VERSION = 3
MAX_WIDGETS = 100
MAX_AI_RECEIPTS = 1024
MAX_DASHBOARD_BYTES = MAX_BODY_SIZE
DEFAULT_DASHBOARD_PAGE_SIZE = 50
MAX_DASHBOARD_PAGE_SIZE = 100
DASHBOARD_HEALTH_SCAN_BATCH = 8
TABLE_PAGE_SIZES = {10, 25, 50, 100}
VISUALIZATION_MODES = {"table", "kpi", "bar", "line", "donut"}


class DashboardStoreError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be a trimmed string up to {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} contains invalid characters")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be from {minimum} to {maximum}")
    return value


def _version_two_layout(value: Any, widget_id: str) -> int:
    if not isinstance(value, dict) or set(value) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} layout is invalid")
    desktop = value["desktop"]
    mobile = value["mobile"]
    if not isinstance(desktop, dict) or set(desktop) != {"x", "y", "width", "height"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} desktop layout is invalid")
    if not isinstance(mobile, dict) or set(mobile) != {"order"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} mobile layout is invalid")
    _integer(desktop["x"], "desktop x", 0, 1_000_000)
    _integer(desktop["y"], "desktop y", 0, 1_000_000)
    _integer(desktop["width"], "desktop width", 96, 4096)
    _integer(desktop["height"], "desktop height", 46, 4096)
    return _integer(mobile["order"], "mobile order", 0, 999)


def _version_one_layout(value: Any, widget_id: str) -> int:
    if not isinstance(value, dict) or set(value) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} legacy layout is invalid")
    desktop = value["desktop"]
    mobile = value["mobile"]
    if not isinstance(mobile, dict) or set(mobile) != {"order", "h"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} legacy mobile layout is invalid")
    if not isinstance(desktop, dict) or set(desktop) != {"x", "y", "w", "h"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} legacy desktop layout is invalid")
    x = _integer(desktop["x"], "desktop x", 0, 11)
    _integer(desktop["y"], "desktop y", 0, 999)
    width = _integer(desktop["w"], "desktop width", 1, 12)
    _integer(desktop["h"], "desktop height", 1, 50)
    if x + width > 12:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} extends past the dashboard grid")
    _integer(mobile["h"], "mobile height", 1, 50)
    return _integer(mobile["order"], "mobile order", 0, 999)


def migrate_dashboard_record(record: Any, dashboard_id: str | None = None) -> dict[str, Any]:
    if not isinstance(record, dict) or isinstance(record.get("version"), bool) or record.get("version") not in {1, 2}:
        return json.loads(json.dumps(record)) if isinstance(record, dict) else record
    allowed_record = {"id", "version", "revision", "updatedAt", "dashboard", "aiOperationReceipts"}
    if set(record) - allowed_record or not isinstance(record.get("dashboard"), dict):
        raise DashboardStoreError(400, "invalid_dashboard", "Legacy dashboard record fields are invalid")
    record_id = record.get("id")
    if not isinstance(record_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(record_id) or dashboard_id and record_id != dashboard_id:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard ID is invalid")
    dashboard = record["dashboard"]
    if set(dashboard) != {"title", "archived", "widgets", "slicers", "viewport"}:
        raise DashboardStoreError(400, "invalid_dashboard", "Legacy dashboard content fields are invalid")
    if record["version"] == 1 and dashboard.get("slicers") != []:
        raise DashboardStoreError(400, "invalid_dashboard", "Version 1 dashboard slicers are invalid")
    if not isinstance(dashboard.get("widgets"), list):
        raise DashboardStoreError(400, "invalid_dashboard", "Legacy dashboard widgets are invalid")
    migrated = json.loads(json.dumps(record))
    migrated["version"] = DASHBOARD_VERSION
    if migrated.get("updatedAt") is None:
        migrated.pop("updatedAt", None)
    ordered_widgets = []
    for index, widget in enumerate(migrated["dashboard"]["widgets"]):
        widget_id = widget.get("id") if isinstance(widget, dict) else "unknown"
        layout = widget.get("layout") if isinstance(widget, dict) else None
        order = _version_one_layout(layout, widget_id) if record["version"] == 1 else _version_two_layout(layout, widget_id)
        normalized_widget = json.loads(json.dumps(widget))
        normalized_widget.pop("layout", None)
        ordered_widgets.append((order, index, normalized_widget))
    migrated["dashboard"]["widgets"] = [item[2] for item in sorted(ordered_widgets, key=lambda item: (item[0], item[1]))]
    viewport = migrated["dashboard"].get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", "Legacy dashboard viewport is invalid")
    normalized_viewport = {}
    for mode in ("desktop", "mobile"):
        value = viewport[mode]
        if not isinstance(value, dict) or set(value) != {"x", "y"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Legacy dashboard viewport is invalid")
        _integer(value["x"], f"{mode} viewport x", 0, 1_000_000)
        normalized_viewport[mode] = {"y": _integer(value["y"], f"{mode} viewport y", 0, 1_000_000)}
    migrated["dashboard"]["viewport"] = normalized_viewport
    return migrated


def _operation_id(prefix: str, operation_id: str) -> str:
    return f"{prefix}_{hashlib.sha256(operation_id.encode()).hexdigest()[:20]}"


def _ai_placeholder_widget(operation_id: str, title: str) -> dict[str, Any]:
    return {"id": _operation_id("widget", operation_id), "kind": "placeholder", "title": title, "configuration": {}}


def _postgres_identifier(value: Any, field: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 63
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be a valid PostgreSQL identifier up to 63 bytes")
    return value


def _table_configuration(value: Any, query: dict[str, Any], widget_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"version", "columns", "pageSize"} or isinstance(value.get("version"), bool) or value.get("version") != 1:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table configuration is invalid")
    page_size = value.get("pageSize")
    if isinstance(page_size, bool) or page_size not in TABLE_PAGE_SIZES:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table page size is invalid")
    targets = {item["id"]: "dimension" for item in query["dimensions"]} | {item["id"]: "measure" for item in query["measures"]}
    columns = value.get("columns")
    if not isinstance(columns, list) or len(columns) != len(targets):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table columns must cover every query result field")
    normalized_columns = []
    seen = set()
    measure_seen = False
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"targetId", "width", "hidden", "pinned", "label"}:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table column is invalid")
        target_id = column.get("targetId")
        if not isinstance(target_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(target_id):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table target is invalid or duplicated")
        target_kind = targets.get(target_id)
        if target_kind is None or target_id in seen:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table target is invalid or duplicated")
        if target_kind == "dimension" and measure_seen:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table dimensions must precede measures")
        measure_seen = measure_seen or target_kind == "measure"
        if not isinstance(column.get("hidden"), bool) or not isinstance(column.get("pinned"), bool):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table column behavior is invalid")
        seen.add(target_id)
        normalized_columns.append({
            "targetId": target_id,
            "width": _integer(column.get("width"), "aggregate table column width", 64, 1024),
            "hidden": column["hidden"],
            "pinned": column["pinned"],
            "label": _bounded_text(column.get("label"), "aggregate table column label", 128),
        })
    if seen != set(targets):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table columns must cover every query result field")
    return {"version": 1, "columns": normalized_columns, "pageSize": page_size}


def _detail_configuration(value: Any, source_columns: list[dict[str, Any]], widget_id: str) -> dict[str, Any]:
    required = {"version", "columns", "defaultSort", "rowIdentifier", "pageSize"}
    if not isinstance(value, dict) or set(value) != required or isinstance(value.get("version"), bool) or value.get("version") != 1:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail configuration is invalid")
    page_size = value.get("pageSize")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size not in TABLE_PAGE_SIZES:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail page size is invalid")
    snapshot_columns = {column["name"]: column for column in source_columns}
    columns = value.get("columns")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 64:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail columns are invalid")
    normalized_columns = []
    configured_columns = set()
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"sourceColumn", "label", "width", "hidden", "searchable", "numberFormat"}:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail column is invalid")
        source_column = column.get("sourceColumn")
        if not isinstance(source_column, str) or source_column not in snapshot_columns or source_column in configured_columns:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail source column is invalid or duplicated")
        if not isinstance(column.get("hidden"), bool) or not isinstance(column.get("searchable"), bool):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail column behavior is invalid")
        configured_columns.add(source_column)
        try:
            number_format = normalize_number_format(column.get("numberFormat"))
        except QueryValidationError as exc:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail column format is invalid: {exc}") from exc
        normalized_columns.append({
            "sourceColumn": source_column,
            "label": _bounded_text(column.get("label"), "detail column label", 128),
            "width": _integer(column.get("width"), "detail column width", 64, 1024),
            "hidden": column["hidden"],
            "searchable": column["searchable"],
            "numberFormat": number_format,
        })
    if all(column["hidden"] for column in normalized_columns):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail report must display at least one column")
    default_sort = value.get("defaultSort")
    normalized_sort = None
    if default_sort is not None:
        if not isinstance(default_sort, dict) or set(default_sort) != {"sourceColumn", "direction", "nulls"}:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail default sort is invalid")
        source_column = default_sort.get("sourceColumn")
        direction = default_sort.get("direction")
        nulls = default_sort.get("nulls")
        if not isinstance(source_column, str) or source_column not in configured_columns or not isinstance(direction, str) or direction not in {"asc", "desc"} or not isinstance(nulls, str) or nulls not in {"first", "last"}:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail default sort is invalid")
        normalized_sort = {
            "sourceColumn": source_column,
            "direction": direction,
            "nulls": nulls,
        }
    row_identifier = value.get("rowIdentifier")
    if row_identifier is not None and (not isinstance(row_identifier, str) or row_identifier not in snapshot_columns):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} detail row identifier is invalid")
    return {
        "version": 1,
        "columns": normalized_columns,
        "defaultSort": normalized_sort,
        "rowIdentifier": row_identifier,
        "pageSize": page_size,
    }


def _visualization_configuration(value: Any, query: dict[str, Any], widget_id: str) -> dict[str, Any]:
    required = {"version", "mode", "selections"}
    if not isinstance(value, dict) or set(value) != required or isinstance(value.get("version"), bool) or value.get("version") != 1:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} visualization configuration is invalid")
    mode = value.get("mode")
    selections = value.get("selections")
    if not isinstance(mode, str) or mode not in VISUALIZATION_MODES or not isinstance(selections, dict) or set(selections) != {"kpi", "bar", "line", "donut"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} visualization mode or selections are invalid")
    dimension_ids = {item["id"] for item in query["dimensions"]}
    measure_ids = {item["id"] for item in query["measures"]}

    def dimension_id(selection: dict[str, Any], kind: str) -> str | None:
        selected = selection.get("dimensionId")
        if selected is not None and (not isinstance(selected, str) or selected not in dimension_ids):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} {kind} dimension is not in its query")
        return selected

    def selected_measures(selection: dict[str, Any], kind: str) -> list[str]:
        selected = selection.get("measureIds")
        if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected) or len(selected) != len(set(selected)) or any(item not in measure_ids for item in selected):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} {kind} measures are invalid")
        return selected

    kpi = selections["kpi"]
    if not isinstance(kpi, dict) or set(kpi) != {"measureIds"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} KPI selection is invalid")
    bar = selections["bar"]
    line = selections["line"]
    if not isinstance(bar, dict) or set(bar) != {"dimensionId", "measureIds"} or not isinstance(line, dict) or set(line) != {"dimensionId", "measureIds"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} chart selection is invalid")
    donut = selections["donut"]
    if not isinstance(donut, dict) or set(donut) != {"dimensionId", "measureId"} or not isinstance(donut.get("measureId"), str) or donut.get("measureId") not in measure_ids:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} donut selection is invalid")
    return {
        "version": 1,
        "mode": mode,
        "selections": {
            "kpi": {"measureIds": selected_measures(kpi, "KPI")},
            "bar": {"dimensionId": dimension_id(bar, "bar"), "measureIds": selected_measures(bar, "bar")},
            "line": {"dimensionId": dimension_id(line, "line"), "measureIds": selected_measures(line, "line")},
            "donut": {"dimensionId": dimension_id(donut, "donut"), "measureId": donut["measureId"]},
        },
    }


def _widget_configuration(value: Any, widget_id: str, widget_kind: str) -> dict[str, Any]:
    aggregate_fields = {"source", "query", "table", "visualization", "detail"}
    allowed = (set(), {"source"}, {"source", "query"}, {"source", "query", "table"}, {"source", "query", "visualization"}, {"source", "query", "table", "visualization"})
    allowed += tuple(fields | {"detail"} for fields in allowed if {"source", "query"} <= fields)
    if not isinstance(value, dict) or set(value) not in allowed:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} configuration must contain at most one source")
    if widget_kind == "aggregate_report" and (not {"source", "query"} <= set(value) or set(value) - aggregate_fields):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate report requires a source and query")
    if widget_kind != "aggregate_report" and ({"table", "visualization", "detail"} & set(value)):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} presentation requires an aggregate report")
    if not value:
        return {}
    try:
        normalized_source = normalize_relation_source(value["source"])
    except RelationSourceValidationError as exc:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source is invalid: {exc}") from exc
    normalized = {"source": normalized_source}
    if "query" in value:
        if "columns" not in normalized_source:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} query requires a source column snapshot")
        try:
            normalized["query"] = normalize_query(
                value["query"], normalized_source["columns"], allow_legacy_snapshot=True,
            )
        except QueryValidationError as exc:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} query is invalid: {exc}") from exc
    if "table" in value:
        normalized["table"] = _table_configuration(value["table"], normalized["query"], widget_id)
    if "visualization" in value:
        normalized["visualization"] = _visualization_configuration(value["visualization"], normalized["query"], widget_id)
    if "detail" in value:
        normalized["detail"] = _detail_configuration(value["detail"], normalized_source["columns"], widget_id)
    return normalized


def validate_dashboard_record(record: Any, dashboard_id: str | None = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard record must be an object")
    if record.get("version") in {1, 2} and not isinstance(record.get("version"), bool):
        record = migrate_dashboard_record(record, dashboard_id)
    allowed_record = {"id", "version", "revision", "updatedAt", "dashboard", "aiOperationReceipts"}
    if set(record) - allowed_record:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard record contains unknown fields")
    record_id = record.get("id")
    if not isinstance(record_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(record_id) or (dashboard_id and record_id != dashboard_id):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard ID is invalid")
    if record.get("version") != DASHBOARD_VERSION:
        raise DashboardStoreError(400, "invalid_dashboard", f"Dashboard version must be {DASHBOARD_VERSION}")
    revision = record.get("revision", 0)
    _integer(revision, "revision", 0, 2_147_483_647)
    dashboard = record.get("dashboard")
    if not isinstance(dashboard, dict):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard content must be an object")
    allowed_dashboard = {"title", "archived", "widgets", "slicers", "viewport"}
    if set(dashboard) != allowed_dashboard:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard content fields are invalid")
    title = _bounded_text(dashboard.get("title"), "title", 128)
    if not isinstance(dashboard.get("archived"), bool):
        raise DashboardStoreError(400, "invalid_dashboard", "archived must be true or false")
    widgets = dashboard.get("widgets")
    slicers = dashboard.get("slicers")
    if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS or not isinstance(slicers, list):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard widgets or slicers are invalid")
    normalized_widgets = []
    widget_ids = set()
    for widget in widgets:
        if not isinstance(widget, dict) or set(widget) != {"id", "kind", "title", "configuration"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget fields are invalid")
        widget_id = widget.get("id")
        if not isinstance(widget_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(widget_id) or widget_id in widget_ids:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget ID is invalid or duplicated")
        widget_ids.add(widget_id)
        kind = _bounded_text(widget.get("kind"), "widget kind", 64)
        if kind not in {"preview", "placeholder", "aggregate_report"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget kind is not supported by this dashboard version")
        normalized_widgets.append({
            "id": widget_id,
            "kind": kind,
            "title": _bounded_text(widget.get("title"), "widget title", 128),
            "configuration": _widget_configuration(widget.get("configuration"), widget_id, kind),
        })
    try:
        normalized_slicers = normalize_dashboard_slicers(slicers, normalized_widgets)
    except SlicerValidationError as exc:
        raise DashboardStoreError(400, exc.code, str(exc), **exc.details) from exc
    viewport = dashboard.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard viewport is invalid")
    normalized_viewport = {}
    for mode in ("desktop", "mobile"):
        value = viewport[mode]
        if not isinstance(value, dict) or set(value) != {"y"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Dashboard viewport is invalid")
        normalized_viewport[mode] = {
            "y": _integer(value["y"], f"{mode} viewport y", 0, 1_000_000),
        }
    if "updatedAt" in record and not isinstance(record.get("updatedAt"), str):
        raise DashboardStoreError(400, "invalid_dashboard", "updatedAt must be a string when present")
    if "aiOperationReceipts" in record and not isinstance(record.get("aiOperationReceipts"), dict):
        raise DashboardStoreError(400, "invalid_dashboard", "aiOperationReceipts must be an object when present")
    return {
        "id": record_id,
        "version": DASHBOARD_VERSION,
        "revision": revision,
        **({"updatedAt": record["updatedAt"]} if isinstance(record.get("updatedAt"), str) else {}),
        "dashboard": {
            "title": title,
            "archived": dashboard["archived"],
            "widgets": normalized_widgets,
            "slicers": normalized_slicers,
            "viewport": normalized_viewport,
        },
        **({"aiOperationReceipts": json.loads(json.dumps(record["aiOperationReceipts"]))} if isinstance(record.get("aiOperationReceipts"), dict) else {}),
    }


def mercury_dashboard_record() -> dict[str, Any]:
    titles = {
        "widget_revenue": "Gross revenue",
        "widget_orders": "Orders",
        "widget_average": "Average order",
        "widget_trend": "Revenue trend",
        "widget_status": "Order status",
        "widget_recent": "Recent orders",
    }
    widgets = []
    for widget_id in titles:
        widgets.append({
            "id": widget_id,
            "kind": "placeholder",
            "title": titles[widget_id],
            "configuration": {},
        })
    return {
        "id": "dashboard_mercury",
        "version": DASHBOARD_VERSION,
        "revision": 0,
        "dashboard": {
            "title": "Mercury overview",
            "archived": False,
            "widgets": widgets,
            "slicers": [],
            "viewport": {"desktop": {"y": 0}, "mobile": {"y": 0}},
        },
    }


class DashboardStore:
    def __init__(
        self,
        dashboard_dir: str | os.PathLike[str],
        *,
        read_only: bool = False,
        max_ai_receipts: int = MAX_AI_RECEIPTS,
    ):
        if isinstance(max_ai_receipts, bool) or not isinstance(max_ai_receipts, int) or max_ai_receipts < 1:
            raise ValueError("max_ai_receipts must be a positive integer")
        self.dashboard_dir = Path(dashboard_dir).expanduser()
        self.read_only = read_only
        self.max_ai_receipts = max_ai_receipts
        self.marker_path = self.dashboard_dir / ".examples_initialized"
        self.lock_dir = self.dashboard_dir / ".locks"
        self._guards = RefCountedKeyedFileGuard(lambda dashboard_id: self.lock_dir / f"{dashboard_id}.lock")
        self.receipt_dir = self.dashboard_dir / ".ai-receipts"
        self.summary_dir = self.dashboard_dir / ".summaries"
        self._cursor_secret = secrets.token_bytes(32)
        self._health_lock = threading.Lock()
        self._health_error: DashboardStoreError | None = None
        self._health_record_ids: set[str] = set()
        self._health_receipt_names: set[str] = set()
        self._health_scan: dict[str, Any] | None = None
        if not read_only:
            self._ensure_directory()
        self._initialize_health()

    def _ensure_directory(self) -> None:
        self.dashboard_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.summary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.dashboard_dir, 0o700)
        for path in self.dashboard_dir.glob("*.json"):
            os.chmod(path, 0o600)

    @staticmethod
    def _directory_entries(path: Path):
        try:
            return os.scandir(path)
        except FileNotFoundError:
            return iter(())

    def _new_health_scan(self) -> dict[str, Any]:
        return {
            "phase": "records", "entries": self._directory_entries(self.dashboard_dir),
            "recordIds": set(), "receiptNames": set(),
        }

    @staticmethod
    def _close_health_entries(scan: dict[str, Any]) -> None:
        close = getattr(scan.get("entries"), "close", None)
        if close is not None:
            close()

    def _health_scan_step(self, maximum_entries: int) -> bool:
        if self._health_scan is None:
            self._health_scan = self._new_health_scan()
        scan = self._health_scan
        inspected = 0
        while inspected < maximum_entries:
            try:
                entry = next(scan["entries"])
            except StopIteration:
                self._close_health_entries(scan)
                if scan["phase"] == "records":
                    scan["phase"] = "receipts"
                    scan["entries"] = self._directory_entries(self.receipt_dir)
                    continue
                self._health_record_ids = scan["recordIds"]
                self._health_receipt_names = scan["receiptNames"]
                self._health_error = None
                self._health_scan = None
                return True
            inspected += 1
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            if scan["phase"] == "records":
                try:
                    self._read(path)
                except DashboardStoreError as exc:
                    # A concurrent delete may remove a name returned by scandir before it is opened.
                    if not isinstance(exc.__cause__, FileNotFoundError):
                        raise
                    scan["recordIds"].discard(path.stem)
                    continue
                scan["recordIds"].add(path.stem)
            else:
                try:
                    self._read_archived_receipt(path)
                except DashboardStoreError as exc:
                    if not isinstance(exc.__cause__, FileNotFoundError):
                        raise
                    scan["receiptNames"].discard(path.name)
                    continue
                scan["receiptNames"].add(path.name)
        return False

    def _initialize_health(self) -> None:
        try:
            while not self._health_scan_step(256):
                pass
            if not self.read_only and (
                not self.dashboard_dir.is_dir() or not self.lock_dir.is_dir() or not self.receipt_dir.is_dir()
            ):
                raise OSError("dashboard store directories are unavailable")
        except DashboardStoreError as exc:
            self._health_error = exc
            if self._health_scan is not None:
                self._close_health_entries(self._health_scan)
            self._health_scan = None
        except OSError as exc:
            self._health_error = DashboardStoreError(
                500, "dashboard_store_error", "Dashboard store is unavailable",
            )
            self._health_error.__cause__ = exc
            if self._health_scan is not None:
                self._close_health_entries(self._health_scan)
            self._health_scan = None

    def _record_health_write(self, dashboard_id: str) -> None:
        with self._health_lock:
            self._health_record_ids.add(dashboard_id)
            if self._health_scan is not None:
                self._health_scan["recordIds"].add(dashboard_id)

    def _record_health_receipt(self, path: Path) -> None:
        with self._health_lock:
            self._health_receipt_names.add(path.name)
            if self._health_scan is not None:
                self._health_scan["receiptNames"].add(path.name)

    def close(self) -> None:
        with self._health_lock:
            if self._health_scan is not None:
                self._close_health_entries(self._health_scan)
                self._health_scan = None

    def __del__(self):
        scan = getattr(self, "_health_scan", None)
        if scan is not None:
            self._close_health_entries(scan)

    @staticmethod
    def validate_id(dashboard_id: Any) -> str:
        if not isinstance(dashboard_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(dashboard_id):
            raise DashboardStoreError(404, "not_found", "Unknown dashboard path")
        return dashboard_id

    def _path(self, dashboard_id: str) -> Path:
        return self.dashboard_dir / f"{dashboard_id}.json"

    @staticmethod
    def _receipt_name(operation_id: str) -> str:
        return hashlib.sha256(operation_id.encode("utf-8")).hexdigest() + ".json"

    def _receipt_path(self, operation_id: str) -> Path:
        return self.receipt_dir / self._receipt_name(operation_id)

    @staticmethod
    def _validate_operation_id(operation_id: Any) -> str:
        if (
            not isinstance(operation_id, str) or not operation_id or len(operation_id.encode("utf-8")) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in operation_id)
        ):
            raise DashboardStoreError(400, "invalid_operation_id", "Dashboard operation identity is invalid")
        return operation_id

    def _read_archived_receipt(self, path: Path, operation_id: str | None = None) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DashboardStoreError(500, "dashboard_receipt_archive_error", "Dashboard receipt archive is malformed") from exc
        base_fields = {"version", "operationId", "dashboardId", "state"}
        if (
            not isinstance(payload, dict) or set(payload) not in (base_fields, base_fields | {"receipt"})
            or payload.get("version") != 1 or payload.get("state") not in {"tracked", "applied"}
            or not isinstance(payload.get("operationId"), str)
            or not isinstance(payload.get("dashboardId"), str) or not DASHBOARD_ID_PATTERN.fullmatch(payload["dashboardId"])
            or payload["state"] == "tracked" and "receipt" in payload
            or payload["state"] == "applied" and not isinstance(payload.get("receipt"), dict)
            or operation_id is not None and payload["operationId"] != operation_id
            or path.name != self._receipt_name(payload["operationId"])
        ):
            raise DashboardStoreError(500, "dashboard_receipt_archive_error", "Dashboard receipt archive is malformed")
        return payload

    def _track_operation(self, dashboard_id: str, operation_id: str) -> None:
        operation_id = self._validate_operation_id(operation_id)
        payload = {
            "version": 1, "operationId": operation_id, "dashboardId": dashboard_id, "state": "tracked",
        }
        path = self._receipt_path(operation_id)
        try:
            if path.is_file():
                existing = self._read_archived_receipt(path, operation_id)
                if existing["dashboardId"] != dashboard_id:
                    raise DashboardStoreError(500, "dashboard_receipt_archive_conflict", "Dashboard receipt archive conflicts with authoritative state")
                return
            write_json(path, payload, mode=0o600)
            self._record_health_receipt(path)
        except DashboardStoreError:
            raise
        except OSError as exc:
            raise DashboardStoreError(500, "dashboard_receipt_archive_error", "Dashboard operation could not be tracked durably") from exc

    def _archive_receipt(self, dashboard_id: str, operation_id: str, receipt: dict[str, Any]) -> None:
        operation_id = self._validate_operation_id(operation_id)
        payload = {
            "version": 1,
            "operationId": operation_id,
            "dashboardId": dashboard_id,
            "state": "applied",
            "receipt": json.loads(json.dumps(receipt)),
        }
        path = self._receipt_path(operation_id)
        try:
            if path.is_file():
                existing = self._read_archived_receipt(path, operation_id)
                if existing["dashboardId"] != dashboard_id or existing["state"] == "applied" and existing != payload:
                    raise DashboardStoreError(500, "dashboard_receipt_archive_conflict", "Dashboard receipt archive conflicts with authoritative state")
                if existing["state"] == "applied":
                    return
            write_json(path, payload, mode=0o600)
            self._record_health_receipt(path)
        except DashboardStoreError:
            raise
        except OSError as exc:
            raise DashboardStoreError(500, "dashboard_receipt_archive_error", "Dashboard receipt could not be archived") from exc

    def _append_receipt(self, record: dict[str, Any], operation_id: str, receipt: dict[str, Any]) -> None:
        operation_id = self._validate_operation_id(operation_id)
        receipts = record.setdefault("aiOperationReceipts", {})
        receipts[operation_id] = receipt
        while len(receipts) > self.max_ai_receipts:
            archived_id = next(iter(receipts))
            self._archive_receipt(record["id"], archived_id, receipts[archived_id])
            del receipts[archived_id]

    def operation_receipt(self, dashboard_id: str, operation_id: str) -> dict[str, Any] | None:
        return self.operation_receipt_evidence(dashboard_id, operation_id)["receipt"]

    def operation_receipt_evidence(self, dashboard_id: str, operation_id: str) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        operation_id = self._validate_operation_id(operation_id)
        archived_path = self._receipt_path(operation_id)
        tracked = False
        if archived_path.is_file():
            payload = self._read_archived_receipt(archived_path, operation_id)
            if payload["state"] == "applied":
                return {"receipt": payload["receipt"], "source": "archive", "archiveComplete": True}
            tracked = True
        try:
            receipt = self.get(dashboard_id).get("aiOperationReceipts", {}).get(operation_id)
        except DashboardStoreError as error:
            if error.status != 404:
                raise
            receipt = None
        if receipt is not None:
            return {"receipt": receipt, "source": "dashboard", "archiveComplete": True}
        for path in sorted(self.dashboard_dir.glob("*.json")):
            if path.stem == dashboard_id:
                continue
            receipt = self._read(path).get("aiOperationReceipts", {}).get(operation_id)
            if receipt is not None:
                return {"receipt": receipt, "source": "dashboard", "archiveComplete": True}
        # Every evicted or deleted receipt is archived before the authoritative dashboard write/removal.
        return {"receipt": None, "source": None, "archiveComplete": tracked}

    @contextmanager
    def _guard(self, dashboard_id: str):
        if self.read_only:
            raise DashboardStoreError(403, "dashboard_store_read_only", "This dashboard store is read-only")
        with self._guards.exclusive(dashboard_id):
            yield

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            return validate_dashboard_record(json.loads(path.read_text(encoding="utf-8")), path.stem)
        except DashboardStoreError as exc:
            raise DashboardStoreError(
                500, "dashboard_record_malformed", "A saved dashboard record is malformed", dashboardId=path.stem,
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DashboardStoreError(
                500, "dashboard_record_malformed", "A saved dashboard record is malformed", dashboardId=path.stem,
            ) from exc

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"], "revision": record["revision"], "updatedAt": record.get("updatedAt"),
            "title": record["dashboard"]["title"], "archived": record["dashboard"]["archived"],
            "widgetCount": len(record["dashboard"]["widgets"]),
        }

    @staticmethod
    def _source_identity(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {
            "device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size,
            "modifiedNs": stat.st_mtime_ns,
        }

    def _summary_path(self, dashboard_id: str) -> Path:
        return self.summary_dir / f"{dashboard_id}.json"

    def _cache_summary(self, record: dict[str, Any]) -> None:
        if self.read_only:
            return
        path = self._path(record["id"])
        try:
            write_json(self._summary_path(record["id"]), {
                "version": 1, "source": self._source_identity(path), "summary": self._summary(record),
            }, mode=0o600)
        except OSError:
            # The cache is never authoritative; a later summary read safely falls back to the dashboard record.
            pass

    def _read_summary(self, path: Path) -> dict[str, Any]:
        try:
            source = self._source_identity(path)
        except OSError as exc:
            raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be inspected") from exc
        cache_path = self._summary_path(path.stem)
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    isinstance(cached, dict) and set(cached) == {"version", "source", "summary"}
                    and cached["version"] == 1 and cached["source"] == source
                    and isinstance(cached["summary"], dict)
                ):
                    summary = cached["summary"]
                    required = {"id", "revision", "updatedAt", "title", "archived", "widgetCount"}
                    if (
                        set(summary) == required and summary["id"] == path.stem
                        and isinstance(summary["revision"], int) and not isinstance(summary["revision"], bool)
                        and isinstance(summary["title"], str) and isinstance(summary["archived"], bool)
                        and isinstance(summary["widgetCount"], int) and summary["widgetCount"] >= 0
                        and (summary["updatedAt"] is None or isinstance(summary["updatedAt"], str))
                    ):
                        return summary
            except (OSError, json.JSONDecodeError):
                pass
        record = self._read(path)
        try:
            if self._source_identity(path) != source:
                raise DashboardStoreError(409, "dashboard_list_changed", "Dashboard records changed while they were being listed")
        except OSError as exc:
            raise DashboardStoreError(409, "dashboard_list_changed", "Dashboard records changed while they were being listed") from exc
        self._cache_summary(record)
        return self._summary(record)

    def _ordered_summaries(self) -> list[dict[str, Any]]:
        summaries = [self._read_summary(path) for path in sorted(self.dashboard_dir.glob("*.json"))]
        return sorted(summaries, key=lambda item: (item["archived"], item["title"].lower(), item["id"]))

    @staticmethod
    def _page_size(value: Any) -> int:
        if value is None:
            return DEFAULT_DASHBOARD_PAGE_SIZE
        if isinstance(value, bool):
            raise DashboardStoreError(400, "invalid_dashboard_page_size", "pageSize must be an integer from 1 to 100")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise DashboardStoreError(400, "invalid_dashboard_page_size", "pageSize must be an integer from 1 to 100") from exc
        if str(parsed) != str(value) or not 1 <= parsed <= MAX_DASHBOARD_PAGE_SIZE:
            raise DashboardStoreError(400, "invalid_dashboard_page_size", "pageSize must be an integer from 1 to 100")
        return parsed

    @staticmethod
    def _list_fingerprint(summaries: list[dict[str, Any]]) -> str:
        encoded = json.dumps(summaries, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _encode_cursor(self, kind: str, page_size: int, fingerprint: str, after: str) -> str:
        return encode_signed_json(self._cursor_secret, {
            "v": 1, "kind": kind, "pageSize": page_size, "fingerprint": fingerprint, "after": after,
        })

    def _decode_cursor(self, cursor: Any, kind: str, page_size: int, fingerprint: str) -> str | None:
        if cursor is None:
            return None
        try:
            payload = decode_signed_json(self._cursor_secret, cursor)
        except ValueError:
            raise DashboardStoreError(400, "invalid_dashboard_cursor", "The dashboard cursor is malformed") from None
        if not isinstance(payload, dict) or set(payload) != {"v", "kind", "pageSize", "fingerprint", "after"} or payload["v"] != 1 or not isinstance(payload["after"], str):
            raise DashboardStoreError(400, "invalid_dashboard_cursor", "The dashboard cursor is malformed")
        if payload["kind"] != kind or payload["pageSize"] != page_size:
            raise DashboardStoreError(409, "dashboard_cursor_mismatch", "The dashboard cursor belongs to a different list or page size")
        if payload["fingerprint"] != fingerprint:
            raise DashboardStoreError(409, "dashboard_cursor_stale", "Dashboards changed; restart dashboard paging")
        return payload["after"]

    def list_page(self, *, summaries: bool, page_size: Any = None, cursor: Any = None) -> dict[str, Any]:
        size = self._page_size(page_size)
        ordered = self._ordered_summaries()
        fingerprint = self._list_fingerprint(ordered)
        after = self._decode_cursor(cursor, "summaries" if summaries else "dashboards", size, fingerprint)
        start = 0
        if after is not None:
            positions = [index for index, item in enumerate(ordered) if item["id"] == after]
            if len(positions) != 1:
                raise DashboardStoreError(409, "dashboard_cursor_stale", "Dashboards changed; restart dashboard paging")
            start = positions[0] + 1
        selected = ordered[start:start + size]
        values = selected if summaries else [self.get(item["id"]) for item in selected]
        if self._list_fingerprint(self._ordered_summaries()) != fingerprint:
            raise DashboardStoreError(409, "dashboard_list_changed", "Dashboard records changed while they were being listed")
        has_more = start + len(selected) < len(ordered)
        next_cursor = self._encode_cursor(
            "summaries" if summaries else "dashboards", size, fingerprint, selected[-1]["id"],
        ) if has_more and selected else None
        return {
            "items": values,
            "page": {"pageSize": size, "returned": len(values), "hasMore": has_more, "nextCursor": next_cursor},
        }

    def list(self) -> list[dict[str, Any]]:
        return [self.get(item["id"]) for item in self._ordered_summaries()]

    def list_summaries(self) -> list[dict[str, Any]]:
        return self._ordered_summaries()

    def get(self, dashboard_id: str) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        with self._guards.thread(dashboard_id):
            path = self._path(dashboard_id)
            if not path.is_file():
                raise DashboardStoreError(404, "not_found", "Dashboard was not found")
            return self._read(path)

    @contextmanager
    def guard_revision(self, dashboard_id: str, expected_revision: int):
        dashboard_id = self.validate_id(dashboard_id)
        if self.read_only:
            record = self.get(dashboard_id)
        else:
            with self._guard(dashboard_id):
                record = self.get(dashboard_id)
        if record["revision"] != expected_revision:
            raise DashboardStoreError(409, "dashboard_changed", "Dashboard changed before the operation could run")
        # Yield only the validated snapshot. Callers must perform PostgreSQL work after the guard is released.
        yield record

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        destination = self._path(record["id"])
        try:
            serialized_size = len(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise DashboardStoreError(400, "invalid_dashboard", "Dashboard record cannot be serialized") from exc
        if serialized_size > MAX_DASHBOARD_BYTES:
            raise DashboardStoreError(
                413, "dashboard_too_large", "Dashboard exceeds the serialized size limit",
                limit=MAX_DASHBOARD_BYTES, actual=serialized_size,
            )
        try:
            write_json(destination, record, mode=0o600)
        except OSError as exc:
            raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be saved") from exc
        self._cache_summary(record)
        self._record_health_write(record["id"])
        return json.loads(json.dumps(record))

    def health(self) -> dict[str, Any]:
        if self._health_lock.acquire(blocking=False):
            try:
                try:
                    self._health_scan_step(DASHBOARD_HEALTH_SCAN_BATCH)
                    if not self.read_only and (
                        not self.dashboard_dir.is_dir() or not self.lock_dir.is_dir() or not self.receipt_dir.is_dir()
                    ):
                        raise OSError("dashboard store directories are unavailable")
                except DashboardStoreError as exc:
                    self._health_error = exc
                    if self._health_scan is not None:
                        self._close_health_entries(self._health_scan)
                    self._health_scan = None
                except OSError as exc:
                    self._health_error = DashboardStoreError(
                        500, "dashboard_store_error", "Dashboard store is unavailable",
                    )
                    self._health_error.__cause__ = exc
                    if self._health_scan is not None:
                        self._close_health_entries(self._health_scan)
                    self._health_scan = None
            finally:
                self._health_lock.release()
        if self._health_error is not None:
            raise self._health_error
        return {
            "ok": True,
            "recordCount": len(self._health_record_ids),
            "receiptArchiveCount": len(self._health_receipt_names),
            "maxRecordBytes": MAX_DASHBOARD_BYTES,
        }

    def create(self, title: Any, source_id: Any = None) -> dict[str, Any]:
        title = _bounded_text(title, "title", 128)
        with self._guard("dashboard_create"):
            if source_id is None:
                dashboard = {
                    "title": title,
                    "archived": False,
                    "widgets": [],
                    "slicers": [],
                    "viewport": {"desktop": {"y": 0}, "mobile": {"y": 0}},
                }
            else:
                source = self.get(self.validate_id(source_id))
                dashboard = json.loads(json.dumps(source["dashboard"]))
                dashboard["title"] = title
                dashboard["archived"] = False
            record = validate_dashboard_record({
                "id": "dashboard_" + secrets.token_hex(8),
                "version": DASHBOARD_VERSION,
                "revision": 0,
                "dashboard": dashboard,
            })
            record["revision"] = 1
            record["updatedAt"] = _utc_now()
            return self._write(record)

    def create_ai(self, operation_id: str, title: Any) -> dict[str, Any]:
        title = _bounded_text(title, "title", 128)
        dashboard_id = f"dashboard_{hashlib.sha256(f'dashboard:{operation_id}'.encode()).hexdigest()[:20]}"
        with self._guard(dashboard_id):
            path = self._path(dashboard_id)
            if path.exists():
                receipt = self.operation_receipt(dashboard_id, operation_id)
                if receipt: return receipt
                raise DashboardStoreError(409, "dashboard_conflict", "Generated dashboard identity is in use")
            record = validate_dashboard_record({"id": dashboard_id, "version": DASHBOARD_VERSION, "revision": 0, "dashboard": {"title": title, "archived": False, "widgets": [], "slicers": [], "viewport": {"desktop": {"y": 0}, "mobile": {"y": 0}}}})
            record.update(revision=1, updatedAt=_utc_now())
            receipt = {"kind": "dashboard_saved", "dashboardId": dashboard_id, "revision": 1, "actionType": "dashboard_create"}
            self._track_operation(dashboard_id, operation_id)
            self._append_receipt(record, operation_id, receipt)
            self._write(validate_dashboard_record(record, dashboard_id))
            return receipt

    def apply_ai_mutation(self, dashboard_id: str, operation_id: str, expected_revision: int, action: dict[str, Any], prepared_widget: dict[str, Any] | None = None) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        with self._guard(dashboard_id):
            current = self.get(dashboard_id)
            receipt = current.get("aiOperationReceipts", {}).get(operation_id)
            if receipt is None:
                archived_path = self._receipt_path(operation_id)
                if archived_path.is_file():
                    archived = self._read_archived_receipt(archived_path, operation_id)
                    if archived["dashboardId"] == dashboard_id and archived["state"] == "applied":
                        receipt = archived["receipt"]
            if receipt: return receipt
            if current["revision"] != expected_revision:
                raise DashboardStoreError(409, "dashboard_changed", "Dashboard changed before the operation could run")
            self._track_operation(dashboard_id, operation_id)
            stored = json.loads(json.dumps(current))
            widgets = stored["dashboard"]["widgets"]
            action_type = action["type"]
            changed_id = None
            if action_type == "widget_create":
                if len(widgets) >= MAX_WIDGETS: raise DashboardStoreError(409, "dashboard_full", "Dashboard has the maximum number of widgets")
                widget = json.loads(json.dumps(prepared_widget)) if prepared_widget else _ai_placeholder_widget(operation_id, action["title"])
                widget.pop("layout", None)
                widgets.append(widget); changed_id = widget["id"]
            else:
                matches = [item for item in widgets if item["id"] == action["widgetId"]]
                if len(matches) != 1 or matches[0]["title"] != action["currentTitle"]:
                    raise DashboardStoreError(409, "dashboard_changed", "Target widget changed")
                widget = matches[0]
                if action_type == "widget_rename": widget["title"] = action["title"]; changed_id = widget["id"]
                elif action_type == "widget_duplicate":
                    duplicate = json.loads(json.dumps(widget)); duplicate["id"] = _operation_id("widget", operation_id); duplicate["title"] = action["title"]
                    duplicate.pop("layout", None); widgets.append(duplicate); changed_id = duplicate["id"]
                elif action_type == "widget_delete":
                    if widget["id"] in bound_widget_ids(stored["dashboard"]["slicers"]):
                        raise DashboardStoreError(
                            409, "slicer_binding_affected",
                            "AI cannot delete a widget while an explicit dashboard slicer binding targets it",
                            widgetIds=[widget["id"]], bindingAction="reject",
                        )
                    widgets.remove(widget); changed_id = widget["id"]
            if action_type == "widget_delete":
                pass
            stored["revision"] = expected_revision + 1; stored["updatedAt"] = _utc_now()
            receipt = {"kind": "dashboard_saved", "dashboardId": dashboard_id, "revision": stored["revision"], "actionType": action_type, "widgetId": changed_id}
            self._append_receipt(stored, operation_id, receipt)
            self._write(validate_dashboard_record(stored, dashboard_id))
            return receipt

    @staticmethod
    def _binding_action(value: Any) -> str:
        if value not in {"reject", "remove"}:
            raise DashboardStoreError(400, "invalid_binding_action", "bindingAction must be reject or remove")
        return value

    @staticmethod
    def _source_by_widget(record: dict[str, Any]) -> dict[str, Any]:
        return {
            widget["id"]: widget.get("configuration", {}).get("source")
            for widget in record["dashboard"]["widgets"]
        }

    @classmethod
    def _resolve_affected_bindings(
        cls, current: dict[str, Any], incoming: dict[str, Any], binding_action: str,
    ) -> dict[str, Any]:
        current_bound = bound_widget_ids(current["dashboard"]["slicers"])
        if not current_bound:
            return incoming
        current_sources = cls._source_by_widget(current)
        incoming_widgets = incoming.get("dashboard", {}).get("widgets", []) if isinstance(incoming, dict) else []
        incoming_sources = {
            widget.get("id"): widget.get("configuration", {}).get("source")
            for widget in incoming_widgets if isinstance(widget, dict)
        }
        affected = {
            widget_id for widget_id in current_bound
            if widget_id not in incoming_sources or incoming_sources[widget_id] != current_sources.get(widget_id)
        }
        if not affected:
            return incoming
        candidate = json.loads(json.dumps(incoming))
        incoming_bound = {
            binding.get("widgetId")
            for slicer in candidate.get("dashboard", {}).get("slicers", []) if isinstance(slicer, dict)
            for binding in slicer.get("bindings", []) if isinstance(binding, dict)
        }
        still_bound = affected & incoming_bound
        if still_bound and binding_action == "reject":
            raise DashboardStoreError(
                409, "slicer_binding_affected",
                "Widget deletion or source replacement affects explicit dashboard slicer bindings",
                widgetIds=sorted(still_bound), bindingAction="reject",
            )
        if still_bound:
            candidate["dashboard"]["slicers"] = remove_widget_bindings(
                candidate["dashboard"]["slicers"], still_bound,
            )
        return candidate

    def save(self, dashboard_id: str, incoming: Any, binding_action: Any = "reject") -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        binding_action = self._binding_action(binding_action)
        with self._guard(dashboard_id):
            if not self._path(dashboard_id).is_file():
                validate_dashboard_record(incoming, dashboard_id)
            current = self.get(dashboard_id)
            incoming = self._resolve_affected_bindings(current, incoming, binding_action)
            record = validate_dashboard_record(incoming, dashboard_id)
            if record["revision"] != current["revision"]:
                raise DashboardStoreError(
                    409,
                    "dashboard_conflict",
                    "Dashboard changed in another session; reload before saving",
                    currentRevision=current["revision"],
                )
            record["revision"] = current["revision"] + 1
            record["updatedAt"] = _utc_now()
            if current.get("aiOperationReceipts"):
                record["aiOperationReceipts"] = json.loads(json.dumps(current["aiOperationReceipts"]))
            else:
                record.pop("aiOperationReceipts", None)
            return self._write(record)

    def upgrade_legacy_sources(
        self, dashboard_id: str, expected_revision: int, replacements: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise DashboardStoreError(400, "validation_error", "Legacy source revision is invalid")
        if not isinstance(replacements, dict) or not replacements or len(replacements) > MAX_WIDGETS:
            raise DashboardStoreError(400, "validation_error", "Legacy source replacements are empty")
        normalized_replacements = {}
        try:
            for widget_id, replacement in replacements.items():
                if (
                    not isinstance(widget_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(widget_id)
                    or not isinstance(replacement, dict) or set(replacement) != {"expectedSource", "source"}
                ):
                    raise RelationSourceValidationError("legacy source replacement fields are invalid")
                expected_source = normalize_relation_source(replacement["expectedSource"])
                source = normalize_relation_source(replacement["source"])
                if expected_source.get("snapshotVersion", 1) != 1 or "columns" not in expected_source:
                    raise RelationSourceValidationError("expected source must be a version 1 column snapshot")
                if source.get("snapshotVersion") != 2:
                    raise RelationSourceValidationError("replacement source must be a version 2 capability snapshot")
                if any(source[key] != expected_source[key] for key in ("profileId", "database", "namespace", "relation")):
                    raise RelationSourceValidationError("replacement source must retain the exact relation target")
                if source["kind"] != expected_source["kind"] and not (
                    expected_source["kind"] == "table" and source["kind"] == "partitioned_table"
                ):
                    raise RelationSourceValidationError("replacement source kind does not match the reviewed legacy relation")
                normalized_replacements[widget_id] = {"expectedSource": expected_source, "source": source}
        except (KeyError, RelationSourceValidationError) as exc:
            raise DashboardStoreError(400, "validation_error", f"Legacy source replacement is invalid: {exc}") from exc
        with self._guard(dashboard_id):
            current = self.get(dashboard_id)
            if current["revision"] != expected_revision:
                raise DashboardStoreError(409, "dashboard_changed", "Dashboard changed before legacy sources could be upgraded")
            stored = json.loads(json.dumps(current))
            widgets = {widget["id"]: widget for widget in stored["dashboard"]["widgets"]}
            for widget_id, replacement in normalized_replacements.items():
                widget = widgets.get(widget_id)
                if widget is None or widget.get("configuration", {}).get("source") != replacement["expectedSource"]:
                    raise DashboardStoreError(409, "dashboard_changed", "A reviewed legacy widget changed before it could be upgraded")
                widget["configuration"]["source"] = json.loads(json.dumps(replacement["source"]))
            stored["revision"] = current["revision"] + 1
            stored["updatedAt"] = _utc_now()
            normalized = validate_dashboard_record(stored, dashboard_id)
            if normalized != stored:
                raise DashboardStoreError(409, "legacy_configuration_changed", "Legacy source upgrade would change dashboard configuration")
            return self._write(normalized)

    def restore_mercury(self, template: Any, expected_revision: Any, binding_action: Any = "reject") -> dict[str, Any]:
        template = validate_dashboard_record(template, "dashboard_mercury")
        binding_action = self._binding_action(binding_action)
        with self._guard("dashboard_mercury"):
            path = self._path("dashboard_mercury")
            current = self._read(path) if path.is_file() else None
            if current is None:
                if expected_revision is not None:
                    raise DashboardStoreError(409, "dashboard_changed", "Mercury was deleted before it could be restored")
                restored = template
                restored["revision"] = 1
            else:
                if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or current["revision"] != expected_revision:
                    raise DashboardStoreError(409, "dashboard_changed", "Mercury changed before it could be restored")
                defaults = {widget["id"]: widget for widget in template["dashboard"]["widgets"]}
                widgets = []
                restored_ids = set()
                for widget in current["dashboard"]["widgets"]:
                    replacement = defaults.get(widget["id"])
                    if replacement is None:
                        widgets.append(widget)
                        continue
                    replacement = json.loads(json.dumps(replacement))
                    widgets.append(replacement)
                    restored_ids.add(widget["id"])
                for widget_id, widget in defaults.items():
                    if widget_id in restored_ids:
                        continue
                    widgets.append(json.loads(json.dumps(widget)))
                restored = json.loads(json.dumps(current))
                restored["dashboard"]["widgets"] = widgets
                restored["revision"] = current["revision"] + 1
                restored = self._resolve_affected_bindings(current, restored, binding_action)
            restored["updatedAt"] = _utc_now()
            return self._write(validate_dashboard_record(restored, "dashboard_mercury"))

    def upgrade_mercury_example(self, template: Any) -> dict[str, Any] | None:
        template = validate_dashboard_record(template, "dashboard_mercury")
        with self._guard("dashboard_mercury"):
            path = self._path("dashboard_mercury")
            if not path.is_file():
                return None
            current = self._read(path)
            defaults = {widget["id"]: widget for widget in template["dashboard"]["widgets"]}
            if not any(
                widget["id"] in defaults and widget["title"] == defaults[widget["id"]]["title"]
                and not widget["configuration"] and widget["kind"] in {"preview", "placeholder"}
                for widget in current["dashboard"]["widgets"]
            ):
                return current
            upgraded = json.loads(json.dumps(current))
            for index, widget in enumerate(upgraded["dashboard"]["widgets"]):
                replacement = defaults.get(widget["id"])
                if replacement is None or widget["title"] != replacement["title"] or widget["configuration"] or widget["kind"] not in {"preview", "placeholder"}:
                    continue
                replacement = json.loads(json.dumps(replacement))
                upgraded["dashboard"]["widgets"][index] = replacement
            upgraded["revision"] = current["revision"] + 1
            upgraded["updatedAt"] = _utc_now()
            return self._write(validate_dashboard_record(upgraded, "dashboard_mercury"))

    def delete(self, dashboard_id: str, expected_revision: Any) -> dict[str, str]:
        dashboard_id = self.validate_id(dashboard_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise DashboardStoreError(400, "invalid_dashboard_binding", "expectedRevision is invalid")
        with self._guard(dashboard_id):
            path = self._path(dashboard_id)
            if not path.is_file():
                raise DashboardStoreError(404, "not_found", "Dashboard was not found")
            current = self._read(path)
            if current["revision"] != expected_revision:
                raise DashboardStoreError(409, "dashboard_changed", "Dashboard changed before it could be deleted", currentRevision=current["revision"])
            try:
                for operation_id, receipt in current.get("aiOperationReceipts", {}).items():
                    self._archive_receipt(dashboard_id, operation_id, receipt)
                summary_path = self._summary_path(dashboard_id)
                if summary_path.exists():
                    remove_file(summary_path)
                remove_file(path)
                with self._health_lock:
                    self._health_record_ids.discard(dashboard_id)
                    if self._health_scan is not None:
                        self._health_scan["recordIds"].discard(dashboard_id)
            except OSError as exc:
                raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be deleted") from exc
        return {"deleted": dashboard_id}

    def initialize_once(self, template: Any = None) -> None:
        with self._guard("dashboard_initialize"):
            if self.marker_path.exists():
                return
            if not self.list():
                record = validate_dashboard_record(template) if template is not None else mercury_dashboard_record()
                record["revision"] = 1
                record["updatedAt"] = _utc_now()
                self._write(validate_dashboard_record(record))
            try:
                descriptor = os.open(self.marker_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write("1\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(self.marker_path, 0o600)
            except OSError as exc:
                raise DashboardStoreError(500, "dashboard_store_error", "Dashboard example marker could not be saved") from exc
