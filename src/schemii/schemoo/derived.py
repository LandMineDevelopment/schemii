"""Authored calculations: visual objects need not introduce SQL relations.

Row expressions inline at their source alias. Summaries group source records
before connecting the result to a model object. Definitions contain references,
never arbitrary SQL fragments. Older owner-rooted summaries retain their grain.
"""

from datetime import date
import math
from uuid import UUID

from .models import Derivation


ARITHMETIC = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}
AGGREGATES = {"list", "count", "count_distinct", "sum", "avg", "min", "max"}
NUMERIC_TYPES = {"smallint", "integer", "bigint", "decimal", "numeric", "real", "double precision", "int2", "int4", "int8", "float4", "float8"}


def unique_keys(model, source):
    if source in model.derived:
        definition = model.derived[source]
        return [definition["groupBy"]] if definition["kind"] == "aggregate" else []
    table = model.table_details[model.keys[source]]
    keys = [key for key in table.get("uniqueKeys", []) if key and all(
        model.column_details[model.keys[source]].get(column, {}).get("nullable") is False for column in key)]
    if table.get("primaryKey"):
        keys.append(table["primaryKey"])
    return keys


def type_family(details):
    value = details.get("dataType", details.get("data_type", "")).lower().split("(")[0].strip()
    if value in NUMERIC_TYPES:
        return "numeric"
    if value in {"text", "varchar", "character varying", "char", "character"}:
        return "text"
    return value


def validate_conditions(model, output, owner, kind):
    for condition in output.get("conditions", []):
        node, column = condition["table"], condition["column"]
        if node not in model.nodes or column not in model.columns[node]:
            raise ValueError(f"A calculated-field condition references an unknown model field: {node}.{column}.")
        if condition.get("compareColumn") is not None:
            model.validate(condition)
            continue
        details = field_details(model, node, column)
        data_type = details.get("dataType", details.get("data_type", "")).lower().split("(")[0].strip()
        if condition.get("valueSource") == "today":
            if data_type and data_type != "date" and not data_type.startswith("timestamp"):
                raise ValueError("Today conditions require a date or timestamp source column.")
            continue
        domain = condition.get("domain")
        if domain:
            if node in model.derived:
                raise ValueError("Calculated condition domains must name a physical value source; use a fixed value or column comparison for a calculated output.")
            physical = model.nodes[node]["table"]
            if domain.get("nodeId", node) != node or domain.get("table", physical) not in ("", physical) or domain.get("column") != column:
                raise ValueError("The calculated condition's domain must use its own source column.")
            if domain.get("labelColumn") and domain["labelColumn"] not in model.columns[node]:
                raise ValueError("The calculated condition's domain display label is not a source column.")
        operator, value = condition["operator"], condition.get("value")
        if operator in {"is_null", "not_null"}:
            continue
        if operator in {"in", "not_in"}:
            if not isinstance(value, list) or not value:
                raise ValueError("Calculated-field IN conditions require at least one fixed value.")
            values = value
        else:
            if value is None or isinstance(value, list):
                raise ValueError("Calculated-field comparisons require one fixed value; use a NULL check or IN when appropriate.")
            values = [value]
        if operator == "contains" and not isinstance(value, str):
            raise ValueError("Contains conditions require a text value.")
        for member in values:
            if isinstance(member, str) and "\x00" in member:
                raise ValueError("Condition values cannot contain null bytes.")
            try:
                if data_type in NUMERIC_TYPES:
                    if isinstance(member, bool):
                        raise ValueError()
                    if not math.isfinite(float(member)):
                        raise ValueError()
                elif data_type == "date":
                    date.fromisoformat(member)
                elif data_type == "uuid":
                    UUID(str(member))
            except (ValueError, TypeError):
                raise ValueError(f"The condition on {model.nodes[node]['label']}.{column} requires a valid {data_type} value.") from None


TYPE_ALIASES = {"int2":"smallint", "int4":"integer", "int":"integer", "int8":"bigint", "decimal":"numeric", "float4":"real", "float8":"double precision"}
NUMERIC_ORDER = ["smallint", "integer", "bigint", "numeric", "real", "double precision"]


