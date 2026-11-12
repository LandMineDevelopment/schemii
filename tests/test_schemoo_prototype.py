import pytest
from sqlglot import parse

from schemii.schemoo.prototype import compile_preview, _parameter_value


@pytest.mark.parametrize("value,expected", [
    ("today -365", "2023-03-02"), (" TODAY - 1 day ", "2024-02-29"),
    ("today+30 days", "2024-03-31"), ("today", "2024-03-01"),
    ("2020-01-01", "2020-01-01"), ("today - 0", "2024-03-01"),
])
def test_relative_date_parameters(value, expected):
    assert _parameter_value(value, "date", "History date", "2024-03-01") == expected


@pytest.mark.parametrize("value", ["today - 1.5", "today - 1 year", "today -- 2", "today + 9999999", "today; SELECT 1", "2024-02-30", None])
def test_invalid_relative_dates_are_actionable(value):
    with pytest.raises(ValueError, match="History date requires YYYY-MM-DD, today"):
        _parameter_value(value, "date", "History date", "2024-03-01")


def test_relative_date_defaults_resolve_each_compilation(catalog):
    query = request(scopes=[{"id": "history", "kind": "required", "alternatives": [{
        "id": "default", "inputs": [{"id": "date", "type": "date", "defaultValue": "today - 365"}],
        "conditions": [{"table": "people", "column": "name", "operator": "gte", "parameterId": "date"}],
    }]}])
    assert "2023-03-02" in compile_preview(catalog, query, _today="2024-03-01")["sql"]
    assert "2023-03-03" in compile_preview(catalog, query, _today="2024-03-02")["sql"]
    assert query["scopes"][0]["alternatives"][0]["inputs"][0]["defaultValue"] == "today - 365"


