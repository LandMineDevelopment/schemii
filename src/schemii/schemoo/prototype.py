"""Catalog-validated prototype compiler; explicit FK roles, scopes and filters.

This is deliberately not a cost optimizer. ``today`` defaults use one UTC date
per compilation. Filter requirement and unmatched-row handling are independent.
"""

from .join_types import comparable_types, validate_join_types
from .models import Condition

from collections import deque
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cached_property
import math
import json
import re
from uuid import UUID
from .domain import domain_source, source_identity, relationship_columns, resolve_relationship
from . import derived


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
        try:
            if isinstance(value, str):
                value = value.strip()
                relative = re.fullmatch(r"today(?:\s*([+-])\s*(\d{1,7})(?:\s+days?)?)?", value, re.IGNORECASE)
                if relative:
                    offset = int(relative[2] or 0) * (-1 if relative[1] == "-" else 1)
                    return (date.fromisoformat(today) + timedelta(days=offset)).isoformat()
            return date.fromisoformat(value).isoformat()
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{label} requires YYYY-MM-DD, today, or today +/- a whole number of days (for example, today - 365), within years 1–9999.") from None
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
        self.tables = {source_identity(catalog, table): {column["name"] for column in table["columns"]} for table in catalog["tables"]}
        self.column_details = {source_identity(catalog, table): {column["name"]: column for column in table["columns"]} for table in catalog["tables"]}
        self.table_details = {source_identity(catalog, table): table for table in catalog["tables"]}
        self.nodes, self.keys = {}, {}
        default_nodes = [{"id": name if namespace == catalog.get("namespace", "public") else json.dumps([namespace, name], ensure_ascii=False, separators=(",", ":")),
                          "table": name, "namespace": namespace, "label": (name if namespace == catalog.get("namespace", "public") else f"{namespace}.{name}")[:100]} for namespace, name in self.tables]
        for node in _list(request.get("nodes", default_nodes), "Nodes", 100):
            key = _id(node.get("id"), "Node ID")
            if node.get("namespace") is not None and (not isinstance(node["namespace"], str) or not 1 <= len(node["namespace"]) <= 63):
                raise ValueError("Source namespaces must be nonempty identifiers of at most 63 characters.")
            if (key in self.nodes or not isinstance(node.get("table"), str)
                    or source_identity(catalog, node) not in self.tables):
                raise ValueError("Model nodes need unique IDs and known physical tables.")
            # Older browser drafts did not persist alias labels. Their stable
            # node ID is the only unambiguous fallback; current drafts carry
            # the author-facing label explicitly.
            label = node.get("label", key)
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
                raise ValueError("Model object labels must be nonempty text of at most 100 characters.")
            self.nodes[key] = {**node, "label": label.strip()}
            self.keys[key] = source_identity(catalog, node)
        derived.configure(self)
        if "edges" in request:
            raw_edges = request["edges"]
        else:
            enabled = request.get("relationships", [])
            if not isinstance(enabled, list) or any(not isinstance(key, str) for key in enabled):
                raise ValueError("Enabled relationships must identify known relationships.")
            raw_edges = []
            for key in dict.fromkeys(enabled):
                relationship = resolve_relationship(catalog, key)
                if relationship is None:
                    raise ValueError("Enabled relationships must identify known relationships.")
                endpoints = {}
                for side in ("source", "target"):
                    candidates = [node for node in self.nodes if self.keys[node] == source_identity(catalog, relationship, side) and node not in self.derived]
                    namespace, table = source_identity(catalog, relationship, side)
                    canonical = table if namespace == catalog.get("namespace", "public") else json.dumps([namespace, table], ensure_ascii=False, separators=(",", ":"))
                    if canonical in candidates:
                        endpoints[side] = canonical
                    elif len(candidates) == 1:
                        endpoints[side] = candidates[0]
                    else:
                        raise ValueError("Choose explicit relationship source and target occurrences when aliases share a physical source.")
                raw_edges.append({"id": key, "relationshipId": key, **endpoints, "enabled": True})
        self.edges = {}
        self.graph = {key: [] for key in self.nodes}
        for item in _list(raw_edges, "Edges", 200):
            key = _id(item.get("id"), "Edge ID")
            relationship = item.get("relationshipId")
            if item.get("source") in self.derived or item.get("target") in self.derived:
                raise ValueError("Derived sources connect through their owner; do not attach ordinary relationships to them.")
            logical = item.get("kind", "foreign_key") == "logical"
            if key in self.edges:
                raise ValueError("Edges need unique IDs.")
            if logical:
                if relationship:
                    raise ValueError("Logical relationships cannot reference a foreign key.")
                edge = {**item, "columns": relationship_columns(item)}
                for side in ("source", "target"):
                    node = item.get(side)
                    for pair in edge["columns"]:
                        self.validate({"table": node, "column": pair[side]})
                    edge[f"{side}Table"] = self.nodes[node]["table"]
                    edge[f"{side}Namespace"] = self.keys[node][0]
                for pair in edge["columns"]:
                    validate_join_types(*(self.column_details[self.keys[item[side]]][pair[side]].get("dataType", "")
                                          for side in ("source", "target")))
            else:
                live = resolve_relationship(catalog, relationship, self.nodes, item)
                if item.get("kind", "foreign_key") != "foreign_key" or not isinstance(relationship, str) or live is None:
                    raise ValueError("Edges need a known foreign key with matching qualified source and target tables.")
                # Public JSON rejects overrides; historical direct compiler
                # dictionaries still discard them and use the full live mapping.
                edge = {**item, **live, "id": key, "source": item["source"], "target": item["target"], "enabled": item.get("enabled", True), "columns": relationship_columns(live)}
                for side in ("source", "target"):
                    for pair in edge["columns"]:
                        self.validate({"table": item[side], "column": pair[side]})
            if type(item.get("enabled", True)) is not bool:
                raise ValueError("Relationship enabled must be a boolean.")
            self.edges[key] = edge
            if item.get("enabled", True):
                self.graph[item["source"]].append((item["target"], key))
                self.graph[item["target"]].append((item["source"], key))
        derived.attach(self)

    def validate(self, item):
        if not isinstance(item, dict):
            raise ValueError("Each field or condition must be an object.")
        node, column = item.get("table"), item.get("column")
        if node == "" or column == "":
            raise ValueError("Choose a source column for each field or filter condition.")
        if not isinstance(node, str) or node not in self.nodes or not isinstance(column, str) or column not in self.columns[node]:
            raise ValueError(f"Unknown model field: {node}.{column}.")
        if item.get("compareColumn") is not None:
            # Direct compiler callers use dictionaries; enforce the same operand
            # contract as API and persisted definitions before generating SQL.
            Condition.model_validate({key: value for key, value in item.items() if key != "valueSource"})
            if item.get("valueSource") == "today":
                raise ValueError("Choose either Today or a comparison column, not both.")
            compare = item["compareColumn"]
            if compare not in self.columns[node]:
                raise ValueError(f"Unknown comparison column on this source: {node}.{compare}.")
            left_type, right_type = (derived.field_details(self, node, name).get("dataType", "") for name in (column, compare))
            if not comparable_types(left_type, right_type):
                raise ValueError(f"Comparison columns must have comparable types without casts ({left_type or 'unknown'} and {right_type or 'unknown'}).")
        if item.get("operator") == "range_contains_date":
            details = derived.field_details(self, node, column)
            if details.get("dataType", "").lower() != "daterange":
                raise ValueError("Contains date requires a PostgreSQL daterange source column.")
        return node

    @cached_property
    def relationship_blocks(self):
        """Biconnected edge blocks, including distinct parallel edges and loops.

        The block/object graph is a forest. Its unique routes distinguish an
        unused cycle hanging from an articulation from a cycle on a needed path.
        """
        discovered, low, stack, blocks, loops = {}, {}, [], [], set()

        def visit(node, parent_edge=None):
            discovered[node] = low[node] = len(discovered)
            for neighbor, key in self.graph[node]:
                if key == parent_edge:
                    continue
                if neighbor == node:
                    if key not in loops:
                        blocks.append({key})
                        loops.add(key)
                elif neighbor not in discovered:
                    stack.append(key)
                    visit(neighbor, key)
                    low[node] = min(low[node], low[neighbor])
                    if low[neighbor] >= discovered[node]:
                        block = set()
                        while True:
                            edge = stack.pop()
                            block.add(edge)
                            if edge == key:
                                break
                        blocks.append(block)
                elif discovered[neighbor] < discovered[node]:
                    stack.append(key)
                    low[node] = min(low[node], discovered[neighbor])

        for node in self.nodes:
            if node not in discovered:
                visit(node)
        return blocks

    def cycles(self):
        cyclic = set()
        for block in self.relationship_blocks:
            key = next(iter(block))
            if len(block) > 1 or self.edges[key]["source"] == self.edges[key]["target"]:
                cyclic.update(block)
        return [key for key in self.edges if key in cyclic]

    def possible_path_nodes(self, source, target):
        """All objects on simple source/target routes, for authored dependencies.

        Cyclic blocks on that route can have several valid authored paths. Blocks
        hanging off the route cannot contribute conditions, even when cyclic.
        Execution separately requires a unique route for each participating source.
        """
        graph: dict[str | int, list[str | int]] = {node: [] for node in self.nodes}
        members = []
        for index, block in enumerate(self.relationship_blocks):
            nodes = {self.edges[key][side] for key in block for side in ("source", "target")}
            members.append(nodes)
            graph[index] = list(nodes)
            for node in nodes:
                graph[node].append(index)
        parents, queue = {source: None}, deque([source])
        while queue:
            previous = queue.popleft()
            for node in graph[previous]:
                if node not in parents:
                    parents[node] = previous
                    queue.append(node)
        result = {source}
        if target in parents:
            node = target
            while node != source:
                result.update(members[node] if isinstance(node, int) else {node})
                node = parents[node]
        return result