def normalized_type(value):
    value = value.lower().split("(")[0].strip()
    return TYPE_ALIASES.get(value, value)


def output_type(operation, left, right=""):
    """PostgreSQL result types, including integer SUM widening and COUNT bigint."""
    left, right = normalized_type(left), normalized_type(right)
    if operation in {"count", "count_distinct"}:
        return "bigint"
    if operation == "list":
        return "text"
    if operation in {"min", "max"}:
        return left
    if operation == "sum":
        return {"smallint":"bigint", "integer":"bigint", "bigint":"numeric"}.get(left, left)
    if operation == "avg":
        return "double precision" if left in {"real", "double precision"} else "numeric" if left else ""
    if not left or not right:
        return ""  # Legacy schema-free compiler fixtures have no type evidence.
    if operation == "divide":
        left = "numeric"  # expression() deliberately casts its numerator.
    return max((left, right), key=NUMERIC_ORDER.index)


def output_references(definition, output):
    source = definition["source"]
    result = [(output.get("nodeId", source), output["column"])]
    if output.get("operand"):
        result.append((output.get("operandNodeId", source), output["operand"]))
    result.extend((item["table"], item[key]) for item in output.get("conditions", [])
                  for key in ("column", "compareColumn") if item.get(key))
    return list(dict.fromkeys(result))


def field_details(model, node, column):
    if node not in model.derived:
        return model.column_details[model.keys[node]].get(column, {})
    return {"name": column, "dataType": model.field_types[(node, column)], "nullable": True}


def field_closure(model, references, *, cross_summaries=False):
    """Expand row expressions; summary inputs belong to their own SQL stage."""
    result = set()
    def visit(reference):
        if reference in result:
            return
        node, column = reference
        if node not in model.columns or column not in model.columns[node]:
            raise ValueError(f"Unknown calculated dependency {node}.{column}.")
        result.add(reference)
        if node in model.derived and (cross_summaries or model.derived[node]["kind"] == "row"):
            for dependency in model.field_dependencies.get(reference, []):
                visit(dependency)
    for reference in references:
        visit(reference)
    return result


def condition_references(conditions):
    return [(item["table"], item[key]) for item in conditions for key in ("column", "compareColumn") if item.get(key)]


