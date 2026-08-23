from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify


MAX_PROVENANCE_OUTPUTS = 512
MAX_INPUTS_PER_OUTPUT = 128
MAX_EXPRESSION_BYTES = 4096
MAX_PROVENANCE_BYTES = 256 * 1024
MAX_VIEW_DEFINITION_BYTES = 64 * 1024
MAX_JOIN_COUNT = 128
MAX_PREDICATES_PER_JOIN = 64
MAX_JOIN_PREDICATES = 512
MAX_JOIN_PROVENANCE_BYTES = 256 * 1024
MAX_SQL_STAGES = 128
MAX_STAGE_OUTPUTS = 512
MAX_STAGE_INPUTS = 128
MAX_STAGE_PREDICATES = 512
MAX_SQL_STAGES_BYTES = 256 * 1024
SQL_STAGES_VERSION = 1
SQL_STAGE_ORDER_SEMANTICS = "syntactic_dependency"


class _TooManyStagesError(Exception):
    pass


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expression_sql(expression: exp.Expr) -> str:
    projection = expression.this if isinstance(expression, exp.Alias) else expression
    return projection.sql(dialect="postgres", pretty=False, comments=False)


def _derivation(expression: exp.Expr) -> str:
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


def _source_catalog(
    sources: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, dict[str, str]]]]:
    source_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for source in sources:
        namespace = source["namespace"]
        relation = source["relation"]
        source_lookup[(namespace, relation)] = source
        schema.setdefault(namespace, {})[relation] = {
            column["name"]: column["type"] for column in source.get("columns", [])
        }
    return source_lookup, schema


def unavailable_sql_stages(
    relation_fingerprint: str, reason: str, *, detail: str | None = None,
) -> dict[str, Any]:
    fingerprint_value = {
        "status": "unavailable",
        "version": SQL_STAGES_VERSION,
        "orderSemantics": SQL_STAGE_ORDER_SEMANTICS,
        "stages": [],
        "reason": reason,
    }
    if detail is not None:
        fingerprint_value["detail"] = detail
    return {
        **fingerprint_value,
        "relationFingerprint": relation_fingerprint,
        "fingerprint": _fingerprint(fingerprint_value),
    }


def _bounded_expression(expression: exp.Expr) -> dict[str, Any]:
    sql = expression.sql(dialect="postgres", pretty=False, comments=False)
    if len(sql.encode("utf-8")) > MAX_EXPRESSION_BYTES:
        return {"status": "unavailable", "reason": "too_large"}
    return {"status": "available", "sql": sql}


def _query_selects(expression: exp.Expr) -> list[exp.Select]:
    if isinstance(expression, exp.Subquery):
        return _query_selects(expression.this)
    if isinstance(expression, exp.Select):
        return [expression]
    if isinstance(expression, exp.SetOperation):
        return [*_query_selects(expression.this), *_query_selects(expression.expression)]
    return []