def analyze_model(catalog: dict, request: dict) -> dict:
    """Validate alias bindings and expose every enabled cycle edge before execution."""
    return {"cycleEdges": _Model(catalog, request).cycles()}


def validate_definition(catalog: dict, definition: dict) -> dict:
    """Validate authored references without requiring report-time input values.

    Draft saves may be incomplete. Atomic edits and assistant saves use this
    stricter boundary so an unbound condition cannot silently break a saved model.
    Query compilation remains responsible for values and participating paths.
    """
    model = _Model(catalog, definition)
    root = definition.get("root")
    if root not in model.nodes or root in model.derived:
        raise ValueError("Choose a known physical model object as the starting object.")
    cycles = model.cycles()
    for field in definition.get("exposedFields") or []:
        model.validate(field)
    for scope in definition.get("scopes", []):
        alternatives = scope.get("alternatives", [])
        if not alternatives or len({a["id"] for a in alternatives}) != len(alternatives):
            raise ModelValidationError("A model filter needs alternatives with unique IDs.", scopeId=scope["id"])
        for alternative in alternatives:
            inputs = alternative.get("inputs", [])
            input_ids = {p["id"] for p in inputs}
            if len(input_ids) != len(inputs):
                raise ModelValidationError("Parameter input IDs must be unique within an alternative.", scopeId=scope["id"], alternativeId=alternative["id"])
            for index, condition in enumerate(alternative.get("conditions", [])):
                try:
                    model.validate(condition)
                    if condition.get("parameterId") is not None and condition["parameterId"] not in input_ids:
                        raise ValueError("The condition references an unknown parameter input.")
                except ValueError as error:
                    raise ModelValidationError(str(error), scopeId=scope["id"], alternativeId=alternative["id"], conditionIndex=index) from error
            for item in [*inputs, *alternative.get("conditions", [])]:
                domain = item.get("domain")
                if domain:
                    columns = model.tables.get(source_identity(catalog, domain_source(domain, model.nodes.values())), set())
                    if domain.get("column") not in columns or domain.get("labelColumn") and domain["labelColumn"] not in columns:
                        raise ModelValidationError("The domain references an unknown source column or display label.", scopeId=scope["id"], alternativeId=alternative["id"])
    return {"cycleEdges": cycles}


