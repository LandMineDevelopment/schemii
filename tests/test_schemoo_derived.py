"""Derived sources preserve grain and share the existing scoped compiler."""

from copy import deepcopy
import sqlite3

import pytest
from sqlglot import exp, parse_one

from schemii.schemoo.catalog import source_issues
from schemii.schemoo.models import ModelDefinition
from schemii.schemoo.prototype import ModelValidationError, analyze_model, compile_preview, validate_definition


CATALOG = {
    "namespace": "main",
    "tables": [{"name": name, "primaryKey": ["id"] if name == "people" else [], "columns": [{"name": column} for column in columns]} for name, columns in [
        ("people", ["id", "salary", "allowance"]), ("cert", ["person_id", "name", "active"]),
        ("assignment", ["person_id", "name"]),
    ]],
    "relationships": [{"id": name, "sourceTable": table, "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"}
                      for name, table in [("cert_fk", "cert"), ("assignment_fk", "assignment")]],
}


def request(kind="aggregate", **changes):
    definition = {"kind": kind, "source": "people", "groupBy": ["id"] if kind == "aggregate" else [], "outputs": [
        {"id": "n", "label": "Certification count", "operation": "count", "nodeId": "cert", "column": "name"},
        {"id": "list", "label": "Certifications", "operation": "list", "nodeId": "cert", "column": "name", "distinct": True},
    ] if kind == "aggregate" else [{"id": "total", "label": "Compensation", "operation": "add", "column": "salary", "operand": "allowance"}]}
    return {"root": "people", "nodes": [*[{"id": t["name"], "table": t["name"], "label": t["name"]} for t in CATALOG["tables"]],
                                         {"id": "derived", "table": "people", "label": "Summary", "derivation": definition}],
            "relationships": ["cert_fk", "assignment_fk"],
            "fields": [{"table": "people", "column": "id"}, {"table": "derived", "column": "n" if kind == "aggregate" else "total"}], **changes}


