"""Bounded, database-independent analysis of PostgreSQL view query bodies."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError


MAX_VIEW_DEFINITION_BYTES = 256 * 1024
MAX_ANALYSIS_ITEMS = 512
MAX_FRAGMENT_BYTES = 8 * 1024


class ViewDefinitionError(ValueError):
    """The supplied text is not one supported PostgreSQL query body."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def parse_view_definition(definition: str) -> exp.Expression:
    """Parse exactly one SELECT-shaped PostgreSQL view query body."""

    text = definition.strip().rstrip(";").strip()
    if not text:
        raise ViewDefinitionError(
            "empty_definition",
            "Enter the SELECT query that defines this view",
        )
    if len(text.encode("utf-8")) > MAX_VIEW_DEFINITION_BYTES:
        raise ViewDefinitionError(
            "definition_too_large",
            "The view definition is too large to analyze",
        )
    try:
        statements = [
            statement
            for statement in parse(text, read="postgres")
            if statement is not None
        ]
    except SqlglotError as error:
        raise ViewDefinitionError(
            "invalid_sql",
            f"PostgreSQL query could not be parsed: {error}",
        ) from error
    if len(statements) != 1:
        raise ViewDefinitionError(
            "multiple_statements",
            "Enter one SELECT query without additional statements",
        )
    statement = statements[0]
    if isinstance(statement, exp.Subquery):
        statement = statement.this
    if not isinstance(statement, (exp.Select, exp.SetOperation)):
        raise ViewDefinitionError(
            "unsupported_statement",
            "Enter only the SELECT query body; Schemii generates CREATE VIEW and the view identity",
        )
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Merge,
        exp.Command,
    )
    if any(statement.find(kind) is not None for kind in forbidden):
        raise ViewDefinitionError(
            "mutating_query",
            "View queries cannot contain data-changing or schema-changing operations",
        )
    if statement.find(exp.Into) is not None:
        raise ViewDefinitionError(
            "select_into",
            "View queries cannot use SELECT INTO",
        )
    return statement


def _sql(expression: exp.Expression | None) -> str | None:
    if expression is None:
        return None
    value = expression.sql(dialect="postgres", pretty=False, comments=False)
    if len(value.encode("utf-8")) > MAX_FRAGMENT_BYTES:
        return None
    return value


def _scope_name(expression: exp.Expression) -> str | None:
    """Return the nearest named CTE containing one derived expression."""

    parent = expression.parent
    while parent is not None:
        if isinstance(parent, exp.CTE):
            return parent.alias_or_name or None
        parent = parent.parent
    return None


def _join_type(join: exp.Join) -> str:
    parts = [join.method, join.side, join.kind]
    label = " ".join(part.upper() for part in parts if part)
    return label or "INNER"


def _set_operation_label(operation: exp.SetOperation) -> str:
    label = type(operation).__name__.upper()
    return f"{label} ALL" if operation.args.get("distinct") is False else label