@dataclass(frozen=True)
class _SelectedScope:
    scope: dict
    alternative: dict
    selection: dict
    conditions: list[dict]
    enabled: bool


@dataclass
class _ValidatedPreview:
    catalog: dict
    request: dict
    model: _Model
    root: str
    fields: list[dict]
    limit: int
    row_filters: list[dict]
    existence: list[tuple[str, list[dict]]]
    report_condition_nodes: set[str]
    selected_scopes: list[_SelectedScope]
    participation_fields: list[dict]
    scope_outer_nodes: Collection[str] | None
    today: str


@dataclass
class _PreviewPlan:
    validated: _ValidatedPreview
    parents: dict[str, tuple[str, str] | None]
    participating: set[str]
    active_scopes: list[str]
    parameter_values: dict
    source_filters: dict[str, list[dict]]
    preserved_required_nodes: set[str]
    existence: list[tuple[str, list[dict]]]
    outer: set[str]
    aliases: dict[str, str]


@dataclass
class _ScopeParticipation:
    active_scopes: list[str]
    parameter_values: dict
    source_filters: dict[str, list[dict]]
    preserved_required_nodes: set[str]
    existence: list[tuple[str, list[dict]]]


@dataclass
class _OutputCompilation:
    expressions: list[str]
    grouping: list[str]
    labels: set[str]
    aggregated: bool
    warnings: list[str]
    repetition_diagnostics: list[dict]


def _validated_conditions(model: _Model, value) -> list[dict]:
    result = _list(value, "Conditions", 32)
    for condition in result:
        model.validate(condition)
    return result


def _traverse_model(model: _Model, start: str) -> dict[str, tuple[str, str] | None]:
    parents: dict[str, tuple[str, str] | None] = {start: None}
    queue = deque([start])
    while queue:
        previous = queue.popleft()
        for node, key in model.graph[previous]:
            if node not in parents:
                parents[node] = (previous, key)
                queue.append(node)
    return parents


def _path_nodes(
    model: _Model,
    parents: dict[str, tuple[str, str] | None],
    root: str,
    nodes: set[str],
) -> set[str]:
    unreachable = sorted(
        (node for node in nodes if node not in parents),
        key=lambda node: model.nodes[node]["label"].casefold(),
    )
    if unreachable:
        wanted = set(nodes) - {root}
        candidates = []
        for order, candidate in enumerate(model.nodes):
            routes = _traverse_model(model, candidate)
            if wanted and wanted.issubset(routes):
                score = sum(
                    0 if node == candidate else _path_length(routes, node, candidate)
                    for node in wanted
                )
                candidates.append((score, order, candidate))
        suggestion = min(candidates, default=None)
        labels = [model.nodes[node]["label"] for node in unreachable]
        target = ", ".join(labels[:2]) + (f" and {len(labels) - 2} more" if len(labels) > 2 else "")
        details = {"unreachableNodes": unreachable}
        if suggestion:
            candidate = suggestion[2]
            details["suggestedRoot"] = {"id": candidate, "label": model.nodes[candidate]["label"]}
        root_label = model.nodes[root]["label"]
        raise ModelValidationError(
            f'Starting object "{root_label}" cannot reach {target}. '
            "Choose a connected starting object or enable the intended relationships.",
            **details,
        )
    result, used_edges = {root}, set()
    for node in nodes:
        while node != root:
            result.add(node)
            node, key = parents[node]
            used_edges.add(key)
    cycles = model.cycles()
    ambiguous = [key for key in cycles if key in used_edges]
    if ambiguous:
        raise ModelValidationError(
            "Participating relationships contain cycles or ambiguous paths. "
            "Disable cycle edges or create separate aliases for the intended route.",
            cycleEdges=cycles,
            ambiguousRelationships=ambiguous,
        )
    return result


def _validate_preview_request(
    catalog: dict,
    request: dict,
    *,
    today: str | None,
    participation_fields: list[dict] | None,
    scope_outer_nodes: Collection[str] | None,
    materializing: bool = False,
    model: _Model | None = None,
) -> _ValidatedPreview:
    """Validate request shape and resolve authored scope alternatives."""
    model = _Model(catalog, request) if model is None else model
    root = request.get("root")
    if not isinstance(root, str) or root not in model.nodes:
        raise ValueError("Choose a known root table.")
    if root in model.derived:
        if not materializing:
            raise ValueError("Choose a physical model object as the starting object; derived sources are fields attached to their owner.")
        root = model.relation_owners[root]
    fields = _list(request.get("fields", []), "Fields", 64)
    if not fields:
        raise ValueError("Select between 1 and 64 fields.")
    limit = request.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Preview limit must be between 1 and 100 rows.")

    legacy_filters = _validated_conditions(model, request.get("filters", []))
    report_groups = _list(request.get("reportFilters", []), "Report filters", 32)
    row_filters = list(legacy_filters)
    existence: list[tuple[str, list[dict]]] = []
    report_condition_nodes: set[str] = set()
    for group in report_groups:
        mode = group.get("mode")
        conditions = _validated_conditions(model, group.get("conditions", []))
        if not conditions:
            raise ValueError("A report filter needs at least one condition.")
        if mode == "rows":
            row_filters.extend(conditions)
        elif mode in ("exists", "not_exists"):
            existence.append((mode, conditions))
        else:
            raise ValueError("Report filter mode must be rows, exists, or not_exists.")
        report_condition_nodes.update(item["table"] for item in conditions)
    for field in fields:
        model.validate(field)

    selections = request.get("selections", {})
    if not isinstance(selections, dict):
        raise ValueError("Parameter selections must be an object.")
    scopes = _list(request.get("scopes", []), "Model scopes", 32)
    selected_scopes: list[_SelectedScope] = []
    seen = set()
    for scope in scopes:
        scope_id = _id(scope.get("id"), "Scope ID")
        if scope_id in seen or scope.get("kind") not in ("required", "conditional"):
            raise ValueError("Scopes need unique IDs and an always-evaluated or source-conditional reach.")
        requirement = scope.get("requirement")
        if requirement is None:  # Compatibility with drafts saved by the earlier contract.
            requirement = {"automatic": "required", "optional": "optional"}.get(
                scope.get("activation", "automatic"), scope.get("activation"))
        if requirement not in ("required", "optional"):
            raise ValueError("Scope requirement must be required or optional.")
        if scope.get("rowBehavior") not in (None, "keep_unmatched", "require_matching"):
            raise ValueError("Filter row behavior must be keep_unmatched or require_matching.")
        seen.add(scope_id)
        alternatives = _list(scope.get("alternatives", []), "Alternatives", 12)
        if not alternatives:
            raise ValueError("A model scope needs at least one alternative.")
        alternative_ids = [_id(alternative.get("id"), "Alternative ID") for alternative in alternatives]
        if len(set(alternative_ids)) != len(alternative_ids):
            raise ValueError("Alternative IDs must be unique within a scope.")
        selection = selections.get(scope_id, {})
        if not isinstance(selection, dict):
            raise ValueError("Each parameter selection must be an object.")
        choice = selection.get("alternativeId", alternatives[0]["id"])
        alternative = next((item for item in alternatives if item["id"] == choice), None)
        if alternative is None:
            raise ValueError(f"Unknown alternative for {scope.get('label', scope_id)}.")
        conditions = _validated_conditions(model, alternative.get("conditions", []))
        enabled = requirement == "required" or selection.get("active") is True
        selected_scopes.append(_SelectedScope(scope, alternative, selection, conditions, enabled))

    return _ValidatedPreview(
        catalog=catalog,
        request=request,
        model=model,
        root=root,
        fields=fields,
        limit=limit,
        row_filters=row_filters,
        existence=existence,
        report_condition_nodes=report_condition_nodes,
        selected_scopes=selected_scopes,
        participation_fields=fields if participation_fields is None else participation_fields,
        scope_outer_nodes=scope_outer_nodes,
        today=today or datetime.now(timezone.utc).date().isoformat(),
    )