@pytest.fixture
def catalog():
    return {
        "namespace": "public",
        "tables": [
            {"name": name, "columns": [{"name": field, "dataType": "text"} for field in fields]}
            for name, fields in [("people", ["id", "name", "org_id"]), ("org", ["id", "name"]), ("cert", ["id", "person_id"])]
        ],
        "relationships": [
            {"id": "org_fk", "sourceTable": "people", "sourceColumn": "org_id", "targetTable": "org", "targetColumn": "id"},
            {"id": "person_fk", "sourceTable": "cert", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"},
        ],
    }


def request(**changes):
    return {"root": "people", "fields": [{"table": "people", "column": "name"}], "relationships": ["org_fk", "person_fk"], **changes}


def test_unused_sources_not_joined(catalog):
    result = compile_preview(catalog, request())
    assert result["usedRelationships"] == []
    assert "JOIN" not in result["sql"]
    assert 'FROM "public"."people" AS t0' in result["sql"]
    assert "LIMIT 100" in result["sql"]


def test_join_paths_include_bridge_and_warn_on_fanout(catalog):
    result = compile_preview(catalog, request(root="org", fields=[{"table": "cert", "column": "id"}]))
    assert result["usedRelationships"] == ["org_fk", "person_fk"]
    assert result["sql"].count("LEFT JOIN") == 2
    assert sum("multiply totals" in warning for warning in result["warnings"]) == 2


def test_groups_dimensions_and_counts_distinct(catalog):
    result = compile_preview(catalog, request(fields=[{"table": "org", "column": "name"}, {"table": "people", "column": "id", "aggregate": "count_distinct"}]))
    assert 'COUNT(DISTINCT t0."id")' in result["sql"]
    assert 'GROUP BY t1."name"' in result["sql"]
    assert result["grain"] == "Grouped by org.name"


def test_joined_filter_reaches_source_and_explains_outer_join_semantics(catalog):
    result = compile_preview(catalog, request(filters=[{"table": "org", "column": "name", "operator": "eq", "value": "IT"}]))
    assert result["usedRelationships"] == ["org_fk"]
    assert 'WHERE t1."name" = E\'IT\'' in result["sql"]
    assert any("unmatched root" in warning for warning in result["warnings"])


@pytest.mark.parametrize("value", ["O'Reilly", "x'; DELETE FROM people; --", "\\'; SELECT 1; --", "%_", "line\nnext"])
def test_filter_literals_cannot_introduce_statements(catalog, value):
    result = compile_preview(catalog, request(filters=[{"table": "people", "column": "name", "operator": "contains", "value": value}]))
    statements = parse(result["sql"], dialect="postgres")
    assert len(statements) == 1
    assert statements[0].key == "select"
    assert "STRPOS" in result["sql"]


@pytest.mark.parametrize("changes", [
    {"root": "missing"}, {"fields": []}, {"limit": 101}, {"limit": True},
    {"relationships": ["missing"]},
    {"fields": [{"table": "people", "column": "missing"}]},
    {"fields": [{"table": "people", "column": "id", "aggregate": "evil"}]},
    {"fields": [{"table": "org", "column": "id"}], "relationships": []},
    {"filters": [{"table": "people", "column": "id", "operator": "eq", "value": None}]},
    {"filters": [{"table": "people", "column": "id", "operator": "eq", "value": float("nan")}]},
])
def test_invalid_requests_rejected(catalog, changes):
    with pytest.raises(ValueError):
        compile_preview(catalog, request(**changes))


def test_parallel_roles_require_explicit_path(catalog):
    catalog["relationships"].append({**catalog["relationships"][0], "id": "other_org_fk"})
    assert compile_preview(catalog, request(relationships=["org_fk", "other_org_fk"]))["usedRelationships"] == []
    with pytest.raises(ValueError, match="ambiguous"):
        compile_preview(catalog, request(relationships=["org_fk", "other_org_fk"], fields=[{"table": "org", "column": "name"}]))


def test_null_operator_and_scalar_summary(catalog):
    result = compile_preview(catalog, request(fields=[{"table": "people", "column": "id", "aggregate": "count"}], filters=[{"table": "people", "column": "org_id", "operator": "is_null"}]))
    assert 'WHERE t0."org_id" IS NULL' in result["sql"]
    assert "GROUP BY" not in result["sql"]
    assert result["grain"] == "One summary row"


def _qualified_composite_catalog():
    return {"namespace": "one", "tables": [
        {"namespace": schema, "name": "shared.name", "primaryKey": ["tenant", "id"], "columns": [
            {"name": name, "dataType": "integer" if name != "label" else "text", "nullable": False}
            for name in ["tenant", "id", "label"]]} for schema in ["one", 'other"schema']],
        "relationships": [{"id": "qualified_fk", "name": "two_pairs", "sourceNamespace": "one", "sourceTable": "shared.name",
            "targetNamespace": 'other"schema', "targetTable": "shared.name", "columns": [
                {"source": "tenant", "target": "tenant"}, {"source": "id", "target": "id"}]}]}


def _qualified_composite_request():
    return {"root": "old_stable_id", "nodes": [
        {"id": "old_stable_id", "table": "shared.name", "label": "Left"},
        {"id": "alias_right", "namespace": 'other"schema', "table": "shared.name", "label": "Right"}],
        "edges": [{"id": "old_edge", "relationshipId": "qualified_fk", "source": "old_stable_id", "target": "alias_right"}],
        "fields": [{"table": "old_stable_id", "column": "label"}, {"table": "alias_right", "column": "label"}]}


def test_qualified_duplicate_names_keep_legacy_identity_and_join_every_pair_forward_reverse_exists():
    from copy import deepcopy
    catalog, query = _qualified_composite_catalog(), _qualified_composite_request()
    before = deepcopy(query)
    forward = compile_preview(catalog, query)
    assert 'FROM "one"."shared.name"' in forward["sql"]
    assert 'JOIN "other""schema"."shared.name"' in forward["sql"]
    assert 't0."tenant" = t1."tenant" AND t0."id" = t1."id"' in forward["sql"]
    assert query == before
    reverse = compile_preview(catalog, {**query, "root": "alias_right"})
    assert 'FROM "other""schema"."shared.name"' in reverse["sql"]
    assert 't1."tenant" = t0."tenant" AND t1."id" = t0."id"' in reverse["sql"]
    existence = compile_preview(catalog, {**query, "fields": [query["fields"][0]], "reportFilters": [
        {"mode": "exists", "conditions": [{"table": "alias_right", "column": "label", "operator": "eq", "value": "second-pair sentinel"}]}]})
    assert 'EXISTS (' in existence["sql"]
    assert 't0."tenant" = e0_1."tenant" AND t0."id" = e0_1."id"' in existence["sql"]
    assert existence["usedRelationships"] == ["old_edge"]


def test_composite_logical_mapping_validates_every_pair_and_complete_uniqueness():
    from copy import deepcopy
    catalog, query = _qualified_composite_catalog(), _qualified_composite_request()
    query["edges"] = [{"id": "logical", "kind": "logical", "source": "old_stable_id", "target": "alias_right",
                       "columns": deepcopy(catalog["relationships"][0]["columns"])}]
    query["fields"] = [{"table": "alias_right", "column": "label"}, {"table": "old_stable_id", "column": "id", "aggregate": "sum"}]
    result = compile_preview(catalog, query)
    assert result["repetitionDiagnostics"] == []  # Complete two-column target key prevents fanout.
    query["edges"][0]["columns"] = [{"source": "tenant", "target": "tenant"}]
    assert compile_preview(catalog, query)["repetitionDiagnostics"][0]["code"] == "measure_repetition"
    query["edges"][0]["columns"].append({"source": "id", "target": "label"})
    with pytest.raises(ValueError, match="comparable"):
        compile_preview(catalog, query)


@pytest.mark.parametrize("mapping", [
    {"columns": []}, {"columns": [{"source": "id"}]},
    {"columns": [{"source": "id", "target": "id"}, {"source": "id", "target": "tenant"}]},
    {"sourceColumn": "id"}, {"sourceColumn": "tenant", "targetColumn": "tenant", "columns": [{"source": "id", "target": "id"}]},
])
def test_logical_mapping_json_rejects_incomplete_duplicate_and_conflicting_forms(mapping):
    from schemii.schemoo.models import ModelEdge
    with pytest.raises(ValueError):
        ModelEdge.model_validate({"id": "logical", "kind": "logical", "source": "left", "target": "right", **mapping})


def test_legacy_scalar_mapping_roundtrips_and_foreign_key_overrides_are_rejected():
    from schemii.schemoo.models import ModelEdge, ModelNode
    edge = {"id": "old", "kind": "logical", "source": "a", "target": "b", "sourceColumn": "id", "targetColumn": "id"}
    assert ModelEdge.model_validate(edge).model_dump(exclude_none=True)["sourceColumn"] == "id"
    with pytest.raises(ValueError):
        ModelEdge.model_validate({**edge, "kind": "foreign_key", "relationshipId": "fk"})
    for namespace in ["", 42, ["one"]]:
        with pytest.raises(ValueError):
            ModelNode.model_validate({"id": "node", "table": "shared.name", "namespace": namespace, "label": "Source"})
    catalog, query = _qualified_composite_catalog(), _qualified_composite_request()
    query["edges"][0]["columns"] = [{"source": "tenant", "target": "tenant"}]
    with pytest.raises(ValueError, match="cannot override"):
        compile_preview(catalog, query)


def test_legacy_fk_hash_resolver_is_default_namespace_and_endpoint_bound():
    from schemii.schemoo.domain import legacy_relationship_id, qualified_relationship_id
    catalog, query = _qualified_composite_catalog(), _qualified_composite_request()
    left = catalog["relationships"][0]
    left.update(sourceNamespace="one", targetNamespace="one", id=qualified_relationship_id("one", "shared.name", "two_pairs"))
    catalog["relationships"].append({**left, "id": qualified_relationship_id('other"schema', "shared.name", "two_pairs"),
                                      "sourceNamespace": 'other"schema', "targetNamespace": 'other"schema'})
    query["nodes"][1].pop("namespace")
    query["edges"][0]["relationshipId"] = legacy_relationship_id(left)
    assert compile_preview(catalog, query)["usedRelationships"] == ["old_edge"]
    query["nodes"][0]["namespace"] = query["nodes"][1]["namespace"] = 'other"schema'
    with pytest.raises(ValueError, match="known foreign key"):
        compile_preview(catalog, query)
