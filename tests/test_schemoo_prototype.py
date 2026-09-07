import pytest
from sqlglot import parse

from schemii.schemoo.prototype import compile_preview


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
    with pytest.raises(ValueError, match="ambiguous"):
        compile_preview(catalog, request(relationships=["org_fk", "other_org_fk"]))


def test_null_operator_and_scalar_summary(catalog):
    result = compile_preview(catalog, request(fields=[{"table": "people", "column": "id", "aggregate": "count"}], filters=[{"table": "people", "column": "org_id", "operator": "is_null"}]))
    assert 'WHERE t0."org_id" IS NULL' in result["sql"]
    assert "GROUP BY" not in result["sql"]
    assert result["grain"] == "One summary row"