def _resolve_scope_participation(
    validated: _ValidatedPreview,
    participating: set[str],
    active_scope_ids: list[str],
) -> _ScopeParticipation:
    """Resolve active scope values and split row predicates from existence predicates."""
    active: list[str] = []
    parameter_values: dict = {}
    source_filters = {node: [] for node in participating}
    preserved_required_nodes: set[str] = set()
    existence = list(validated.existence)
    for selected in validated.selected_scopes:
        scope, alternative, selection, items, enabled = (
            selected.scope,
            selected.alternative,
            selected.selection,
            selected.conditions,
            selected.enabled,
        )
        if not enabled:
            continue
        applicable = items if scope["kind"] == "required" else [
            item for item in items if item["table"] in participating
        ]
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
                domain_columns = validated.model.tables.get(source_identity(validated.catalog, domain_source(domain, validated.model.nodes.values())), set())
                label_column = domain.get("labelColumn", "")
                if not isinstance(label_column, str) or domain["column"] not in domain_columns or (label_column and label_column not in domain_columns):
                    raise ValueError("The parameter domain references an unknown source column or display label.")
            value = supplied.get(key)
            if value is None or value == "" or value == []:
                value = parameter.get("defaultValue")
            if value is None or value == [] or isinstance(value, str) and not value.strip():
                raise ModelValidationError(
                    f"{scope.get('label', scope_id)} requires {parameter.get('label', key)}.",
                    activeScopes=active_scope_ids,
                    requiredNodes=sorted(participating),
                    scopeId=scope_id,
                    parameterId=key,
                )
            kind = parameter.get("type", "text")
            bindings = [item for item in items if item.get("parameterId") == key and item.get("operator") not in ("is_null", "not_null")]
            multiple = any(item.get("operator") in ("in", "not_in") for item in bindings)
            label = parameter.get("label", key)
            if multiple and any(item.get("operator") not in ("in", "not_in") for item in bindings):
                raise ValueError(f"{label} cannot bind to both single-value and IN comparisons; use separate parameters.")
            if kind == "source" and not bindings:
                raise ValueError("Choose from source parameters need a source-column binding.")
            if multiple:
                values[key] = [_parameter_value(member, kind, label, validated.today) for member in _filter_values(value)]
            else:
                if isinstance(value, list):
                    raise ValueError(f"{label} accepts one value; use IN to select multiple values.")
                values[key] = _parameter_value(value, kind, label, validated.today)
        resolved = []
        for condition in applicable:
            parameter_id = condition.get("parameterId")
            if parameter_id is not None and (not isinstance(parameter_id, str) or parameter_id not in values):
                raise ValueError("A condition references an undefined parameter.")
            resolved.append({
                **condition,
                "value": values[parameter_id] if parameter_id is not None else condition.get("value"),
            })
        parameter_values[scope_id] = values
        row_behavior = scope.get("rowBehavior") or (
            "require_matching" if scope["kind"] == "required" else "keep_unmatched"
        )
        if row_behavior == "require_matching" and resolved:
            existence.append(("required", resolved))
        else:
            for item in resolved:
                source_filters[item["table"]].append(item)
                if scope["kind"] == "required":
                    preserved_required_nodes.add(item["table"])
    return _ScopeParticipation(
        active_scopes=active,
        parameter_values=parameter_values,
        source_filters=source_filters,
        preserved_required_nodes=preserved_required_nodes,
        existence=existence,
    )


