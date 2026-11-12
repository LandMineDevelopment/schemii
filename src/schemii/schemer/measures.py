"""Typed report arithmetic over complete semantic aggregates, never cached rows."""

from decimal import Decimal
from sqlglot import exp, parse_one
from schemii.common.api.errors import ApiProblem
from schemii.schemoo.derived import normalized_type, output_type, NUMERIC_ORDER
from schemii.schemoo.service import document
from .dashboard_models import ReportOutput, base_measures
from .time_analysis import source_type

REPORT_NAMESPACE = "@report"


def key(output):
    return output.table, output.column, output.aggregate, output.kind


def reference(calculation):
    return ReportOutput(
        table=REPORT_NAMESPACE, column=calculation.id, kind="calculated"
    )


def fail(message):
    raise ApiProblem(422, "invalid_calculated_measure", message)


def output_definitions(tile):
    from .dashboard_models import tile_dimensions

    fields = (
        tile.detail_fields
        if tile.kind == "detail"
        else [*tile_dimensions(tile), *base_measures(tile)]
    )
    result = [ReportOutput(**document(field)) for field in fields]
    time = getattr(tile, "time_analysis", None)
    if time:
        for field in base_measures(tile):
            kinds = (
                ["comparison", "change", "percent_change"]
                if time.comparison != "none"
                else []
            )
            if time.running_total:
                kinds.append("running_total")
            result.extend(ReportOutput(**document(field), kind=kind) for kind in kinds)
    return [*result, *(reference(item) for item in getattr(tile, "calculations", []))]


