"""Bounded pivot groups and totals over complete qualifying semantic inputs.

The wire representation is long-form. Explicit prefix depths distinguish genuine
NULL dimension values from rolled-up axes in previews, drills and CSV exports.
"""
from sqlglot import exp, parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.service import document, plan_query
from .dashboard_models import tile_dimensions
from .time_analysis import bucket_expression, source_type


META_LABELS = ["Row grouping depth", "Column grouping depth", "Pivot status",
               "Column groups (at least when capacity reached)", "Preview rows omitted",
               "Qualifying cell groups before Top N"]


def grouping_depths(tile):
    rows, columns = len(tile.row_dimensions), len(tile.column_dimensions)
    totals = tile.pivot_totals
    depths = {(rows, columns)}
    row_levels = [rows, *(range(1, rows) if totals.row_subtotals else [])]
    column_levels = [columns, *(range(1, columns) if totals.column_subtotals else [])]
    depths.update((r, c) for r in row_levels for c in column_levels)
    if totals.row_totals:
        depths.update((r, 0) for r in row_levels)
    if totals.column_totals:
        depths.update((0, c) for c in column_levels)
    if totals.grand_total:
        depths.add((0, 0))
    return sorted(depths)


def scope_kind(tile, row_depth, column_depth):
    if (row_depth, column_depth) == (len(tile.row_dimensions), len(tile.column_dimensions)):
        return "cell"
    if row_depth == column_depth == 0:
        return "grand_total"
    if column_depth == 0 and row_depth == len(tile.row_dimensions):
        return "row"
    if row_depth == 0 and column_depth == len(tile.column_dimensions):
        return "column"
    return "subtotal"


def pivot_selection(tile, selection):
    if hasattr(selection, "model_dump"):
        selection = selection.model_dump(mode="json", by_alias=True)
    if not isinstance(selection, dict) or set(selection) != {"dimensions", "measureIndex", "pivot"}:
        raise ApiProblem(422, "invalid_tile_drill", "Select a pivot cell, row, column, subtotal or grand total with its exact scope.")
    scope = selection["pivot"]
    if not isinstance(scope, dict) or set(scope) != {"kind", "rowDepth", "columnDepth"}:
        raise ApiProblem(422, "invalid_tile_drill", "A pivot selection needs its grouping depths and scope kind.")
    r, c = scope["rowDepth"], scope["columnDepth"]
    if type(r) is not int or type(c) is not int or (r, c) not in grouping_depths(tile) or scope["kind"] != scope_kind(tile, r, c):
        raise ApiProblem(422, "invalid_tile_drill", "This total or grouping is not configured on the saved pivot.")
    return {"dimensions": selection["dimensions"], "measureIndex": selection["measureIndex"],
            "rowDepth": r, "columnDepth": c}, scope["kind"]


def raw_inputs(catalog, model, tile, query, original, *, detail_fields=()):
    """Preserve the parent participation/grain while projecting raw semantic values."""
    fields = []
    for field in [*query.explore.fields, *detail_fields]:
        if (field.table, field.column) not in {(item.table, item.column) for item in fields}:
            fields.append(field.model_copy(update={"aggregate": "none"}))
    if len(fields) > 64:
        raise ApiProblem(422, "invalid_tile_drill", "Pivot and detail columns together support at most 64 semantic inputs.")
    plan = plan_query(catalog, model.definition, query.explore.model_copy(update={"fields": fields}),
        model.catalog_fingerprint, _bounded=False,
        _participation_fields=[document(field) for field in query.explore.fields],
        _scope_outer_nodes=original["outerNodes"])
    statement = parse_one(plan["sql"], read="postgres")
    references = {}
    outputs = []
    for index, (field, output) in enumerate(zip(fields, statement.expressions)):
        alias = f"input_{index + 1}"
        expression = output.this if isinstance(output, exp.Alias) else output
        time = getattr(tile, "time_analysis", None)
        if time and (field.table, field.column) == (time.table, time.column):
            expression = bucket_expression(expression, time, source_type(catalog, model.definition, field.table, field.column))
        outputs.append(expression.as_(alias, quoted=True))
        references[(field.table, field.column)] = alias
    statement.set("expressions", outputs)
    return plan, statement.sql(dialect="postgres"), references