def _plan_preview(validated: _ValidatedPreview) -> _PreviewPlan:
    """Plan relation participation, active scopes, and stable aliases."""
    model, root = validated.model, validated.root
    parents = _traverse_model(model, root)
    references = [(field["table"], field["column"]) for field in validated.participation_fields]
    references += derived.condition_references(validated.row_filters)
    references += derived.condition_references([item for _, conditions in validated.existence for item in conditions])
    for selected in validated.selected_scopes:
        if selected.enabled and selected.scope["kind"] == "required":
            references += derived.condition_references(selected.conditions)
    participating = {root, *(node for node, _ in derived.field_closure(model, references))}
    participating = _path_nodes(model, parents, root, participating)
    active_scope_ids = [
        selected.scope["id"]
        for selected in validated.selected_scopes
        if selected.enabled and (
            selected.scope["kind"] == "required"
            or any(item["table"] in participating for item in selected.conditions)
        )
    ]
    scope_participation = _resolve_scope_participation(
        validated, participating, active_scope_ids
    )
    outer = _path_nodes(
        model,
        parents,
        root,
        {
            root,
            *scope_participation.preserved_required_nodes,
            *(node for node, _ in derived.field_closure(model, [
                *((field["table"], field["column"]) for field in validated.participation_fields),
                *derived.condition_references(validated.row_filters),
                *derived.condition_references([item for node in scope_participation.preserved_required_nodes for item in scope_participation.source_filters.get(node, [])]),
            ])),
        },
    )
    aliases = {node: f"t{index}" for index, node in enumerate(node for node in parents if node in outer)}
    for node in outer:
        if node in model.derived and model.derived[node]["kind"] == "row":
            aliases[node] = aliases[model.relation_owners[node]]
    return _PreviewPlan(
        validated=validated,
        parents=parents,
        participating=participating,
        active_scopes=scope_participation.active_scopes,
        parameter_values=scope_participation.parameter_values,
        source_filters=scope_participation.source_filters,
        preserved_required_nodes=scope_participation.preserved_required_nodes,
        existence=scope_participation.existence,
        outer=outer,
        aliases=aliases,
    )


