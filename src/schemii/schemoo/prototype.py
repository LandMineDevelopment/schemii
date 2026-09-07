"""Catalog-validated prototype compiler; explicit FK roles, scopes and filters.

This is deliberately not a cost optimizer. ``today`` defaults use one UTC date
per compilation. Conditional predicates prefilter sources, preserving LEFT JOINs.
"""

from collections import deque
from datetime import date, datetime, timezone
import math
from uuid import UUID
from .domain import domain_table


class ModelValidationError(ValueError):
    """Validation error with optional UI diagnostics, never executable SQL."""

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Filter numbers must be finite.")
        return str(value)
    if isinstance(value, str) and "\x00" not in value:
        return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    raise ValueError("Filter values must be text, numbers, or booleans; use is_null for NULL.")


def _list(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of at most {maximum} objects.")
    return value


def _filter_values(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        raise ValueError("IN and NOT IN require a list of 1 to 500 values.")
    for member in value:
        if member is None or isinstance(member, (list, dict)):
            raise ValueError("IN values must be individual non-NULL values; use Include NULL rows separately.")
        if isinstance(member, str) and len(member) > 4000:
            raise ValueError("Filter text values must be at most 4000 characters.")
        _literal(member)
    return value


def _parameter_value(value, kind, label, today):
    if kind == "date":
        value = today if value == "today" else value
        try:
            return date.fromisoformat(value).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"{label} requires a date in YYYY-MM-DD format.") from None
    if kind == "number":
        try:
            if isinstance(value, bool):
                raise ValueError()
            value = float(value)
            if not math.isfinite(value):
                raise ValueError()
            return value
        except (TypeError, ValueError):
            raise ValueError(f"{label} requires a finite number.") from None
    if kind == "integer":
        try:
            if isinstance(value, bool) or not str(value).strip().lstrip("+-").isdigit():
                raise ValueError()
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} requires a whole number.") from None
    if kind == "uuid":
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            raise ValueError(f"{label} requires a valid UUID.") from None
    if kind == "boolean":
        if value is True or value == "true":
            return True
        if value is False or value == "false":
            return False
        raise ValueError(f"{label} requires true or false.")
    if kind == "source":
        # Domain lookups retain the source scalar type (including false and 0).
        # Use the same validation as fixed literals; never stringify selections.
        _literal(value)
    elif kind != "text" or not isinstance(value, str):
        raise ValueError("Unsupported parameter type or value.")
    return value


def _id(value, label):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError(f"{label} must be a nonempty identifier of at most 200 characters.")
    return value


def _path_length(parents, node, root):
    """Count edges in one BFS parent map without retaining another graph shape."""
    length = 0
    while node != root:
        node = parents[node][0]
        length += 1
    return length