def configure(model):
    """Register signatures first, then validate the field DAG independently of order."""
    model.derived, model.field_dependencies, model.field_types = {}, {}, {}
    model.columns = {key: set(model.tables[model.keys[key]]) for key in model.nodes}
    for key, node in model.nodes.items():
        if not node.get("derivation"):
            continue
        definition = Derivation.model_validate(node["derivation"]).model_dump(exclude_none=True)
        groups, outputs = definition["groupBy"], definition["outputs"]
        if len({output["id"] for output in outputs}) != len(outputs) or any(output["id"] in groups for output in outputs):
            raise ValueError(f"Calculated source {key} outputs need unique IDs distinct from grouping keys.")
        model.derived[key] = definition
        model.columns[key] = set(groups) | {output["id"] for output in outputs}
    model.anchors, model.relation_owners = {}, {}
    visiting_sources = []
    def source_owner(node):
        if node in model.anchors:
            return
        if node not in model.nodes:
            raise ValueError(f"Unknown calculation source on dependency path {' -> '.join([*visiting_sources, node])}.")
        if node in visiting_sources:
            raise ValueError(f"Unsupported nested calculation source cycle: {' -> '.join([*visiting_sources, node])}.")
        if node not in model.derived:
            model.anchors[node] = model.relation_owners[node] = node
            return
        visiting_sources.append(node)
        definition = model.derived[node]
        source = definition["source"]
        source_owner(source)
        if model.keys[node] != model.keys[source]:
            raise ValueError(f"Calculated source {node} must reference its owning source's qualified physical table.")
        target = (definition.get("connection") or {}).get("target", source)
        if target != source:
            if target not in model.nodes or target in model.derived:
                raise ValueError("Choose a physical model object as the summary connection target.")
            source_owner(target)
        model.anchors[node] = model.anchors[target]
        model.relation_owners[node] = node if definition["kind"] == "aggregate" else model.relation_owners[source]
        visiting_sources.pop()
    for node in model.nodes:
        source_owner(node)
    for key, definition in model.derived.items():
        source, groups = definition["source"], definition["groupBy"]
        if len(set(groups)) != len(groups) or any(group not in model.columns[source] for group in groups):
            raise ValueError("Choose distinct, existing source columns for the grouping keys.")
        if definition["kind"] == "aggregate" and not groups:
            raise ValueError("An aggregate source requires at least one grouping key.")
        if definition["kind"] == "row" and groups:
            raise ValueError("Row calculations do not have grouping keys.")
        for column in groups:
            model.field_dependencies[(key, column)] = [(source, column)]
        for output in definition["outputs"]:
            model.field_dependencies[(key, output["id"])] = [*output_references(definition, output), *((source, column) for column in groups)]
    visiting, order = [], []
    def infer(reference):
        if reference in model.field_types:
            return model.field_types[reference]
        node, column = reference
        path = " -> ".join(f"{key}.{field}" for key, field in [*visiting, reference])
        if node not in model.columns or column not in model.columns[node]:
            raise ValueError(f"Calculated dependency {path} references an unknown source column.")
        if reference in visiting:
            raise ValueError(f"Calculated field dependency cycle: {path}.")
        if node not in model.derived:
            details = model.column_details[model.keys[node]].get(column, {})
            value = details.get("dataType", details.get("data_type", ""))
        else:
            visiting.append(reference)
            definition = model.derived[node]
            for dependency in model.field_dependencies[reference]:
                infer(dependency)
            output = next((item for item in definition["outputs"] if item["id"] == column), None)
            if output is None:
                value = infer((definition["source"], column))
            else:
                refs = output_references(definition, output)
                left = infer(refs[0])
                operation = output["operation"]
                right = infer((output.get("operandNodeId", definition["source"]), output["operand"])) if output.get("operand") else ""
                if operation in {*ARITHMETIC, "sum", "avg"}:
                    for kind in ([left, right] if operation in ARITHMETIC else [left]):
                        if kind and normalized_type(kind) not in NUMERIC_ORDER:
                            raise ValueError(f"{operation} requires numeric columns on dependency path {path}; found {kind}.")
                if definition["kind"] == "row":
                    if operation not in ARITHMETIC or not output.get("operand"):
                        raise ValueError("Row calculations require an arithmetic operation and a second source column.")
                    if any(model.anchors[ref_node] != model.anchors[definition["source"]] for ref_node, _ in refs):
                        raise ValueError(f"Row calculation {node}.{column} inputs and conditions must share its owning record or mapped summary grain.")
                elif operation not in AGGREGATES or output.get("operand") or output.get("operandNodeId"):
                    raise ValueError("Aggregate sources accept list, count, sum, average, minimum, or maximum outputs.")
                if definition["kind"] == "aggregate" and any(ref_node == node for ref_node, _ in refs):
                    raise ValueError(f"Summary {node}.{column} must read a separate materialized summary for reaggregation; it cannot flatten aggregate outputs at its own stage.")
                value = output_type(operation, left, right)
            visiting.pop()
            order.append(reference)
        model.field_types[reference] = value
        return value
    for node in model.nodes:
        for column in model.columns[node]:
            infer((node, column))
    model.field_order = order
    # A field DAG may still request mutually recursive SQL materializations via
    # different outputs. Every summary must depend on an earlier relation stage.
    stages = {}
    for node, definition in model.derived.items():
        if definition["kind"] != "aggregate":
            continue
        references = [reference for (owner, _), dependencies in model.field_dependencies.items()
                      if owner == node for reference in dependencies]
        stages[node] = {key for key, _ in field_closure(model, references)
                        if key in model.derived and model.derived[key]["kind"] == "aggregate"}
    stage_path, completed = [], set()
    def stage(node):
        if node in stage_path:
            raise ValueError(f"Calculated summary stage cycle: {' -> '.join([*stage_path, node])}; split the calculation into independent materialized summaries.")
        if node in completed:
            return
        stage_path.append(node)
        for dependency in sorted(stages[node]):
            stage(dependency)
        stage_path.pop()
        completed.add(node)
    for node in stages:
        stage(node)
    for key, definition in model.derived.items():
        source, groups = definition["source"], definition["groupBy"]
        connection = definition.get("connection")
        if connection:
            if definition["kind"] != "aggregate":
                raise ValueError("Only aggregate summaries have a model connection.")
            target = connection["target"]
            mappings = connection["columns"]
            sources, targets = [pair["source"] for pair in mappings], [pair["target"] for pair in mappings]
            if len(sources) != len(groups) or set(sources) != set(groups):
                raise ValueError("The summary connection must map every grouping key exactly once.")
            if len(set(targets)) != len(targets) or any(column not in model.columns[target] for column in targets):
                raise ValueError("Choose distinct, existing target columns for the summary connection.")
            if not any(set(key).issubset(targets) for key in unique_keys(model, target)):
                raise ValueError("The summary connection must include the target's complete primary key or a nonnullable unique key.")
            for pair in mappings:
                left, right = type_family(field_details(model, source, pair["source"])), type_family(field_details(model, target, pair["target"]))
                if left and right and left != right:
                    raise ValueError("Summary connection columns must have compatible data types.")
        elif definition["kind"] == "aggregate" and not any(set(key).issubset(groups) for key in unique_keys(model, source)):
            raise ValueError("Grouping must include the owner's complete primary key or a nonnullable unique key, so each summary belongs to one owner record.")
        for output in definition["outputs"]:
            validate_conditions(model, output, source, definition["kind"])