class _PreviewCompiler:
    """Compile a planned query into SQL and its user-facing diagnostics."""

    def __init__(self, plan: _PreviewPlan, *, outputs=None, output_labels=None, bounded=True):
        self.plan = plan
        self.validated = plan.validated
        self.model = self.validated.model
        self.outputs = outputs
        self.output_labels = output_labels
        self.bounded = bounded
        self.expression_path = []
        self.aggregate_relations = {}
        self.aggregate_plans = []
        references = [(field["table"], field["column"]) for field in self.validated.participation_fields]
        references += derived.condition_references(self.validated.row_filters)
        references += derived.condition_references([item for _, conditions in self.plan.existence for item in conditions])
        references += derived.condition_references([item for conditions in self.plan.source_filters.values() for item in conditions])
        self.required_fields = derived.field_closure(self.model, references)
        self.outer_graph = {node: [] for node in plan.outer}
        for node in plan.parents:
            if node == self.validated.root or node not in plan.outer:
                continue
            previous, key = plan.parents[node]
            self.outer_graph[node].append((previous, key))
            self.outer_graph[previous].append((node, key))

    def field_expression(self, node, column, aliases, *, skip_scope=None):
        reference = (node, column, skip_scope)
        if reference in self.expression_path:
            path = " -> ".join(f"{key}.{field}" for key, field, _ in [*self.expression_path, reference])
            raise ValueError(f"Calculated filter dependency cycle: {path}.")
        self.expression_path.append(reference)
        try:
            return self._field_expression(node, column, aliases, skip_scope=skip_scope)
        finally:
            self.expression_path.pop()

    def _field_expression(self, node, column, aliases, *, skip_scope=None):
        definition = self.model.derived.get(node)
        expression = ((aliases[node] + ".") if aliases else "") + _identifier(column)
        if definition is None:
            return expression
        output = next((value for value in definition["outputs"] if value["id"] == column), None)
        if output is not None and definition["kind"] == "aggregate":
            return f"COALESCE({expression}, 0)" if definition.get("connection") and output["operation"] in {"count", "count_distinct"} else expression
        if output is not None:
            value = self.field_expression(output.get("nodeId", definition["source"]), output["column"], aliases, skip_scope=skip_scope)
            operand = self.field_expression(output.get("operandNodeId", definition["source"]), output["operand"], aliases, skip_scope=skip_scope)
            expression = derived.expression(output, value, operand, _identifier, _literal)
            conditions = self.compile_output_predicates(output, aliases, skip_scope=skip_scope)
            if conditions:
                expression = f"CASE WHEN {conditions} THEN {expression} ELSE NULL END"
        if definition["kind"] == "row" and skip_scope != node:
            conditions = " AND ".join(self.compile_predicate(item, aliases, skip_scope=node) for item in self.plan.source_filters.get(node, []))
            if conditions:
                expression = f"CASE WHEN {conditions} THEN {expression} ELSE NULL END"
        if len(expression) > 65536:
            raise ValueError(f"Calculated expression {node}.{column} expands beyond the bounded SQL expression size; split the calculation into materialized summaries.")
        return expression

    def compile_predicate(self, item: dict, aliases: dict | None, *, skip_scope=None) -> str:
        expression = self.field_expression(item["table"], item["column"], aliases, skip_scope=skip_scope)
        operator = item.get("operator")
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        if operator in ("is_null", "not_null"):
            result = f"{expression} IS {'NOT ' if operator == 'not_null' else ''}NULL"
        elif operator in ("in", "not_in"):
            members = ", ".join(_literal(value) for value in _filter_values(item.get("value")))
            result = f"{expression} {'NOT IN' if operator == 'not_in' else 'IN'} ({members})"
        elif operator == "range_contains_date":
            self.model.validate(item)
            value = item.get("value")
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Contains date requires a valid date in YYYY-MM-DD format.") from None
            result = f"{expression} @> DATE {_literal(value)}"
        elif operator == "contains":
            if not isinstance(item.get("value"), str):
                raise ValueError("Contains filters require text.")
            result = f"STRPOS(CAST({expression} AS TEXT), {_literal(item['value'])}) > 0"
        elif isinstance(operator, str) and operator in operators:
            operand = (
                self.field_expression(item["table"], item["compareColumn"], aliases, skip_scope=skip_scope)
                if item.get("compareColumn") is not None
                else _literal(item.get("value"))
            )
            result = f"{expression} {operators[operator]} {operand}"
        else:
            raise ValueError("Unsupported filter operator.")
        if type(item.get("allowNull", False)) is not bool:
            raise ValueError("Allow null must be a boolean.")
        return f"({result} OR {expression} IS NULL)" if item.get("allowNull") and operator not in ("is_null", "not_null") else result

    def compile_output_predicates(self, output: dict, names: dict | None, *, skip_scope=None) -> str:
        return " AND ".join(
            self.compile_predicate(
                {**condition, "value": self.validated.today}
                if condition.get("valueSource") == "today"
                else condition,
                names, skip_scope=skip_scope,
            )
            for condition in output.get("conditions", [])
        )

    def compile_aggregate_relation(self, node: str) -> str:
        if node in self.aggregate_relations:
            return self.aggregate_relations[node]
        model, request = self.model, self.validated.request
        definition = model.derived[node]
        owner = definition["source"]
        needed = {column for key, column in self.required_fields if key == node}
        outputs = [output for output in definition["outputs"] if output["id"] in needed]
        references = [(owner, column) for column in definition["groupBy"]]
        references += [reference for output in outputs for reference in derived.output_references(definition, output)]
        dependencies = derived.field_closure(model, references)
        allowed_nodes = {key for key in model.nodes if key not in model.derived} | {key for key, _ in dependencies}
        inner_fields = [{"table": owner, "column": name} for name in definition["groupBy"]]
        labels = {index: name for index, name in enumerate(definition["groupBy"])}
        operations = {}
        for output in outputs:
            index = len(inner_fields)
            inner_fields.append({"table": output.get("nodeId", owner), "column": output["column"]})
            operations[index], labels[index] = output, output["id"]
        scopes = []
        for scope in request.get("scopes", []):
            alternatives = []
            for option in scope.get("alternatives", []):
                conditions = [item for item in option.get("conditions", []) if item["table"] in allowed_nodes and item["table"] != node]
                inputs = [parameter for parameter in option.get("inputs", []) if any(item.get("parameterId") == parameter["id"] for item in conditions)]
                alternatives.append({**option, "conditions": conditions, "inputs": inputs})
            if any(option["conditions"] for option in alternatives):
                scopes.append({**scope, "alternatives": alternatives})
        inner_request = {**request, "root": owner, "fields": inner_fields,
                         "filters": [], "reportFilters": [], "scopes": scopes}
        root = model.relation_owners[owner]
        routes = _traverse_model(model, root)
        _path_nodes(model, routes, root, {root, *(key for key, _ in dependencies)})
        contributor_paths = []
        for output in outputs:
            contributor = output.get("nodeId", owner)
            references = derived.field_closure(model, derived.output_references(definition, output))
            route = set()
            for value, _ in references:
                if value not in routes:
                    raise ValueError("A calculated output is disconnected from its owning source.")
                current = value
                while current != root:
                    previous, edge_id = routes[current]
                    edge = model.edges[edge_id]
                    if current not in model.derived or model.derived[current]["kind"] == "aggregate":
                        if not edge.get("derived"):
                            route.add(current)
                            if definition.get("connection") and current != edge["target"]:
                                raise ValueError("Summary lookups must not multiply source records. Summarize the many-side source separately before connecting its result.")
                    current = previous
            upstream = derived.field_closure(model, [(contributor, output["column"])])
            if (output["operation"] in {"sum", "avg", "count"}
                    and any(key in model.derived and model.derived[key]["kind"] == "aggregate" for key, _ in upstream)
                    and model.anchors[contributor] != model.anchors[owner]):
                raise ValueError("Reaggregate a materialized summary at its own grain; raw source records would repeat this calculated measure.")
            contributor_paths.append(route)
        if any(not (a <= b or b <= a) for a in contributor_paths for b in contributor_paths):
            raise ValueError("Outputs from independent relationship branches need separate aggregate sources to avoid multiplying their values.")
        longest = max(contributor_paths, key=len, default=set())
        for output, path in zip(outputs, contributor_paths):
            for extra in longest - path:
                _, edge_id = routes[extra]
                edge = model.edges[edge_id]
                if extra == edge["source"] and output["operation"] not in {"min", "max", "count_distinct"} and not output.get("distinct"):
                    raise ValueError("These outputs summarize different record grains. Use separate aggregate sources so related rows cannot multiply a count, total, or list.")
        inner = compile_preview(self.validated.catalog, inner_request, _outputs=operations,
                                _output_labels=labels, _bounded=False, _today=self.validated.today,
                                _materializing=True, _model=model)
        self.aggregate_plans.append(inner)
        self.aggregate_relations[node] = "(\n" + inner["sql"].removesuffix(";") + "\n)"
        return self.aggregate_relations[node]

    def relation(self, node: str) -> str:
        if node in self.model.derived:
            if self.model.derived[node]["kind"] == "row":
                return self.relation(self.model.relation_owners[node])
            relation = self.compile_aggregate_relation(node)
            clauses = self.plan.source_filters.get(node, [])
            if clauses:
                predicate = " AND ".join(self.compile_predicate(item, {node:"summary_filter"}) for item in clauses)
                return f"(SELECT * FROM {relation} AS summary_filter WHERE {predicate})"
            return relation
        namespace, table = self.model.keys[node]
        physical = f"{_identifier(namespace)}.{_identifier(table)}"
        clauses = self.plan.source_filters.get(node, [])
        return "(SELECT * FROM " + physical + " WHERE " + " AND ".join(
            self.compile_predicate(item, None) for item in clauses
        ) + ")" if clauses else physical

    def multiplying_relationships(self, contributor: str) -> list[dict]:
        visited, queue = {contributor}, deque([(contributor, [contributor])])
        relationships = []
        while queue:
            previous, path_nodes = queue.popleft()
            for node, key in self.outer_graph[previous]:
                if node in visited:
                    continue
                visited.add(node)
                edge = self.model.edges[key]
                if not edge.get("derived") and (node == edge["source"] or edge.get("kind") == "logical"):
                    side = "source" if node == edge["source"] else "target"
                    columns = [pair[side] for pair in relationship_columns(edge)]
                    table = self.model.table_details[self.model.keys[node]]
                    equality_keys = [table.get("primaryKey", []), *table.get("uniqueKeys", [])]
                    if not any(unique and set(unique) <= set(columns) for unique in equality_keys):
                        relationships.append({
                            "id": key,
                            "fromNode": previous,
                            "toNode": node,
                            "source": {"node": edge["source"], "label": self.model.nodes[edge["source"]]["label"], "column": ", ".join(pair["source"] for pair in relationship_columns(edge)), **({"columns": [pair["source"] for pair in relationship_columns(edge)]} if len(relationship_columns(edge)) > 1 else {})},
                            "target": {"node": edge["target"], "label": self.model.nodes[edge["target"]]["label"], "column": ", ".join(pair["target"] for pair in relationship_columns(edge)), **({"columns": [pair["target"] for pair in relationship_columns(edge)]} if len(relationship_columns(edge)) > 1 else {})},
                            "path": [{"node": item, "label": self.model.nodes[item]["label"]} for item in [*path_nodes, node]],
                        })
                queue.append((node, [*path_nodes, node]))
        return relationships

    def compile_outputs(self) -> _OutputCompilation:
        expressions, grouping, labels = [], [], set()
        aggregated = self.output_labels is not None
        warnings, repetition_diagnostics = [], []
        for field_index, field in enumerate(self.validated.fields):
            node, name = field["table"], field["column"]
            expression = self.field_expression(node, name, self.plan.aliases)
            aggregate = field.get("aggregate", "none")
            # Node IDs are stable internal references and may contain generated
            # alias identifiers. Result columns use the author-facing model label.
            label = f"{self.model.nodes[node]['label']}.{name}"
            definition = self.model.derived.get(node)
            if definition:
                output = next((item for item in definition["outputs"] if item["id"] == name), None)
                if output:
                    label = f"{self.model.nodes[node]['label']}.{output['label']}"
            if self.outputs and field_index in self.outputs:
                expression = derived.expression(self.outputs[field_index], expression, None, _identifier, _literal)
                clause = self.compile_output_predicates(self.outputs[field_index], self.plan.aliases)
                if clause:
                    expression += f" FILTER (WHERE {clause})"
                aggregate = "derived"
                aggregated = True
            if self.output_labels is not None:
                label = self.output_labels[field_index]
            if aggregate == "none":
                if expression not in grouping:
                    grouping.append(expression)
            elif isinstance(aggregate, str) and aggregate in {"count", "count_distinct", "sum", "avg", "min", "max"}:
                relationships = self.multiplying_relationships(node) if aggregate in {"count", "sum", "avg"} else []
                if relationships and any(key in self.model.derived and self.model.derived[key]["kind"] == "aggregate" for key, _ in derived.field_closure(self.model, [(node, name)])):
                    raise ValueError("A calculated summary measure would be repeated by these report joins. Aggregate its materialized grain before adding many-side detail.")
                if relationships:
                    message = (
                        f"{aggregate.upper()} of {label} may be affected by repeated rows from joins. "
                        "The selected aggregation runs as configured. Consider a separate aggregate source "
                        "grouped at this measure's grain; use COUNT DISTINCT only when you intend to count distinct values."
                    )
                    repetition_diagnostics.append({
                        "code": "measure_repetition",
                        "message": message,
                        "outputIndex": field_index,
                        "measure": {"table": node, "column": name, "aggregate": aggregate, "label": f"{aggregate.upper()} of {label}"},
                        "relationships": relationships,
                    })
                    warnings.append(message)
                aggregated = True
                label = f"{aggregate}({label})"
                expression = f"COUNT(DISTINCT {expression})" if aggregate == "count_distinct" else f"{aggregate.upper()}({expression})"
            elif aggregate != "derived" or not self.outputs:
                raise ValueError("Unsupported aggregation.")
            if label in labels:
                raise ValueError(f"Field selected twice: {label}.")
            labels.add(label)
            expressions.append(f"{expression} AS {_identifier(label)}")
        return _OutputCompilation(expressions, grouping, labels, aggregated, warnings, repetition_diagnostics)

    def join_predicate(self, edge, names):
        if edge.get("derived"):
            source = edge["source"]
            definition = self.model.derived[source]
            pairs = edge["columns"]
            result = []
            for pair in pairs:
                details = derived.field_details(self.model, edge["target"], pair["target"])
                operator = "=" if definition.get("connection") or details.get("nullable") is False else "IS NOT DISTINCT FROM"
                result.append(f"{names[source]}.{_identifier(pair['source'])} {operator} " + self.field_expression(edge["target"], pair["target"], names))
            return " AND ".join(result)
        return " AND ".join(f"{names[edge['source']]}.{_identifier(pair['source'])} = "
                            f"{names[edge['target']]}.{_identifier(pair['target'])}" for pair in relationship_columns(edge))

    def compile_joins(self, nodes: set[str], names: dict[str, str], join_kind: str) -> tuple[list[str], list[str]]:
        result, warnings = [], []
        for node in self.plan.parents:
            if node == self.validated.root or node not in nodes:
                continue
            previous, key = self.plan.parents[node]
            edge = self.model.edges[key]
            if edge.get("derived") and self.model.derived[edge["source"]]["kind"] == "row":
                continue
            result.append(f"{join_kind} JOIN {self.relation(node)} AS {names[node]} ON " + self.join_predicate(edge, names))
            if join_kind == "LEFT" and not edge.get("derived") and (previous == edge["target"] or edge.get("kind") == "logical"):
                warnings.append(f"Joining {previous} to {node} can repeat {previous} rows and multiply totals.")
        return result, warnings

    def compile_existence_predicates(self) -> list[str]:
        where = [self.compile_predicate(item, self.plan.aliases) for item in self.validated.row_filters]
        for index, (mode, items) in enumerate(self.plan.existence):
            nodes = _path_nodes(
                self.model,
                self.plan.parents,
                self.validated.root,
                {key for key, _ in derived.field_closure(self.model, derived.condition_references(items))},
            )
            names = {
                node: f"e{index}_{position}"
                for position, node in enumerate(node for node in self.plan.parents if node in nodes)
            }
            names[self.validated.root] = "t0"
            if mode == "required":
                # Mandatory scope constrains the actual returned detail, not merely
                # some other matching child of the root. Reuse every shared outer alias,
                # and introduce only scope-only branches inside EXISTS.
                scope_outer = (
                    self.plan.outer
                    if self.validated.scope_outer_nodes is None
                    else self.plan.outer & set(self.validated.scope_outer_nodes)
                )
                names.update({node: self.plan.aliases[node] for node in nodes & scope_outer})
                nodes = nodes - scope_outer
                if not nodes:
                    where.extend(self.compile_predicate(item, names) for item in items)
                    continue
            for key in self.plan.parents:
                if key in names and key in self.model.derived and self.model.derived[key]["kind"] == "row":
                    names[key] = names[self.model.relation_owners[key]]
            # Keep outer references in WHERE so PostgreSQL can pull up EXISTS into
            # a semi/anti join. An outer reference in JOIN ON prevents that rewrite
            # and can force a separate subtree traversal for every outer row.
            local_nodes = nodes if mode == "required" else nodes - {self.validated.root}
            body, correlations = ["SELECT 1"], []
            for node in self.plan.parents:
                if node not in local_nodes:
                    continue
                if node in self.model.derived and self.model.derived[node]["kind"] == "row":
                    continue
                previous, edge_id = self.plan.parents[node]
                edge = self.model.edges[edge_id]
                equality = self.join_predicate(edge, names)
                source = f"{self.relation(node)} AS {names[node]}"
                if node in self.model.derived and self.model.derived[node]["kind"] == "aggregate":
                    if len(body) == 1:
                        body.append(f"FROM (SELECT 1) AS existence_seed_{index}")
                    body.append(f"LEFT JOIN {source} ON {equality}")
                elif previous in local_nodes:
                    body.append(f"INNER JOIN {source} ON {equality}")
                else:
                    body.append(("FROM " if len(body) == 1 else "CROSS JOIN ") + source)
                    correlations.append(equality)
            body.append("WHERE " + " AND ".join([
                *correlations,
                *(self.compile_predicate(item, names) for item in items),
            ]))
            where.append(("NOT " if mode == "not_exists" else "") + "EXISTS (\n  " + "\n  ".join(body) + "\n)")
        return where

    def finalize_response(
        self,
        output: _OutputCompilation,
        from_relation: str,
        join_clauses: list[str],
        join_warnings: list[str],
        where: list[str],
    ) -> dict:
        lines = [
            "SELECT\n  " + ",\n  ".join(output.expressions),
            f"FROM {from_relation} AS t0",
            *join_clauses,
        ]
        if where:
            lines.append("WHERE " + "\n  AND ".join(where))
        if output.aggregated and output.grouping:
            lines.append("GROUP BY " + ", ".join(output.grouping))
        if self.bounded:
            lines.append(f"LIMIT {self.validated.limit}")
        warnings = [*output.warnings, *join_warnings]
        if any(item["table"] != self.validated.root for item in self.validated.row_filters):
            warnings.append("Filters apply to joined results; a filter on a related table may remove unmatched root rows.")
        if self.bounded:
            warnings.append(f"Preview is limited to {self.validated.limit} rows; row order is not guaranteed.")
        fields = self.validated.fields
        grain = "One summary row" if output.aggregated and not output.grouping else (
            "Grouped by " + ", ".join(
                f"{field['table']}.{field['column']}"
                for field in fields
                if field.get("aggregate", "none") == "none"
            )
            if output.aggregated
            else "Joined detail rows (related records can repeat root rows)"
        )
        used_relationships = [
            self.plan.parents[node][1]
            for node in self.plan.parents
            if node != self.validated.root and node in self.plan.participating | self.plan.outer
        ]
        required_nodes = [node for node in self.plan.parents if node in self.plan.participating]
        active_scopes = list(self.plan.active_scopes)
        parameter_values = dict(self.plan.parameter_values)
        for plan in self.aggregate_plans:
            used_relationships.extend(plan["usedRelationships"])
            required_nodes.extend(plan["requiredNodes"])
            active_scopes.extend(plan["activeScopes"])
            parameter_values.update(plan["parameterValues"])
        return {
            "sql": "\n".join(lines) + ";",
            "outerNodes": [node for node in self.plan.parents if node in self.plan.outer],
            "usedRelationships": list(dict.fromkeys(used_relationships)),
            "requiredNodes": list(dict.fromkeys(required_nodes)),
            "activeScopes": list(dict.fromkeys(active_scopes)),
            "parameterValues": parameter_values,
            "warnings": warnings,
            "repetitionDiagnostics": output.repetition_diagnostics,
            "grain": grain,
        }

    def compile(self) -> dict:
        """Emit output expressions, filters, joins, and response diagnostics in order."""
        output = self.compile_outputs()
        where = self.compile_existence_predicates()
        from_relation = self.relation(self.validated.root)
        join_clauses, join_warnings = self.compile_joins(self.plan.outer, self.plan.aliases, "LEFT")
        return self.finalize_response(output, from_relation, join_clauses, join_warnings, where)


def compile_preview(
    catalog: dict,
    request: dict,
    *,
    _outputs=None,
    _output_labels=None,
    _bounded=True,
    _today=None,
    _participation_fields=None,
    _scope_outer_nodes: Collection[str] | None = None,
    _materializing=False,
    _model=None,
) -> dict:
    """Validate, plan participation, then compile a bounded semantic preview."""
    if _outputs:
        additional = [{"table": key, "column": column} for output in _outputs.values()
                      for key, column in derived.condition_references(output.get("conditions", []))]
        _participation_fields = [*(request.get("fields", []) if _participation_fields is None else _participation_fields), *additional]
    validated = _validate_preview_request(
        catalog,
        request,
        today=_today,
        participation_fields=_participation_fields,
        scope_outer_nodes=_scope_outer_nodes,
        materializing=_materializing,
        model=_model,
    )
    plan = _plan_preview(validated)
    return _PreviewCompiler(
        plan,
        outputs=_outputs,
        output_labels=_output_labels,
        bounded=_bounded,
    ).compile()


def model_field_details(catalog, definition, node, column):
    """Typed field metadata from the same validated DAG used by SQL compilation."""
    model = _Model(catalog, definition)
    model.validate({"table": node, "column": column})
    return derived.field_details(model, node, column)