class _Model:
    def __init__(self, catalog, request):
        if not isinstance(request, dict):
            raise ValueError("Query request must be an object.")
        self.tables = {table["name"]: {column["name"] for column in table["columns"]} for table in catalog["tables"]}
        self.nodes = {}
        for node in _list(request.get("nodes", [{"id": name, "table": name} for name in self.tables]), "Nodes", 100):
            key = _id(node.get("id"), "Node ID")
            if key in self.nodes or not isinstance(node.get("table"), str) or node["table"] not in self.tables:
                raise ValueError("Model nodes need unique IDs and known physical tables.")
            # Older browser drafts did not persist alias labels. Their stable
            # node ID is the only unambiguous fallback; current drafts carry
            # the author-facing label explicitly.
            label = node.get("label", key)
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
                raise ValueError("Model object labels must be nonempty text of at most 100 characters.")
            self.nodes[key] = {**node, "label": label.strip()}
        catalog_edges = {edge["id"]: edge for edge in catalog.get("relationships", [])}
        if "edges" in request:
            raw_edges = request["edges"]
        else:
            enabled = request.get("relationships", [])
            if not isinstance(enabled, list) or any(not isinstance(key, str) or key not in catalog_edges for key in enabled):
                raise ValueError("Enabled relationships must identify known relationships.")
            raw_edges = [{"id": key, "relationshipId": key, "source": catalog_edges[key]["sourceTable"], "target": catalog_edges[key]["targetTable"], "enabled": True} for key in dict.fromkeys(enabled)]
        self.edges = {}
        self.graph = {key: [] for key in self.nodes}
        for item in _list(raw_edges, "Edges", 200):
            key = _id(item.get("id"), "Edge ID")
            relationship = item.get("relationshipId")
            if key in self.edges or not isinstance(relationship, str) or relationship not in catalog_edges:
                raise ValueError("Edges need unique IDs and a known foreign key.")
            edge = {**catalog_edges[relationship], **item}
            for side in ("source", "target"):
                node = item.get(side)
                if not isinstance(node, str) or node not in self.nodes or self.nodes[node]["table"] != catalog_edges[relationship][f"{side}Table"]:
                    raise ValueError("Relationship aliases must match the foreign key's source and target tables.")
                self.validate({"table": node, "column": catalog_edges[relationship][f"{side}Column"]})
                # Never accept caller overrides of physical join columns.
                edge[f"{side}Column"] = catalog_edges[relationship][f"{side}Column"]
            if type(item.get("enabled", True)) is not bool:
                raise ValueError("Relationship enabled must be a boolean.")
            self.edges[key] = edge
            if item.get("enabled", True):
                self.graph[item["source"]].append((item["target"], key))
                self.graph[item["target"]].append((item["source"], key))

    def validate(self, item):
        if not isinstance(item, dict):
            raise ValueError("Each field or condition must be an object.")
        node, column = item.get("table"), item.get("column")
        if node == "" or column == "":
            raise ValueError("Choose a source column for each field or filter condition.")
        if not isinstance(node, str) or node not in self.nodes or not isinstance(column, str) or column not in self.tables[self.nodes[node]["table"]]:
            raise ValueError(f"Unknown model field: {node}.{column}.")
        return node

    def cycles(self):
        # TODO(schemoo-graph): Replace the per-edge BFS below with one Tarjan
        # bridge-finding DFS (tracking parent edge IDs so parallel FKs and self
        # loops remain correct). Keep model-wide cycle edges available for red
        # canvas diagnostics, but only let cycles in a compiled query's
        # participating subgraph block that preview.
        # An undirected edge belongs to a cycle exactly when its endpoints are
        # still connected after removing that edge. Handles aliases/self/parallel.
        cycles = []
        for key, edge in self.edges.items():
            if not edge.get("enabled", True):
                continue
            visited, queue = {edge["source"]}, deque([edge["source"]])
            while queue:
                for neighbor, candidate in self.graph[queue.popleft()]:
                    if candidate != key and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            if edge["target"] in visited:
                cycles.append(key)
        return cycles


def analyze_model(catalog: dict, request: dict) -> dict:
    """Validate alias bindings and expose every enabled cycle edge before execution."""
    return {"cycleEdges": _Model(catalog, request).cycles()}