def _and_terms(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return [*_and_terms(expression.this), *_and_terms(expression.expression)]
    return [expression]


def _derivation(expression: exp.Expression) -> str:
    projection = expression.this if isinstance(expression, exp.Alias) else expression
    if isinstance(projection, exp.Column):
        return "direct"
    if projection.find(exp.Window):
        return "window"
    if projection.find(exp.AggFunc):
        return "aggregate"
    if not projection.find(exp.Column):
        return "constant"
    return "expression"


def _relation_index(relations: Iterable[dict[str, Any]]) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    exact: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        namespace = str(relation.get("namespace") or "desired")
        name = str(relation["name"])
        exact[(namespace, name)] = relation
        by_name.setdefault(name, []).append(relation)
    return exact, by_name


def _resolved_relation(
    namespace: str | None,
    name: str,
    *,
    current_namespace: str,
    exact: dict[tuple[str, str], dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if namespace:
        return exact.get((namespace, name))
    local = exact.get((current_namespace, name))
    if local is not None:
        return local
    candidates = by_name.get(name, [])
    return candidates[0] if len(candidates) == 1 else None


def referenced_relations(
    definition: str,
    *,
    current_namespace: str = "desired",
) -> list[tuple[str, str]]:
    """Return physical relation names, excluding query-local CTE references."""

    statement = parse_view_definition(definition)
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    references: list[tuple[str, str]] = []
    for table in statement.find_all(exp.Table):
        if not table.db and table.name in cte_names:
            continue
        identity = (table.db or current_namespace, table.name)
        if identity not in references:
            references.append(identity)
        if len(references) >= MAX_ANALYSIS_ITEMS:
            break
    return references


def analyze_view_definition(
    definition: str,
    relations: Iterable[dict[str, Any]] = (),
    *,
    current_namespace: str = "desired",
) -> dict[str, Any]:
    """Derive the compact view story from one query body without database I/O."""

    statement = parse_view_definition(definition)
    exact, by_name = _relation_index(relations)
    ctes = list(statement.find_all(exp.CTE))[:MAX_ANALYSIS_ITEMS]
    cte_names = {cte.alias_or_name for cte in ctes}
    warnings: set[str] = set()
    aliases: dict[str, dict[str, Any]] = {}
    sources_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for table in statement.find_all(exp.Table):
        reference_alias = table.alias or table.name
        if not table.db and table.name in cte_names:
            aliases[reference_alias] = {
                "namespace": None,
                "name": table.name,
                "kind": "stage",
                "resolved": True,
                "columns": [],
            }
            continue
        namespace = table.db or current_namespace
        resolved = _resolved_relation(
            table.db or None,
            table.name,
            current_namespace=current_namespace,
            exact=exact,
            by_name=by_name,
        )
        key = (namespace, table.name)
        source = sources_by_key.get(key)
        if source is None:
            columns = [
                {
                    "name": str(column["name"]),
                    "data_type": str(column.get("data_type") or ""),
                    "uses": [],
                }
                for column in (resolved or {}).get("columns", [])
            ]
            source = {
                "namespace": namespace,
                "name": table.name,
                "kind": str((resolved or {}).get("kind") or "relation"),
                "resolved": resolved is not None,
                "aliases": [],
                "column_count": len(columns),
                "columns": columns,
            }
            sources_by_key[key] = source
            if resolved is None:
                warnings.add("unresolved_relation")
        if reference_alias not in source["aliases"]:
            source["aliases"].append(reference_alias)
        aliases[reference_alias] = source
        aliases.setdefault(table.name, source)

    sources = list(sources_by_key.values())

    def mark_usage(item: dict[str, Any], role: str) -> None:
        if not item["resolved"] or not item["source"]:
            return
        source = next(
            (candidate for candidate in sources if candidate["name"] == item["source"]),
            None,
        )
        if source is None:
            return
        column = next(
            (candidate for candidate in source["columns"] if candidate["name"] == item["column"]),
            None,
        )
        if column is not None and role not in column["uses"]:
            column["uses"].append(role)

    def input_for(column: exp.Column, *, warn: bool = True) -> dict[str, Any]:
        source = aliases.get(column.table) if column.table else None
        if source is None and not column.table:
            matching = [
                candidate
                for candidate in sources
                if any(item["name"] == column.name for item in candidate["columns"])
            ]
            if len(matching) == 1:
                source = matching[0]
            elif len(sources) == 1:
                source = sources[0]
        if source is None:
            if warn:
                warnings.add("unresolved_column_source")
            return {
                "source": column.table or None,
                "column": column.name,
                "resolved": False,
            }
        return {
            "source": source["name"],
            "column": column.name,
            "resolved": source["resolved"],
        }

    def projection_type(
        projection: exp.Expression,
        inputs: list[dict[str, Any]],
    ) -> str | None:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        if not isinstance(value, exp.Column) or len(inputs) != 1:
            return None
        source = aliases.get(value.table) if value.table else None
        if source is None and len(sources) == 1:
            source = sources[0]
        column = next(
            (
                item
                for item in (source or {}).get("columns", [])
                if item["name"] == value.name
            ),
            None,
        )
        return column["data_type"] or None if column else None

    def expression_detail(
        expression: exp.Expression,
        *,
        role: str,
    ) -> dict[str, Any] | None:
        value = _sql(expression)
        if not value:
            return None
        inputs: list[dict[str, Any]] = []
        for column in expression.find_all(exp.Column):
            if column.is_star:
                continue
            if (
                role == "sort"
                and not column.table
                and any(output["name"] == column.name for output in outputs)
            ):
                continue
            item = input_for(column)
            mark_usage(item, role)
            if item not in inputs:
                inputs.append(item)
        return {
            "expression": value,
            "inputs": inputs,
            "scope": _scope_name(expression),
        }

    for column in statement.find_all(exp.Column):
        if not column.is_star:
            mark_usage(input_for(column, warn=False), "read")

    selects = list(getattr(statement, "selects", []) or [])
    if not selects and isinstance(statement, exp.SetOperation):
        selects = list(getattr(statement.this, "selects", []) or [])
        warnings.add("set_operation_output_contract")
    outputs: list[dict[str, Any]] = []
    for projection in selects[:MAX_ANALYSIS_ITEMS]:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        star_source: dict[str, Any] | None = None
        if isinstance(value, exp.Star):
            star_source = sources[0] if len(sources) == 1 else None
        elif isinstance(value, exp.Column) and value.is_star:
            star_source = aliases.get(value.table)
        if star_source and star_source.get("columns"):
            for column in star_source["columns"]:
                mark_usage(
                    {
                        "source": star_source["name"],
                        "column": column["name"],
                        "resolved": True,
                    },
                    "output",
                )
                outputs.append({
                    "ordinal": len(outputs) + 1,
                    "name": column["name"],
                    "data_type": column["data_type"] or None,
                    "derivation": "direct",
                    "expression": f"{star_source['name']}.{column['name']}",
                    "inputs": [
                        {
                            "source": star_source["name"],
                            "column": column["name"],
                            "resolved": True,
                        }
                    ],
                })
            continue
        if isinstance(value, exp.Star) or (
            isinstance(value, exp.Column) and value.is_star
        ):
            warnings.add("unresolved_wildcard")
        inputs: list[dict[str, Any]] = []
        for column in value.find_all(exp.Column):
            item = input_for(column)
            mark_usage(item, "output")
            if item not in inputs:
                inputs.append(item)
        name = projection.alias or (
            value.name
            if isinstance(value, exp.Column) and not value.is_star
            else None
        )
        if not name:
            warnings.add("unnamed_output")
        outputs.append({
            "ordinal": len(outputs) + 1,
            "name": name,
            "data_type": projection_type(projection, inputs),
            "derivation": _derivation(projection),
            "expression": _sql(value),
            "inputs": inputs,
        })
    if len(selects) > MAX_ANALYSIS_ITEMS:
        warnings.add("too_many_outputs")

    transformations: list[dict[str, Any]] = []
    if ctes:
        transformations.append({
            "kind": "stages",
            "count": len(ctes),
            "items": [cte.alias_or_name for cte in ctes],
            "sql": None,
        })

    joins = list(statement.find_all(exp.Join))[:MAX_ANALYSIS_ITEMS]
    join_details: list[dict[str, Any]] = []
    if joins:
        items = []
        conditions = []
        for join in joins:
            side = _join_type(join)
            target = join.this.alias_or_name or _sql(join.this) or "relation"
            items.append(f"{side} {target}")
            condition = join.args.get("on")
            condition_sql = None
            inputs: list[dict[str, Any]] = []
            if condition is not None and (value := _sql(condition)):
                conditions.append(value)
                condition_sql = value
                detail = expression_detail(condition, role="join")
                inputs = detail["inputs"] if detail else []
            elif join.args.get("using"):
                using = join.args["using"]
                condition_sql = "USING (" + ", ".join(item.name for item in using) + ")"
                conditions.append(condition_sql)
            target_name = join.this.name if isinstance(join.this, exp.Table) else target
            target_alias = join.this.alias if isinstance(join.this, exp.Table) else None
            join_details.append({
                "join_type": side,
                "target": target_name or target,
                "alias": target_alias or None,
                "expression": condition_sql,
                "inputs": inputs,
                "scope": _scope_name(join),
            })
        transformations.append({
            "kind": "joins",
            "count": len(joins),
            "items": items,
            "sql": " AND ".join(conditions) or None,
        })

    where_terms = [
        term
        for where in statement.find_all(exp.Where)
        if isinstance(where.parent, exp.Select)
        for term in _and_terms(where.this)
    ][:MAX_ANALYSIS_ITEMS]
    row_filters = [
        detail
        for term in where_terms
        if (detail := expression_detail(term, role="filter")) is not None
    ]
    if where_terms:
        transformations.append({
            "kind": "filters",
            "count": len(where_terms),
            "items": [_sql(term) for term in where_terms if _sql(term)],
            "sql": None,
        })

    aggregate_filter_terms = [
        term
        for aggregate_filter in statement.find_all(exp.Filter)
        if isinstance(aggregate_filter.expression, exp.Where)
        for term in _and_terms(aggregate_filter.expression.this)
    ][:MAX_ANALYSIS_ITEMS]
    aggregate_filters = [
        detail
        for term in aggregate_filter_terms
        if (detail := expression_detail(term, role="aggregate_filter")) is not None
    ]

    grouping = [
        item
        for group in statement.find_all(exp.Group)
        for item in group.expressions
    ][:MAX_ANALYSIS_ITEMS]
    grouping_details = [
        detail
        for item in grouping
        if (detail := expression_detail(item, role="group")) is not None
    ]
    if grouping:
        transformations.append({
            "kind": "groups",
            "count": len(grouping),
            "items": [_sql(item) for item in grouping if _sql(item)],
            "sql": None,
        })

    aggregates = list(dict.fromkeys(
        value for item in statement.find_all(exp.AggFunc) if (value := _sql(item))
    ))[:MAX_ANALYSIS_ITEMS]
    if aggregates:
        transformations.append({
            "kind": "aggregates",
            "count": len(aggregates),
            "items": aggregates,
            "sql": None,
        })

    windows = list(dict.fromkeys(
        value for item in statement.find_all(exp.Window) if (value := _sql(item))
    ))[:MAX_ANALYSIS_ITEMS]
    if windows:
        transformations.append({
            "kind": "windows",
            "count": len(windows),
            "items": windows,
            "sql": None,
        })

    having_terms = [
        term
        for having in statement.find_all(exp.Having)
        for term in _and_terms(having.this)
    ][:MAX_ANALYSIS_ITEMS]
    group_filters = [
        detail
        for term in having_terms
        if (detail := expression_detail(term, role="having")) is not None
    ]
    if having_terms:
        transformations.append({
            "kind": "having",
            "count": len(having_terms),
            "items": [_sql(term) for term in having_terms if _sql(term)],
            "sql": None,
        })

    distinct_count = sum(
        1
        for select in statement.find_all(exp.Select)
        if select.args.get("distinct")
    )
    if distinct_count:
        transformations.append(
            {
                "kind": "distinct",
                "count": distinct_count,
                "items": [],
                "sql": None,
            }
        )

    set_operations = list(statement.find_all(exp.SetOperation))
    if set_operations:
        transformations.append({
            "kind": "sets",
            "count": len(set_operations),
            "items": [_set_operation_label(item) for item in set_operations],
            "sql": None,
        })

    order_items = [
        item
        for order in statement.find_all(exp.Order)
        for item in order.expressions
    ]
    ordering = [
        detail
        for item in order_items[:MAX_ANALYSIS_ITEMS]
        if (detail := expression_detail(item, role="sort")) is not None
    ]
    if order_items:
        transformations.append({
            "kind": "sorts",
            "count": len(order_items),
            "items": [
                _sql(item)
                for item in order_items[:MAX_ANALYSIS_ITEMS]
                if _sql(item)
            ],
            "sql": None,
        })

    limits = list(statement.find_all(exp.Limit))
    limit = next(
        (value for item in limits if (value := _sql(item.expression))),
        None,
    )
    if limits:
        transformations.append({
            "kind": "limits",
            "count": len(limits),
            "items": [value for item in limits if (value := _sql(item.expression))],
            "sql": None,
        })

    return {
        "status": "partial" if warnings else "available",
        "sources": sources,
        "transformations": transformations,
        "outputs": outputs,
        "formatted_sql": statement.sql(
            dialect="postgres",
            pretty=True,
            comments=False,
        ),
        "stages": [cte.alias_or_name for cte in ctes],
        "joins": join_details,
        "row_filters": row_filters,
        "aggregate_filters": aggregate_filters,
        "grouping": grouping_details,
        "group_filters": group_filters,
        "ordering": ordering,
        "distinct": bool(distinct_count),
        "limit": limit,
        "set_operations": [_set_operation_label(item) for item in set_operations],
        "stage_count": len(ctes),
        "join_count": len(joins),
        "filter_count": len(where_terms),
        "grouping_count": len(grouping),
        "aggregate_count": len(aggregates),
        "window_count": len(windows),
        "warnings": sorted(warnings),
    }
