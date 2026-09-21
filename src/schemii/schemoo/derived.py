"""Authored calculations: visual objects need not introduce SQL relations.

Row expressions inline at their source alias. Summaries group source records
before connecting the result to a model object. Definitions contain references,
never arbitrary SQL fragments. Older owner-rooted summaries retain their grain.
"""

from collections import deque
from datetime import date
import math
from uuid import UUID

from .models import Derivation


ARITHMETIC = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}
AGGREGATES = {"list", "count", "count_distinct", "sum", "avg", "min", "max"}
NUMERIC_TYPES = {"smallint", "integer", "bigint", "decimal", "numeric", "real", "double precision", "int2", "int4", "int8", "float4", "float8"}


def unique_keys(model, source):
    table = model.table_details[model.nodes[source]["table"]]
    keys = [key for key in table.get("uniqueKeys", []) if key and all(
        model.column_details[table["name"]].get(column, {}).get("nullable") is False for column in key)]
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
        if node not in model.nodes or model.nodes[node].get("derivation") or column not in model.columns[node]:
            raise ValueError("A calculated-field condition references an unknown physical model field.")
        if kind == "row" and node != owner:
            raise ValueError("Row calculation conditions must use fields on the owning source.")
        if condition.get("compareColumn") is not None:
            model.validate(condition)
            continue
        details = model.column_details[model.nodes[node]["table"]][column]
        data_type = details.get("dataType", details.get("data_type", "")).lower().split("(")[0].strip()
        if condition.get("valueSource") == "today":
            if data_type and data_type != "date" and not data_type.startswith("timestamp"):
                raise ValueError("Today conditions require a date or timestamp source column.")
            continue
        domain = condition.get("domain")
        if domain:
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


def configure(model):
    """Validate derived definitions and add internal owner associations."""
    model.derived = {}
    model.columns = {key: set(model.tables[node["table"]]) for key, node in model.nodes.items()}
    for key, node in model.nodes.items():
        if not node.get("derivation"):
            continue
        definition = Derivation.model_validate(node["derivation"]).model_dump(exclude_none=True)
        source = definition["source"]
        if source not in model.nodes or model.nodes[source].get("derivation"):
            raise ValueError("Choose a physical model object as the calculation source; nested derived sources are not supported yet.")
        if node["table"] != model.nodes[source]["table"]:
            raise ValueError("A derived source must reference its calculation source's physical table.")
        groups = definition["groupBy"]
        outputs = definition["outputs"]
        if len(set(groups)) != len(groups) or any(group not in model.columns[source] for group in groups):
            raise ValueError("Choose distinct, existing source columns for the grouping keys.")
        if definition["kind"] == "aggregate" and not groups:
            raise ValueError("An aggregate source requires at least one grouping key.")
        connection = definition.get("connection")
        if connection:
            if definition["kind"] != "aggregate":
                raise ValueError("Only aggregate summaries have a model connection.")
            target = connection["target"]
            if target not in model.nodes or model.nodes[target].get("derivation"):
                raise ValueError("Choose a physical model object as the summary connection target.")
            mappings = connection["columns"]
            sources, targets = [item["source"] for item in mappings], [item["target"] for item in mappings]
            if len(sources) != len(groups) or set(sources) != set(groups):
                raise ValueError("The summary connection must map every grouping key exactly once.")
            if len(set(targets)) != len(targets) or any(column not in model.columns[target] for column in targets):
                raise ValueError("Choose distinct, existing target columns for the summary connection.")
            if not any(set(key).issubset(targets) for key in unique_keys(model, target)):
                raise ValueError("The summary connection must include the target's complete primary key or a nonnullable unique key.")
            for item in mappings:
                source_type = type_family(model.column_details[model.nodes[source]["table"]][item["source"]])
                target_type = type_family(model.column_details[model.nodes[target]["table"]][item["target"]])
                if source_type and target_type and source_type != target_type:
                    raise ValueError("Summary connection columns must have compatible data types.")
        elif definition["kind"] == "aggregate":
            # Existing persisted definitions group by their owning record key.
            if not any(set(key).issubset(groups) for key in unique_keys(model, source)):
                raise ValueError("Grouping must include the owner's complete primary key or a nonnullable unique key, so each summary belongs to one owner record.")
        if definition["kind"] == "row" and groups:
            raise ValueError("Row calculations do not have grouping keys.")
        if len({out["id"] for out in outputs}) != len(outputs) or any(out["id"] in groups for out in outputs):
            raise ValueError("Calculated outputs need unique IDs distinct from grouping keys.")
        for output in outputs:
            contributor = output.get("nodeId", source)
            if contributor not in model.nodes or model.nodes[contributor].get("derivation"):
                raise ValueError("Calculated outputs must reference physical model fields.")
            if output["column"] not in model.columns[contributor]:
                raise ValueError("A calculated output references an unknown source column.")
            validate_conditions(model, output, source, definition["kind"])
            operation = output["operation"]
            numeric_columns = [output["column"]] if operation in {*ARITHMETIC, "sum", "avg"} else []
            if operation in ARITHMETIC and output.get("operand"):
                numeric_columns.append(output["operand"])
            for column in numeric_columns:
                details = model.column_details[model.nodes[contributor]["table"]].get(column, {})
                kind = details.get("dataType", details.get("data_type", "")).lower().split("(")[0].strip()
                if kind and kind not in NUMERIC_TYPES:
                    raise ValueError(f"{operation} requires numeric columns; {model.nodes[contributor]['label']}.{column} has type {kind}.")
            if definition["kind"] == "row":
                if contributor != source or output["operation"] not in ARITHMETIC:
                    raise ValueError("Row calculations currently combine two numeric fields on their owning source.")
                if output.get("operand") not in model.columns[source]:
                    raise ValueError("Choose a second source column for this row calculation.")
            elif output["operation"] not in AGGREGATES or output.get("operand"):
                raise ValueError("Aggregate sources accept list, count, sum, average, minimum, or maximum outputs.")
        model.derived[key] = definition
        model.columns[key] = set(groups) | {out["id"] for out in outputs}


def attach(model):
    for key, definition in model.derived.items():
        edge_id = f"derived:{key}"
        if edge_id in model.edges:
            raise ValueError("Relationship IDs cannot collide with derived-source associations.")
        source = definition["source"]
        routes, queue = {source: None}, deque([source])
        while queue:
            previous = queue.popleft()
            for node, _ in model.graph[previous]:
                if node not in routes and node not in model.derived:
                    routes[node] = previous
                    queue.append(node)
        for output in definition["outputs"]:
            contributor = output.get("nodeId", source)
            allowed = {source}
            if contributor in routes:
                while contributor != source:
                    allowed.add(contributor)
                    contributor = routes[contributor]
            if any(condition["table"] not in allowed for condition in output.get("conditions", [])):
                raise ValueError("Calculated-field conditions must use sources on the owner's path to that output; a condition cannot add a separate relationship branch.")
        groups = definition["groupBy"]
        connection = definition.get("connection")
        target = connection["target"] if connection else source
        mapping = connection["columns"][0] if connection else None
        model.edges[edge_id] = {"id": edge_id, "source": key, "target": target,
                                "sourceColumn": mapping["source"] if mapping else groups[0] if groups else "",
                                "targetColumn": mapping["target"] if mapping else groups[0] if groups else "",
                                "derived": True, "enabled": True}
        model.graph[key].append((target, edge_id))
        model.graph[target].append((key, edge_id))


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