def compile_pivot(catalog, model, tile, query, original, qualified, displayed, *, exporting=False):
    dimensions = tile_dimensions(tile)
    d, rows, columns = len(dimensions), len(tile.row_dimensions), len(tile.column_dimensions)
    _, raw, inputs = raw_inputs(catalog, model, tile, query, original)
    q = lambda name: '"' + name.replace('"', '""') + '"'
    outputs = [f"output_{i + 1}" for i in range(d + len(tile.measures))]
    axes = outputs[:d]
    row_axes, col_axes = axes[:rows], axes[rows:]
    strip = lambda sql: sql.rstrip().removesuffix(";")
    equality = lambda left, right: f"{left} IS NOT DISTINCT FROM {right}"
    correlation = " AND ".join(equality(f'cell.{q(alias)}', f'raw.{q(inputs[(field.table, field.column)])}')
        for alias, field in zip(axes, dimensions))
    ctes = [f"pivot_qualified AS ({strip(qualified['sql'])})",
            f"pivot_displayed AS ({strip(displayed['sql'])})",
            f"pivot_raw AS ({raw})",
            f"pivot_inputs AS (SELECT raw.* FROM pivot_raw raw WHERE EXISTS (SELECT 1 FROM pivot_qualified cell WHERE {correlation}))",
            "pivot_counts AS (SELECT COUNT(*) AS groups FROM pivot_qualified)"]
    col_order = ", ".join(q(name) + " ASC NULLS LAST" for name in col_axes)
    row_order = ", ".join(q(name) + " ASC NULLS LAST" for name in row_axes)
    column_cap = "" if exporting else f" LIMIT {tile.pivot_totals.maximum_columns + 1}"
    row_cap = "" if exporting else f" LIMIT {tile.pivot_totals.maximum_rows + 1}"
    ctes += [f"pivot_columns AS (SELECT DISTINCT {', '.join(map(q, col_axes))} FROM pivot_qualified ORDER BY {col_order}{column_cap})",
             "pivot_column_count AS (SELECT COUNT(*) AS columns FROM pivot_columns)",
             f"pivot_rows AS (SELECT DISTINCT {', '.join(map(q, row_axes))} FROM pivot_displayed ORDER BY {row_order}{row_cap})",
             f"pivot_visible_rows AS (SELECT * FROM pivot_rows ORDER BY {row_order}" + ("" if exporting else f" LIMIT {tile.pivot_totals.maximum_rows}") + ")"]
    capacity = "TRUE" if exporting else f"(SELECT columns FROM pivot_column_count) <= {tile.pivot_totals.maximum_columns}"
    omitted = "FALSE" if exporting else f"(SELECT COUNT(*) FROM pivot_rows) > {tile.pivot_totals.maximum_rows}"

    def visible(prefix, source, available):
        return " AND ".join(equality(f"visible.{q(alias)}", expression) for alias, expression in zip(available, prefix)) or "TRUE"

    results = []
    for r, c in grouping_depths(tile):
        retained = [*range(r), *range(rows, rows + c)]
        leaf = (r, c) == (rows, columns)
        if leaf:
            expressions = [f"data.{q(alias)}" for alias in outputs]
            conditions = [capacity,
                f"EXISTS (SELECT 1 FROM pivot_visible_rows visible WHERE {visible(expressions[:rows], 'data', row_axes)})"]
            relation, grouping = "pivot_displayed data", ""
        else:
            axis_values = [f"data.{q(inputs[(field.table, field.column)])}" for field in dimensions]
            expressions = [axis_values[i] if i in retained else "NULL" for i in range(d)]
            for measure in tile.measures:
                value = f"data.{q(inputs[(measure.table, measure.column)])}"
                expressions.append(f"COUNT(DISTINCT {value})" if measure.aggregate == "count_distinct" else f"{measure.aggregate.upper()}({value})")
            conditions = [capacity]
            if r:
                conditions.append(f"EXISTS (SELECT 1 FROM pivot_visible_rows visible WHERE {visible(axis_values[:r], 'data', row_axes[:r])})")
            relation = "pivot_inputs data"
            grouping = " GROUP BY " + ", ".join(axis_values[i] for i in retained) if retained else ""
        projection = [f"{value} AS {q(alias)}" for value, alias in zip(expressions, outputs)]
        projection += [f"{r} AS pivot_row_depth", f"{c} AS pivot_column_depth", "'data' AS pivot_status",
                       "(SELECT columns FROM pivot_column_count) AS pivot_column_count",
                       f"{omitted} AS pivot_rows_omitted", "(SELECT groups FROM pivot_counts) AS pivot_qualifying_groups"]
        results.append(f"SELECT {', '.join(projection)} FROM {relation} WHERE {' AND '.join(conditions)}{grouping}" +
                       (f" HAVING {capacity}" if not retained else ""))
    if not exporting:
        status = [f"NULL AS {q(alias)}" for alias in outputs]
        status += ["0 AS pivot_row_depth", "0 AS pivot_column_depth", "'column_capacity' AS pivot_status",
                   "columns AS pivot_column_count", "FALSE AS pivot_rows_omitted", "(SELECT groups FROM pivot_counts) AS pivot_qualifying_groups"]
        results.append(f"SELECT {', '.join(status)} FROM pivot_column_count WHERE columns > {tile.pivot_totals.maximum_columns}")
    ctes.append("pivot_result AS (" + " UNION ALL ".join(results) + ")")
    order = [f"CASE WHEN pivot_row_depth=0 OR pivot_column_depth=0 THEN 0 WHEN pivot_row_depth<{rows} OR pivot_column_depth<{columns} THEN 1 ELSE 2 END"]
    order += [q(alias) + " ASC NULLS LAST" for alias in row_axes] + ["pivot_row_depth DESC"]
    order += [q(alias) + " ASC NULLS LAST" for alias in col_axes] + ["pivot_column_depth DESC"]
    sql = "WITH " + ", ".join(ctes) + " SELECT * FROM pivot_result ORDER BY " + ", ".join(order) + ";"
    warnings = list(displayed.get("warnings", []))
    warnings.append("Pivot totals reaggregate the complete qualifying semantic input population before Top N; they are not sums of displayed cells.")
    semantic_summaries = {node.id for node in model.definition.nodes if node.derivation and node.derivation.kind == "aggregate"}
    if any(measure.table in semantic_summaries for measure in tile.measures):
        warnings.append("A measure over a model summary aggregates per-owner semantic values; it is not the original raw-child-row aggregate.")
    return {**displayed, "sql": sql, "outputLabels": [*displayed["outputLabels"], *META_LABELS], "warnings": warnings,
            "pivot": {"rowDimensions": [document(field) for field in tile.row_dimensions],
                      "columnDimensions": [document(field) for field in tile.column_dimensions],
                      "measureCount": len(tile.measures), "metadataOffset": len(outputs),
                      "maximumColumns": tile.pivot_totals.maximum_columns, "maximumRows": tile.pivot_totals.maximum_rows,
                      "totalsPopulation": "Complete qualifying cell groups before Top N", "topN": tile.top_n,
                      "export": "full long-form groups with explicit grouping depths" if exporting else "bounded matrix preview"}}