def derive_sql_stages(
    definition: str,
    sources: list[dict[str, Any]],
    *,
    current_namespace: str,
    relation_fingerprint: str,
) -> dict[str, Any]:
    """Describe real query-local CTE, FROM/JOIN subquery, and root stages.

    Stage inputs are resolved only to other parsed stages or exact relations from
    the verified dependency snapshot. The ordering is syntactic and dependency
    aware; it does not describe PostgreSQL execution order.
    """
    if len(definition.encode("utf-8")) > MAX_VIEW_DEFINITION_BYTES:
        return unavailable_sql_stages(relation_fingerprint, "too_large")

    source_lookup = {
        (source["namespace"], source["relation"]): source
        for source in sources
    }
    try:
        expression = parse_one(definition, read="postgres")
        if not isinstance(expression, (exp.Select, exp.SetOperation, exp.Subquery)):
            return unavailable_sql_stages(relation_fingerprint, "unsupported_statement")

        specs: list[dict[str, Any]] = []
        derived_ids: dict[int, str] = {}

        def add_stage(
            query: exp.Expr,
            *,
            kind: str,
            name: str | None,
            parent_stage_id: str | None,
            path: tuple[Any, ...],
            scope: dict[str, str],
            output_names: list[str],
            recursive_scope: bool = False,
            sql_query: exp.Expr | None = None,
        ) -> dict[str, Any]:
            if len(specs) >= MAX_SQL_STAGES:
                raise _TooManyStagesError
            stage_identity = {"version": SQL_STAGES_VERSION, "kind": kind, "path": path, "name": name}
            stage_id = "stage_" + _fingerprint(stage_identity)[:24]
            spec = {
                "stageId": stage_id,
                "query": query,
                "sqlQuery": sql_query if sql_query is not None else query,
                "kind": kind,
                "name": name,
                "parentStageId": parent_stage_id,
                "scope": dict(scope),
                "outputNames": output_names,
                "recursiveScope": recursive_scope,
                "syntaxOrdinal": len(specs),
            }
            specs.append(spec)
            return spec

        def alias_columns(node: exp.Expr) -> list[str]:
            alias = node.args.get("alias")
            if not isinstance(alias, exp.TableAlias):
                return []
            return [column.name for column in alias.args.get("columns") or []]

        def register_query(
            query: exp.Expr,
            *,
            parent_stage_id: str | None,
            inherited_scope: dict[str, str],
            path: tuple[Any, ...],
        ) -> dict[str, str]:
            local_scope = dict(inherited_scope)
            with_clause = query.args.get("with_")
            cte_specs: list[tuple[dict[str, Any], exp.CTE, dict[str, str], tuple[Any, ...]]] = []
            if isinstance(with_clause, exp.With):
                recursive_with = bool(with_clause.args.get("recursive"))
                for index, cte in enumerate(with_clause.expressions):
                    cte_name = cte.alias_or_name
                    cte_path = (*path, "cte", index)
                    visible_scope = dict(local_scope)
                    spec = add_stage(
                        cte.this,
                        kind="cte",
                        name=cte_name,
                        parent_stage_id=parent_stage_id,
                        path=cte_path,
                        scope=visible_scope,
                        output_names=alias_columns(cte),
                        recursive_scope=recursive_with,
                    )
                    if recursive_with:
                        visible_scope[cte_name] = spec["stageId"]
                        spec["scope"] = visible_scope
                    cte_specs.append((spec, cte, visible_scope, cte_path))
                    local_scope[cte_name] = spec["stageId"]

                for spec, cte, visible_scope, cte_path in cte_specs:
                    spec["scope"] = register_query(
                        cte.this,
                        parent_stage_id=spec["stageId"],
                        inherited_scope=visible_scope,
                        path=(*cte_path, "query"),
                    )

            for select_index, select in enumerate(_query_selects(query)):
                from_clause = select.args.get("from_")
                source_nodes = ([from_clause.this] if from_clause is not None else []) + [
                    join.this for join in select.args.get("joins") or []
                ]
                for source_index, source_node in enumerate(source_nodes):
                    candidate = source_node.this if isinstance(source_node, exp.Lateral) else source_node
                    if not isinstance(candidate, exp.Subquery):
                        continue
                    derived_path = (*path, "select", select_index, "source", source_index)
                    derived_reference = source_node if isinstance(source_node, exp.Lateral) else candidate
                    spec = add_stage(
                        candidate.this,
                        kind="derived_table",
                        name=derived_reference.alias_or_name or None,
                        parent_stage_id=parent_stage_id,
                        path=derived_path,
                        scope=local_scope,
                        output_names=alias_columns(derived_reference),
                    )
                    derived_ids[id(candidate)] = spec["stageId"]
                    spec["scope"] = register_query(
                        candidate.this,
                        parent_stage_id=spec["stageId"],
                        inherited_scope=local_scope,
                        path=(*derived_path, "query"),
                    )
            return local_scope

        root_scope = register_query(
            expression, parent_stage_id=None, inherited_scope={}, path=("root",),
        )
        root_sql_query = expression.copy()
        root_sql_body = root_sql_query.this if isinstance(root_sql_query, exp.Subquery) else root_sql_query
        root_sql_body.set("with_", None)
        add_stage(
            expression,
            kind="query_block",
            name=None,
            parent_stage_id=None,
            path=("root", "query_block"),
            scope=root_scope,
            output_names=[],
            sql_query=root_sql_query,
        )

        records_by_id: dict[str, dict[str, Any]] = {}
        reasons_by_id: dict[str, set[str]] = {}
        for spec in specs:
            reasons: set[str] = set()
            query = spec["query"]
            sql_envelope = _bounded_expression(spec["sqlQuery"])
            if sql_envelope["status"] != "available":
                reasons.add("stage_sql_too_large")

            selects = _query_selects(query)
            output_columns: list[dict[str, Any]] = []
            projections = selects[0].expressions if selects else []
            if not selects:
                reasons.add("unsupported_query_shape")
            if len(projections) > MAX_STAGE_OUTPUTS:
                projections = projections[:MAX_STAGE_OUTPUTS]
                reasons.add("too_many_output_columns")
            explicit_names = spec["outputNames"]
            for index, projection in enumerate(projections):
                projected = projection.this if isinstance(projection, exp.Alias) else projection
                if index < len(explicit_names):
                    name = explicit_names[index]
                    name_source = "column_alias_list"
                elif isinstance(projection, exp.Alias):
                    name = projection.alias
                    name_source = "explicit_alias"
                elif isinstance(projected, exp.Column):
                    name = projected.name
                    name_source = "source_column"
                elif isinstance(projected, exp.Star):
                    name = "*"
                    name_source = "wildcard"
                    reasons.add("unresolved_wildcard_outputs")
                else:
                    name = None
                    name_source = "unavailable"
                    reasons.add("unresolved_output_name")
                projection_envelope = _bounded_expression(projected)
                if projection_envelope["status"] != "available":
                    reasons.add("output_expression_too_large")
                output_columns.append({
                    "ordinal": index + 1,
                    "name": name,
                    "nameSource": name_source,
                    "expression": projection_envelope,
                })
            if len(explicit_names) > len(projections):
                reasons.add("unresolved_output_columns")

            inputs: list[dict[str, Any]] = []
            dependencies: set[str] = set()
            join_predicates: list[dict[str, Any]] = []
            where_predicates: list[dict[str, Any]] = []
            having_predicates: list[dict[str, Any]] = []
            predicate_count = 0

            def append_predicates(target: list[dict[str, Any]], predicate: exp.Expr) -> None:
                nonlocal predicate_count
                for term in _and_terms(predicate):
                    if predicate_count >= MAX_STAGE_PREDICATES:
                        reasons.add("too_many_predicates")
                        return
                    envelope = _bounded_expression(term)
                    if envelope["status"] != "available":
                        reasons.add("predicate_expression_too_large")
                    target.append({"ordinal": len(target) + 1, "expression": envelope})
                    predicate_count += 1

            for select in selects:
                from_clause = select.args.get("from_")
                references: list[tuple[exp.Expr, exp.Join | None]] = []
                if from_clause is not None:
                    references.append((from_clause.this, None))
                references.extend((join.this, join) for join in select.args.get("joins") or [])
                for source_node, join in references:
                    candidate = source_node.this if isinstance(source_node, exp.Lateral) else source_node
                    source_value: dict[str, Any] | None = None
                    reference_alias = (
                        source_node.alias_or_name
                        if isinstance(source_node, exp.Lateral)
                        else candidate.alias_or_name if isinstance(candidate, (exp.Table, exp.Subquery)) else ""
                    )
                    if isinstance(candidate, exp.Subquery):
                        stage_id = derived_ids.get(id(candidate))
                        if stage_id is not None:
                            source_value = {"type": "stage", "stageId": stage_id}
                            dependencies.add(stage_id)
                        else:
                            reasons.add("unresolved_derived_table")
                    elif isinstance(candidate, exp.Table):
                        if not candidate.db and not candidate.catalog and candidate.name in spec["scope"]:
                            stage_id = spec["scope"][candidate.name]
                            source_value = {"type": "stage", "stageId": stage_id}
                            dependencies.add(stage_id)
                        elif candidate.catalog:
                            reasons.add("unresolved_relation")
                        else:
                            namespace = candidate.db or current_namespace
                            source = source_lookup.get((namespace, candidate.name))
                            if source is None:
                                reasons.add("unresolved_relation")
                            else:
                                source_value = {
                                    "type": "relation",
                                    "profileId": source["profileId"],
                                    "database": source["database"],
                                    "namespace": source["namespace"],
                                    "relation": source["relation"],
                                    "kind": source["kind"],
                                }
                    else:
                        reasons.add("unsupported_input_source")
                    if source_value is not None:
                        if len(inputs) >= MAX_STAGE_INPUTS:
                            reasons.add("too_many_inputs")
                        else:
                            inputs.append({
                                "inputOrdinal": len(inputs) + 1,
                                "referenceAlias": reference_alias or None,
                                "source": source_value,
                            })

                    if join is not None:
                        condition = join.args.get("on")
                        if condition is not None:
                            append_predicates(join_predicates, condition)
                        elif (join.method or "").upper() == "NATURAL":
                            reasons.add("unsupported_natural_join_predicate")
                        elif join.args.get("using"):
                            reasons.add("unsupported_using_join_predicate")

                where = select.args.get("where")
                if isinstance(where, exp.Where):
                    append_predicates(where_predicates, where.this)
                having = select.args.get("having")
                if isinstance(having, exp.Having):
                    append_predicates(having_predicates, having.this)

            recursive = spec["stageId"] in dependencies
            if recursive:
                reasons.add("recursive_query")
            elif spec["recursiveScope"] and spec["kind"] == "cte":
                recursive = False

            record = {
                "stageId": spec["stageId"],
                "displayOrdinal": 0,
                "kind": spec["kind"],
                "parentStageId": spec["parentStageId"],
                "recursive": recursive,
                "lifetime": "query",
                "dependsOnStageIds": list(dependencies),
                "sql": sql_envelope,
                "outputColumns": output_columns,
                "inputs": inputs,
                "joinPredicates": join_predicates,
                "wherePredicates": where_predicates,
                "havingPredicates": having_predicates,
                "mappingStatus": "partial" if reasons else "available",
            }
            if spec["name"] is not None:
                record["name"] = spec["name"]
            if reasons:
                record["reasons"] = sorted(reasons)
            records_by_id[spec["stageId"]] = record
            reasons_by_id[spec["stageId"]] = reasons

        ordered_ids: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        syntax_ordinals = {spec["stageId"]: spec["syntaxOrdinal"] for spec in specs}

        def visit(stage_id: str) -> None:
            if stage_id in visited or stage_id in visiting:
                return
            visiting.add(stage_id)
            record = records_by_id[stage_id]
            dependencies = sorted(
                record["dependsOnStageIds"],
                key=lambda item: syntax_ordinals.get(item, len(syntax_ordinals)),
            )
            for dependency in dependencies:
                if dependency != stage_id and dependency in records_by_id:
                    visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)
            ordered_ids.append(stage_id)

        for spec in specs:
            visit(spec["stageId"])
        ordinals = {stage_id: index + 1 for index, stage_id in enumerate(ordered_ids)}
        stages = []
        for stage_id in ordered_ids:
            record = records_by_id[stage_id]
            record["displayOrdinal"] = ordinals[stage_id]
            record["dependsOnStageIds"].sort(key=lambda item: ordinals.get(item, len(ordinals) + 1))
            stages.append(record)

        envelope_reasons = sorted({reason for reasons in reasons_by_id.values() for reason in reasons})
        status = "partial" if envelope_reasons else "available"
        fingerprint_value = {
            "status": status,
            "version": SQL_STAGES_VERSION,
            "orderSemantics": SQL_STAGE_ORDER_SEMANTICS,
            "stages": stages,
        }
        if envelope_reasons:
            fingerprint_value["reasons"] = envelope_reasons
        if len(json.dumps(fingerprint_value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_SQL_STAGES_BYTES:
            return unavailable_sql_stages(relation_fingerprint, "too_large")
        return {
            **fingerprint_value,
            "relationFingerprint": relation_fingerprint,
            "fingerprint": _fingerprint(fingerprint_value),
        }
    except _TooManyStagesError:
        return unavailable_sql_stages(relation_fingerprint, "too_many_stages")
    except Exception as exc:
        return unavailable_sql_stages(
            relation_fingerprint, "analysis_failed", detail=type(exc).__name__,
        )


def derive_column_provenance(
    definition: str,
    output_columns: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    current_namespace: str,
    relation_fingerprint: str,
) -> dict[str, Any]:
    """Derive bounded per-output lineage from one verified relation snapshot.

    SQLGlot resolves PostgreSQL aliases, CTEs, subqueries, and projections. Every
    reported leaf is then checked against source columns read in the same
    repeatable-read PostgreSQL transaction. Unresolved leaves remain explicit;
    they are never promoted to authoritative mappings.
    """
    if not output_columns:
        return {"status": "unavailable", "reason": "no_outputs"}
    if len(output_columns) > MAX_PROVENANCE_OUTPUTS:
        return {"status": "unavailable", "reason": "too_many_outputs"}
    if len(definition.encode("utf-8")) > MAX_VIEW_DEFINITION_BYTES:
        return {"status": "unavailable", "reason": "too_large"}

    source_lookup, schema = _source_catalog(sources)

    try:
        expression = parse_one(definition, read="postgres")
        outputs: list[dict[str, Any]] = []
        partial = False
        for column in output_columns:
            root = lineage(
                column["name"], expression, schema=schema, dialect="postgres", db=current_namespace,
            )
            sql = _expression_sql(root.expression)
            sql_bytes = len(sql.encode("utf-8"))
            inputs: list[dict[str, Any]] = []
            unresolved = False
            seen: set[tuple[str, str, str]] = set()
            for node in root.walk():
                if isinstance(node.expression, exp.Placeholder):
                    unresolved = True
                    continue
                if not isinstance(node.expression, exp.Table):
                    continue
                try:
                    reference = parse_one(node.name, read="postgres", into=exp.Column)
                except SqlglotError:
                    unresolved = True
                    continue
                namespace = node.expression.db or current_namespace
                relation = node.expression.name
                source = source_lookup.get((namespace, relation))
                source_column = next(
                    (item for item in source.get("columns", []) if item["name"] == reference.name), None,
                ) if source else None
                identity = (namespace, relation, reference.name)
                if source is None or source_column is None:
                    unresolved = True
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                inputs.append({
                    "database": source["database"],
                    "namespace": namespace,
                    "relation": relation,
                    "kind": source["kind"],
                    "columnName": source_column["name"],
                    "columnOrdinal": source_column["ordinal"],
                })
            inputs.sort(key=lambda item: (
                item["namespace"], item["relation"], item["columnOrdinal"], item["columnName"],
            ))
            if len(inputs) > MAX_INPUTS_PER_OUTPUT:
                unresolved = True
                inputs = inputs[:MAX_INPUTS_PER_OUTPUT]
            expression_envelope = (
                {"status": "available", "sql": sql}
                if sql_bytes <= MAX_EXPRESSION_BYTES
                else {"status": "unavailable", "reason": "too_large"}
            )
            mapping_status = "partial" if unresolved or expression_envelope["status"] != "available" else "available"
            partial = partial or mapping_status != "available"
            output = {
                "outputName": column["name"],
                "outputOrdinal": column["ordinal"],
                "derivation": _derivation(root.expression),
                "mappingStatus": mapping_status,
                "expression": expression_envelope,
                "inputs": inputs,
            }
            if unresolved:
                output["reason"] = "unresolved_source_column"
            outputs.append(output)
    except (SqlglotError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return {"status": "unavailable", "reason": "analysis_failed", "detail": type(exc).__name__}

    payload = {
        "status": "partial" if partial else "available",
        "relationFingerprint": relation_fingerprint,
        "outputs": outputs,
    }
    if len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_PROVENANCE_BYTES:
        return {"status": "unavailable", "reason": "too_large"}
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def _join_type(join: exp.Join) -> str:
    kind = (join.kind or "").lower()
    side = (join.side or "").lower()
    if kind == "cross":
        return "cross"
    return side if side in {"left", "right", "full"} else "inner"


def _join_condition_kind(join: exp.Join) -> str:
    if (join.method or "").upper() == "NATURAL":
        return "natural"
    if join.args.get("using"):
        return "using"
    if join.args.get("on") is not None:
        return "on"
    return "none"


def _join_query_scope(select: exp.Select, root: exp.Expr) -> str:
    """Classify a join without claiming a query-local stage identity.

    The outer SELECT and set-operation operands belong to the root query block.
    SELECTs nested in CTEs or non-root subqueries remain nested evidence.
    """
    ancestor = select.parent
    while ancestor is not None:
        if isinstance(ancestor, exp.Select):
            return "nested"
        if isinstance(ancestor, exp.CTE):
            return "nested"
        if isinstance(ancestor, exp.Subquery) and ancestor is not root:
            return "nested"
        ancestor = ancestor.parent
    return "root"


def _and_terms(expression: exp.Expr) -> list[exp.Expr]:
    if isinstance(expression, exp.And):
        return [*_and_terms(expression.this), *_and_terms(expression.expression)]
    return [expression]


def _source_for_table(
    table: exp.Expr | None,
    source_lookup: dict[tuple[str, str], dict[str, Any]],
    current_namespace: str,
) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(table, exp.Table):
        return None
    namespace = table.db or current_namespace
    source = source_lookup.get((namespace, table.name))
    if source is None:
        return None
    return table.alias_or_name, source


def _join_endpoint(
    column: exp.Column,
    aliases: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    reference_alias = column.table
    source = aliases.get(reference_alias) if reference_alias else None
    source_column = next(
        (item for item in source.get("columns", []) if item["name"] == column.name), None,
    ) if source else None
    if source is None or source_column is None:
        return None
    return {
        "referenceAlias": reference_alias,
        "referenceColumnName": column.name,
        "database": source["database"],
        "namespace": source["namespace"],
        "relation": source["relation"],
        "kind": source["kind"],
        "columnName": source_column["name"],
        "columnOrdinal": source_column["ordinal"],
    }


def derive_join_provenance(
    definition: str,
    sources: list[dict[str, Any]],
    *,
    current_namespace: str,
    relation_fingerprint: str,
) -> dict[str, Any]:
    """Derive bounded, verified direct equality predicates from explicit joins.

    The normalized condition remains visible even when a predicate shape cannot be
    represented. Only endpoints resolved to exact source columns from the verified
    repeatable-read catalog snapshot are promoted to interactive join edges.
    """
    if len(definition.encode("utf-8")) > MAX_VIEW_DEFINITION_BYTES:
        return {"status": "unavailable", "reason": "too_large"}
    source_lookup, schema = _source_catalog(sources)
    try:
        original = parse_one(definition, read="postgres")
        qualified = qualify(
            original.copy(), schema=schema, dialect="postgres", db=current_namespace,
            validate_qualify_columns=False,
        )
        original_selects = list(original.find_all(exp.Select))
        qualified_selects = list(qualified.find_all(exp.Select))
        join_count = sum(len(select.args.get("joins") or []) for select in qualified_selects)
        if join_count > MAX_JOIN_COUNT:
            return {"status": "unavailable", "reason": "too_many_joins"}

        joins: list[dict[str, Any]] = []
        total_predicates = 0
        partial = False
        for select_index, select in enumerate(qualified_selects):
            original_select = original_selects[select_index] if select_index < len(original_selects) else None
            original_joins = original_select.args.get("joins") or [] if original_select is not None else []
            from_source = _source_for_table(
                select.args.get("from_").this if select.args.get("from_") is not None else None,
                source_lookup, current_namespace,
            )
            aliases: dict[str, dict[str, Any]] = {}
            prior_aliases: set[str] = set()
            if from_source is not None:
                aliases[from_source[0]] = from_source[1]
                prior_aliases.add(from_source[0])

            for local_index, join in enumerate(select.args.get("joins") or []):
                original_join = original_joins[local_index] if local_index < len(original_joins) else join
                right_source = _source_for_table(join.this, source_lookup, current_namespace)
                right_alias = join.this.alias_or_name if isinstance(join.this, (exp.Table, exp.Subquery)) else ""
                if right_source is not None:
                    aliases[right_source[0]] = right_source[1]
                    right_alias = right_source[0]
                condition_kind = _join_condition_kind(original_join)
                condition = join.args.get("on")
                condition_sql = condition.sql(dialect="postgres", pretty=False, comments=False) if condition is not None else ""
                condition_envelope = (
                    {"status": "available", "sql": condition_sql}
                    if condition_sql and len(condition_sql.encode("utf-8")) <= MAX_EXPRESSION_BYTES
                    else {"status": "unavailable", "reason": "not_applicable" if not condition_sql else "too_large"}
                )
                reasons: set[str] = set()
                predicates: list[dict[str, Any]] = []
                if condition_kind == "natural":
                    reasons.add("unsupported_natural_join")
                elif condition is not None:
                    terms = _and_terms(condition)
                    if len(terms) > MAX_PREDICATES_PER_JOIN:
                        return {"status": "unavailable", "reason": "too_many_predicates"}
                    total_predicates += len(terms)
                    if total_predicates > MAX_JOIN_PREDICATES:
                        return {"status": "unavailable", "reason": "too_many_predicates"}
                    for term in terms:
                        if not isinstance(term, exp.EQ) or not isinstance(term.this, exp.Column) or not isinstance(term.expression, exp.Column):
                            reasons.add("unsupported_predicate_shape")
                            continue
                        left = _join_endpoint(term.this, aliases)
                        right = _join_endpoint(term.expression, aliases)
                        if left is None or right is None:
                            reasons.add("unresolved_source_column")
                            continue
                        if left["referenceAlias"] == right_alias and right["referenceAlias"] in prior_aliases:
                            left, right = right, left
                        if left["referenceAlias"] not in prior_aliases or right["referenceAlias"] != right_alias:
                            reasons.add("unsupported_predicate_shape")
                            continue
                        predicates.append({
                            "predicateOrdinal": len(predicates) + 1,
                            "operator": "eq",
                            "expression": {"status": "available", "sql": term.sql(dialect="postgres", pretty=False, comments=False)},
                            "left": left,
                            "right": right,
                        })
                elif _join_type(join) != "cross":
                    reasons.add("missing_join_condition")
                if right_source is None:
                    reasons.add("unresolved_join_source")
                if condition_envelope["status"] != "available" and condition is not None:
                    reasons.add("condition_too_large")
                mapping_status = "partial" if reasons else "available"
                partial = partial or mapping_status == "partial"
                record = {
                    "joinOrdinal": len(joins) + 1,
                    "queryScope": _join_query_scope(original_select, original) if original_select is not None else "nested",
                    "joinType": _join_type(join),
                    "conditionKind": condition_kind,
                    "rightReferenceAlias": right_alias,
                    "mappingStatus": mapping_status,
                    "condition": condition_envelope,
                    "predicates": predicates,
                }
                if reasons:
                    record["reasons"] = sorted(reasons)
                joins.append(record)
                if right_source is not None:
                    prior_aliases.add(right_alias)
    except (SqlglotError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return {"status": "unavailable", "reason": "analysis_failed", "detail": type(exc).__name__}

    payload = {
        "status": "partial" if partial else "available",
        "relationFingerprint": relation_fingerprint,
        "joins": joins,
    }
    if len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_JOIN_PROVENANCE_BYTES:
        return {"status": "unavailable", "reason": "too_large"}
    payload["fingerprint"] = _fingerprint(payload)
    return payload