class MeasureGraph:
    def __init__(self, tile, catalog, definition):
        self.tile, self.catalog, self.definition = tile, catalog, definition
        self.outputs = output_definitions(tile)
        self.positions = {key(item): index for index, item in enumerate(self.outputs)}
        self.calculations = {
            item.id: item for item in getattr(tile, "calculations", [])
        }
        self.types, self.scopes, self.constituents, self.order = {}, {}, {}, []
        self.population = {}
        path = []

        def infer(identifier):
            if identifier in self.types:
                return self.types[identifier]
            if identifier in path:
                fail(
                    f"Calculated measure cycle: {' → '.join([*path, identifier])}. Rebind an operand to break the cycle."
                )
            item = self.calculations.get(identifier)
            if item is None:
                fail(
                    f"Missing calculation on dependency path {' → '.join([*path, identifier])}."
                )
            path.append(identifier)
            scopes = set()
            constituents = []
            for operand in [item.left, item.right]:
                if operand.constant is not None:
                    continue
                output = operand.output
                if key(output) not in self.positions:
                    fail(
                        f"{item.label}: referenced output was removed. Restore its base input or choose a replacement."
                    )
                if output.kind == "calculated":
                    if operand.scope != "group":
                        fail(
                            f"{item.label}: population operands must reference a base aggregate, not another calculation."
                        )
                    infer(output.column)
                    scopes.update(self.scopes[output.column])
                    constituents.extend(self.constituents[output.column])
                else:
                    self.validate_numeric(output, item.label)
                    if operand.scope != "group":
                        if output.kind != "value" or output.aggregate == "none":
                            fail(
                                f"{item.label}: population scopes require a raw/semantic base aggregate. Choose SUM, AVG, COUNT, DISTINCT, MIN or MAX."
                            )
                        time = getattr(tile, "time_analysis", None)
                        if time and (time.comparison != "none" or time.running_total):
                            fail(
                                f"{item.label}: population denominators cannot combine calendar windows. Use period values or group-scope operands."
                            )
                        self.population[(key(output), operand.scope)] = output
                    scopes.add(operand.scope)
                    constituents.append(operand.model_dump(exclude_none=True))
            path.pop()
            self.types[identifier] = "numeric"
            self.scopes[identifier] = scopes
            self.constituents[identifier] = list(
                {str(value): value for value in constituents}.values()
            )
            self.order.append(identifier)
            return "numeric"

        for identifier in self.calculations:
            infer(identifier)
        for predicate in getattr(tile, "aggregate_filters", []):
            if (
                predicate.kind == "calculated"
                and "qualifying_groups" in self.scopes.get(predicate.column, set())
            ):
                fail(
                    f"Result filter on {predicate.column} would define its own qualifying-groups denominator. Filter an independent measure or use the source population."
                )
        used = {
            key(operand.output)
            for item in self.calculations.values()
            for operand in (item.left, item.right)
            if operand.output is not None
        }
        used.update(
            key(item)
            for item in [
                *getattr(tile, "sort", []),
                *getattr(tile, "aggregate_filters", []),
            ]
        )
        if (
            getattr(tile, "kpi_comparison", None)
            and tile.kpi_comparison.baseline.output
        ):
            used.add(key(tile.kpi_comparison.baseline.output))
        for field in getattr(tile, "hidden_measures", []):
            if not any(
                identity[:3] == (field.table, field.column, field.aggregate)
                for identity in used
            ):
                fail(
                    f"Hidden input {field.table}.{field.column} is unused. Bind it to a calculation/sort/filter or explicitly remove it; unrelated branches cannot be activated implicitly."
                )
        for item in self.calculations.values():
            if not item.visible and key(reference(item)) not in used:
                fail(
                    f"Hidden calculation {item.label} is unused. Display it, bind a reference, or explicitly remove it."
                )
        if tile.kind == "kpi" and any(
            field.aggregate == "none" for field in base_measures(tile)
        ):
            fail(
                "KPI calculations and comparisons require explicitly aggregated inputs to produce a single value. Choose SUM, AVG, COUNT, MIN or MAX."
            )
        comparison = getattr(tile, "kpi_comparison", None)
        if comparison:
            primary = self.outputs[self.visible_indices()[0]]
            if primary.kind != "calculated":
                self.validate_numeric(primary, "KPI value")
        if comparison and comparison.baseline.output:
            operand = comparison.baseline
            if key(operand.output) not in self.positions:
                fail(
                    "The KPI baseline output was removed. Restore it or choose a replacement."
                )
            if operand.output.kind == "calculated":
                infer(operand.output.column)
            else:
                self.validate_numeric(operand.output, "KPI baseline")
            if operand.scope != "group":
                if operand.output.kind != "value" or operand.output.aggregate == "none":
                    fail("KPI population baselines require a base aggregate.")
                self.population[(key(operand.output), operand.scope)] = operand.output

    def validate_grain(self, plan):
        if (
            getattr(self.tile, "calculations", [])
            or getattr(self.tile, "hidden_measures", [])
            or getattr(self.tile, "kpi_comparison", None)
        ) and plan.get("repetitionDiagnostics"):
            fail(
                "Calculated inputs cross a multiplying relationship grain. Use owner-grain model summaries or independent measures before composing this calculation."
            )

    def validate_numeric(self, output, label):
        from .dashboard_models import tile_dimensions

        if any(
            (field.table, field.column, field.aggregate)
            == (output.table, output.column, output.aggregate)
            for field in tile_dimensions(self.tile)
        ):
            fail(f"{label}: choose an aggregate measure, not a grouping dimension.")
        kind = (
            output_type(
                output.aggregate,
                source_type(self.catalog, self.definition, output.table, output.column),
            )
            if output.aggregate != "none"
            else source_type(self.catalog, self.definition, output.table, output.column)
        )
        if output.kind in {"change", "percent_change", "running_total"}:
            kind = "numeric"
        if normalized_type(kind) not in NUMERIC_ORDER:
            fail(
                f"{label}: arithmetic requires numeric measures; found {kind or 'unknown type'}."
            )

    def visible_indices(self):
        hidden = {
            key(ReportOutput(**document(field)))
            for field in getattr(self.tile, "hidden_measures", [])
        }
        hidden_base = {
            (field.table, field.column, field.aggregate)
            for field in getattr(self.tile, "hidden_measures", [])
        }
        return [
            index
            for index, output in enumerate(self.outputs)
            if (
                self.calculations[output.column].visible
                if output.kind == "calculated"
                else key(output) not in hidden
                and (output.table, output.column, output.aggregate) not in hidden_base
            )
        ]

    def metadata(self):
        visible = self.visible_indices()
        return [
            {
                "id": identifier,
                "dataType": "numeric",
                "output": document(reference(self.calculations[identifier])),
                "label": self.calculations[identifier].label,
                "displayLabel": self.calculations[identifier].label
                + (
                    " (%)"
                    if self.calculations[identifier].operation == "percent_of"
                    else ""
                ),
                "unit": "percent"
                if self.calculations[identifier].operation == "percent_of"
                else "number",
                "scale": 100
                if self.calculations[identifier].operation == "percent_of"
                else 1,
                "index": visible.index(
                    self.positions[key(reference(self.calculations[identifier]))]
                )
                if self.positions[key(reference(self.calculations[identifier]))]
                in visible
                else None,
                "constituents": self.constituents[identifier],
            }
            for identifier in self.calculations
        ]


