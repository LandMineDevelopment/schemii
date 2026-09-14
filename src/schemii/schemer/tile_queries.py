"""Compile dashboard tiles and their contributing rows from saved model rules.

Drill projections retain the original participating paths. Added detail branches
are LEFT joins; they cannot activate previously inactive conditional scopes or
change a required scope's correlation. They can repeat contributors, which is
reported explicitly rather than implying a drill row count equals a measure.
"""

from sqlglot import exp, parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.models import ExploreState
from schemii.schemoo.service import document, load_model, model_catalog, plan_query
from schemii.schemoo.prototype import _literal
from .models import ReportQuery


def _problem(message):
    raise ApiProblem(422, "invalid_tile_drill", message)


def _key(field):
    return field.table, field.column


def tile_report_query(dashboard, tile, selections=None):
    fields = tile.detail_fields if tile.kind == "detail" else [*tile.dimensions, *tile.measures]
    return ReportQuery(model_id=dashboard.model_id, expected_revision=dashboard.model_revision,
                       explore=ExploreState(fields=fields, selections=dashboard.selections if selections is None else selections,
                                            reportFilters=tile.report_filters, limit=tile.limit))


def _text_order_fields(catalog, definition, fields):
    """Only coerce known PostgreSQL types lacking ordinary ordering operators."""
    nodes = {node.id: node for node in definition.nodes}
    tables = {table["name"]: table for table in catalog["tables"]}

    def needs_text(table, column, aggregate="none"):
        if aggregate not in {"none", "min", "max"}:
            return False
        node = nodes[table]
        if node.derivation:
            output = next((item for item in node.derivation.outputs if item.id == column), None)
            if output:
                return output.operation in {"min", "max"} and needs_text(output.nodeId or node.derivation.source, output.column)
            return needs_text(node.derivation.source, column)
        details = next((item for item in tables[node.table]["columns"] if item["name"] == column), {})
        kind = details.get("dataType", details.get("data_type", "")).lower().strip()
        return kind.removesuffix("[]") in {"json", "xml", "point", "line", "lseg", "box", "path", "polygon", "circle"}

    return {index for index, field in enumerate(fields) if needs_text(field.table, field.column, field.aggregate)}


def _ordered(plan, sql, page_size, text_order_fields=()):
    # One retained execution supplies forward-only batches. Sorting happens
    # once; subsequent result requests advance its cursor without rerunning SQL.
    # Keep numeric/date dimensions in their native order (not 1, 10, 2).
    # JSON and geometric values without ordering operators use their text form.
    output_labels = [output.alias_or_name for output in sql.expressions]
    # PostgreSQL silently truncates identifiers to 63 bytes. Author-facing
    # labels can therefore collide after truncation (especially when a model
    # has a descriptive source name and several similarly named fields). Use
    # compact, deterministic SQL aliases for the nested result and retain the
    # complete labels as response metadata for the dashboard UI.
    aliases = [f"output_{index + 1}" for index in range(len(output_labels))]
    sql.set("expressions", [
        (output.this if isinstance(output, exp.Alias) else output).as_(alias, quoted=True)
        for output, alias in zip(sql.expressions, aliases)
    ])
    sql = exp.select(*(exp.column(alias, table="page_rows", quoted=True) for alias in aliases)).from_(sql.subquery("page_rows"))
    ordering = []
    for index, alias in enumerate(aliases):
        expression = exp.column(alias, table="page_rows", quoted=True)
        if index in text_order_fields:
            expression = exp.Cast(this=expression, to=exp.DataType.build("text"))
        ordering.append(expression)
    sql = sql.order_by(*ordering)
    return {**plan, "sql": sql.sql(dialect="postgres") + ";", "rowLimit": page_size,
            "outputLabels": output_labels}