def execute(sql):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE people(id INTEGER, salary INTEGER, allowance INTEGER);
            INSERT INTO people VALUES (1, 100, 20), (2, 80, 10), (3, 40, 0);
            CREATE TABLE cert(person_id INTEGER, name TEXT, active INTEGER);
            INSERT INTO cert VALUES (1, 'CPR', 1), (1, 'First aid', 0), (2, 'CPR', 1);
            CREATE TABLE assignment(person_id INTEGER, name TEXT);
            INSERT INTO assignment VALUES (1, 'A'), (1, 'B'), (2, 'C');
        """)
        translated = sqlite_sql(sql)
        return connection.execute(translated).fetchall()


def sqlite_sql(sql):
    # SQLGlot 28 renders PostgreSQL E'...' as bare SQLite tokens. Fixture
    # literals contain no backslash escapes and can use ordinary strings.
    return parse_one(sql, dialect="postgres").transform(
        lambda node: exp.Literal.string(node.this) if isinstance(node, exp.ByteString) else node
    ).sql(dialect="sqlite")


def test_row_calculation_is_inline_and_does_not_add_a_join():
    result = compile_preview(CATALOG, request("row"))
    assert 't0."salary" + t0."allowance"' in result["sql"]
    assert "JOIN" not in result["sql"]
    assert sorted(execute(result["sql"])) == [(1, 120), (2, 90), (3, 40)]


def test_aggregate_isolated_from_other_outer_branches_and_unused_outputs_pruned():
    query = request()
    query["fields"].append({"table": "assignment", "column": "name"})
    result = compile_preview(CATALOG, query)
    assert "STRING_AGG" not in result["sql"]
    assert result["sql"].count("LIMIT") == 1
    assert 'GROUP BY t0."id"' in result["sql"]
    assert sorted(execute(result["sql"])) == [(1, 2, "A"), (1, 2, "B"), (2, 1, "C"), (3, 0, None)]


def test_list_is_ordered_and_uses_author_labels():
    query = request(fields=[{"table": "derived", "column": "list"}])
    sql = compile_preview(CATALOG, query)["sql"]
    assert 'STRING_AGG(DISTINCT CAST(t1."name" AS TEXT)' in sql
    assert 'ORDER BY CAST(t1."name" AS TEXT)' in sql
    assert 'AS "Summary.Certifications"' in sql
    assert parse_one(sql, dialect="postgres").key == "select"


def test_unused_derived_source_costs_no_join_or_aggregation():
    sql = compile_preview(CATALOG, request(fields=[{"table": "people", "column": "id"}]))["sql"]
    assert "JOIN" not in sql and "COUNT" not in sql


def cyclic_summary():
    catalog = deepcopy(CATALOG)
    for table in catalog["tables"][1:]:
        table["columns"][0]["dataType"] = "text"
    query = request()
    query["edges"] = [{"id": edge["id"], "relationshipId": edge["id"], "source": edge["sourceTable"],
                       "target": edge["targetTable"]} for edge in catalog["relationships"]]
    query["edges"].append({"id": "contributors", "kind": "logical", "source": "cert", "sourceColumn": "person_id",
                           "target": "assignment", "targetColumn": "person_id"})
    return catalog, query


def test_unused_calculation_and_unused_contributors_do_not_block_preview():
    catalog, query = cyclic_summary()
    query["fields"] = [{"table": "people", "column": "id"}]
    assert compile_preview(catalog, query)["usedRelationships"] == []
    outputs = query["nodes"][-1]["derivation"]["outputs"]
    outputs.append({"id": "owners", "label": "Owners", "operation": "count_distinct", "column": "id"})
    query["fields"] = [{"table": "derived", "column": "owners"}]
    plan = compile_preview(catalog, query)
    assert plan["usedRelationships"] == ["derived:derived"]
    assert 'COUNT(DISTINCT t0."id")' in plan["sql"]
    assert '"main"."cert"' not in plan["sql"]


def test_selected_calculated_contributor_rejects_ambiguous_inner_route():
    catalog, query = cyclic_summary()
    with pytest.raises(ModelValidationError, match="ambiguous") as error:
        compile_preview(catalog, query)
    assert error.value.details["cycleEdges"] == ["cert_fk", "assignment_fk", "contributors"]
    assert error.value.details["ambiguousRelationships"] == ["cert_fk"]


def test_unused_calculated_condition_accepts_any_possible_contributor_route():
    catalog, query = cyclic_summary()
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [
        {"table": "assignment", "column": "name", "operator": "eq", "value": "A"}]
    query["fields"] = [{"table": "people", "column": "id"}]
    assert compile_preview(catalog, query)["usedRelationships"] == []
    assert validate_definition(catalog, query) == analyze_model(catalog, query)
    query["fields"] = [{"table": "derived", "column": "n"}]
    with pytest.raises(ModelValidationError, match="ambiguous"):
        compile_preview(catalog, query)


def test_unused_calculated_condition_still_rejects_separate_cyclic_branch():
    catalog, query = cyclic_summary()
    query["nodes"] += [{"id": node, "table": "cert"} for node in ("other_cert", "third_cert")]
    query["edges"] += [{"id": node, "relationshipId": "cert_fk", "source": node, "target": "people"}
                       for node in ("other_cert", "third_cert")]
    query["edges"].append({"id": "other_contributors", "kind": "logical", "source": "other_cert", "sourceColumn": "person_id",
                           "target": "third_cert", "targetColumn": "person_id"})
    query["nodes"][3]["derivation"]["outputs"][0]["conditions"] = [
        {"table": "other_cert", "column": "name", "operator": "eq", "value": "A"}]
    query["fields"] = [{"table": "people", "column": "id"}]
    with pytest.raises(ValueError, match="separate relationship branch"):
        compile_preview(catalog, query)


def source_summary(**changes):
    query = request(**changes)
    node = query["nodes"][-1]
    node["table"] = "cert"
    node["derivation"].update(source="cert", groupBy=["person_id"], connection={
        "target": "people", "columns": [{"source": "person_id", "target": "id"}]})
    return query


def test_source_first_summary_groups_child_without_rejoining_people():
    result = compile_preview(CATALOG, source_summary())
    sql = result["sql"]
    assert sql.count('"main"."people"') == 1
    assert 'GROUP BY t0."person_id"' in sql
    assert 'ON t1."person_id" = t0."id"' in sql
    assert 'COALESCE(t1."n", 0)' in sql
    assert sorted(execute(sql)) == [(1, 2), (2, 1), (3, 0)]


def test_source_first_summary_retains_field_conditions_and_zero_count():
    query = source_summary()
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [
        {"table": "cert", "column": "active", "operator": "eq", "value": 1}]
    sql = compile_preview(CATALOG, query)["sql"]
    assert 'FILTER (WHERE t0."active" = 1)' in sql
    assert sorted(execute(sql)) == [(1, 1), (2, 1), (3, 0)]


def test_source_first_summary_only_counts_are_coalesced():
    query = source_summary()
    output = query["nodes"][-1]["derivation"]["outputs"][0]
    output.update(operation="sum", column="active")
    sql = compile_preview(CATALOG, query)["sql"]
    assert "COALESCE" not in sql
    assert sorted(execute(sql)) == [(1, 1), (2, 1), (3, None)]
    output.update(operation="count_distinct", column="name")
    assert sorted(execute(compile_preview(CATALOG, query)["sql"])) == [(1, 2), (2, 1), (3, 0)]


@pytest.mark.parametrize("connection, message", [
    ({"target": "people", "columns": [{"source": "name", "target": "id"}]}, "every grouping key"),
    ({"target": "people", "columns": [{"source": "person_id", "target": "salary"}]}, "complete primary key"),
    ({"target": "people", "columns": [{"source": "person_id", "target": "missing"}]}, "existing target columns"),
    ({"target": "derived", "columns": [{"source": "person_id", "target": "id"}]}, "physical model object"),
    ({"target": "people", "columns": [{"source": "person_id", "target": "id"}, {"source": "person_id", "target": "salary"}]}, "every grouping key"),
])
def test_source_first_summary_validates_connection(connection, message):
    query = source_summary()
    query["nodes"][-1]["derivation"]["connection"] = connection
    with pytest.raises(ValueError, match=message):
        compile_preview(CATALOG, query)


def test_source_first_summary_validates_connection_types():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["columns"][0]["dataType"] = "uuid"
    catalog["tables"][1]["columns"][0]["dataType"] = "text"
    with pytest.raises(ValueError, match="compatible data types"):
        compile_preview(catalog, source_summary())


def test_source_first_summary_maps_composite_keys():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["primaryKey"] = ["id", "salary"]
    query = source_summary()
    definition = query["nodes"][-1]["derivation"]
    definition["groupBy"] = ["person_id", "active"]
    definition["connection"]["columns"].append({"source": "active", "target": "salary"})
    sql = compile_preview(catalog, query)["sql"]
    assert 'GROUP BY t0."person_id", t0."active"' in sql
    assert 'ON t1."person_id" = t0."id" AND t1."active" = t0."salary"' in sql


def test_source_first_summary_respects_conditional_and_required_model_rules():
    for kind, expected in [("conditional", [(1, 1), (2, 1), (3, 0)]), ("required", [(1, 1), (2, 1)])]:
        query = source_summary(scopes=[{"id": "active", "kind": kind, "alternatives": [{"id": "a", "conditions": [
            {"table": "cert", "column": "active", "operator": "eq", "value": 1}]}]}])
        assert sorted(execute(compile_preview(CATALOG, query)["sql"])) == expected


def test_source_first_summary_allows_lookup_before_aggregation_only_when_needed():
    query = source_summary()
    query["nodes"][-1]["derivation"]["outputs"][0].update(operation="max", nodeId="people", column="salary")
    sql = compile_preview(CATALOG, query)["sql"]
    assert sql.count('"main"."people"') == 2
    assert sorted(execute(sql)) == [(1, 100), (2, 80), (3, None)]


def test_source_first_summary_rejects_multiplying_lookup_branch():
    query = source_summary()
    query["nodes"][-1]["derivation"]["outputs"][0].update(nodeId="assignment")
    with pytest.raises(ValueError, match="must not multiply source records"):
        compile_preview(CATALOG, query)


def test_nonnullable_owner_key_uses_equijoin_for_hash_join_planning():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["columns"][0]["nullable"] = False
    sql = compile_preview(catalog, request())["sql"]
    assert 'ON t1."id" = t0."id"' in sql


def test_numeric_calculation_rejects_known_nonnumeric_type_before_execution():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["columns"][1]["dataType"] = "text"
    with pytest.raises(ValueError, match="requires numeric columns"):
        compile_preview(catalog, request("row"))


def test_owner_grain_rejects_nonunique_grouping_key():
    query = request()
    query["nodes"][-1]["derivation"]["groupBy"] = ["salary"]
    with pytest.raises(ValueError, match="complete primary key"):
        compile_preview(CATALOG, query)


def test_owner_grain_requires_complete_composite_key():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["primaryKey"] = ["id", "salary"]
    with pytest.raises(ValueError, match="complete primary key"):
        compile_preview(catalog, request())
    query = request()
    query["nodes"][-1]["derivation"]["groupBy"] = ["id", "salary"]
    assert 'GROUP BY t0."id", t0."salary"' in compile_preview(catalog, query)["sql"]


def test_nonnullable_unique_constraint_is_a_valid_owner_key():
    catalog = deepcopy(CATALOG)
    table = catalog["tables"][0]
    table["primaryKey"] = []
    table["uniqueKeys"] = [["id"]]
    table["columns"][0]["nullable"] = False
    assert "COUNT" in compile_preview(catalog, request())["sql"]
    table["columns"][0]["nullable"] = True
    with pytest.raises(ValueError, match="nonnullable unique key"):
        compile_preview(catalog, request())


def test_conditional_parameter_applies_inside_aggregate():
    query = request(scopes=[{"id": "active", "kind": "conditional", "alternatives": [{"id": "a", "inputs": [{"id": "enabled", "type": "integer", "defaultValue": 1}],
        "conditions": [{"table": "cert", "column": "active", "operator": "eq", "parameterId": "enabled"}]}]}])
    result = compile_preview(CATALOG, query)
    assert 'WHERE "active" = 1' in result["sql"]
    assert result["activeScopes"] == ["active"]
    assert result["parameterValues"] == {"active": {"enabled": 1}}
    assert "cert_fk" in result["usedRelationships"]
    assert sorted(execute(result["sql"])) == [(1, 1), (2, 1), (3, 0)]


def test_required_rule_is_preserved_inside_aggregate_and_for_outer_qualification():
    query = request(scopes=[{"id": "active", "kind": "required", "alternatives": [{"id": "a", "conditions": [
        {"table": "cert", "column": "active", "operator": "eq", "value": 1}]}]}])
    result = compile_preview(CATALOG, query)
    assert sorted(execute(result["sql"])) == [(1, 1), (2, 1)]


def test_group_key_alone_still_produces_one_summary_per_owner():
    result = compile_preview(CATALOG, request(fields=[{"table": "derived", "column": "id"}]))
    assert 'GROUP BY t0."id"' in result["sql"]
    assert sorted(execute(result["sql"])) == [(1,), (2,), (3,)]


def test_independent_contributing_branches_are_rejected():
    query = request()
    query["nodes"][-1]["derivation"]["outputs"].append({"id": "jobs", "label": "Jobs", "operation": "count", "nodeId": "assignment", "column": "name"})
    query["fields"].append({"table": "derived", "column": "jobs"})
    with pytest.raises(ValueError, match="independent relationship branches"):
        compile_preview(CATALOG, query)


def test_mixed_record_grains_do_not_silently_multiply_totals():
    query = request()
    query["nodes"][-1]["derivation"]["outputs"].append({"id": "pay", "label": "Pay", "operation": "sum", "column": "salary"})
    query["fields"].append({"table": "derived", "column": "pay"})
    with pytest.raises(ValueError, match="different record grains"):
        compile_preview(CATALOG, query)


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d.update(source="derived"), "nested"),
    (lambda d: d.update(groupBy=[]), "grouping key"),
    (lambda d: d["outputs"][0].update(column="missing"), "unknown source column"),
    (lambda d: d["outputs"][0].update(id="id"), "unique IDs"),
])
def test_invalid_derivations_rejected(mutation, message):
    query = request()
    mutation(query["nodes"][-1]["derivation"])
    with pytest.raises(ValueError, match=message):
        compile_preview(CATALOG, query)


def test_derived_metadata_and_source_drift_validation():
    query = request()
    definition = ModelDefinition(nodes=query["nodes"], root="people", exposedFields=query["fields"])
    assert definition.nodes[-1].derivation.source == "people"
    assert source_issues(CATALOG, definition.model_dump(exclude_none=True)) == []
    catalog = deepcopy(CATALOG)
    catalog["tables"][1]["columns"] = [{"name": "person_id"}]
    assert any(issue["column"] == "name" for issue in source_issues(catalog, definition.model_dump(exclude_none=True)))


def temporal_summary():
    catalog = deepcopy(CATALOG)
    catalog["tables"][1]["columns"].extend([
        {"name": "effective", "dataType": "date"}, {"name": "expires", "dataType": "date"},
        {"name": "cost", "dataType": "integer"}])
    query = request()
    conditions = [
        {"table": "cert", "column": "effective", "operator": "lte", "valueSource": "today"},
        {"table": "cert", "column": "expires", "operator": "gte", "valueSource": "today", "allowNull": True},
    ]
    outputs = query["nodes"][-1]["derivation"]["outputs"]
    outputs.extend([
        {"id": "valid", "label": "Valid count", "operation": "count", "nodeId": "cert", "column": "name", "conditions": conditions},
        {"id": "cost", "label": "Valid cost", "operation": "sum", "nodeId": "cert", "column": "cost", "conditions": conditions},
    ])
    query["fields"].extend([{"table": "derived", "column": "valid"}, {"table": "derived", "column": "cost"}])
    return catalog, query


def execute_temporal(sql):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE people(id INTEGER, salary INTEGER, allowance INTEGER);
            INSERT INTO people VALUES (1, 100, 20), (2, 80, 10), (3, 40, 0);
            CREATE TABLE cert(person_id INTEGER, name TEXT, active INTEGER, effective TEXT, expires TEXT, cost INTEGER);
            INSERT INTO cert VALUES
                (1, 'CPR', 1, '2020-01-01', '2030-01-01', 10),
                (1, 'Expired', 0, '2020-01-01', '2021-01-01', 20),
                (1, 'Future', 0, '2030-01-01', '2040-01-01', 30),
                (2, 'No expiry', 1, '2020-01-01', NULL, 40);
        """)
        return connection.execute(sqlite_sql(sql)).fetchall()


