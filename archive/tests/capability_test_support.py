from schemii.postgres_common import canonical_fingerprint


def _identity(oid, namespace, name, input_oid, result_oid=16):
    return {
        "oid": oid, "namespace": namespace, "name": name, "inputTypeOid": input_oid,
        "resultTypeOid": result_oid, "catalogVersion": f"test:{oid}",
    }


def capabilities(
    oid=25, *, namespace="pg_catalog", name="text", category="S", equality=True,
    ordering=True, pattern=True, aggregates=("count", "minimum", "maximum"), temporal="none",
    numeric=False,
):
    operators = []
    names = []
    if equality:
        names.extend(("eq", "neq"))
    if ordering:
        names.extend(("lt", "lte", "gt", "gte"))
    available = {logical: _identity(oid * 100 + index, namespace, {"eq": "=", "neq": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[logical], oid) for index, logical in enumerate(names, 1)}
    for logical in ("eq", "neq", "lt", "lte", "gt", "gte"):
        if logical in available:
            operators.append({"name": logical, "operator": available[logical]})
    if ordering:
        operators.append({"name": "between", "operators": {"lower": available["gte"], "upper": available["lte"]}})
    if equality:
        operators.extend(({"name": "in", "operator": available["eq"]}, {"name": "not_in", "operator": available["eq"]}))
    if pattern:
        like = _identity(oid * 100 + 20, namespace, "~~", oid)
        operators.extend({"name": logical, "operator": like} for logical in ("like", "contains", "starts_with", "ends_with"))
    operators.extend(({"name": "is_null"}, {"name": "is_not_null"}))
    order = ("eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in", "like", "contains", "starts_with", "ends_with", "is_null", "is_not_null")
    operators.sort(key=lambda item: order.index(item["name"]))
    aggregate_items = []
    aggregate_order = ("count", "sum", "average", "minimum", "maximum")
    sql_names = {"average": "avg", "minimum": "min", "maximum": "max"}
    for index, logical in enumerate(aggregate_order, 1):
        if logical in aggregates:
            aggregate_items.append({
                "name": logical,
                "aggregate": _identity(oid * 1000 + index, namespace, sql_names.get(logical, logical), oid, 1700 if numeric else oid),
                "sortable": ordering, "zeroable": numeric,
            })
    value = {
        "version": 1, "declaredTypeOid": oid, "baseTypeOid": oid,
        "declaredType": {"namespace": namespace, "name": name, "kind": "b", "category": category, "catalogVersion": f"type:{oid}"},
        "type": {"namespace": namespace, "name": name, "kind": "b", "category": category, "catalogVersion": f"type:{oid}"},
        "collation": None, "array": None, "range": None, "groupable": equality,
        "distinct": equality, "sortable": ordering, "numeric": numeric,
        "filterOperators": operators, "aggregates": aggregate_items, "temporal": temporal,
    }
    value["capabilityFingerprint"] = canonical_fingerprint(value)
    return value


def column(column_name, formatted_type, nullable, ordinal, **options):
    return {"name": column_name, "type": formatted_type, "nullable": nullable, "ordinal": ordinal, "capabilities": capabilities(**options)}


def capabilities_for_formatted_type(formatted_type):
    value = formatted_type.lower()
    if value in {"text", "character varying"}:
        return capabilities(25, name="text")
    if value in {"date"}:
        return capabilities(1082, name="date", category="D", pattern=False, temporal="date")
    if value.startswith("timestamp with"):
        return capabilities(1184, name="timestamptz", category="D", pattern=False, temporal="timestamp_tz")
    if value.startswith("timestamp"):
        return capabilities(1114, name="timestamp", category="D", pattern=False, temporal="timestamp")
    if value.startswith("numeric"):
        return capabilities(1700, name="numeric", category="N", pattern=False, numeric=True, aggregates=("count", "sum", "average", "minimum", "maximum"))
    if value in {"bigint", "integer"}:
        oid, name = (20, "int8") if value == "bigint" else (23, "int4")
        return capabilities(oid, name=name, category="N", pattern=False, numeric=True, aggregates=("count", "sum", "average", "minimum", "maximum"))
    if value == "uuid":
        return capabilities(2950, name="uuid", category="U", pattern=False)
    return capabilities(90000, namespace="test_types", name="custom", category="U", ordering=False, pattern=False, aggregates=("count",))
