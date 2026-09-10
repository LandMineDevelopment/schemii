"""Derived sources preserve grain and share the existing scoped compiler."""

from copy import deepcopy
import sqlite3

import pytest
from sqlglot import exp, parse_one

from schemii.schemoo.catalog import source_issues
from schemii.schemoo.models import ModelDefinition
from schemii.schemoo.prototype import compile_preview


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
    ({"table": "missing", "column": "name", "operator": "eq", "value": "A"}, "unknown physical"),
    ({"table": "cert", "column": "missing", "operator": "eq", "value": "A"}, "unknown physical"),
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