def attach(model):
    for key, definition in model.derived.items():
        edge_id = f"derived:{key}"
        if edge_id in model.edges:
            raise ValueError("Relationship IDs cannot collide with derived-source associations.")
        source = definition["source"]
        groups = definition["groupBy"]
        connection = definition.get("connection")
        target = connection["target"] if connection else source
        mapping = connection["columns"][0] if connection else None
        model.edges[edge_id] = {"id": edge_id, "source": key, "target": target,
                                "sourceColumn": mapping["source"] if mapping else groups[0] if groups else "",
                                "targetColumn": mapping["target"] if mapping else groups[0] if groups else "",
                                "columns": connection["columns"] if connection else [{"source": column, "target": column} for column in groups],
                                "derived": True, "enabled": True}
        model.graph[key].append((target, edge_id))
        model.graph[target].append((key, edge_id))
    # Finish the graph before validating dependencies: the model's cached blocks
    # must describe every relationship, including these leaf associations.
    for definition in model.derived.values():
        source = definition["source"]
        for output in definition["outputs"]:
            contributor = output.get("nodeId", source)
            allowed = model.possible_path_nodes(source, contributor)
            anchors = {model.anchors[node] for node in allowed}
            if any(model.anchors[condition["table"]] not in anchors for condition in output.get("conditions", [])):
                raise ValueError("Calculated-field conditions must use sources on the owner's path to that output; a condition cannot add a separate relationship branch.")


def expression(output, value, operand, identifier, literal):
    operation = output["operation"]
    if operation in ARITHMETIC:
        # Division uses numeric arithmetic and returns NULL for a zero divisor.
        if operation == "divide":
            return f"(CAST({value} AS NUMERIC) / NULLIF({operand}, 0))"
        return f"({value} {ARITHMETIC[operation]} {operand})"
    distinct = "DISTINCT " if output.get("distinct") or operation == "count_distinct" else ""
    if operation == "list":
        value = f"CAST({value} AS TEXT)"
        return f"STRING_AGG({distinct}{value}, {literal(output.get('delimiter', ', '))} ORDER BY {value})"
    function = "COUNT" if operation == "count_distinct" else operation.upper()
    return f"{function}({distinct}{value})"
