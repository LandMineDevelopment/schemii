"""Calendar analysis over complete, filtered PostgreSQL aggregate results."""
import re

from sqlglot import exp, parse_one
from schemii.common.api.errors import ApiProblem
from schemii.schemoo.derived import type_family


def source_type(catalog, definition, table, column):
    node = next(node for node in definition.nodes if node.id == table)
    if node.derivation:
        output = next((item for item in node.derivation.outputs if item.id == column), None)
        if output:
            if output.operation in {"count", "count_distinct", "sum", "avg", "add", "subtract", "multiply", "divide"}:
                return "numeric"
            if output.operation == "list":
                return "text"
            return source_type(catalog, definition, output.nodeId or node.derivation.source, output.column)
        return source_type(catalog, definition, node.derivation.source, column)
    details = next(c for t in catalog["tables"] if t["name"] == node.table for c in t["columns"] if c["name"] == column)
    return re.sub(r"\(\d+\)", "", details.get("dataType", details.get("data_type", "")).lower()).strip()


def validate_time_analysis(tile, catalog, definition):
    analysis = getattr(tile, "time_analysis", None)
    if not analysis:
        return None
    kind = source_type(catalog, definition, analysis.table, analysis.column)
    if kind not in {"date", "timestamp", "timestamp without time zone", "timestamp with time zone", "timestamptz"}:
        raise ApiProblem(422, "invalid_time_analysis", "Time analysis requires a date or timestamp dimension.")
    if analysis.comparison != "none" or analysis.running_total:
        for field in tile.measures:
            if field.aggregate not in {"count", "count_distinct"} and type_family({"dataType": source_type(catalog, definition, field.table, field.column)}) != "numeric":
                raise ApiProblem(422, "invalid_time_analysis", "Comparisons and running totals require numeric measures.")
    return kind


def bucket_expression(expression, analysis, kind) -> exp.Expression:
    value = expression.sql(dialect="postgres")
    if kind in {"timestamptz", "timestamp with time zone"}:
        zone = exp.Literal.string(analysis.timezone).sql(dialect="postgres")
        value = f"({value} AT TIME ZONE {zone})"
    if analysis.granularity == "week" and analysis.week_start == "sunday":
        value = f"DATE_TRUNC('week', {value} + INTERVAL '1 day') - INTERVAL '1 day'"
    else:
        value = f"DATE_TRUNC('{analysis.granularity}', {value})"
    return parse_one(f"CAST({value} AS DATE)", read="postgres")


def compile_time_analysis(tile, statement, catalog, definition):
    analysis = tile.time_analysis
    kind = validate_time_analysis(tile, catalog, definition)
    time_index = next(i for i, field in enumerate(tile.dimensions) if (field.table, field.column) == (analysis.table, analysis.column))
    outputs = statement.expressions
    labels = [output.alias_or_name for output in outputs]
    source = outputs[time_index].this.copy()
    bucket = bucket_expression(source, analysis, kind)
    outputs[time_index].set("this", bucket)
    group = statement.args.get("group")
    if group:
        group.set("expressions", [bucket.copy() if item == source else item for item in group.expressions])
    statement = statement.where(exp.Not(this=exp.Is(this=source, expression=exp.Null())))
    aliases = [f"field_{i}" for i in range(len(outputs))]
    statement.set("expressions", [output.this.as_(alias, quoted=True) for output, alias in zip(outputs, aliases)])
    q = lambda alias, prefix="current": f'{prefix}."{alias}"'
    time = q(aliases[time_index])
    partitions = [q(aliases[i]) for i in range(len(tile.dimensions)) if i != time_index]
    projection = [q(alias) for alias in aliases]
    join = ""
    if analysis.comparison != "none":
        interval = "1 year" if analysis.comparison == "prior_year" else f"1 {analysis.granularity}"
        previous = f"({time} - INTERVAL '{interval}')"
        if analysis.comparison == "prior_year" and analysis.granularity == "week":
            previous = bucket_expression(parse_one(previous, read="postgres"), analysis, "date").sql(dialect="postgres")
        conditions = [f'{q(aliases[time_index], "previous")} = {previous}']
        conditions += [f'{q(aliases[i], "previous")} IS NOT DISTINCT FROM {q(aliases[i])}' for i in range(len(tile.dimensions)) if i != time_index]
        join = " LEFT JOIN buckets AS previous ON " + " AND ".join(conditions)
    metadata = []
    for i in range(len(tile.measures)):
        index = len(tile.dimensions) + i
        value = q(aliases[index])
        if analysis.comparison != "none":
            previous = q(aliases[index], "previous")
            # Cast before subtraction: bigint sums/differences must not overflow.
            difference = f"(CAST({value} AS NUMERIC) - CAST({previous} AS NUMERIC))"
            for suffix, sql in [("comparison", previous), ("change", difference), ("percent_change", f"100.0 * {difference} / NULLIF(CAST({previous} AS NUMERIC), 0)")]:
                metadata.append({"outputIndex": len(projection), "measureIndex": i, "kind": suffix})
                projection.append(sql)
                title = {"comparison": "Previous period" if analysis.comparison == "previous_period" else "Prior year", "change": "Change", "percent_change": "Change (%)"}[suffix]
                labels.append(f"{labels[index]} · {title}")
        if analysis.running_total:
            partition = "PARTITION BY " + ", ".join(partitions) + " " if partitions else ""
            metadata.append({"outputIndex": len(projection), "measureIndex": i, "kind": "running_total"})
            projection.append(f"SUM(CAST({value} AS NUMERIC)) OVER ({partition}ORDER BY {time} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)")
            labels.append(f"{labels[index]} · Running total")
    select = ", ".join(f"{value} AS {exp.to_identifier(label, quoted=True).sql(dialect='postgres')}" for value, label in zip(projection, labels))
    sql = parse_one(f"WITH buckets AS ({statement.sql(dialect='postgres')}) SELECT {select} FROM buckets AS current{join}", read="postgres")
    return sql, {"timeAnalysis": analysis.model_dump(mode="json", by_alias=True), "computedOutputs": metadata,
                 "timeDimensionIndex": time_index,
                 "warnings": ["Time analysis omits missing periods and null dates; missing comparison values and zero-baseline percentages are NULL. Partial filtered periods are compared as-is, without prorating. Running totals sum period measures within each other-dimension group over the complete filtered query. Dates and timezone-free timestamps use their calendar values; zoned timestamps use the selected timezone."]}
