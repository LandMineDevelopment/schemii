"""Semantic prototype contracts: alias paths, parameter activation, row scopes."""

import pytest
import sqlite3
from sqlglot import parse_one

from schemii.schemoo.prototype import ModelValidationError, analyze_model, compile_preview


@pytest.fixture
def catalog():
    return {
        "tables": [{"name": name, "columns": [{"name": column} for column in columns]} for name, columns in [
            ("people", ["id", "name"]), ("assignment", ["person_id", "org_id", "start", "end"]), ("org", ["id", "name"])
        ]],
        "relationships": [
            {"id": "person_fk", "sourceTable": "assignment", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"},
            {"id": "org_fk", "sourceTable": "assignment", "sourceColumn": "org_id", "targetTable": "org", "targetColumn": "id"},
        ],
    }


def query(**changes):
    return {"root": "people", "fields": [{"table": "people", "column": "name"}], "relationships": ["person_fk", "org_fk"], **changes}


def scope(kind="required", table="org", column="id", default="42"):
    return {"id": "scope", "label": "Organization scope", "kind": kind, "alternatives": [{"id": "chosen", "inputs": [{"id": "value", "label": "Organization", "type": "text", "defaultValue": default}], "conditions": [{"table": table, "column": column, "operator": "eq", "parameterId": "value"}]}]}


@pytest.mark.parametrize("operator,sql_operator", [("in", "IN"), ("not_in", "NOT IN")])
@pytest.mark.parametrize("location", ["required", "conditional", "rows", "exists", "not_exists"])
def test_membership_filters_across_all_scopes(catalog, operator, sql_operator, location):
    condition = {"table": "people", "column": "name", "operator": operator, "value": ["Alice", "O'Reilly"], "allowNull": True}
    if location in ("required", "conditional"):
        options = {"scopes": [{"id": "fixed", "kind": location, "alternatives": [{"id": "one", "conditions": [condition]}]}]}
    else:
        options = {"reportFilters": [{"id": "report", "mode": location, "conditions": [condition]}]}
    sql = compile_preview(catalog, query(**options))["sql"]
    assert f"{sql_operator} (E'Alice', E'O''Reilly')" in sql
    assert 'OR ' in sql and 'IS NULL' in sql
    assert parse_one(sql, dialect="postgres").key == "select"


@pytest.mark.parametrize("value", [[], "Alice", [None], [["Alice"]], [{"x": 1}], [float("inf")], ["x"] * 501])
def test_membership_rejects_invalid_values(catalog, value):
    with pytest.raises(ValueError):
        compile_preview(catalog, query(reportFilters=[{"conditions": [{"table": "people", "column": "name", "operator": "in", "value": value}]}]))


@pytest.mark.parametrize("kind,value,expected", [("integer", ["1", "2"], [1, 2]), ("number", ["1.5", "2"], [1.5, 2.0]), ("boolean", ["true", "false"], [True, False]), ("date", ["2026-01-01", "2026-02-02"], ["2026-01-01", "2026-02-02"]), ("text", ["Alice", "Bob"], ["Alice", "Bob"])])
@pytest.mark.parametrize("supplied", [False, True])
def test_membership_parameter_defaults_and_values_are_typed(catalog, kind, value, expected, supplied):
    rule = scope(default=value)
    alternative = rule["alternatives"][0]
    alternative["inputs"][0]["type"] = kind
    alternative["conditions"][0]["operator"] = "in"
    selections = {"scope": {"values": {"value": value}}} if supplied else {}
    result = compile_preview(catalog, query(scopes=[rule], selections=selections))
    assert result["parameterValues"]["scope"]["value"] == expected


def test_membership_parameter_rejects_mixed_bindings_and_invalid_member(catalog):
    rule = scope(default=["1", "no"])
    alternative = rule["alternatives"][0]
    alternative["inputs"][0]["type"] = "integer"
    alternative["conditions"][0]["operator"] = "in"
    with pytest.raises(ValueError, match="whole number"):
        compile_preview(catalog, query(scopes=[rule]))
    alternative["conditions"].append({"table": "org", "column": "id", "operator": "eq", "parameterId": "value"})
    with pytest.raises(ValueError, match="separate parameters"):
        compile_preview(catalog, query(scopes=[rule]))


@pytest.mark.parametrize("value", [0, False, 1.5, "one", [0, 2], [False, True]])
def test_source_parameter_preserves_domain_scalar_types(catalog, value):
    rule = scope(default=value)
    alternative = rule["alternatives"][0]
    alternative["inputs"][0]["type"] = "source"
    alternative["conditions"][0]["operator"] = "in" if isinstance(value, list) else "eq"
    result = compile_preview(catalog, query(scopes=[rule]))
    actual = result["parameterValues"]["scope"]["value"]
    assert actual == value
    assert type(actual) is type(value)
    if isinstance(value, list):
        assert [type(item) for item in actual] == [type(item) for item in value]


def test_source_parameter_rejects_nonfinite_domain_value(catalog):
    rule = scope(default=float("inf"))
    rule["alternatives"][0]["inputs"][0]["type"] = "source"
    with pytest.raises(ValueError, match="finite"):
        compile_preview(catalog, query(scopes=[rule]))


def test_membership_contract_accepts_arrays_and_rejects_null_or_nested_members():
    from pydantic import ValidationError
    from schemii.schemoo.models import Condition, ParameterInput, ScopeSelection
    assert Condition(operator="in", value=["A", "B"]).value == ["A", "B"]
    assert ParameterInput(id="p", defaultValue=[1, 2]).defaultValue == [1, 2]
    assert ScopeSelection(values={"p": [True, False]}).values == {"p": [True, False]}
    # An unfinished authoring draft must be able to browse before choosing values.
    assert Condition(operator="in", value=[]).value == []
    assert ParameterInput(id="p", defaultValue=[]).defaultValue == []
    for value in ([None], [["A"]], ["x"] * 501):
        with pytest.raises(ValidationError):
            Condition(operator="in", value=value)


def test_membership_parameter_empty_default_requires_input(catalog):
    rule = scope(default=[])
    rule["alternatives"][0]["conditions"][0]["operator"] = "in"
    with pytest.raises(ModelValidationError, match="requires Organization"):
        compile_preview(catalog, query(scopes=[rule]))
    result = compile_preview(catalog, query(scopes=[rule], selections={"scope": {"values": {"value": ["42"]}}}))
    assert result["parameterValues"]["scope"]["value"] == ["42"]


def test_required_scope_forces_path_without_outer_fanout(catalog):
    result = compile_preview(catalog, query(scopes=[scope()]))
    assert result["usedRelationships"] == ["person_fk", "org_fk"]
    assert result["requiredNodes"] == ["people", "assignment", "org"]
    assert "WHERE EXISTS" in result["sql"]
    assert result["sql"].count("INNER JOIN") == 1
    assert "LEFT JOIN" not in result["sql"]
    assert 'e0_1."person_id" = t0."id"' in result["sql"]
    assert parse_one(result["sql"], dialect="postgres").key == "select"


def test_optional_scope_is_orthogonal_to_evaluation_reach_and_only_runs_when_activated(catalog):
    configured = {**scope(), "requirement": "optional"}
    inactive = compile_preview(catalog, query(scopes=[configured]))
    assert inactive["activeScopes"] == []
    assert inactive["usedRelationships"] == []
    assert "42" not in inactive["sql"]

    active = compile_preview(catalog, query(scopes=[configured], selections={"scope": {"active": True}}))
    assert active["activeScopes"] == ["scope"]
    assert active["usedRelationships"] == ["person_fk", "org_fk"]
    assert "42" in active["sql"]


def test_legacy_scope_activation_migrates_to_requirement():
    from schemii.schemoo.models import ModelScope

    migrated = ModelScope.model_validate({**scope(), "activation": "optional"})
    assert migrated.requirement == "optional"
    assert "activation" not in migrated.model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("operator", ["not_null", "is_null"])
def test_fixed_null_scope_needs_no_inputs_and_ignores_stale_allow_null(catalog, operator):
    rule = {"id": "fixed", "label": "Fixed rule", "kind": "required", "alternatives": [
        {"id": "only", "inputs": [], "conditions": [
            {"table": "org", "column": "name", "operator": operator, "allowNull": True}
        ]}
    ]}
    result = compile_preview(catalog, query(scopes=[rule]))
    assert result["usedRelationships"] == ["person_fk", "org_fk"]
    assert "WHERE EXISTS" in result["sql"]
    assert f'"name" IS {"NOT " if operator == "not_null" else ""}NULL' in result["sql"]
    assert " OR " not in result["sql"]


def test_alias_result_columns_use_author_label_not_internal_node_id(catalog):
    nodes = [
        {"id": "people", "table": "people", "label": "People"},
        {"id": "alias_444f281a6900", "table": "org", "label": "Org parent name"},
        {"id": "assignment", "table": "assignment", "label": "assignment"},
    ]
    edges = [
        {"id": "person", "relationshipId": "person_fk", "source": "assignment", "target": "people", "enabled": True},
        {"id": "parent", "relationshipId": "org_fk", "source": "assignment", "target": "alias_444f281a6900", "enabled": True},
    ]
    result = compile_preview(catalog, {
        "root": "people", "nodes": nodes, "edges": edges,
        "fields": [{"table": "alias_444f281a6900", "column": "name"}],
    })
    assert 'AS "Org parent name.name"' in result["sql"]
    assert "alias_444f281a6900.name" not in result["sql"]


def test_disconnected_start_reports_labels_and_a_connected_replacement(catalog):
    nodes = [
        {"id": "people", "table": "people", "label": "People"},
        {"id": "assignment", "table": "assignment", "label": "Assignments"},
        {"id": "org_role", "table": "org", "label": "Parent organization"},
    ]
    edges = [{"id": "org", "relationshipId": "org_fk", "source": "assignment", "target": "org_role", "enabled": True}]
    with pytest.raises(ModelValidationError) as raised:
        compile_preview(catalog, {"root": "people", "nodes": nodes, "edges": edges,
            "fields": [{"table": "org_role", "column": "name"}]})
    assert 'Starting object "People" cannot reach Parent organization' in str(raised.value)
    assert raised.value.details["unreachableNodes"] == ["org_role"]
    assert raised.value.details["suggestedRoot"] == {"id": "org_role", "label": "Parent organization"}


def test_duplicate_author_labels_cannot_create_ambiguous_result_columns(catalog):
    nodes = [
        {"id": "people", "table": "people", "label": "People"},
        {"id": "org", "table": "org", "label": "Organization"},
        {"id": "other_org", "table": "org", "label": "Organization"},
        {"id": "assignment", "table": "assignment", "label": "assignment"},
    ]
    edges = [
        {"id": "person", "relationshipId": "person_fk", "source": "assignment", "target": "people", "enabled": True},
        {"id": "org_one", "relationshipId": "org_fk", "source": "assignment", "target": "org", "enabled": True},
        {"id": "org_two", "relationshipId": "org_fk", "source": "assignment", "target": "other_org", "enabled": True},
    ]
    with pytest.raises(ValueError, match=r"Field selected twice: Organization.name"):
        compile_preview(catalog, {"root": "people", "nodes": nodes, "edges": edges,
            "fields": [{"table": "org", "column": "name"}, {"table": "other_org", "column": "name"}]})


@pytest.mark.parametrize("mode,expected", [("required", ["Alice"]), ("exists", ["Alice"]), ("not_exists", ["Bob"])])
def test_scope_semantics_and_decorrelatable_shape(catalog, mode, expected):
    configured = scope(default="42")
    changes = {"scopes": [configured]} if mode == "required" else {"reportFilters": [{"mode": mode, "conditions": [{"table": "org", "column": "id", "operator": "eq", "value": "42"}]}]}
    sql = compile_preview(catalog, query(**changes))["sql"]
    assert "seed" not in sql
    assert all("t0." not in line for line in sql.splitlines() if "JOIN" in line)
    with sqlite3.connect(":memory:") as db:
        db.executescript("ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.people(id TEXT, name TEXT); CREATE TABLE public.assignment(person_id TEXT, org_id TEXT, start TEXT, end TEXT); CREATE TABLE public.org(id TEXT, name TEXT); INSERT INTO public.people VALUES ('a','Alice'),('b','Bob'); INSERT INTO public.org VALUES ('42','Chosen'),('43','Other'); INSERT INTO public.assignment VALUES ('a','42',NULL,NULL),('a','42',NULL,NULL),('b','43',NULL,NULL);")
        result = db.execute(parse_one(sql, dialect="postgres").sql(dialect="sqlite")).fetchall()
    assert [row[0] for row in result] == expected


@pytest.mark.parametrize("kind,value,expected", [
    ("integer", "9007199254740993", 9007199254740993),
    ("boolean", "false", False),
    ("uuid", "A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11", "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"),
    ("source", "parent-42", "parent-42"),
])
def test_parameter_types_preserve_values_and_required_source_path(catalog, kind, value, expected):
    configured = scope(default=value)
    configured["alternatives"][0]["inputs"][0]["type"] = kind
    result = compile_preview(catalog, query(scopes=[configured]))
    assert result["parameterValues"]["scope"]["value"] == expected
    assert "org" in result["requiredNodes"]


@pytest.mark.parametrize("kind,value", [("integer", "1.5"), ("integer", True), ("uuid", "bad-id"), ("boolean", "yes"), ("source", {"invalid": 42})])
def test_invalid_parameter_types_are_rejected(catalog, kind, value):
    configured = scope(default=value)
    configured["alternatives"][0]["inputs"][0]["type"] = kind
    with pytest.raises(ValueError):
        compile_preview(catalog, query(scopes=[configured]))


def test_source_choice_requires_value_and_binding(catalog):
    configured = scope(default="")
    alternative = configured["alternatives"][0]
    alternative["inputs"][0]["type"] = "source"
    with pytest.raises(ModelValidationError, match="requires Organization"):
        compile_preview(catalog, query(scopes=[configured]))
    alternative["inputs"][0]["defaultValue"] = "42"
    alternative["conditions"] = []
    with pytest.raises(ValueError, match="source-column binding"):
        compile_preview(catalog, query(scopes=[configured]))


def test_domain_configuration_keeps_type_and_does_not_add_lookup_table_to_query(catalog):
    configured = scope(table="people", column="id", default="42")
    parameter = configured["alternatives"][0]["inputs"][0]
    parameter.update(type="integer", domain={"table": "org", "column": "id", "labelColumn": "name"})
    result = compile_preview(catalog, query(scopes=[configured]))
    assert result["parameterValues"]["scope"]["value"] == 42
    assert result["requiredNodes"] == ["people"]
    parameter["domain"]["column"] = "missing"
    with pytest.raises(ValueError, match="unknown source"):
        compile_preview(catalog, query(scopes=[configured]))
    parameter["domain"].update(column="id", labelColumn=[])
    with pytest.raises(ValueError, match="unknown source"):
        compile_preview(catalog, query(scopes=[configured]))


def test_domain_alias_resolves_physical_table_and_missing_alias_is_not_a_fallback(catalog):
    configured = scope(table="people", column="id")
    configured["alternatives"][0]["inputs"][0]["domain"] = {"nodeId": "org_role", "table": "stale_hint", "column": "id", "labelColumn": "name"}
    nodes = [{"id": t["name"], "table": t["name"]} for t in catalog["tables"]] + [{"id": "org_role", "table": "org", "label": "Parent org"}]
    result = compile_preview(catalog, query(nodes=nodes, scopes=[configured]))
    assert result["requiredNodes"] == ["people"]
    with pytest.raises(ValueError, match="domain source alias was removed"):
        compile_preview(catalog, query(nodes=nodes[:-1], scopes=[configured]))


def test_incomplete_source_binding_has_actionable_error(catalog):
    configured = scope()
    configured["alternatives"][0]["conditions"][0].update(table="", column="")
    with pytest.raises(ValueError, match="Choose a source column"):
        compile_preview(catalog, query(scopes=[configured]))


def test_conditional_scope_inactive_does_not_demand_parameter_or_join(catalog):
    result = compile_preview(catalog, query(scopes=[scope("conditional", default="")]))
    assert result["activeScopes"] == []
    assert result["usedRelationships"] == []


def test_required_scope_correlates_returned_assignment_not_other_assignment(catalog):
    result = compile_preview(catalog, query(fields=[{"table": "assignment", "column": "start"}], scopes=[scope()]))
    sql = result["sql"]
    assert 'LEFT JOIN "public"."assignment" AS t1' in sql
    assert 'FROM "public"."org" AS e0_2' in sql
    assert 'WHERE t1."org_id" = e0_2."id"' in sql
    assert sql.count('JOIN "public"."assignment"') == 1


def test_required_scope_all_shared_applies_directly_to_returned_rows(catalog):
    result = compile_preview(catalog, query(fields=[{"table": "org", "column": "name"}], scopes=[scope()]))
    assert 'WHERE t2."id" = E\'42\'' in result["sql"]
    assert "EXISTS" not in result["sql"]


def test_conditional_scope_activated_by_traversed_intermediary(catalog):
    with pytest.raises(ModelValidationError) as caught:
        compile_preview(catalog, query(fields=[{"table": "org", "column": "name"}], scopes=[scope("conditional", "assignment", "start", "")]))
    assert caught.value.details["activeScopes"] == ["scope"]
    assert caught.value.details["parameterId"] == "value"


def test_conditional_filters_prefilter_optional_source_and_existence(catalog):
    conditional = scope("conditional", "assignment", "start", "2026-01-01")
    conditional["alternatives"][0]["conditions"][0]["operator"] = "lte"
    condition = {"table": "org", "column": "name", "operator": "eq", "value": "Sales"}
    result = compile_preview(catalog, query(fields=[{"table": "assignment", "column": "org_id"}], scopes=[conditional], reportFilters=[{"mode": "exists", "conditions": [condition]}]))
    assert result["activeScopes"] == ["scope"]
    assert result["sql"].count('(SELECT * FROM "public"."assignment" WHERE "start" <=') == 2
    assert "LEFT JOIN" in result["sql"]
    assert "WHERE EXISTS" in result["sql"]


def test_separate_existence_groups_and_same_record_conditions(catalog):
    first = [{"table": "assignment", "column": "start", "operator": "gte", "value": "2026-01-01"}, {"table": "assignment", "column": "end", "operator": "is_null"}]
    second = [{"table": "assignment", "column": "org_id", "operator": "eq", "value": 4}]
    result = compile_preview(catalog, query(reportFilters=[{"mode": "exists", "conditions": first}, {"mode": "not_exists", "conditions": second}]))
    assert 'e0_1."start"' in result["sql"] and 'e0_1."end"' in result["sql"]
    assert 'e1_1."org_id"' in result["sql"]
    assert "AND NOT EXISTS" in result["sql"]


def test_required_unrestricted_alternative_does_not_add_path(catalog):
    unrestricted = {"id": "time", "kind": "required", "alternatives": [{"id": "all", "conditions": [], "inputs": []}]}
    result = compile_preview(catalog, query(scopes=[unrestricted]))
    assert result["activeScopes"] == ["time"]
    assert result["usedRelationships"] == []


def test_date_default_resolved_and_bad_date_rejected(catalog):
    configured = scope(table="assignment", column="start", default="today")
    configured["alternatives"][0]["inputs"][0]["type"] = "date"
    result = compile_preview(catalog, query(scopes=[configured]))
    assert len(result["parameterValues"]["scope"]["value"]) == 10
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        compile_preview(catalog, query(scopes=[configured], selections={"scope": {"values": {"value": "yesterday"}}}))


def test_alias_roles_share_physical_table_without_cycle(catalog):
    nodes = [{"id": name, "table": name} for name in ("people", "assignment", "org")]
    nodes.append({"id": "other_org", "table": "org"})
    edges = [{"id": "p", "relationshipId": "person_fk", "source": "assignment", "target": "people"}, {"id": "o", "relationshipId": "org_fk", "source": "assignment", "target": "org"}, {"id": "o2", "relationshipId": "org_fk", "source": "assignment", "target": "other_org"}]
    result = compile_preview(catalog, query(nodes=nodes, edges=edges, fields=[{"table": "org", "column": "name"}, {"table": "other_org", "column": "name"}]))
    assert result["sql"].count('JOIN "public"."org"') == 2
    assert analyze_model(catalog, {"nodes": nodes, "edges": edges}) == {"cycleEdges": []}
    edges[-1]["target"] = "org"
    assert analyze_model(catalog, {"nodes": nodes, "edges": edges}) == {"cycleEdges": ["o", "o2"]}


@pytest.mark.parametrize("changes", [{"nodes": [None]}, {"scopes": [None]}, {"reportFilters": [{"mode": "exists", "conditions": []}]}, {"selections": []}, {"scopes": [{"id": "x", "kind": "required", "alternatives": []}]}])
def test_invalid_payloads_fail_cleanly(catalog, changes):
    with pytest.raises(ValueError):
        compile_preview(catalog, query(**changes))


def test_caller_cannot_override_fk_columns(catalog):
    edge = {"id": "a", "relationshipId": "person_fk", "source": "assignment", "target": "people", "sourceColumn": "bad_sql"}
    result = compile_preview(catalog, query(edges=[edge], fields=[{"table": "assignment", "column": "start"}]))
    assert "bad_sql" not in result["sql"]
    assert '"person_id" = t0."id"' in result["sql"]


def test_cycle_analysis_includes_self_loops_and_disconnected_cycles(catalog):
    catalog["relationships"].append({"id": "self", "sourceTable": "org", "sourceColumn": "id", "targetTable": "org", "targetColumn": "id"})
    request = query(relationships=["self"])
    assert analyze_model(catalog, request)["cycleEdges"] == ["self"]
    with pytest.raises(ModelValidationError, match="cycles"):
        compile_preview(catalog, request)


def test_cycle_analysis_marks_entire_triangle_not_bridge(catalog):
    catalog["relationships"].append({"id": "third", "sourceTable": "people", "sourceColumn": "id", "targetTable": "org", "targetColumn": "id"})
    request = query(relationships=["person_fk", "org_fk", "third"])
    assert analyze_model(catalog, request)["cycleEdges"] == ["person_fk", "org_fk", "third"]


def test_required_scope_activates_conditional_temporal_filter(catalog):
    temporal = scope("conditional", "assignment", "end", "2026-09-05")
    temporal["id"] = "time"
    temporal["alternatives"][0]["conditions"][0].update(operator="gte", allowNull=True)
    result = compile_preview(catalog, query(scopes=[scope(), temporal]))
    assert result["activeScopes"] == ["scope", "time"]
    assert '("end" >= E\'2026-09-05\' OR "end" IS NULL)' in result["sql"]
    assert "LEFT JOIN" not in result["sql"]


def test_no_enabled_path_for_mandatory_filter_rejected(catalog):
    with pytest.raises(ValueError, match="Starting object.*cannot reach"):
        compile_preview(catalog, query(relationships=[], scopes=[scope()]))


@pytest.mark.parametrize("kind", ["required", "conditional"])
@pytest.mark.parametrize("behavior,expected", [("keep_unmatched", [("Alice", "42"), ("Bob", None)]), ("require_matching", [("Alice", "42")])])
def test_filter_activation_independent_of_unmatched_rows(catalog, kind, behavior, expected):
    rule = scope(kind, "assignment", "org_id")
    rule["rowBehavior"] = behavior
    sql = compile_preview(catalog, query(fields=[{"table": "people", "column": "name"}, {"table": "assignment", "column": "org_id"}], scopes=[rule]))["sql"]
    with sqlite3.connect(":memory:") as db:
        db.executescript("ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.people(id TEXT, name TEXT); CREATE TABLE public.assignment(person_id TEXT, org_id TEXT, start TEXT, end TEXT); INSERT INTO public.people VALUES ('a','Alice'),('b','Bob'); INSERT INTO public.assignment VALUES ('a','42',NULL,NULL),('b','43',NULL,NULL);")
        assert sorted(db.execute(parse_one(sql, dialect="postgres").sql(dialect="sqlite")).fetchall()) == expected


def test_conditional_matching_filter_does_not_force_source(catalog):
    rule = {**scope("conditional"), "rowBehavior": "require_matching"}
    result = compile_preview(catalog, query(scopes=[rule]))
    assert result["activeScopes"] == []
    assert result["requiredNodes"] == ["people"]


@pytest.mark.parametrize("aggregate", ["sum", "count", "avg"])
def test_parent_measures_warn_about_child_fanout(catalog, aggregate):
    result = compile_preview(catalog, query(fields=[{"table": "people", "column": "id", "aggregate": aggregate}, {"table": "assignment", "column": "org_id"}]))
    assert any("repeated rows from joins" in warning for warning in result["warnings"])


@pytest.mark.parametrize("aggregate", ["min", "max", "count_distinct"])
def test_duplicate_invariant_aggregates_allow_child_fanout(catalog, aggregate):
    compile_preview(catalog, query(fields=[{"table": "people", "column": "id", "aggregate": aggregate}, {"table": "assignment", "column": "org_id"}]))


def test_child_measure_with_parent_lookup_preserves_grain(catalog):
    compile_preview(catalog, query(fields=[{"table": "people", "column": "name"}, {"table": "assignment", "column": "org_id", "aggregate": "count"}]))


def test_unique_reverse_foreign_key_preserves_measure_grain(catalog):
    catalog["tables"][1]["primaryKey"] = ["person_id"]
    compile_preview(catalog, query(fields=[{"table": "people", "column": "id", "aggregate": "count"}, {"table": "assignment", "column": "org_id"}]))


def test_multiple_child_branches_warn_about_measure_multiplication(catalog):
    catalog["tables"].append({"name": "phone", "columns": [{"name": "person_id"}, {"name": "number"}]})
    catalog["relationships"].append({"id": "phone_fk", "sourceTable": "phone", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"})
    result = compile_preview(catalog, query(relationships=["person_fk", "phone_fk"], fields=[{"table": "assignment", "column": "org_id", "aggregate": "count"}, {"table": "phone", "column": "number"}]))
    assert any("repeated rows from joins" in warning for warning in result["warnings"])


def test_filter_row_behavior_persists_and_legacy_default_is_explicitly_unspecified():
    from schemii.schemoo.models import ModelScope
    assert ModelScope.model_validate(scope()).rowBehavior is None
    value = ModelScope.model_validate({**scope(), "rowBehavior": "keep_unmatched"})
    assert value.model_dump()["rowBehavior"] == "keep_unmatched"


def test_required_keep_unmatched_joins_unselected_bound_source(catalog):
    rule = {**scope("required", "assignment", "org_id"), "rowBehavior": "keep_unmatched"}
    result = compile_preview(catalog, query(scopes=[rule]))
    assert result["activeScopes"] == ["scope"]
    assert 'LEFT JOIN (SELECT * FROM "public"."assignment"' in result["sql"]
    with sqlite3.connect(":memory:") as db:
        db.executescript("ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.people(id TEXT, name TEXT); CREATE TABLE public.assignment(person_id TEXT, org_id TEXT, start TEXT, end TEXT); INSERT INTO public.people VALUES ('a','Alice'),('b','Bob'); INSERT INTO public.assignment VALUES ('a','42',NULL,NULL),('a','43',NULL,NULL),('b','43',NULL,NULL);")
        assert sorted(db.execute(parse_one(result["sql"], dialect="postgres").sql(dialect="sqlite")).fetchall()) == [("Alice",), ("Bob",)]
    result = compile_preview(catalog, query(scopes=[rule], fields=[{"table": "people", "column": "id", "aggregate": "count"}]))
    assert any("repeated rows from joins" in warning for warning in result["warnings"])


def test_alias_branches_do_not_bypass_measure_grain_validation(catalog):
    nodes = [{"id": "people", "table": "people"}, {"id": "a", "table": "assignment"}, {"id": "b", "table": "assignment"}]
    edges = [{"id": alias, "relationshipId": "person_fk", "source": alias, "target": "people"} for alias in ("a", "b")]
    result = compile_preview(catalog, query(nodes=nodes, edges=edges, fields=[{"table": "a", "column": "org_id", "aggregate": "count"}, {"table": "b", "column": "org_id"}]))
    assert any("repeated rows from joins" in warning for warning in result["warnings"])

def test_fanout_warning_preserves_requested_count_and_duplicate_rows(catalog):
    result = compile_preview(catalog, query(fields=[
        {"table": "people", "column": "id", "aggregate": "count"},
        {"table": "assignment", "column": "org_id"},
    ]))
    assert any("repeated rows from joins" in warning for warning in result["warnings"])
    assert "COUNT(DISTINCT" not in result["sql"]
    with sqlite3.connect(":memory:") as db:
        db.executescript("ATTACH DATABASE ':memory:' AS public; CREATE TABLE public.people(id TEXT, name TEXT); CREATE TABLE public.assignment(person_id TEXT, org_id TEXT); INSERT INTO public.people VALUES ('a','Alice'); INSERT INTO public.assignment VALUES ('a','42'),('a','42');")
        assert db.execute(parse_one(result["sql"], dialect="postgres").sql(dialect="sqlite")).fetchall() == [(2, "42")]
