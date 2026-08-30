from __future__ import annotations

from typing import Any

from .dashboard_store import mercury_dashboard_record, validate_dashboard_record
from .postgres_service import PostgresService, PostgresServiceError


MERCURY_DASHBOARD_ID = "dashboard_mercury"
MERCURY_PROFILE_ID = "schemii_example_postgres"
MERCURY_NAMESPACE = "bookstore"
MERCURY_RELATION = "order_summary"


def _number_format(style: str) -> dict[str, Any]:
    if style == "currency":
        return {"style": "currency", "currency": "USD", "fractionDigits": 2}
    return {"style": style}


def _measure(item_id: str, label: str, column: str | None, aggregation: str, number_style: str) -> dict[str, Any]:
    return {
        "id": item_id, "label": label, "column": column, "aggregation": aggregation,
        "distinct": False, "nullBehavior": "preserve", "numberFormat": _number_format(number_style),
    }


def _query(dimensions: list[tuple[str, str, str]], measures: list[dict[str, Any]], sort: list[dict[str, str]] | None = None, limit: int = 100) -> dict[str, Any]:
    return {
        "version": 2,
        "dimensions": [{"id": item_id, "label": label, "column": column} for item_id, label, column in dimensions],
        "measures": measures,
        "filters": [],
        "sort": sort or [],
        "limit": limit,
    }