def q(name):
    return '"' + name.replace('"', '""') + '"'


def predicate_sql(index, predicate, alias="groups"):
    value = f"{alias}.{q('measure_' + str(index))}"
    if predicate.operator in {"is_null", "not_null"}:
        return value + (
            " IS NULL" if predicate.operator == "is_null" else " IS NOT NULL"
        )
    from schemii.schemoo.prototype import _literal

    return (
        value
        + " "
        + {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[
            predicate.operator
        ]
        + " "
        + _literal(predicate.value)
    )


def arithmetic(item, left, right):
    left, right = f"CAST({left} AS NUMERIC)", f"CAST({right} AS NUMERIC)"
    if item.operation in {"divide", "percent_of"}:
        ratio = f"({left} / NULLIF({right},0))"
        return f"({ratio} * 100)" if item.operation == "percent_of" else ratio
    return (
        f"({left} "
        + {"add": "+", "subtract": "-", "multiply": "*"}[item.operation]
        + f" {right})"
    )


def raw_aggregate(output, alias):
    return (
        f"COUNT(DISTINCT {alias})"
        if output.aggregate == "count_distinct"
        else f"{output.aggregate.upper()}({alias})"
    )


def compile_measures(graph, statement, *, populations=None, qualification_filters=()):
    """Append each DAG node in a separate SQL stage, preserving all base outputs."""
    populations = populations or {}
    base_count = len(graph.outputs) - len(graph.calculations)
    labels = [output.alias_or_name for output in statement.expressions[:base_count]]
    trailing = [output.alias_or_name for output in statement.expressions[base_count:]]
    statement = statement.copy()
    statement.set(
        "expressions",
        [
            (output.this if isinstance(output, exp.Alias) else output).as_(
                f"measure_{index}"
                if index < base_count
                else f"trailing_{index - base_count}",
                quoted=True,
            )
            for index, output in enumerate(statement.expressions)
        ],
    )
    ctes = [f"measure_base AS ({statement.sql(dialect='postgres')})"]
    previous = "measure_base"
    completed = set()
    pop_names = {scope: f"population_{scope}" for _, scope in graph.population}
    for scope, sql in populations.items():
        if scope != "qualifying_groups":
            ctes.append(f"{pop_names[scope]} AS ({sql})")

    def operand_sql(operand, alias="groups"):
        if operand.constant is not None:
            return f"CAST({str(Decimal(str(operand.constant)))} AS NUMERIC)"
        if operand.scope != "group":
            position = graph.positions[key(operand.output)]
            return f"(SELECT {q('measure_' + str(position))} FROM {pop_names[operand.scope]})"
        return f"{alias}.{q('measure_' + str(graph.positions[key(operand.output)]))}"

    def stage(identifier):
        nonlocal previous
        item = graph.calculations[identifier]
        position = graph.positions[key(reference(item))]
        name = f"measure_stage_{len(completed)}"
        ctes.append(
            f"{name} AS (SELECT groups.*, {arithmetic(item, operand_sql(item.left), operand_sql(item.right))} AS {q('measure_' + str(position))} FROM {previous} groups)"
        )
        previous = name
        completed.add(identifier)

    for identifier in graph.order:
        if "qualifying_groups" not in graph.scopes[identifier]:
            stage(identifier)
    # Independent filters define the population before ranking/top N. A filter
    # depending on this population was rejected by MeasureGraph as circular.
    qualification = (
        " AND ".join(
            predicate_sql(index, predicate)
            for index, predicate in qualification_filters
        )
        or "TRUE"
    )
    ctes.append(
        f"measure_qualifying AS (SELECT groups.* FROM {previous} groups WHERE {qualification})"
    )
    if "qualifying_groups" in populations:
        ctes.append(
            f"{pop_names['qualifying_groups']} AS ({populations['qualifying_groups']})"
        )
    for identifier in graph.order:
        if identifier not in completed:
            stage(identifier)
    output_labels = [
        *labels,
        *[
            item.label + (" (%)" if item.operation == "percent_of" else "")
            for item in graph.calculations.values()
        ],
    ]
    expressions = [
        f"groups.{q('measure_' + str(index))} AS {q(label)}"
        for index, label in enumerate(output_labels)
    ]
    kpi = getattr(graph.tile, "kpi_comparison", None)
    kpi_metadata = None
    if kpi:
        current = f"groups.{q('measure_' + str(graph.visible_indices()[0]))}"
        baseline = operand_sql(kpi.baseline)
        values = [
            baseline,
            f"({current} - {baseline})",
            f"(CAST(({current} - {baseline}) AS NUMERIC) / NULLIF(ABS({baseline}),0) * 100)",
        ]
        status = (
            f"CASE WHEN {current} IS NULL OR {baseline} IS NULL THEN 'unavailable' WHEN {current} = {baseline} THEN 'at baseline' WHEN "
            + (
                f"{current} > {baseline}"
                if kpi.favorable == "higher"
                else f"{current} < {baseline}"
                if kpi.favorable == "lower"
                else "FALSE"
            )
            + " THEN 'favorable' ELSE 'unfavorable' END"
        )
        values.append(status)
        primary_output = graph.outputs[graph.visible_indices()[0]]
        percent_primary = (
            primary_output.kind == "calculated"
            and graph.calculations[primary_output.column].operation == "percent_of"
        )
        kpi_metadata = {
            "label": kpi.label,
            "favorable": kpi.favorable,
            "indices": [len(graph.visible_indices()) + index for index in range(4)],
            "percentUnit": "percent",
            "percentScale": 100,
            "baselineUnit": "percent" if percent_primary else "number",
            "baselineScale": 100 if percent_primary else 1,
            "absoluteUnit": "percentage_points" if percent_primary else "number",
            "absoluteScale": 1,
        }
        expressions.extend(
            f"{value} AS {q(label)}"
            for value, label in zip(
                values,
                [kpi.label, "Absolute variance", "Variance (%)", "KPI assessment"],
            )
        )
        output_labels += [
            kpi.label,
            "Absolute variance",
            "Variance (%)",
            "KPI assessment",
        ]
    expressions.extend(
        f"groups.{q('trailing_' + str(index))} AS {q(label)}"
        for index, label in enumerate(trailing)
    )
    sql = (
        "WITH "
        + ", ".join(ctes)
        + " SELECT "
        + ", ".join(expressions)
        + f" FROM {previous} groups"
    )
    return parse_one(sql, read="postgres"), {
        "calculatedMeasures": graph.metadata(),
        "kpiComparison": kpi_metadata,
        "calculationPopulation": "complete query before Top N; no cached rows",
    }


def population_queries(
    graph, catalog, model, tile, query, original, *, qualifying=None
):
    """Reaggregate raw semantic inputs, including AVG and COUNT DISTINCT."""
    if not graph.population:
        return {}
    from .pivot import raw_inputs
    from .dashboard_models import tile_dimensions

    _, raw, inputs, axis_inputs = raw_inputs(catalog, model, tile, query, original)
    result = {}
    for scope in {scope for _, scope in graph.population}:
        expressions = []
        for (identity, population), output in graph.population.items():
            if population != scope:
                continue
            alias = f"raw.{q(inputs[(output.table, output.column)])}"
            expressions.append(
                f"{raw_aggregate(output, alias)} AS {q('measure_' + str(graph.positions[identity]))}"
            )
        where = ""
        calendar = getattr(tile, "time_analysis", None)
        if calendar and scope == "source_population":
            # Calendar reports omit undated records before grouping. Their
            # source population must retain the same eligibility boundary.
            where = f" WHERE raw.{q(axis_inputs[(calendar.table, calendar.column)])} IS NOT NULL"
        if scope == "qualifying_groups":
            relation = (
                f"({qualifying['sql'].rstrip().removesuffix(';')})"
                if qualifying
                else "measure_qualifying"
            )
            correlations = []
            for index, field in enumerate(tile_dimensions(tile)):
                name = f"output_{index + 1}" if qualifying else f"measure_{index}"
                correlations.append(
                    f"qualified.{q(name)} IS NOT DISTINCT FROM raw.{q(axis_inputs[(field.table, field.column)])}"
                )
            where = (
                f" WHERE EXISTS (SELECT 1 FROM {relation} qualified"
                + (" WHERE " + " AND ".join(correlations) if correlations else "")
                + ")"
            )
        result[scope] = (
            "SELECT " + ", ".join(expressions) + f" FROM ({raw}) raw" + where
        )
    return result