def test_output_conditions_do_not_change_sibling_totals_or_remove_owners():
    catalog, query = temporal_summary()
    result = compile_preview(catalog, query, _today="2026-09-07")
    assert result["sql"].count("FILTER (WHERE") == 2
    assert sorted(execute_temporal(result["sql"])) == [(1, 3, 1, 10), (2, 1, 1, 40), (3, 0, 0, None)]


def test_null_expiry_is_excluded_unless_author_explicitly_includes_nulls():
    catalog, query = temporal_summary()
    query["nodes"][-1]["derivation"]["outputs"][2]["conditions"][1]["allowNull"] = False
    result = compile_preview(catalog, query, _today="2026-09-07")
    assert sorted(execute_temporal(result["sql"])) == [(1, 3, 1, 10), (2, 1, 0, None), (3, 0, 0, None)]


def test_today_resolves_once_for_all_outputs_and_recursive_compilation(monkeypatch):
    import schemii.schemoo.prototype as compiler
    from datetime import datetime, timezone
    calls = []
    class Clock:
        @staticmethod
        def now(tz):
            calls.append(tz)
            return datetime(2026, 9, 7, 23, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(compiler, "datetime", Clock)
    catalog, query = temporal_summary()
    sql = compiler.compile_preview(catalog, query)["sql"]
    assert calls == [timezone.utc]
    assert sql.count("E'2026-09-07'") == 4


def test_list_output_conditions_use_filter_without_changing_other_outputs():
    query = request(fields=[{"table": "derived", "column": "list"}, {"table": "derived", "column": "n"}])
    query["nodes"][-1]["derivation"]["outputs"][1]["conditions"] = [
        {"table": "cert", "column": "name", "operator": "in", "value": ["CPR", "O'Reilly"]}]
    sql = compile_preview(CATALOG, query)["sql"]
    assert 'ORDER BY CAST(t1."name" AS TEXT)) FILTER (WHERE t1."name" IN' in sql
    assert sql.count("FILTER (WHERE") == 1
    assert len(list(parse_one(sql, dialect="postgres").find_all(exp.Select))) == 2


def test_row_condition_is_inline_case_and_preserves_unmatched_rows():
    query = request("row")
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [
        {"table": "people", "column": "salary", "operator": "gte", "value": 90}]
    sql = compile_preview(CATALOG, query)["sql"]
    assert "CASE WHEN" in sql and "JOIN" not in sql
    assert sorted(execute(sql)) == [(1, 120), (2, None), (3, None)]


def test_output_condition_accepts_intermediate_source_without_adding_join():
    query = request()
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [
        {"table": "people", "column": "salary", "operator": "gte", "value": 90}]
    assert sorted(execute(compile_preview(CATALOG, query)["sql"])) == [(1, 2), (2, 0), (3, 0)]


@pytest.mark.parametrize("condition, message", [
    ({"table": "missing", "column": "name", "operator": "eq", "value": "A"}, "unknown source"),
    ({"table": "cert", "column": "missing", "operator": "eq", "value": "A"}, "unknown source"),
    ({"table": "assignment", "column": "name", "operator": "eq", "value": "A"}, "separate relationship branch"),
    ({"table": "cert", "column": "name", "operator": "eq", "parameterId": "p"}, "parameter bindings"),
    ({"table": "cert", "column": "name", "operator": "in", "value": []}, "at least one"),
    ({"table": "cert", "column": "name", "operator": "eq", "value": None}, "one fixed value"),
    ({"table": "cert", "column": "name", "operator": "contains", "value": 3}, "text value"),
])
def test_invalid_output_conditions_are_rejected(condition, message):
    query = request()
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [condition]
    with pytest.raises(ValueError, match=message):
        compile_preview(CATALOG, query)


def test_today_is_explicit_and_literal_today_stays_text():
    query = request()
    output = query["nodes"][-1]["derivation"]["outputs"][0]
    output["conditions"] = [{"table": "cert", "column": "name", "operator": "eq", "value": "today"}]
    sql = compile_preview(CATALOG, query)["sql"]
    assert "= E'today'" in sql
    catalog = deepcopy(CATALOG)
    catalog["tables"][1]["columns"][1]["dataType"] = "text"
    output["conditions"][0].update(valueSource="today", value=None)
    with pytest.raises(ValueError, match="date or timestamp"):
        compile_preview(catalog, query)


def test_condition_domain_metadata_uses_the_condition_source_only():
    query = request()
    output = query["nodes"][-1]["derivation"]["outputs"][0]
    condition = {"table": "cert", "column": "name", "operator": "eq", "value": "CPR", "domain": {"nodeId": "cert", "table": "cert", "column": "name"}}
    output["conditions"] = [condition]
    assert sorted(execute(compile_preview(CATALOG, query)["sql"])) == [(1, 1), (2, 1), (3, 0)]
    condition["domain"]["nodeId"] = "assignment"
    with pytest.raises(ValueError, match="own source column"):
        compile_preview(CATALOG, query)


def test_unused_output_conditions_are_not_executed_and_drift_detects_their_sources():
    catalog, query = temporal_summary()
    query["fields"] = [{"table": "derived", "column": "n"}]
    sql = compile_preview(catalog, query)["sql"]
    assert "FILTER" not in sql and "expires" not in sql
    definition = ModelDefinition(nodes=query["nodes"], root="people")
    assert source_issues(CATALOG, definition.model_dump(exclude_none=True))


@pytest.mark.parametrize("kind,column", [("row", "total"), ("aggregate", "n")])
def test_reaggregating_calculated_values_warns_about_outer_branch_multiplication(kind, column):
    query = request(kind, fields=[{"table": "derived", "column": column, "aggregate": "sum"}, {"table": "assignment", "column": "name"}])
    if kind == "aggregate":
        with pytest.raises(ValueError, match="materialized grain"):
            compile_preview(CATALOG, query)
        return
    result = compile_preview(CATALOG, query)
    assert any("repeated rows from joins" in warning for warning in result["warnings"])
    diagnostic, = result["repetitionDiagnostics"]
    assert diagnostic["measure"]["table"] == "derived"
    assert diagnostic["measure"]["column"] == column
    assert diagnostic["relationships"]


def test_summary_values_can_be_summed_without_a_multiplying_branch():
    query = request(fields=[{"table": "derived", "column": "n", "aggregate": "sum"}])
    plan = compile_preview(CATALOG, query)
    assert plan["repetitionDiagnostics"] == []
    assert execute(plan["sql"]) == [(3,)]


def composed_row_query():
    query = request("row")
    query["nodes"].insert(0, {"id":"downstream", "table":"people", "label":"Downstream", "derivation":{
        "kind":"row", "source":"people", "outputs":[{"id":"scaled", "label":"Scaled compensation", "operation":"multiply",
            "nodeId":"derived", "column":"total", "operandNodeId":"people", "operand":"allowance"}]}})
    query["fields"] = [{"table":"people","column":"id"},{"table":"downstream","column":"scaled"}]
    return query


def test_composed_rows_are_topologically_validated_and_filter_the_same_case_expression():
    query = composed_row_query()
    query["nodes"][-1]["derivation"]["outputs"][0]["conditions"] = [{"table":"people","column":"salary","operator":"gt","value":80}]
    query["reportFilters"] = [{"mode":"rows","conditions":[{"table":"downstream","column":"scaled","operator":"gt","value":100}]}]
    plan = compile_preview(CATALOG,query)
    assert "JOIN" not in plan["sql"]
    assert sorted(execute(plan["sql"])) == [(1,2400)]
    query["nodes"].reverse()
    assert execute(compile_preview(CATALOG,query)["sql"]) == [(1,2400)]


def test_same_node_outputs_can_reference_later_authored_fields():
    query = request("row")
    output = query["nodes"][-1]["derivation"]["outputs"]
    output.insert(0,{"id":"scaled","label":"Scaled","operation":"multiply","nodeId":"derived","column":"total","operand":"allowance"})
    query["fields"][1]["column"] = "scaled"
    assert sorted(execute(compile_preview(CATALOG,query)["sql"])) == [(1,2400),(2,900),(3,0)]


@pytest.mark.parametrize("mode",["rows","exists","not_exists"])
def test_filter_only_certification_count_is_materialized_without_display_or_unrelated_branches(mode):
    query = source_summary(fields=[{"table":"people","column":"id"}],reportFilters=[{"mode":mode,"conditions":[{"table":"derived","column":"n","operator":"gt","value":1}]}])
    plan = compile_preview(CATALOG,query)
    assert sorted(execute(plan["sql"])) == ([(2,),(3,)] if mode=="not_exists" else [(1,)])
    assert "assignment" not in plan["sql"] and "STRING_AGG" not in plan["sql"]
    assert "COUNT(" in plan["sql"]


@pytest.mark.parametrize("mode",["rows","exists","not_exists"])
def test_count_zero_uses_the_same_unmatched_value_in_select_and_each_filter_mode(mode):
    query = source_summary(reportFilters=[{"mode":mode,"conditions":[{"table":"derived","column":"n","operator":"eq","value":0}]}])
    actual = sorted(execute(compile_preview(CATALOG,query)["sql"]))
    assert actual == ([(1,2),(2,1)] if mode=="not_exists" else [(3,0)])


def test_summary_model_predicate_runs_after_grouping_and_preserves_keep_unmatched_vs_require_matching():
    for behavior,expected in [("keep_unmatched",[(1,2),(2,0),(3,0)]),("require_matching",[(1,2)])]:
        query = source_summary(scopes=[{"id":"enough","kind":"required","rowBehavior":behavior,"alternatives":[{"id":"a","inputs":[{"id":"minimum","type":"integer","defaultValue":1}],"conditions":[{"table":"derived","column":"n","operator":"gt","parameterId":"minimum"}]}]}])
        plan = compile_preview(CATALOG,query)
        assert sorted(execute(plan["sql"])) == expected
        assert plan["parameterValues"] == {"enough":{"minimum":1}}
        assert "GROUP BY" in plan["sql"] and "COUNT(" in plan["sql"]


def test_row_on_materialized_count_and_safe_reaggregation_do_not_flatten_aggregates():
    query = source_summary()
    query["nodes"].insert(0,{"id":"computed","table":"people","label":"Computed","derivation":{"kind":"row","source":"people","outputs":[
        {"id":"value","label":"Count plus allowance","operation":"add","nodeId":"derived","column":"n","operand":"allowance"}]}})
    query["fields"] = [{"table":"people","column":"id"},{"table":"computed","column":"value"}]
    assert sorted(execute(compile_preview(CATALOG,query)["sql"])) == [(1,22),(2,11),(3,0)]
    query["fields"] = [{"table":"computed","column":"value","aggregate":"sum"}]
    assert execute(compile_preview(CATALOG,query)["sql"]) == [(33,)]
    query["fields"].append({"table":"assignment","column":"name"})
    with pytest.raises(ValueError,match="materialized grain"):
        compile_preview(CATALOG,query)


def test_missing_self_and_transitive_cycles_have_actionable_field_paths():
    query = composed_row_query()
    query["nodes"][0]["derivation"]["outputs"][0]["column"] = "missing"
    with pytest.raises(ValueError,match=r"downstream.scaled.*derived.missing"):
        compile_preview(CATALOG,query)
    query = composed_row_query()
    query["nodes"][-1]["derivation"]["outputs"][0].update(nodeId="downstream",column="scaled")
    with pytest.raises(ValueError,match=r"dependency cycle.*downstream.scaled.*derived.total"):
        compile_preview(CATALOG,query)


def test_summary_of_materialized_summary_and_row_source_are_not_flattened():
    query = source_summary()
    query["nodes"].insert(0,{"id":"nested","table":"cert","label":"Nested","derivation":{
        "kind":"aggregate","source":"derived","groupBy":["person_id"],"outputs":[{"id":"again","label":"Again","operation":"sum","column":"n"}]}})
    query["fields"] = [{"table":"people","column":"id"},{"table":"nested","column":"again"}]
    plan = compile_preview(CATALOG,query)
    assert sorted(execute(plan["sql"])) == [(1,2),(2,1),(3,None)]
    assert plan["sql"].count("GROUP BY") >= 2
    assert "SUM(COUNT" not in plan["sql"]
    query["nodes"][0] = {"id":"nested","table":"cert","label":"Nested","derivation":{
        "kind":"row","source":"derived","outputs":[{"id":"again","label":"Again","operation":"add","column":"n","operand":"n"}]}}
    assert sorted(execute(compile_preview(CATALOG,query)["sql"])) == [(1,4),(2,2),(3,0)]


def test_invalid_numeric_inputs_and_cross_grain_rows_fail_before_sql():
    catalog = deepcopy(CATALOG)
    for column in catalog["tables"][0]["columns"]:
        column["dataType"] = "integer"
    catalog["tables"][0]["columns"][1]["dataType"] = "text"
    with pytest.raises(ValueError,match=r"numeric.*derived.total.*text"):
        compile_preview(catalog,request("row"))
    query = request("row")
    query["nodes"][-1]["derivation"]["outputs"][0].update(nodeId="cert",column="active")
    with pytest.raises(ValueError,match=r"derived.total.*grain"):
        compile_preview(CATALOG,query)


def test_dependency_scope_and_filter_only_row_do_not_activate_unused_summary():
    query = composed_row_query()
    query["fields"] = [{"table":"people","column":"id"}]
    query["scopes"] = [{"id":"positive","kind":"conditional","rowBehavior":"require_matching","alternatives":[{"id":"a","conditions":[{"table":"downstream","column":"scaled","operator":"gt","value":1000}]}]}]
    query["reportFilters"] = [{"mode":"rows","conditions":[{"table":"downstream","column":"scaled","operator":"gt","value":0}]}]
    plan = compile_preview(CATALOG,query)
    assert execute(plan["sql"]) == [(1,)]
    assert plan["activeScopes"] == ["positive"]
    assert "cert" not in plan["sql"] and "assignment" not in plan["sql"]


def test_distinct_field_dag_still_rejects_mutually_recursive_summary_stages():
    query = request()
    query["nodes"][-1]["derivation"]["outputs"] = [
        {"id":"first","label":"First","operation":"count","nodeId":"cert","column":"name"},
        {"id":"second","label":"Second","operation":"sum","nodeId":"other","column":"leaf"}]
    query["nodes"].append({"id":"other","table":"people","label":"Other","derivation":{
        "kind":"aggregate","source":"people","groupBy":["id"],"outputs":[
            {"id":"nested","label":"Nested","operation":"sum","nodeId":"derived","column":"first"},
            {"id":"leaf","label":"Leaf","operation":"count","column":"id"}]}})
    query["fields"] = [{"table":"derived","column":"first"}]
    with pytest.raises(ValueError,match=r"summary stage cycle.*derived.*other"):
        compile_preview(CATALOG,query)


def test_summary_output_condition_includes_its_filter_only_upstream_calculated_field():
    query = source_summary()
    query["nodes"].append({"id":"conditional","table":"people","label":"Conditional","derivation":{
        "kind":"aggregate","source":"people","groupBy":["id"],"outputs":[{"id":"n","label":"N","operation":"count","column":"id",
            "conditions":[{"table":"derived","column":"n","operator":"gt","value":1}]}]}})
    query["fields"] = [{"table":"people","column":"id"},{"table":"conditional","column":"n"}]
    plan = compile_preview(CATALOG,query)
    assert sorted(execute(plan["sql"])) == [(1,1),(2,0),(3,0)]
    assert "FILTER (WHERE COALESCE" in plan["sql"]


def test_calculated_operand_source_contract_tracks_its_actual_qualified_leaf():
    from schemii.schemoo.catalog import model_source_contract
    query = source_summary()
    query["nodes"].append({"id":"row","table":"people","label":"Row","derivation":{"kind":"row","source":"people","outputs":[
        {"id":"value","label":"Value","operation":"add","nodeId":"derived","column":"n","operandNodeId":"people","operand":"salary"}]}})
    definition = ModelDefinition.model_validate({"root":"people","nodes":query["nodes"],"exposedFields":[{"table":"row","column":"value"}]}).model_dump(exclude_none=True)
    contract = model_source_contract(CATALOG,definition)
    assert "salary" in {column["name"] for table in contract["tables"] if table["name"]=="people" for column in table["columns"]}
    assert not source_issues(CATALOG,{**definition,"sourceContract":contract})
    changed = deepcopy(CATALOG)
    changed["tables"][0]["columns"] = [column for column in changed["tables"][0]["columns"] if column["name"]!="salary"]
    assert any(issue.get("severity")=="breaking" for issue in source_issues(changed,{**definition,"sourceContract":contract}))
