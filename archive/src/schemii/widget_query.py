from __future__ import annotations

import math
import re
from typing import Any, Callable

from .result_limits import ResultLimiter, ResultLimits
from .query_type_capabilities import (
    CapabilityValidationError,
    aggregate_sql,
    operator_sql,
    require_current_capabilities,
)


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
AGGREGATIONS = {"count_rows", "count", "sum", "average", "minimum", "maximum"}
FILTER_OPERATORS = {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in", "like", "contains", "starts_with", "ends_with", "is_null", "is_not_null"}
NULL_FILTER_OPERATORS = {"is_null", "is_not_null"}


class QueryValidationError(ValueError):
    pass


WIDGET_RESULT_LIMITS = ResultLimits(
    max_cell_bytes=64 * 1024,
    max_row_bytes=256 * 1024,
    max_result_bytes=1024 * 1024,
    max_nesting=8,
    max_collection_items=1000,
)


def limit_widget_rows(
    rows: list[Any], aliases: list[str], *, max_rows: int,
    max_result_bytes: int = WIDGET_RESULT_LIMITS.max_result_bytes,
    envelope: Callable[[list[list[Any]]], Any] | None = None,
) -> dict[str, Any]:
    """Bound raw widget rows before they cross an HTTP response boundary."""
    limits = ResultLimits(
        max_cell_bytes=WIDGET_RESULT_LIMITS.max_cell_bytes,
        max_row_bytes=WIDGET_RESULT_LIMITS.max_row_bytes,
        max_result_bytes=max_result_bytes,
        max_nesting=WIDGET_RESULT_LIMITS.max_nesting,
        max_collection_items=WIDGET_RESULT_LIMITS.max_collection_items,
    )
    return ResultLimiter(limits).rows(rows, aliases, max_rows=max_rows, envelope=envelope)


def _text(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise QueryValidationError(f"{field} must be a trimmed string up to {maximum} characters")
    return value


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise QueryValidationError(f"{field} is invalid")
    return value


def _bounded_list(value: Any, field: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise QueryValidationError(f"{field} must contain from {minimum} to {maximum} items")
    return value


def normalize_number_format(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "style" not in value:
        raise QueryValidationError("measure numberFormat is invalid")
    style = value.get("style")
    if style in {"auto", "integer"} and set(value) == {"style"}:
        return {"style": style}
    if style in {"decimal", "percent"} and set(value) == {"style", "fractionDigits"}:
        digits = value.get("fractionDigits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 20:
            raise QueryValidationError("fractionDigits must be from 0 to 20")
        return {"style": style, "fractionDigits": digits}
    if style == "currency" and set(value) == {"style", "currency", "fractionDigits"}:
        currency = value.get("currency")
        digits = value.get("fractionDigits")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency) or isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 20:
            raise QueryValidationError("currency number format is invalid")
        return {"style": style, "currency": currency, "fractionDigits": digits}
    raise QueryValidationError("measure numberFormat fields are invalid")


def _capability(column: dict[str, Any]) -> dict[str, Any]:
    capabilities = column.get("capabilities")
    if not isinstance(capabilities, dict):
        raise QueryValidationError("source capabilities are unavailable; reselect the source")
    return capabilities


def _filter_capability(column: dict[str, Any], logical_name: str) -> dict[str, Any] | None:
    return next((item for item in _capability(column)["filterOperators"] if item["name"] == logical_name), None)


def _aggregate_capability(column: dict[str, Any], logical_name: str) -> dict[str, Any] | None:
    return next((item for item in _capability(column)["aggregates"] if item["name"] == logical_name), None)


def _catalog_type_sql(column: dict[str, Any], quote: Callable[[str], str]) -> str:
    type_identity = _capability(column)["type"]
    return f"{quote(type_identity['namespace'])}.{quote(type_identity['name'])}"


def _typed_column(column: dict[str, Any], quote: Callable[[str], str]) -> str:
    return f"({quote(column['name'])}::{_catalog_type_sql(column, quote)})"


def _typed_parameter(column: dict[str, Any], quote: Callable[[str], str]) -> str:
    return f"%s::{_catalog_type_sql(column, quote)}"


def _compile_filter_groups(filter_groups: list[dict[str, Any]], columns: dict[str, dict[str, Any]], quote: Callable[[str], str]) -> tuple[list[list[str]], list[Any]]:
    parameters = []
    predicate_groups = []
    for group in filter_groups:
        predicates = []
        for item in group["conditions"]:
            source_column = columns[item["column"]]
            column = _typed_column(source_column, quote)
            parameter = _typed_parameter(source_column, quote)
            operator = item["operator"]
            capability = _filter_capability(columns[item["column"]], operator)
            if operator in {"eq", "neq", "lt", "lte", "gt", "gte"}:
                predicates.append(f"{column} {operator_sql(capability['operator'], quote)} {parameter}")
                parameters.append(item["values"][0])
            elif operator == "between":
                predicates.append(f"({column} {operator_sql(capability['operators']['lower'], quote)} {parameter} AND {column} {operator_sql(capability['operators']['upper'], quote)} {parameter})")
                parameters.extend(item["values"])
            elif operator in {"in", "not_in"}:
                comparisons = " OR ".join(f"{column} {operator_sql(capability['operator'], quote)} {parameter}" for _ in item["values"])
                predicates.append(f"{'NOT ' if operator == 'not_in' else ''}({comparisons})")
                parameters.extend(item["values"])
            elif operator in {"like", "contains", "starts_with", "ends_with"}:
                value = item["values"][0]
                if operator != "like":
                    value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    value = f"%{value}%" if operator == "contains" else f"{value}%" if operator == "starts_with" else f"%{value}"
                pattern = f"(pg_catalog.like_escape(%s::text, E'\\\\')::{_catalog_type_sql(source_column, quote)})"
                predicates.append(f"{column} {operator_sql(capability['operator'], quote)} {pattern}")
                parameters.append(value)
            else:
                predicates.append(f"{column} IS {'NOT ' if operator == 'is_not_null' else ''}NULL")
        predicate_groups.append(predicates)
    return predicate_groups, parameters


def normalize_query(query: Any, source_columns: list[dict[str, Any]] | None = None, *, allow_legacy_snapshot: bool = False) -> dict[str, Any]:
    if not isinstance(query, dict) or set(query) not in (
        {"version", "dimensions", "measures", "filters", "sort"},
        {"version", "dimensions", "measures", "filters", "sort", "limit"},
    ) or query.get("version") not in {1, 2}:
        raise QueryValidationError("query must use supported version-1 or version-2 fields")
    input_version = query["version"]
    columns = {column.get("name"): column for column in source_columns or []}
    legacy_snapshot = source_columns is not None and any("capabilities" not in column for column in source_columns)
    if legacy_snapshot and not allow_legacy_snapshot:
        try:
            require_current_capabilities(source_columns or [])
        except CapabilityValidationError as exc:
            raise QueryValidationError(str(exc)) from exc

    def supports(column: dict[str, Any], operation: str) -> bool:
        if legacy_snapshot:
            from .legacy_query_capabilities import supports as legacy_supports
            return legacy_supports(str(column.get("type", "")), operation)
        capabilities = _capability(column)
        if operation in {"groupable", "distinct", "sortable", "numeric"}:
            return bool(capabilities[operation])
        if operation == "temporal":
            return capabilities["temporal"] != "none"
        if operation == "zeroable":
            return any(item["zeroable"] for item in capabilities["aggregates"])
        if operation in AGGREGATIONS:
            return _aggregate_capability(column, operation) is not None
        return _filter_capability(column, operation) is not None
    dimensions = []
    measures = []
    filter_groups = []
    sorts = []
    ids: set[str] = set()
    dimension_columns: set[str] = set()

    for item in _bounded_list(query.get("dimensions"), "dimensions", 0, 32):
        if not isinstance(item, dict) or set(item) != {"id", "label", "column"}:
            raise QueryValidationError("dimension fields are invalid")
        item_id = _id(item.get("id"), "dimension ID")
        column = _text(item.get("column"), "dimension column", 63)
        if item_id in ids or column in dimension_columns or source_columns is not None and column not in columns:
            raise QueryValidationError("dimension ID or source column is invalid or duplicated")
        if source_columns is not None and not supports(columns[column], "groupable"):
            raise QueryValidationError("dimension requires a groupable PostgreSQL column")
        ids.add(item_id)
        dimension_columns.add(column)
        dimensions.append({"id": item_id, "label": _text(item.get("label"), "dimension label"), "column": column})

    for item in _bounded_list(query.get("measures"), "measures", 1, 32):
        fields = {"id", "label", "column", "aggregation", "distinct", "nullBehavior", "numberFormat"}
        if not isinstance(item, dict) or set(item) != fields:
            raise QueryValidationError("measure fields are invalid")
        item_id = _id(item.get("id"), "measure ID")
        aggregation = item.get("aggregation")
        column = item.get("column")
        distinct = item.get("distinct")
        null_behavior = item.get("nullBehavior")
        if item_id in ids or aggregation not in AGGREGATIONS or not isinstance(distinct, bool) or null_behavior not in {"preserve", "zero"}:
            raise QueryValidationError("measure identity or behavior is invalid")
        if aggregation == "count_rows":
            if column is not None or distinct or null_behavior != "preserve":
                raise QueryValidationError("count_rows cannot use a column, distinct, or zero null behavior")
        else:
            column = _text(column, "measure column", 63)
            if source_columns is not None and column not in columns:
                raise QueryValidationError("measure source column does not exist")
            if aggregation != "count" and distinct:
                raise QueryValidationError("distinct is supported only for count")
            if aggregation == "count" and null_behavior != "preserve":
                raise QueryValidationError("count must preserve native null behavior")
            if source_columns is not None and not supports(columns[column], aggregation):
                raise QueryValidationError(f"{aggregation} is not supported for this PostgreSQL column")
            if aggregation == "count" and distinct and source_columns is not None and not supports(columns[column], "distinct"):
                raise QueryValidationError("count distinct requires a comparable PostgreSQL column")
            if null_behavior == "zero" and aggregation not in {"sum", "average", "minimum", "maximum"}:
                raise QueryValidationError("zero null behavior is invalid for this aggregation")
            if null_behavior == "zero" and source_columns is not None and (
                legacy_snapshot and not supports(columns[column], "zeroable")
                or not legacy_snapshot and not bool(_aggregate_capability(columns[column], aggregation)["zeroable"])
            ):
                raise QueryValidationError("zero null behavior requires a numeric PostgreSQL column")
        ids.add(item_id)
        measures.append({
            "id": item_id, "label": _text(item.get("label"), "measure label"), "column": column,
            "aggregation": aggregation, "distinct": distinct, "nullBehavior": null_behavior,
            "numberFormat": normalize_number_format(item.get("numberFormat")),
        })

    raw_filters = _bounded_list(query.get("filters"), "filters", 0, 32 if input_version == 2 else 64)
    legacy_group_id = "filter_group_legacy"
    reserved_filter_ids = {item.get("id") for item in raw_filters if isinstance(item, dict)}
    while legacy_group_id in ids or legacy_group_id in reserved_filter_ids:
        legacy_group_id += "_"
    raw_groups = [{"id": legacy_group_id, "conditions": raw_filters}] if input_version == 1 and raw_filters else raw_filters
    condition_count = 0
    for group in raw_groups:
        if not isinstance(group, dict) or set(group) != {"id", "conditions"}:
            raise QueryValidationError("filter group fields are invalid")
        group_id = _id(group.get("id"), "filter group ID")
        if group_id in ids:
            raise QueryValidationError("filter group ID is duplicated")
        ids.add(group_id)
        conditions = []
        for item in _bounded_list(group.get("conditions"), "filter group conditions", 1, 64):
            condition_count += 1
            if condition_count > 64 or not isinstance(item, dict) or set(item) != {"id", "column", "operator", "values"}:
                raise QueryValidationError("filter fields are invalid")
            item_id = _id(item.get("id"), "filter ID")
            column = _text(item.get("column"), "filter column", 63)
            operator = item.get("operator")
            values = item.get("values")
            if item_id in ids or operator not in FILTER_OPERATORS or source_columns is not None and column not in columns:
                raise QueryValidationError("filter identity, operator, or column is invalid")
            if source_columns is not None and not supports(columns[column], operator):
                raise QueryValidationError("filter operator is not supported for this PostgreSQL column type")
            if not isinstance(values, list) or len(values) > 100:
                raise QueryValidationError("filter values are invalid")
            expected = 0 if operator in NULL_FILTER_OPERATORS else 2 if operator == "between" else None if operator in {"in", "not_in"} else 1
            if expected is not None and len(values) != expected or expected is None and not values:
                raise QueryValidationError("filter value count is invalid")
            for value in values:
                if value is None or isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value):
                    raise QueryValidationError("filter values must be finite non-null scalars")
                if operator in {"like", "contains", "starts_with", "ends_with"} and not isinstance(value, str):
                    raise QueryValidationError("text filter values must be strings")
            ids.add(item_id)
            conditions.append({"id": item_id, "column": column, "operator": operator, "values": list(values)})
        filter_groups.append({"id": group_id, "conditions": conditions})

    targets = {item["id"]: "dimension" for item in dimensions} | {item["id"]: "measure" for item in measures}
    sorted_targets: set[str] = set()
    for item in _bounded_list(query.get("sort"), "sort", 0, 64):
        if not isinstance(item, dict) or set(item) != {"targetKind", "targetId", "direction", "nulls"}:
            raise QueryValidationError("sort fields are invalid")
        target_id = _id(item.get("targetId"), "sort target")
        if item.get("targetKind") != targets.get(target_id) or target_id in sorted_targets or item.get("direction") not in {"asc", "desc"} or item.get("nulls") not in {"first", "last"}:
            raise QueryValidationError("sort target or behavior is invalid")
        if source_columns is not None:
            target = next(value for value in dimensions + measures if value["id"] == target_id)
            if item["targetKind"] == "dimension":
                sortable = supports(columns[target["column"]], "sortable")
            elif target["aggregation"] in {"count", "count_rows"}:
                sortable = True
            else:
                aggregate = _aggregate_capability(columns[target["column"]], target["aggregation"]) if not legacy_snapshot else None
                sortable = aggregate["sortable"] if aggregate is not None else supports(columns[target["column"]], "sortable")
            if not sortable:
                raise QueryValidationError("sort target does not have a PostgreSQL ordering capability")
        sorted_targets.add(target_id)
        sorts.append({"targetKind": item["targetKind"], "targetId": target_id, "direction": item["direction"], "nulls": item["nulls"]})

    limit = query.get("limit", 500)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise QueryValidationError("query limit must be from 1 to 500")
    return {"version": 2, "dimensions": dimensions, "measures": measures, "filters": filter_groups, "sort": sorts, "limit": limit}


def normalize_detail_request(
    selection: Any, detail: Any, offset: Any, limit: Any, sort: Any, searches: Any,
    query: dict[str, Any], source_columns: list[dict[str, Any]],
) -> dict[str, Any]:
    columns_by_name = {column.get("name"): column for column in source_columns}
    dimensions_by_id = {item["id"]: item for item in query["dimensions"]}
    measures_by_id = {item["id"]: item for item in query["measures"]}
    if not isinstance(selection, dict) or set(selection) not in ({"dimensions"}, {"dimensions", "measureId"}):
        raise QueryValidationError("selection fields are invalid")
    selected_dimensions = selection.get("dimensions")
    if not isinstance(selected_dimensions, list) or len(selected_dimensions) != len(dimensions_by_id):
        raise QueryValidationError("selection must contain every executed dimension exactly once")
    selected_values = {}
    seen_dimensions = set()
    for item in selected_dimensions:
        if not isinstance(item, dict) or set(item) not in ({"targetId", "value"}, {"targetId", "operator", "values"}):
            raise QueryValidationError("selected dimension fields are invalid")
        target_id = item.get("targetId")
        value = item.get("value")
        values = item.get("values")
        selected_dimension = dimensions_by_id.get(target_id)
        selected_source = columns_by_name.get(selected_dimension["column"]) if selected_dimension else None
        range_selection = item.get("operator") == "gte_lt" and selected_source is not None and _capability(selected_source)["temporal"] != "none" and isinstance(values, list) and len(values) == 2 and all(
            selected is not None and not isinstance(selected, (dict, list)) and (not isinstance(selected, float) or math.isfinite(selected))
            for selected in values
        )
        exact_selection = set(item) == {"targetId", "value"} and not isinstance(value, (dict, list)) and not (isinstance(value, float) and not math.isfinite(value))
        if target_id not in dimensions_by_id or target_id in seen_dimensions or not (exact_selection or range_selection):
            raise QueryValidationError("selected dimension is invalid or duplicated")
        seen_dimensions.add(target_id)
        selected_values[target_id] = {"targetId": target_id, "operator": "gte_lt", "values": values} if range_selection else {"targetId": target_id, "value": value}
    if seen_dimensions != set(dimensions_by_id):
        raise QueryValidationError("selection must contain every executed dimension exactly once")
    normalized_selection = [
        selected_values[target_id]
        for target_id in dimensions_by_id
    ]
    measure_id = selection.get("measureId")
    if "measureId" in selection and measure_id not in measures_by_id:
        raise QueryValidationError("selected measure is not in the executed query")

    if not isinstance(detail, dict) or set(detail) != {"version", "columns", "rowIdentifier"} or detail.get("version") != 1 or isinstance(detail.get("version"), bool):
        raise QueryValidationError("detail configuration fields are invalid")
    detail_columns = []
    detail_ids = set()
    detail_source_columns = set()
    for item in _bounded_list(detail.get("columns"), "detail columns", 1, 64):
        if not isinstance(item, dict) or set(item) != {"id", "label", "column", "numberFormat", "searchable"}:
            raise QueryValidationError("detail column fields are invalid")
        item_id = _id(item.get("id"), "detail column ID")
        column_name = _text(item.get("column"), "detail source column", 63)
        source_column = columns_by_name.get(column_name)
        if item_id in detail_ids or column_name in detail_source_columns or source_column is None or not isinstance(item.get("searchable"), bool):
            raise QueryValidationError("detail column identity or source binding is invalid or duplicated")
        detail_ids.add(item_id)
        detail_source_columns.add(column_name)
        detail_columns.append({
            "id": item_id, "label": _text(item.get("label"), "detail column label"), "column": column_name,
            "numberFormat": normalize_number_format(item.get("numberFormat")), "searchable": item["searchable"],
        })
    row_identifier = detail.get("rowIdentifier")
    if row_identifier is not None and (not isinstance(row_identifier, str) or row_identifier not in columns_by_name):
        raise QueryValidationError("detail row identifier must be null or a source column")
    if row_identifier is not None and not _capability(columns_by_name[row_identifier])["sortable"]:
        raise QueryValidationError("detail row identifier does not have a PostgreSQL ordering capability")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10_000_000:
        raise QueryValidationError("offset must be an integer from 0 to 10000000")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise QueryValidationError("limit must be an integer from 1 to 100")
    searchable_by_id = {item["id"]: item for item in detail_columns if item["searchable"]}
    search_values = {}
    for item in _bounded_list(searches, "detail searches", 0, 64):
        if not isinstance(item, dict) or set(item) != {"targetId", "value"}:
            raise QueryValidationError("detail search fields are invalid")
        target_id = item.get("targetId")
        value = item.get("value")
        if target_id not in searchable_by_id or target_id in search_values:
            raise QueryValidationError("detail search target is invalid or duplicated")
        search_values[target_id] = _text(value, "detail search value", 256)
    normalized_searches = [
        {"targetId": item["id"], "value": search_values[item["id"]]}
        for item in detail_columns if item["id"] in search_values
    ]
    if sort is not None:
        if not isinstance(sort, dict) or set(sort) != {"targetId", "direction", "nulls"} or sort.get("targetId") not in detail_ids or sort.get("direction") not in {"asc", "desc"} or sort.get("nulls") not in {"first", "last"}:
            raise QueryValidationError("detail sort is invalid")
        sort_column = next(item["column"] for item in detail_columns if item["id"] == sort["targetId"])
        if not _capability(columns_by_name[sort_column])["sortable"]:
            raise QueryValidationError("detail sort column does not have a PostgreSQL ordering capability")
        sort = {key: sort[key] for key in ("targetId", "direction", "nulls")}
    return {
        "selection": {"dimensions": normalized_selection, **({"measureId": measure_id} if "measureId" in selection else {})},
        "detail": {"version": 1, "columns": detail_columns, "rowIdentifier": row_identifier},
        "offset": offset, "limit": limit, "sort": sort, "searches": normalized_searches,
    }


def _measure_expression(item: dict[str, Any], columns: dict[str, dict[str, Any]], quote: Callable[[str], str]) -> str:
    if item["aggregation"] == "count_rows":
        expression = "pg_catalog.count(*)"
    else:
        distinct = "DISTINCT " if item["distinct"] else ""
        capability = _aggregate_capability(columns[item["column"]], item["aggregation"])
        expression = f'{aggregate_sql(capability["aggregate"], quote)}({distinct}{_typed_column(columns[item["column"]], quote)})'
    return f"COALESCE({expression}, 0)" if item["nullBehavior"] == "zero" else expression


def _measure_output(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"], "kind": "measure", "label": item["label"], "sourceColumn": item["column"],
        "aggregation": item["aggregation"], "distinct": item["distinct"],
        "nullBehavior": item["nullBehavior"], "numberFormat": item["numberFormat"],
    }


def _temporal_value(value: str, temporal: str) -> str:
    if temporal == "date":
        return f"({value}::timestamp AT TIME ZONE 'UTC')"
    if temporal == "timestamp_tz":
        return value
    return f"({value} AT TIME ZONE %s)"


def _temporal_expression(column: dict[str, Any], quote: Callable[[str], str]) -> str:
    return _temporal_value(_typed_column(column, quote), _capability(column)["temporal"])


def normalize_temporal_series(query: Any, source_columns: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_query(query, source_columns)
    if len(normalized["dimensions"]) != 1:
        raise QueryValidationError("temporal series requires exactly one dimension")
    if not 1 <= len(normalized["measures"]) <= 8:
        raise QueryValidationError("temporal series requires from 1 to 8 measures")
    dimension = normalized["dimensions"][0]
    source_column = next((item for item in source_columns if item["name"] == dimension["column"]), None)
    source_type = str(source_column["type"]) if source_column else ""
    if source_column is None or _capability(source_column)["temporal"] == "none":
        raise QueryValidationError("temporal series dimension must use a date or timestamp column")
    if any(_filter_capability(source_column, operator) is None for operator in ("gte", "lt")):
        raise QueryValidationError("temporal series dimension requires catalog-resolved range operators")
    for aggregation in ("count", "minimum", "maximum"):
        if _aggregate_capability(source_column, aggregation) is None:
            raise QueryValidationError(f"temporal series requires a catalog-resolved {aggregation} aggregate")
    return {**normalized, "temporalSourceType": source_type, "temporalKind": _capability(source_column)["temporal"]}


def compile_temporal_series_manifest(
    source: dict[str, Any], series: dict[str, Any], quote: Callable[[str], str],
    source_columns: list[dict[str, Any]], *, source_time_zone: str = "UTC",
) -> dict[str, Any]:
    dimension = series["dimensions"][0]
    columns = {column["name"]: column for column in source_columns}
    source_column = columns[dimension["column"]]
    temporal = _temporal_expression(source_column, quote)
    predicate_groups, parameters = _compile_filter_groups(series["filters"], columns, quote)
    predicates = []
    if predicate_groups:
        formatted_groups = ["(\n        " + "\n        AND ".join(group) + "\n    )" for group in predicate_groups]
        predicates.append("(\n    " + "\n    OR ".join(formatted_groups) + "\n)")
    predicates.append(f"{temporal} IS NOT NULL")
    relation = f'{quote(source["namespace"])}.{quote(source["relation"])}'
    minimum = aggregate_sql(_aggregate_capability(source_column, "minimum")["aggregate"], quote)
    maximum = aggregate_sql(_aggregate_capability(source_column, "maximum")["aggregate"], quote)
    count = aggregate_sql(_aggregate_capability(source_column, "count")["aggregate"], quote)
    typed_source = _typed_column(source_column, quote)
    minimum_expression = _temporal_value(f"{minimum}({typed_source})", _capability(source_column)["temporal"])
    maximum_expression = _temporal_value(f"{maximum}({typed_source})", _capability(source_column)["temporal"])
    sql = (
        f'SELECT\n    {minimum_expression} AS "__schemer_min",\n'
        f'    {maximum_expression} AS "__schemer_max",\n'
        f'    {count}(DISTINCT {typed_source}) AS "__schemer_points"\nFROM {relation}\nWHERE\n    '
        + "\n    AND ".join(predicates)
    )
    if series["temporalKind"] == "timestamp":
        parameters = [source_time_zone, source_time_zone, *parameters, source_time_zone]
    return {"sql": sql, "parameters": parameters, "dimension": dimension}


def compile_temporal_series_window(
    source: dict[str, Any], series: dict[str, Any], quote: Callable[[str], str],
    bucket_seconds: int, window_start: Any, window_end: Any, maximum_rows: int,
    source_columns: list[dict[str, Any]], *, source_time_zone: str = "UTC",
) -> dict[str, Any]:
    dimension = series["dimensions"][0]
    columns = {column["name"]: column for column in source_columns}
    temporal = _temporal_expression(columns[dimension["column"]], quote)
    bucket = f"pg_catalog.to_timestamp(pg_catalog.floor(extract(epoch FROM {temporal}) / %s) * %s)"
    select = [f'{bucket} AS "__schemer_t0"']
    output = [{
        "id": dimension["id"], "kind": "dimension", "label": dimension["label"],
        "sourceColumn": dimension["column"], "type": series["temporalSourceType"], "temporal": True,
    }]
    aliases = ["__schemer_t0"]
    for index, item in enumerate(series["measures"]):
        alias = f"__schemer_m{index}"
        select.append(f'{_measure_expression(item, columns, quote)} AS {quote(alias)}')
        output.append(_measure_output(item))
        aliases.append(alias)
    predicate_groups, filter_parameters = _compile_filter_groups(series["filters"], columns, quote)
    predicates = []
    if predicate_groups:
        formatted_groups = ["(\n        " + "\n        AND ".join(group) + "\n    )" for group in predicate_groups]
        predicates.append("(\n    " + "\n    OR ".join(formatted_groups) + "\n)")
    predicates.extend((f"{temporal} >= %s", f"{temporal} < %s"))
    relation = f'{quote(source["namespace"])}.{quote(source["relation"])}'
    sql = (
        "SELECT\n    " + ",\n    ".join(select) + f"\nFROM {relation}\nWHERE\n    "
        + "\n    AND ".join(predicates)
        + '\nGROUP BY\n    1\nORDER BY\n    "__schemer_t0" ASC NULLS LAST\nLIMIT %s'
    )
    parameters = [bucket_seconds, bucket_seconds, *filter_parameters, window_start, window_end, maximum_rows + 1]
    if series["temporalKind"] == "timestamp":
        parameters = [
            source_time_zone, bucket_seconds, bucket_seconds, *filter_parameters,
            source_time_zone, window_start, source_time_zone, window_end, maximum_rows + 1,
        ]
    return {"sql": sql, "parameters": parameters, "columns": output, "aliases": aliases}


def compile_query(source: dict[str, Any], query: dict[str, Any], quote: Callable[[str], str], source_columns: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions = query["dimensions"]
    measures = query["measures"]
    aliases: dict[str, str] = {}
    select = []
    output = []
    columns = {column["name"]: column for column in source_columns}
    for index, item in enumerate(dimensions):
        alias = f"__schemer_d{index}"
        aliases[item["id"]] = alias
        select.append(f'{quote(item["column"])} AS {quote(alias)}')
        output.append({"id": item["id"], "kind": "dimension", "label": item["label"], "sourceColumn": item["column"]})
    for index, item in enumerate(measures):
        alias = f"__schemer_m{index}"
        aliases[item["id"]] = alias
        expression = _measure_expression(item, columns, quote)
        select.append(f"{expression} AS {quote(alias)}")
        output.append(_measure_output(item))
    predicate_groups, parameters = _compile_filter_groups(query["filters"], columns, quote)
    relation = f'{quote(source["namespace"])}.{quote(source["relation"])}'
    sql = "SELECT\n    " + ",\n    ".join(select) + f"\nFROM {relation}"
    if predicate_groups:
        formatted_groups = ["(\n        " + "\n        AND ".join(group) + "\n    )" for group in predicate_groups]
        sql += "\nWHERE\n    " + "\n    OR ".join(formatted_groups)
    if dimensions:
        sql += "\nGROUP BY\n    " + ",\n    ".join(quote(item["column"]) for item in dimensions)
    sort_parts = [f'{quote(aliases[item["targetId"]])} {item["direction"].upper()} NULLS {item["nulls"].upper()}' for item in query["sort"]]
    sorted_ids = {item["targetId"] for item in query["sort"]}
    sort_parts.extend(f'{quote(aliases[item["id"]])} ASC NULLS LAST' for item in dimensions if item["id"] not in sorted_ids and _capability(columns[item["column"]])["sortable"])
    if sort_parts:
        sql += "\nORDER BY\n    " + ",\n    ".join(sort_parts)
    sql += "\nLIMIT %s"
    parameters.append(query["limit"] + 1)
    return {"sql": sql, "parameters": parameters, "columns": output, "aliases": [aliases[item["id"]] for item in dimensions + measures]}


def compile_detail_query(
    source: dict[str, Any], query: dict[str, Any], request: dict[str, Any],
    source_columns: list[dict[str, Any]], quote: Callable[[str], str],
    retained_limit: int | None = None,
) -> dict[str, Any]:
    detail_columns = request["detail"]["columns"]
    source_by_name = {column["name"]: column for column in source_columns}
    dimensions_by_id = {item["id"]: item for item in query["dimensions"]}
    measures_by_id = {item["id"]: item for item in query["measures"]}
    aliases = {item["id"]: f"__schemer_c{index}" for index, item in enumerate(detail_columns)}
    output_columns = [{
        "id": item["id"], "label": item["label"], "sourceColumn": item["column"],
        "type": source_by_name[item["column"]]["type"], "nullable": source_by_name[item["column"]]["nullable"],
        "numberFormat": item["numberFormat"], "searchable": item["searchable"],
        "operators": [operator["name"] for operator in _capability(source_by_name[item["column"]])["filterOperators"]],
    } for item in detail_columns]
    predicate_groups, parameters = _compile_filter_groups(query["filters"], source_by_name, quote)
    predicates = []
    if predicate_groups:
        formatted_groups = ["(\n        " + "\n        AND ".join(group) + "\n    )" for group in predicate_groups]
        predicates.append("(\n    " + "\n    OR ".join(formatted_groups) + "\n)")
    for selected in request["selection"]["dimensions"]:
        dimension = dimensions_by_id[selected["targetId"]]
        column = quote(dimension["column"])
        if selected.get("operator") == "gte_lt":
            selected_column = source_by_name[dimension["column"]]
            typed_column = _typed_column(selected_column, quote)
            parameter = _typed_parameter(selected_column, quote)
            lower = _filter_capability(selected_column, "gte")
            upper = _filter_capability(selected_column, "lt")
            predicates.extend((
                f"{typed_column} {operator_sql(lower['operator'], quote)} {parameter}",
                f"{typed_column} {operator_sql(upper['operator'], quote)} {parameter}",
            ))
            parameters.extend(selected["values"])
        elif selected["value"] is None:
            predicates.append(f"{column} IS NULL")
        else:
            equality = _filter_capability(source_by_name[dimension["column"]], "eq")
            selected_column = source_by_name[dimension["column"]]
            predicates.append(f"{_typed_column(selected_column, quote)} {operator_sql(equality['operator'], quote)} {_typed_parameter(selected_column, quote)}")
            parameters.append(selected["value"])
    measure_id = request["selection"].get("measureId")
    if measure_id is not None and measures_by_id[measure_id]["aggregation"] != "count_rows":
        predicates.append(f'{quote(measures_by_id[measure_id]["column"])} IS NOT NULL')
    detail_by_id = {item["id"]: item for item in detail_columns}
    for search in request["searches"]:
        column = quote(detail_by_id[search["targetId"]]["column"])
        escaped = search["value"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        predicates.append(f"CAST({column} AS text) OPERATOR(pg_catalog.~~*) pg_catalog.like_escape(%s::text, E'\\\\')")
        parameters.append(f"%{escaped}%")
    relation = f'{quote(source["namespace"])}.{quote(source["relation"])}'
    where = "\nWHERE\n    " + "\n    AND ".join(predicates) if predicates else ""
    count_sql = f'SELECT pg_catalog.count(*) AS {quote("__schemer_count")}\nFROM {relation}{where}'
    select = ",\n    ".join(f'{quote(item["column"])} AS {quote(aliases[item["id"]])}' for item in detail_columns)
    select_sql = f"SELECT\n    {select}\nFROM {relation}{where}"
    sort = request["sort"]
    row_identifier = request["detail"]["rowIdentifier"]
    if sort is not None:
        sort_parts = [f'{quote(aliases[sort["targetId"]])} {sort["direction"].upper()} NULLS {sort["nulls"].upper()}']
        sorted_column = next(item["column"] for item in detail_columns if item["id"] == sort["targetId"])
        if row_identifier is not None and row_identifier != sorted_column:
            sort_parts.append(f'{quote(row_identifier)} ASC NULLS LAST')
        select_sql += "\nORDER BY\n    " + ",\n    ".join(sort_parts)
    elif row_identifier is not None:
        select_sql += f'\nORDER BY\n    {quote(row_identifier)} ASC NULLS LAST'
    select_sql += "\nLIMIT %s OFFSET %s"
    return {
        "countSql": count_sql, "countParameters": list(parameters),
        "sql": select_sql, "parameters": [*parameters, retained_limit or request["limit"], request["offset"]],
        "columns": output_columns, "aliases": [aliases[item["id"]] for item in detail_columns],
    }