def tile_plan(services, owner, dashboard, tile_id, *, selections=None, selection=None, fresh=False):
    """Return (saved model, unbounded SQL plan) for one retained execution.

    selection is {dimensions: [{table, column, value}], measureIndex: int}.
    No SQL, root overrides, filter operators or arbitrary fields are accepted.
    """
    tile = next((item for item in dashboard.tiles if item.id == tile_id), None)
    if tile is None:
        raise ApiProblem(404, "tile_not_found", "This dashboard tile no longer exists.")
    query = tile_report_query(dashboard, tile, selections)
    model = load_model(services, owner, query.model_id, query.expected_revision)
    query.explore.root = model.definition.root
    optional_selections = getattr(tile, "selections", {})
    required = {scope.id for scope in model.definition.scopes if scope.kind == "required"}
    if required & optional_selections.keys():
        _problem("Required model slicers belong to the whole dashboard and cannot be overridden by a tile.")
    configured = set(getattr(dashboard, "optional_filters", []))
    unauthorized = {scope.id for scope in model.definition.scopes
                    if scope.requirement == "optional" and scope.id not in configured}
    if any(query.explore.selections.get(scope_id, None) and query.explore.selections[scope_id].active
           for scope_id in unauthorized):
        _problem("This optional model filter is not available on the dashboard.")
    query.explore.selections = {**query.explore.selections, **optional_selections}
    catalog = model_catalog(services, owner, model, fresh=fresh)
    original = plan_query(catalog, model.definition, query.explore, model.catalog_fingerprint, _bounded=False)
    if selection is None:
        return model, _ordered(original, parse_one(original["sql"], read="postgres"), tile.limit,
                            _text_order_fields(catalog, model.definition, query.explore.fields))
    if hasattr(selection, "model_dump"):
        selection = selection.model_dump(mode="json", by_alias=True)
    if tile.kind == "detail" or not tile.detail_fields:
        _problem("Choose drill-through detail columns in the tile editor first.")
    if not isinstance(selection, dict) or set(selection) != {"dimensions", "measureIndex"}:
        _problem("Select the dimensions and measure of a tile result.")
    measure_index = selection["measureIndex"]
    if type(measure_index) is not int or not 0 <= measure_index < len(tile.measures):
        _problem("The selected measure does not belong to this tile.")
    values = selection["dimensions"]
    if not isinstance(values, list) or len(values) != len(tile.dimensions):
        _problem("Supply exactly the dimensions displayed by this tile.")
    selected = {}
    for item in values:
        if not isinstance(item, dict) or set(item) != {"table", "column", "value"}:
            _problem("A dimension selection needs its table, column and value.")
        key = item["table"], item["column"]
        if key in selected or key not in {_key(field) for field in tile.dimensions}:
            _problem("The selected dimensions do not match this tile.")
        value = item["value"]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            _problem("Select one scalar value for each dimension.")
        selected[key] = value
    raw_fields = []
    for field in [*query.explore.fields, *tile.detail_fields]:
        if _key(field) not in {_key(existing) for existing in raw_fields}:
            raw_fields.append(field.model_copy(update={"aggregate": "none"}))
    if len(raw_fields) > 64:
        _problem("Tile and detail columns together must use at most 64 source fields.")
    raw = query.explore.model_copy(update={"fields": raw_fields})
    plan = plan_query(catalog, model.definition, raw, model.catalog_fingerprint, _bounded=False,
                      _participation_fields=[document(field) for field in query.explore.fields],
                      _scope_outer_nodes=original["outerNodes"])
    statement = parse_one(plan["sql"], read="postgres")
    labels = {_key(field): output.alias for field, output in zip(raw_fields, statement.expressions)}
    column = lambda key: exp.column(labels[key], table="contributors", quoted=True)
    conditions = []
    for key, value in selected.items():
        if value is None:
            conditions.append(exp.Is(this=column(key), expression=exp.Null()))
        else:
            try:
                literal = parse_one(_literal(value), read="postgres")
            except (ValueError, TypeError):
                _problem("The selected dimension has an invalid value.")
            conditions.append(exp.EQ(this=column(key), expression=literal))
    # SQL aggregates ignore NULL inputs, including count(column) and DISTINCT.
    # MIN/MAX drill shows all inputs used to compute the extremum, not just ties.
    measure = tile.measures[measure_index]
    conditions.append(exp.Not(this=exp.Is(this=column(_key(measure)), expression=exp.Null())))
    statement = exp.select(*(column(_key(field)) for field in tile.detail_fields)).from_(statement.subquery("contributors")).where(*conditions)
    warnings = list(plan.get("warnings", []))
    if set(plan["outerNodes"]) - set(original["outerNodes"]):
        warnings.append("Related detail branches can repeat a contributing row. Detail row counts are not the chart measure.")
    if measure.aggregate == "count_distinct":
        warnings.append("These are raw contributing rows; repeated values count once in COUNT DISTINCT.")
    plan.update(warnings=warnings, grain="Raw rows contributing to the selected measure", drill=True)
    return model, _ordered(plan, statement, tile.limit,
                        _text_order_fields(catalog, model.definition, tile.detail_fields))