def compile_preview(catalog: dict, request: dict) -> dict:
    model = _Model(catalog, request)
    cycle_edges = model.cycles()
    if cycle_edges:
        raise ModelValidationError("Enabled relationships contain cycles or ambiguous paths. Disable cycle edges or create separate aliases.", cycleEdges=cycle_edges)
    root = request.get("root")
    if not isinstance(root, str) or root not in model.nodes:
        raise ValueError("Choose a known root table.")
    fields = _list(request.get("fields", []), "Fields", 64)
    if not fields:
        raise ValueError("Select between 1 and 64 fields.")
    limit = request.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Preview limit must be between 1 and 100 rows.")
    def traverse(start):
        result, queue = {start: None}, deque([start])
        while queue:
            previous = queue.popleft()
            for node, key in model.graph[previous]:
                if node not in result:
                    result[node] = (previous, key)
                    queue.append(node)
        return result

    parents = traverse(root)

    def path(nodes):
        unreachable = sorted((node for node in nodes if node not in parents), key=lambda node: model.nodes[node]["label"].casefold())
        if unreachable:
            wanted = set(nodes) - {root}
            candidates = []
            for order, candidate in enumerate(model.nodes):
                routes = traverse(candidate)
                if wanted and wanted.issubset(routes):
                    score = sum(0 if node == candidate else _path_length(routes, node, candidate) for node in wanted)
                    candidates.append((score, order, candidate))
            suggestion = min(candidates, default=None)
            labels = [model.nodes[node]["label"] for node in unreachable]
            target = ", ".join(labels[:2]) + (f" and {len(labels) - 2} more" if len(labels) > 2 else "")
            details = {"unreachableNodes": unreachable}
            if suggestion:
                candidate = suggestion[2]
                details["suggestedRoot"] = {"id": candidate, "label": model.nodes[candidate]["label"]}
            raise ModelValidationError(
                f'Starting object "{model.nodes[root]["label"]}" cannot reach {target}. '
                "Choose a connected starting object or enable the intended relationships.",
                **details,
            )
        result = {root}
        for node in nodes:
            while node != root:
                result.add(node)
                node = parents[node][0]
        return result

    def conditions(value):
        result = _list(value, "Conditions", 32)
        for condition in result:
            model.validate(condition)
        return result

    legacy_filters = conditions(request.get("filters", []))
    report_groups = _list(request.get("reportFilters", []), "Report filters", 32)
    row_filters, existence = list(legacy_filters), []
    participating = {root, *(model.validate(field) for field in fields)}
    for group in report_groups:
        mode = group.get("mode")
        items = conditions(group.get("conditions", []))
        if not items:
            raise ValueError("A report filter needs at least one condition.")
        if mode == "rows":
            row_filters.extend(items)
        elif mode in ("exists", "not_exists"):
            existence.append((mode, items))
        else:
            raise ValueError("Report filter mode must be rows, exists, or not_exists.")
        participating.update(item["table"] for item in items)
    participating.update(item["table"] for item in row_filters)
    scopes = _list(request.get("scopes", []), "Model scopes", 32)
    selections = request.get("selections", {})
    if not isinstance(selections, dict):
        raise ValueError("Parameter selections must be an object.")
    selected, seen = [], set()
    for scope in scopes:
        scope_id = _id(scope.get("id"), "Scope ID")
        if scope_id in seen or scope.get("kind") not in ("required", "conditional"):
            raise ValueError("Scopes need unique IDs and a required or conditional kind.")
        seen.add(scope_id)
        alternatives = _list(scope.get("alternatives", []), "Alternatives", 12)
        if not alternatives:
            raise ValueError("A model scope needs at least one alternative.")
        alternative_ids = [_id(alt.get("id"), "Alternative ID") for alt in alternatives]
        if len(set(alternative_ids)) != len(alternative_ids):
            raise ValueError("Alternative IDs must be unique within a scope.")
        selection = selections.get(scope_id, {})
        if not isinstance(selection, dict):
            raise ValueError("Each parameter selection must be an object.")
        choice = selection.get("alternativeId", alternatives[0]["id"])
        alternative = next((alt for alt in alternatives if alt["id"] == choice), None)
        if alternative is None:
            raise ValueError(f"Unknown alternative for {scope.get('label', scope_id)}.")
        items = conditions(alternative.get("conditions", []))
        selected.append((scope, alternative, selection, items))
        if scope["kind"] == "required":
            participating.update(item["table"] for item in items)
    participating = path(participating)
    active_ids = [scope["id"] for scope, _, _, items in selected if scope["kind"] == "required" or any(item["table"] in participating for item in items)]
    active, parameter_values, source_filters = [], {}, {node: [] for node in participating}
    today = datetime.now(timezone.utc).date().isoformat()
    for scope, alternative, selection, items in selected:
        applicable = items if scope["kind"] == "required" else [item for item in items if item["table"] in participating]
        if scope["kind"] == "conditional" and not applicable:
            continue
        scope_id = scope["id"]
        active.append(scope_id)
        supplied = selection.get("values", {})
        if not isinstance(supplied, dict):
            raise ValueError("Parameter values must be an object.")
        values, input_ids = {}, set()
        for parameter in _list(alternative.get("inputs", []), "Parameters", 16):
            key = _id(parameter.get("id"), "Parameter ID")
            if key in input_ids:
                raise ValueError("Parameter IDs must be unique within an alternative.")
            input_ids.add(key)
            domain = parameter.get("domain")
            if domain is not None:
                if not isinstance(domain, dict) or not isinstance(domain.get("table"), str) or not isinstance(domain.get("column"), str):
                    raise ValueError("Choose a domain value column in the parameter setup.")
                domain_columns = model.tables.get(domain_table(domain, model.nodes.values()), set())
                label_column = domain.get("labelColumn", "")
                if not isinstance(label_column, str) or domain["column"] not in domain_columns or (label_column and label_column not in domain_columns):
                    raise ValueError("The parameter domain references an unknown source column or display label.")
            value = supplied.get(key)
            if value is None or value == "" or value == []:
                value = parameter.get("defaultValue")
            if value is None or value == [] or isinstance(value, str) and not value.strip():
                raise ModelValidationError(f"{scope.get('label', scope_id)} requires {parameter.get('label', key)}.", activeScopes=active_ids, requiredNodes=sorted(participating), scopeId=scope_id, parameterId=key)
            kind = parameter.get("type", "text")
            bindings = [c for c in items if c.get("parameterId") == key and c.get("operator") not in ("is_null", "not_null")]
            multiple = any(c.get("operator") in ("in", "not_in") for c in bindings)
            label = parameter.get("label", key)
            if multiple and any(c.get("operator") not in ("in", "not_in") for c in bindings):
                raise ValueError(f"{label} cannot bind to both single-value and IN comparisons; use separate parameters.")
            if kind == "source" and not bindings:
                raise ValueError("Choose from source parameters need a source-column binding.")
            if multiple:
                values[key] = [_parameter_value(member, kind, label, today) for member in _filter_values(value)]
            else:
                if isinstance(value, list):
                    raise ValueError(f"{label} accepts one value; use IN to select multiple values.")
                values[key] = _parameter_value(value, kind, label, today)
        resolved = []
        for condition in applicable:
            parameter_id = condition.get("parameterId")
            if parameter_id is not None and (not isinstance(parameter_id, str) or parameter_id not in values):
                raise ValueError("A condition references an undefined parameter.")
            resolved.append({**condition, "value": values[parameter_id] if parameter_id is not None else condition.get("value")})
        parameter_values[scope_id] = values
        if scope["kind"] == "required" and resolved:
            existence.append(("required", resolved))
        elif scope["kind"] == "conditional":
            for item in resolved:
                source_filters[item["table"]].append(item)

    def predicate(item, aliases):
        expression = (aliases[item["table"]] + "." if aliases else "") + _identifier(item["column"])
        operator = item.get("operator")
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        if operator in ("is_null", "not_null"):
            result = f"{expression} IS {'NOT ' if operator == 'not_null' else ''}NULL"
        elif operator in ("in", "not_in"):
            members = ", ".join(_literal(value) for value in _filter_values(item.get("value")))
            result = f"{expression} {'NOT IN' if operator == 'not_in' else 'IN'} ({members})"
        elif operator == "contains":
            if not isinstance(item.get("value"), str):
                raise ValueError("Contains filters require text.")
            result = f"STRPOS(CAST({expression} AS TEXT), {_literal(item['value'])}) > 0"
        elif isinstance(operator, str) and operator in operators:
            result = f"{expression} {operators[operator]} {_literal(item.get('value'))}"
        else:
            raise ValueError("Unsupported filter operator.")
        if type(item.get("allowNull", False)) is not bool:
            raise ValueError("Allow null must be a boolean.")
        return f"({result} OR {expression} IS NULL)" if item.get("allowNull") and operator not in ("is_null", "not_null") else result

    namespace = catalog.get("namespace", "public")
    def relation(node):
        physical = f"{_identifier(namespace)}.{_identifier(model.nodes[node]['table'])}"
        clauses = source_filters.get(node, [])
        return "(SELECT * FROM " + physical + " WHERE " + " AND ".join(predicate(item, None) for item in clauses) + ")" if clauses else physical

    outer = path({root, *(field["table"] for field in fields), *(item["table"] for item in row_filters)})
    aliases = {node: f"t{index}" for index, node in enumerate(node for node in parents if node in outer)}
    warnings = []
    def joins(nodes, names, join_kind):
        result = []
        for node in parents:
            if node == root or node not in nodes:
                continue
            previous, key = parents[node]
            edge = model.edges[key]
            result.append(f"{join_kind} JOIN {relation(node)} AS {names[node]} ON {names[edge['source']]}.{_identifier(edge['sourceColumn'])} = {names[edge['target']]}.{_identifier(edge['targetColumn'])}")
            if join_kind == "LEFT" and previous == edge["target"]:
                warnings.append(f"Joining {previous} to {node} can repeat {previous} rows and multiply totals.")
        return result

    expressions, grouping, labels, aggregated = [], [], set(), False
    for field in fields:
        node, name = field["table"], field["column"]
        expression = f"{aliases[node]}.{_identifier(name)}"
        aggregate = field.get("aggregate", "none")
        # Node IDs are stable internal references and may contain generated
        # alias identifiers. Result columns use the author-facing model label.
        label = f"{model.nodes[node]['label']}.{name}"
        if aggregate == "none":
            if expression not in grouping:
                grouping.append(expression)
        elif isinstance(aggregate, str) and aggregate in {"count", "count_distinct", "sum", "avg", "min", "max"}:
            aggregated = True
            label = f"{aggregate}({label})"
            expression = f"COUNT(DISTINCT {expression})" if aggregate == "count_distinct" else f"{aggregate.upper()}({expression})"
        else:
            raise ValueError("Unsupported aggregation.")
        if label in labels:
            raise ValueError(f"Field selected twice: {label}.")
        labels.add(label)
        expressions.append(f"{expression} AS {_identifier(label)}")
    where = [predicate(item, aliases) for item in row_filters]
    for index, (mode, items) in enumerate(existence):
        nodes = path({item["table"] for item in items})
        names = {node: f"e{index}_{position}" for position, node in enumerate(node for node in parents if node in nodes)}
        names[root] = "t0"
        if mode == "required":
            # Mandatory scope constrains the actual returned detail, not merely
            # some other matching child of the root. Reuse every shared outer
            # alias, and introduce only scope-only branches inside EXISTS.
            names.update({node: aliases[node] for node in nodes & outer})
            nodes = nodes - outer
            if not nodes:
                where.extend(predicate(item, names) for item in items)
                continue
        # Keep outer references in WHERE so PostgreSQL can pull up EXISTS into
        # a semi/anti join. An outer reference in JOIN ON prevents that rewrite
        # and can force a separate subtree traversal for every outer row.
        local_nodes = nodes - outer if mode == "required" else nodes - {root}
        body, correlations = ["SELECT 1"], []
        for node in parents:
            if node not in local_nodes:
                continue
            previous, edge_id = parents[node]
            edge = model.edges[edge_id]
            equality = f"{names[edge['source']]}.{_identifier(edge['sourceColumn'])} = {names[edge['target']]}.{_identifier(edge['targetColumn'])}"
            source = f"{relation(node)} AS {names[node]}"
            if previous in local_nodes:
                body.append(f"INNER JOIN {source} ON {equality}")
            else:
                body.append(("FROM " if len(body) == 1 else "CROSS JOIN ") + source)
                correlations.append(equality)
        body.append("WHERE " + " AND ".join([*correlations, *(predicate(item, names) for item in items)]))
        where.append(("NOT " if mode == "not_exists" else "") + "EXISTS (\n  " + "\n  ".join(body) + "\n)")
    lines = ["SELECT\n  " + ",\n  ".join(expressions), f"FROM {relation(root)} AS t0", *joins(outer, aliases, "LEFT")]
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if aggregated and grouping:
        lines.append("GROUP BY " + ", ".join(grouping))
    lines.append(f"LIMIT {limit}")
    if any(item["table"] != root for item in row_filters):
        warnings.append("Filters apply to joined results; a filter on a related table may remove unmatched root rows.")
    warnings.append(f"Preview is limited to {limit} rows; row order is not guaranteed.")
    grain = "One summary row" if aggregated and not grouping else ("Grouped by " + ", ".join(f"{field['table']}.{field['column']}" for field in fields if field.get("aggregate", "none") == "none") if aggregated else "Joined detail rows (related records can repeat root rows)")
    return {"sql": "\n".join(lines) + ";", "usedRelationships": [parents[node][1] for node in parents if node != root and node in participating], "requiredNodes": [node for node in parents if node in participating], "activeScopes": active, "parameterValues": parameter_values, "warnings": warnings, "grain": grain}
