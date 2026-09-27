"""Bounded, database-independent analysis of read-only PostgreSQL queries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope


MAX_QUERY_BYTES = 256 * 1024
MAX_ANALYSIS_ITEMS = 512
MAX_FRAGMENT_BYTES = 8 * 1024


class QueryDefinitionError(ValueError):
    """The supplied text is not one supported read-only PostgreSQL query."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def parse_query_definition(definition: str) -> exp.Expression:
    """Parse exactly one SELECT-shaped PostgreSQL query."""

    text = definition.strip().rstrip(";").strip()
    if not text:
        raise QueryDefinitionError(
            "empty_definition",
            "Enter a PostgreSQL SELECT query to analyze",
        )
    if len(text.encode("utf-8")) > MAX_QUERY_BYTES:
        raise QueryDefinitionError(
            "definition_too_large",
            "The query is too large to analyze",
        )
    try:
        statements = [
            statement
            for statement in parse(text, read="postgres")
            if statement is not None
        ]
    except SqlglotError as error:
        raise QueryDefinitionError(
            "invalid_sql",
            f"PostgreSQL query could not be parsed: {error}",
        ) from error
    if len(statements) != 1:
        raise QueryDefinitionError(
            "multiple_statements",
            "Enter one SELECT query without additional statements",
        )
    statement = statements[0]
    if isinstance(statement, exp.Subquery):
        statement = statement.this
    if not isinstance(statement, (exp.Select, exp.SetOperation)):
        raise QueryDefinitionError(
            "unsupported_statement",
            "Enter one read-only SELECT query",
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
        raise QueryDefinitionError(
            "mutating_query",
            "Query analysis does not allow data-changing or schema-changing operations",
        )
    if statement.find(exp.Into) is not None:
        raise QueryDefinitionError(
            "select_into",
            "Query analysis does not allow SELECT INTO",
        )
    return statement


def _sql(expression: exp.Expression | None) -> str | None:
    if expression is None:
        return None
    value = expression.sql(dialect="postgres", pretty=False, comments=False)
    if len(value.encode("utf-8")) > MAX_FRAGMENT_BYTES:
        return None
    return value


def _data_type_sql(expression: exp.Expression | None) -> str | None:
    """Return one source-derived PostgreSQL type when SQLGlot can prove it."""

    data_type = expression.type if expression is not None else None
    if data_type is None or data_type.is_type(exp.DataType.Type.UNKNOWN):
        return None
    return data_type.sql(dialect="postgres")


def _annotate_source_types(
    statement: exp.Expression,
    *,
    current_namespace: str,
    exact: dict[tuple[str, str], dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> None:
    """Annotate expressions from known relation shapes without requiring database I/O."""

    schema: dict[str, dict[str, str]] = {}
    referenced_names = {table.name for table in statement.find_all(exp.Table)}
    for name, candidates in by_name.items():
        if name not in referenced_names:
            continue
        relation = exact.get((current_namespace, name))
        if relation is None and len(candidates) == 1:
            relation = candidates[0]
        columns = {
            str(column["name"]): str(column.get("data_type") or "")
            for column in (relation or {}).get("columns", [])
            if column.get("data_type")
        }
        if columns:
            schema[name] = columns
    if schema:
        annotate_types(statement, schema=schema, dialect="postgres")


def _unique_expression_details(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated predicates while retaining all source inputs."""

    unique: dict[tuple[str, str | None], dict[str, Any]] = {}
    for item in items:
        key = (item["expression"], item.get("scope"))
        existing = unique.get(key)
        if existing is None:
            unique[key] = item
            continue
        for expression_input in item.get("inputs", []):
            if expression_input not in existing["inputs"]:
                existing["inputs"].append(expression_input)
    return list(unique.values())


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


def _query_owner(expression: exp.Expression) -> exp.Expression | None:
    """Return the nearest SELECT or set operation that owns an AST node."""

    parent: exp.Expression | None = expression
    while parent is not None:
        if isinstance(parent, (exp.Select, exp.SetOperation)):
            return parent
        parent = parent.parent
    return None


def _scope_kind(scope: Scope) -> str:
    return {
        ScopeType.ROOT: "final",
        ScopeType.CTE: "cte",
        ScopeType.DERIVED_TABLE: "derived_table",
        ScopeType.SUBQUERY: "subquery",
        ScopeType.UNION: "set_branch",
        ScopeType.UDTF: "table_function",
    }[scope.scope_type]


def _scope_result_name(scope: Scope, counters: dict[str, int]) -> str:
    parent = scope.expression.parent
    if scope.scope_type in {ScopeType.CTE, ScopeType.DERIVED_TABLE}:
        if parent is not None and parent.alias_or_name:
            return parent.alias_or_name
    if scope.scope_type is ScopeType.ROOT:
        return "query result"
    kind = _scope_kind(scope)
    counters[kind] = counters.get(kind, 0) + 1
    return f"{kind.replace('_', ' ')} {counters[kind]}"


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


@dataclass
class _SourceAnalysis:
    """Relation and alias index plus the source records shared by analysis phases."""

    aliases: dict[str, dict[str, Any]]
    sources: list[dict[str, Any]]
    warnings: set[str]

    def mark_usage(self, item: dict[str, Any], role: str) -> None:
        if not item["resolved"] or not item["source"]:
            return
        source = next(
            (candidate for candidate in self.sources if candidate["name"] == item["source"]),
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

    def input_for(self, column: exp.Column, *, warn: bool = True) -> dict[str, Any]:
        source = self.aliases.get(column.table) if column.table else None
        if source is None and not column.table:
            matching = [
                candidate
                for candidate in self.sources
                if any(item["name"] == column.name for item in candidate["columns"])
            ]
            if len(matching) == 1:
                source = matching[0]
            elif len(self.sources) == 1:
                source = self.sources[0]
        if source is None:
            if warn:
                self.warnings.add("unresolved_column_source")
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
        self,
        projection: exp.Expression,
        inputs: list[dict[str, Any]],
    ) -> str | None:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(value, exp.Column) and len(inputs) == 1:
            source = self.aliases.get(value.table) if value.table else None
            if source is None and len(self.sources) == 1:
                source = self.sources[0]
            column = next(
                (
                    item
                    for item in (source or {}).get("columns", [])
                    if item["name"] == value.name
                ),
                None,
            )
            if column and column["data_type"]:
                return column["data_type"]
        return _data_type_sql(projection)

    def expression_detail(
        self,
        expression: exp.Expression,
        *,
        role: str,
        outputs: list[dict[str, Any]],
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
            item = self.input_for(column)
            self.mark_usage(item, role)
            if item not in inputs:
                inputs.append(item)
        return {
            "expression": value,
            "inputs": inputs,
            "scope": _scope_name(expression),
        }


@dataclass
class _ExpressionAnalysis:
    """Output lineage and expression details collected before response assembly."""

    outputs: list[dict[str, Any]]
    joins: list[dict[str, Any]]
    where_terms: list[exp.Expression]
    row_filters: list[dict[str, Any]]
    aggregate_filters: list[dict[str, Any]]
    grouping_items: list[exp.Expression]
    grouping: list[dict[str, Any]]
    having_terms: list[exp.Expression]
    group_filters: list[dict[str, Any]]
    ordering_items: list[exp.Expression]
    ordering: list[dict[str, Any]]


@dataclass
class _TransformationAnalysis:
    """Query transformations and bounded counters used by the public response."""

    items: list[dict[str, Any]]
    distinct: bool
    limit: str | None
    set_operations: list[str]
    join_count: int
    filter_count: int
    grouping_count: int
    aggregate_count: int
    window_count: int


def _index_sources(
    statement: exp.Expression,
    *,
    current_namespace: str,
    exact: dict[tuple[str, str], dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
    ctes: list[exp.CTE],
    warnings: set[str],
) -> _SourceAnalysis:
    """Resolve physical relations and index query-local aliases."""

    cte_names = {cte.alias_or_name for cte in ctes}
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

    for subquery in statement.find_all(exp.Subquery):
        if not subquery.alias:
            continue
        aliases.setdefault(subquery.alias, {
            "namespace": None,
            "name": subquery.alias,
            "kind": "stage",
            "resolved": True,
            "columns": [],
        })

    return _SourceAnalysis(
        aliases=aliases,
        sources=list(sources_by_key.values()),
        warnings=warnings,
    )


def _analyze_expressions(
    statement: exp.Expression,
    sources: _SourceAnalysis,
) -> _ExpressionAnalysis:
    """Resolve projection lineage and clause-level column use in stable order."""

    for column in statement.find_all(exp.Column):
        if not column.is_star:
            sources.mark_usage(sources.input_for(column, warn=False), "read")

    selects = list(getattr(statement, "selects", []) or [])
    if not selects and isinstance(statement, exp.SetOperation):
        selects = list(getattr(statement.this, "selects", []) or [])
        sources.warnings.add("set_operation_output_contract")
    outputs: list[dict[str, Any]] = []
    for projection in selects[:MAX_ANALYSIS_ITEMS]:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        star_source: dict[str, Any] | None = None
        if isinstance(value, exp.Star):
            star_source = sources.sources[0] if len(sources.sources) == 1 else None
        elif isinstance(value, exp.Column) and value.is_star:
            star_source = sources.aliases.get(value.table)
        if star_source and star_source.get("columns"):
            for column in star_source["columns"]:
                sources.mark_usage(
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
            sources.warnings.add("unresolved_wildcard")
        inputs: list[dict[str, Any]] = []
        for column in value.find_all(exp.Column):
            item = sources.input_for(column)
            sources.mark_usage(item, "output")
            if item not in inputs:
                inputs.append(item)
        name = projection.alias or (
            value.name
            if isinstance(value, exp.Column) and not value.is_star
            else None
        )
        if not name:
            sources.warnings.add("unnamed_output")
        outputs.append({
            "ordinal": len(outputs) + 1,
            "name": name,
            "data_type": sources.projection_type(projection, inputs),
            "derivation": _derivation(projection),
            "expression": _sql(value),
            "inputs": inputs,
        })
    if len(selects) > MAX_ANALYSIS_ITEMS:
        sources.warnings.add("too_many_outputs")

    joins: list[dict[str, Any]] = []
    for join in list(statement.find_all(exp.Join))[:MAX_ANALYSIS_ITEMS]:
        side = _join_type(join)
        target = join.this.alias_or_name or _sql(join.this) or "relation"
        condition = join.args.get("on")
        condition_sql = None
        inputs: list[dict[str, Any]] = []
        if condition is not None and (value := _sql(condition)):
            condition_sql = value
            detail = sources.expression_detail(condition, role="join", outputs=outputs)
            inputs = detail["inputs"] if detail else []
        elif join.args.get("using"):
            using = join.args["using"]
            condition_sql = "USING (" + ", ".join(item.name for item in using) + ")"
        target_name = join.this.name if isinstance(join.this, exp.Table) else target
        target_alias = join.this.alias if isinstance(join.this, exp.Table) else None
        joins.append({
            "join_type": side,
            "target": target_name or target,
            "alias": target_alias or None,
            "expression": condition_sql,
            "inputs": inputs,
            "scope": _scope_name(join),
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
        if (detail := sources.expression_detail(term, role="filter", outputs=outputs)) is not None
    ]

    aggregate_filter_terms = [
        term
        for aggregate_filter in statement.find_all(exp.Filter)
        if isinstance(aggregate_filter.expression, exp.Where)
        for term in _and_terms(aggregate_filter.expression.this)
    ][:MAX_ANALYSIS_ITEMS]
    aggregate_filters = _unique_expression_details(
        detail
        for term in aggregate_filter_terms
        if (detail := sources.expression_detail(term, role="aggregate_filter", outputs=outputs)) is not None
    )

    grouping_items = [
        item
        for group in statement.find_all(exp.Group)
        for item in group.expressions
    ][:MAX_ANALYSIS_ITEMS]
    grouping = [
        detail
        for item in grouping_items
        if (detail := sources.expression_detail(item, role="group", outputs=outputs)) is not None
    ]

    having_terms = [
        term
        for having in statement.find_all(exp.Having)
        for term in _and_terms(having.this)
    ][:MAX_ANALYSIS_ITEMS]
    group_filters = [
        detail
        for term in having_terms
        if (detail := sources.expression_detail(term, role="having", outputs=outputs)) is not None
    ]

    ordering_items = [
        item
        for order in statement.find_all(exp.Order)
        for item in order.expressions
    ]
    ordering = [
        detail
        for item in ordering_items[:MAX_ANALYSIS_ITEMS]
        if (detail := sources.expression_detail(item, role="sort", outputs=outputs)) is not None
    ]

    return _ExpressionAnalysis(
        outputs=outputs,
        joins=joins,
        where_terms=where_terms,
        row_filters=row_filters,
        aggregate_filters=aggregate_filters,
        grouping_items=grouping_items,
        grouping=grouping,
        having_terms=having_terms,
        group_filters=group_filters,
        ordering_items=ordering_items,
        ordering=ordering,
    )


def _collect_transformations(
    statement: exp.Expression,
    *,
    ctes: list[exp.CTE],
    expressions: _ExpressionAnalysis,
) -> _TransformationAnalysis:
    """Build ordered transformation summaries and their legacy counters."""

    transformations: list[dict[str, Any]] = []
    if ctes:
        transformations.append({
            "kind": "stages",
            "count": len(ctes),
            "items": [cte.alias_or_name for cte in ctes],
            "sql": None,
        })

    joins = expressions.joins
    if joins:
        items = [
            f"{join['join_type']} {join['alias'] or join['target']}"
            for join in joins
        ]
        conditions = [join["expression"] for join in joins if join["expression"]]
        transformations.append({
            "kind": "joins",
            "count": len(joins),
            "items": items,
            "sql": " AND ".join(conditions) or None,
        })

    where_terms = expressions.where_terms
    if where_terms:
        transformations.append({
            "kind": "filters",
            "count": len(where_terms),
            "items": [_sql(term) for term in where_terms if _sql(term)],
            "sql": None,
        })

    grouping_items = expressions.grouping_items
    if grouping_items:
        transformations.append({
            "kind": "groups",
            "count": len(grouping_items),
            "items": [_sql(item) for item in grouping_items if _sql(item)],
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

    having_terms = expressions.having_terms
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
        transformations.append({
            "kind": "distinct",
            "count": distinct_count,
            "items": [],
            "sql": None,
        })

    set_operations = list(statement.find_all(exp.SetOperation))
    if set_operations:
        transformations.append({
            "kind": "sets",
            "count": len(set_operations),
            "items": [_set_operation_label(item) for item in set_operations],
            "sql": None,
        })

    ordering_items = expressions.ordering_items
    if ordering_items:
        transformations.append({
            "kind": "sorts",
            "count": len(ordering_items),
            "items": [
                _sql(item)
                for item in ordering_items[:MAX_ANALYSIS_ITEMS]
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

    return _TransformationAnalysis(
        items=transformations,
        distinct=bool(distinct_count),
        limit=limit,
        set_operations=[_set_operation_label(item) for item in set_operations],
        join_count=len(joins),
        filter_count=len(where_terms),
        grouping_count=len(grouping_items),
        aggregate_count=len(aggregates),
        window_count=len(windows),
    )


def _assemble_query_analysis(
    statement: exp.Expression,
    *,
    ctes: list[exp.CTE],
    sources: _SourceAnalysis,
    expressions: _ExpressionAnalysis,
    transformations: _TransformationAnalysis,
    query_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the public response after source, expression and transform phases."""

    return {
        "status": "partial" if sources.warnings else "available",
        "sources": sources.sources,
        "transformations": transformations.items,
        "outputs": expressions.outputs,
        "formatted_sql": statement.sql(
            dialect="postgres",
            pretty=True,
            comments=False,
        ),
        "stages": [cte.alias_or_name for cte in ctes],
        "joins": expressions.joins,
        "row_filters": expressions.row_filters,
        "aggregate_filters": expressions.aggregate_filters,
        "grouping": expressions.grouping,
        "group_filters": expressions.group_filters,
        "ordering": expressions.ordering,
        "distinct": transformations.distinct,
        "limit": transformations.limit,
        "set_operations": transformations.set_operations,
        "query_steps": query_steps,
        "stage_count": len(ctes),
        "join_count": transformations.join_count,
        "filter_count": transformations.filter_count,
        "grouping_count": transformations.grouping_count,
        "aggregate_count": transformations.aggregate_count,
        "window_count": transformations.window_count,
        "warnings": sorted(sources.warnings),
    }


def referenced_relations(
    definition: str,
    *,
    current_namespace: str = "desired",
) -> list[tuple[str, str]]:
    """Return physical relation names, excluding query-local CTE references."""

    statement = parse_query_definition(definition)
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


def _query_steps(
    statement: exp.Expression,
    *,
    current_namespace: str,
    exact: dict[tuple[str, str], dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
    warnings: set[str],
) -> list[dict[str, Any]]:
    """Build dependency-ordered query scopes for a chronological query story."""

    steps: list[dict[str, Any]] = []
    steps_by_scope: dict[int, dict[str, Any]] = {}
    counters: dict[str, int] = {}

    for scope in traverse_scope(statement):
        query = scope.expression
        kind = _scope_kind(scope)
        result_name = _scope_result_name(scope, counters)
        selected_sources = scope.selected_sources

        source_order: list[str] = []
        if isinstance(query, exp.Select):
            from_clause = query.args.get("from_")
            if from_clause is not None and from_clause.this is not None:
                source_order.append(from_clause.this.alias_or_name)
            source_order.extend(
                join.this.alias_or_name
                for join in query.args.get("joins") or []
                if join.this is not None
            )
        source_order.extend(alias for alias in selected_sources if alias not in source_order)

        participants: list[dict[str, Any]] = []
        participant_by_reference: dict[str, dict[str, Any]] = {}
        known_columns: dict[str, dict[str, str | None]] = {}
        known_column_order: dict[str, list[str]] = {}

        for reference in source_order:
            selected = selected_sources.get(reference)
            if selected is None:
                continue
            node, source = selected
            namespace: str | None = None
            name = reference
            participant_kind = "relation"
            resolved = False
            columns: list[dict[str, Any]] = []
            column_types: dict[str, str | None] = {}
            column_order: list[str] = []

            if isinstance(source, exp.Table):
                namespace = source.db or current_namespace
                name = source.name
                relation = _resolved_relation(
                    source.db or None,
                    source.name,
                    current_namespace=current_namespace,
                    exact=exact,
                    by_name=by_name,
                )
                resolved = relation is not None
                participant_kind = str((relation or {}).get("kind") or "relation")
                for column in (relation or {}).get("columns", []):
                    column_name = str(column["name"])
                    column_order.append(column_name)
                    column_types[column_name] = str(column.get("data_type") or "") or None
            elif isinstance(source, Scope):
                producer = steps_by_scope.get(id(source))
                if producer is not None:
                    name = producer["result_name"]
                    participant_kind = "intermediate"
                    resolved = True
                    for output in producer["outputs"]:
                        if not output["name"]:
                            continue
                        column_order.append(output["name"])
                        column_types[output["name"]] = output["data_type"]

            participant = {
                "reference": reference,
                "namespace": namespace,
                "name": name,
                "kind": participant_kind,
                "resolved": resolved,
                "columns": columns,
            }
            participants.append(participant)
            participant_by_reference[reference] = participant
            participant_by_reference.setdefault(name, participant)
            known_columns[reference] = column_types
            known_column_order[reference] = column_order

        scope_column_ids = {id(column) for column in scope.columns}
        output_names: set[str] = set()

        def aggregate_filter_predicate(column: exp.Column, projection: exp.Expression) -> bool:
            parent = column.parent
            while parent is not None and parent is not projection:
                if isinstance(parent, exp.Where) and isinstance(parent.parent, exp.Filter):
                    return True
                parent = parent.parent
            return False

        def participant_for(column: exp.Column) -> dict[str, Any] | None:
            if column.table:
                return participant_by_reference.get(column.table)
            matching = [
                participant
                for participant in participants
                if column.name in known_columns.get(participant["reference"], {})
            ]
            if len(matching) == 1:
                return matching[0]
            return participants[0] if len(participants) == 1 else None

        def record_input(
            column: exp.Column,
            role: str,
            *,
            warn: bool = True,
        ) -> dict[str, Any]:
            participant = participant_for(column)
            if participant is None:
                if warn:
                    warnings.add("unresolved_column_source")
                return {
                    "source": column.table or None,
                    "column": column.name,
                    "resolved": False,
                }
            reference = participant["reference"]
            existing = next(
                (item for item in participant["columns"] if item["name"] == column.name),
                None,
            )
            if existing is None:
                existing = {
                    "name": column.name,
                    "data_type": known_columns.get(reference, {}).get(column.name),
                    "roles": [],
                    "filter_only": False,
                }
                participant["columns"].append(existing)
            if role not in existing["roles"]:
                existing["roles"].append(role)
            return {
                "source": reference,
                "column": column.name,
                "resolved": participant["resolved"],
            }

        def detail(expression: exp.Expression, role: str) -> dict[str, Any] | None:
            value = _sql(expression)
            if not value:
                return None
            inputs: list[dict[str, Any]] = []
            for column in expression.find_all(exp.Column):
                if id(column) not in scope_column_ids or column.is_star:
                    continue
                if role == "sort" and not column.table and column.name in output_names:
                    continue
                item = record_input(column, role)
                if item not in inputs:
                    inputs.append(item)
            return {"expression": value, "inputs": inputs, "scope": result_name}

        projections = list(getattr(query, "selects", []) or [])
        outputs: list[dict[str, Any]] = []
        for projection in projections[:MAX_ANALYSIS_ITEMS]:
            value = projection.this if isinstance(projection, exp.Alias) else projection
            wildcard_participant: dict[str, Any] | None = None
            if isinstance(value, exp.Star) and len(participants) == 1:
                wildcard_participant = participants[0]
            elif isinstance(value, exp.Column) and value.is_star:
                wildcard_participant = participant_by_reference.get(value.table)
            if wildcard_participant is not None:
                reference = wildcard_participant["reference"]
                for column_name in known_column_order.get(reference, []):
                    synthetic = exp.column(column_name, table=reference)
                    item = record_input(synthetic, "output")
                    output = {
                        "ordinal": len(outputs) + 1,
                        "name": column_name,
                        "data_type": known_columns[reference].get(column_name),
                        "derivation": "direct",
                        "expression": f"{reference}.{column_name}",
                        "inputs": [item],
                    }
                    outputs.append(output)
                    output_names.add(column_name)
                continue

            inputs: list[dict[str, Any]] = []
            for column in value.find_all(exp.Column):
                if (
                    id(column) not in scope_column_ids
                    or column.is_star
                    or aggregate_filter_predicate(column, value)
                ):
                    continue
                item = record_input(column, "output")
                if item not in inputs:
                    inputs.append(item)
            output_name = projection.alias or (
                value.name
                if isinstance(value, exp.Column) and not value.is_star
                else None
            )
            if output_name:
                output_names.add(output_name)
            data_type = None
            if isinstance(value, exp.Column) and len(inputs) == 1:
                participant = participant_for(value)
                if participant is not None:
                    data_type = known_columns.get(participant["reference"], {}).get(value.name)
            if data_type is None:
                data_type = _data_type_sql(projection)
            outputs.append({
                "ordinal": len(outputs) + 1,
                "name": output_name,
                "data_type": data_type,
                "derivation": _derivation(projection),
                "expression": _sql(value),
                "inputs": inputs,
            })

        joins: list[dict[str, Any]] = []
        for join in (
            query.args.get("joins") or []
            if isinstance(query, exp.Select)
            else []
        ):
            target_reference = join.this.alias_or_name
            target_participant = participant_by_reference.get(target_reference)
            condition = join.args.get("on")
            condition_detail = detail(condition, "join") if condition is not None else None
            condition_sql = condition_detail["expression"] if condition_detail else None
            inputs = condition_detail["inputs"] if condition_detail else []
            if condition_sql is None and join.args.get("using"):
                condition_sql = "USING (" + ", ".join(
                    item.name for item in join.args["using"]
                ) + ")"
            joins.append({
                "join_type": _join_type(join),
                "target": target_participant["name"] if target_participant else target_reference,
                "alias": target_reference if target_participant and target_reference != target_participant["name"] else None,
                "expression": condition_sql,
                "inputs": inputs,
                "scope": result_name,
            })

        where = query.args.get("where") if isinstance(query, exp.Select) else None
        row_filters = [
            item
            for term in (_and_terms(where.this) if where is not None else [])
            if (item := detail(term, "filter")) is not None
        ]
        aggregate_filters = _unique_expression_details(
            item
            for aggregate_filter in query.find_all(exp.Filter)
            if _query_owner(aggregate_filter) is query
            and isinstance(aggregate_filter.expression, exp.Where)
            for term in _and_terms(aggregate_filter.expression.this)
            if (item := detail(term, "aggregate_filter")) is not None
        )
        group = query.args.get("group") if isinstance(query, exp.Select) else None
        grouping = [
            item
            for expression in (group.expressions if group is not None else [])
            if (item := detail(expression, "group")) is not None
        ]
        having = query.args.get("having") if isinstance(query, exp.Select) else None
        group_filters = [
            item
            for term in (_and_terms(having.this) if having is not None else [])
            if (item := detail(term, "having")) is not None
        ]
        order = query.args.get("order")
        ordering = [
            item
            for expression in (order.expressions if order is not None else [])
            if (item := detail(expression, "sort")) is not None
        ]
        limit_expression = query.args.get("limit")
        limit = _sql(limit_expression.expression) if limit_expression is not None else None

        filter_roles = {"filter", "aggregate_filter"}
        for participant in participants:
            for column in participant["columns"]:
                column["filter_only"] = bool(column["roles"]) and set(column["roles"]) <= filter_roles

        step = {
            "ordinal": len(steps) + 1,
            "kind": kind,
            "result_name": result_name,
            "participants": participants,
            "joins": joins,
            "row_filters": row_filters,
            "aggregate_filters": aggregate_filters,
            "grouping": grouping,
            "group_filters": group_filters,
            "ordering": ordering,
            "distinct": bool(query.args.get("distinct")),
            "limit": limit,
            "outputs": outputs,
        }
        steps.append(step)
        steps_by_scope[id(scope)] = step

    return steps


def analyze_query_definition(
    definition: str,
    relations: Iterable[dict[str, Any]] = (),
    *,
    current_namespace: str = "desired",
) -> dict[str, Any]:
    """Derive a chronological query story without database I/O."""

    statement = parse_query_definition(definition)
    exact, by_name = _relation_index(relations)
    _annotate_source_types(
        statement,
        current_namespace=current_namespace,
        exact=exact,
        by_name=by_name,
    )
    ctes = list(statement.find_all(exp.CTE))[:MAX_ANALYSIS_ITEMS]
    warnings: set[str] = set()
    sources = _index_sources(
        statement,
        current_namespace=current_namespace,
        exact=exact,
        by_name=by_name,
        ctes=ctes,
        warnings=warnings,
    )
    expressions = _analyze_expressions(statement, sources)
    transformations = _collect_transformations(
        statement,
        ctes=ctes,
        expressions=expressions,
    )
    query_steps = _query_steps(
        statement,
        current_namespace=current_namespace,
        exact=exact,
        by_name=by_name,
        warnings=warnings,
    )
    return _assemble_query_analysis(
        statement,
        ctes=ctes,
        sources=sources,
        expressions=expressions,
        transformations=transformations,
        query_steps=query_steps,
    )