def _presentation(query: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    dimensions = query["dimensions"]
    measures = query["measures"]
    dimension_id = dimensions[0]["id"] if dimensions else None
    measure_ids = [item["id"] for item in measures]
    table = {
        "version": 1,
        "columns": [
            {
                "targetId": item["id"], "width": 180 if item in dimensions else 140,
                "hidden": False, "pinned": bool(item in dimensions and item is dimensions[0]), "label": item["label"],
            }
            for item in dimensions + measures
        ],
        "pageSize": 25,
    }
    visualization = {
        "version": 1,
        "mode": mode,
        "selections": {
            "kpi": {"measureIds": measure_ids},
            "bar": {"dimensionId": dimension_id, "measureIds": measure_ids},
            "line": {"dimensionId": dimension_id, "measureIds": measure_ids},
            "donut": {"dimensionId": dimension_id, "measureId": measure_ids[0]},
        },
    }
    return table, visualization


def _detail(source: dict[str, Any]) -> dict[str, Any]:
    columns = []
    for column in source["columns"]:
        name = column["name"]
        style = "currency" if name == "order_total" else "integer" if name in {"order_id", "customer_id", "item_count"} else "auto"
        columns.append({
            "sourceColumn": name,
            "label": name.replace("_", " ").title(),
            "width": 200 if name in {"customer_name", "ordered_at", "shipped_at"} else 140,
            "hidden": name == "customer_id",
            "searchable": True,
            "numberFormat": _number_format(style),
        })
    return {
        "version": 1,
        "columns": columns,
        "defaultSort": {"sourceColumn": "ordered_at", "direction": "desc", "nulls": "last"},
        "rowIdentifier": "order_id",
        "pageSize": 25,
    }


def build_mercury_dashboard(descriptor: dict[str, Any]) -> dict[str, Any]:
    required = {"profileId", "database", "namespace", "relation", "kind", "fingerprint", "columns"}
    if not isinstance(descriptor, dict) or not required <= set(descriptor):
        raise PostgresServiceError(502, "mercury_source_invalid", "The Mercury tutorial source descriptor is incomplete")
    if descriptor["profileId"] != MERCURY_PROFILE_ID or descriptor["namespace"] != MERCURY_NAMESPACE or descriptor["relation"] != MERCURY_RELATION or descriptor["kind"] != "view":
        raise PostgresServiceError(409, "mercury_source_changed", "The Mercury tutorial source identity is not the expected bookstore view")
    source = {
        key: descriptor[key]
        for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")
    }
    snapshot_version = descriptor.get("snapshotVersion", 1)
    if snapshot_version == 2:
        if any("capabilities" not in column for column in descriptor["columns"]):
            raise PostgresServiceError(502, "mercury_source_invalid", "The Mercury tutorial source capability snapshot is incomplete")
        source["snapshotVersion"] = 2
        source["columns"] = [
            {
                **{key: column[key] for key in ("name", "type", "nullable", "ordinal")},
                "capabilities": column["capabilities"],
            }
            for column in descriptor["columns"]
        ]
    else:
        source["columns"] = [
            {key: column[key] for key in ("name", "type", "nullable", "ordinal")}
            for column in descriptor["columns"]
        ]
    available_columns = {column["name"] for column in source["columns"]}
    expected_columns = {"order_id", "customer_id", "customer_name", "status", "ordered_at", "shipped_at", "order_date", "item_count", "order_total"}
    if not expected_columns <= available_columns:
        raise PostgresServiceError(409, "mercury_source_changed", "The Mercury tutorial view is missing required columns")

    widget_queries = {
        "widget_revenue": _query([], [_measure("measure_revenue", "Gross revenue", "order_total", "sum", "currency")]),
        "widget_orders": _query([], [_measure("measure_orders", "Orders", None, "count_rows", "integer")]),
        "widget_average": _query([], [_measure("measure_average_order", "Average order", "order_total", "average", "currency")]),
        "widget_trend": _query(
            [("dimension_order_date", "Order date", "order_date")],
            [_measure("measure_daily_revenue", "Gross revenue", "order_total", "sum", "currency")],
            [{"targetKind": "dimension", "targetId": "dimension_order_date", "direction": "asc", "nulls": "last"}],
            366,
        ),
        "widget_status": _query(
            [("dimension_status", "Status", "status")],
            [_measure("measure_status_orders", "Orders", None, "count_rows", "integer")],
            [{"targetKind": "measure", "targetId": "measure_status_orders", "direction": "desc", "nulls": "last"}],
        ),
        "widget_recent": _query(
            [
                ("dimension_order_id", "Order", "order_id"),
                ("dimension_customer", "Customer", "customer_name"),
                ("dimension_status", "Status", "status"),
                ("dimension_ordered_at", "Ordered at", "ordered_at"),
            ],
            [
                _measure("measure_item_count", "Items", "item_count", "minimum", "integer"),
                _measure("measure_order_total", "Total", "order_total", "minimum", "currency"),
            ],
            [{"targetKind": "dimension", "targetId": "dimension_ordered_at", "direction": "desc", "nulls": "last"}],
            10,
        ),
    }
    modes = {
        "widget_revenue": "kpi", "widget_orders": "kpi", "widget_average": "kpi",
        "widget_trend": "line", "widget_status": "donut", "widget_recent": "table",
    }
    record = mercury_dashboard_record()
    for widget in record["dashboard"]["widgets"]:
        query = widget_queries[widget["id"]]
        table, visualization = _presentation(query, modes[widget["id"]])
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": source, "query": query, "table": table,
            "visualization": visualization, "detail": _detail(source),
        }
    return validate_dashboard_record(record)


def mercury_dashboard_from_service(service: PostgresService) -> dict[str, Any]:
    profile = next((item for item in service.list_profiles() if item.get("id") == MERCURY_PROFILE_ID), None)
    if profile is None:
        raise PostgresServiceError(404, "mercury_source_unavailable", "The included Mercury PostgreSQL profile is unavailable")
    database = profile.get("dbname")
    if not isinstance(database, str):
        raise PostgresServiceError(409, "mercury_source_changed", "The included Mercury PostgreSQL profile has no database")
    if not service.namespace_exists(MERCURY_PROFILE_ID, database, MERCURY_NAMESPACE):
        raise PostgresServiceError(404, "mercury_source_unavailable", "The bookstore namespace is unavailable")
    descriptor = service.inspect_relation(MERCURY_PROFILE_ID, database, MERCURY_NAMESPACE, MERCURY_RELATION, "view")
    return build_mercury_dashboard(descriptor)
