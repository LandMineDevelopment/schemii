"""Logical hierarchy links preserve filtering grain without requiring an FK."""

from copy import deepcopy
import sqlite3

import pytest
from pydantic import ValidationError
from sqlglot import parse_one

from schemii.schemoo.catalog import source_issues
from schemii.schemoo.models import ModelEdge
from schemii.schemoo.prototype import compile_preview, ModelValidationError


@pytest.fixture
def catalog():
    return {
        "namespace": "main",
        "tables": [
            {"name": name, "primaryKey": ["id"], "columns": [
                {"name": column, "dataType": "integer", "nullable": False}
                for column in columns
            ]}
            for name, columns in [
                ("slate_fact", ["id", "org_id", "amount"]),
                ("org_hier", ["id", "child_id", "parent_id"]),
                ("org_dim", ["id"]),
            ]
        ],
        "relationships": [{"id": "org_fk", "sourceTable": "slate_fact", "sourceColumn": "org_id",
                           "targetTable": "org_dim", "targetColumn": "id"}],
    }


def query(**changes):
    return {
        "root": "facts",
        "nodes": [{"id": node, "table": table, "label": node} for node, table in
                  [("facts", "slate_fact"), ("hierarchy", "org_hier"), ("organization", "org_dim")]],
        "edges": [{"id": "hierarchy_link", "kind": "logical", "source": "facts", "target": "hierarchy",
                   "sourceColumn": "org_id", "targetColumn": "child_id"}],
        "fields": [{"table": "facts", "column": "id"}],
        **changes,
    }


def rows(sql):
    with sqlite3.connect(":memory:") as db:
        db.executescript('''
            CREATE TABLE slate_fact(id INTEGER PRIMARY KEY, org_id INTEGER, amount INTEGER);
            CREATE TABLE org_hier(id INTEGER PRIMARY KEY, child_id INTEGER, parent_id INTEGER);
            CREATE TABLE org_dim(id INTEGER PRIMARY KEY);
            INSERT INTO slate_fact VALUES (1, 10, 100), (2, 20, 200), (3, 30, 300);
            INSERT INTO org_hier VALUES (1, 10, 100), (2, 10, 200), (3, 20, 100);
        ''')
        return sorted(db.execute(parse_one(sql, dialect="postgres").sql(dialect="sqlite")).fetchall())


def test_logical_edge_persists_without_foreign_key_and_defaults_conservatively():
    edge = ModelEdge.model_validate(query()["edges"][0])
    assert edge.relationshipId is None
    assert edge.cardinality == "many_to_many"
    assert ModelEdge.model_validate(edge.model_dump()) == edge


@pytest.mark.parametrize("change", [
    {"relationshipId": "org_fk"}, {"sourceColumn": None}, {"targetColumn": None},
    {"cardinality": "certainly_unique"}, {"source": ""},
    {"kind": "foreign_key"}, {"kind": "foreign_key", "relationshipId": "org_fk"},
])
def test_invalid_relationship_contracts_rejected(change):
    with pytest.raises(ValidationError):
        ModelEdge.model_validate({**query()["edges"][0], **change})


@pytest.mark.parametrize("aggregate,expected", [("none", [(1,), (2,)]), ("sum", [(3,)])])
def test_required_ancestor_scope_uses_exists_without_dimension_or_duplicate_facts(catalog, aggregate, expected):
    result = compile_preview(catalog, query(fields=[{"table": "facts", "column": "id", "aggregate": aggregate}], scopes=[{
        "id": "ancestor", "kind": "required", "alternatives": [{
            "id": "chosen", "conditions": [{"table": "hierarchy", "column": "parent_id",
                                              "operator": "in", "value": [100, 200]}],
        }],
    }]))
    assert "EXISTS" in result["sql"]
    assert '"org_dim"' not in result["sql"]
    assert "LEFT JOIN" not in result["sql"]
    assert result["usedRelationships"] == ["hierarchy_link"]
    assert rows(result["sql"]) == expected


def test_explicit_hierarchy_output_retains_each_matching_path(catalog):
    result = compile_preview(catalog, query(
        fields=[{"table": "facts", "column": "id"}, {"table": "hierarchy", "column": "parent_id"}],
        filters=[{"table": "hierarchy", "column": "parent_id", "operator": "not_null"}],
    ))
    assert rows(result["sql"]) == [(1, 100), (1, 200), (2, 100)]
    assert any("multiply totals" in warning for warning in result["warnings"])


def test_starting_table_controls_traversal_independently_of_edge_drawing_direction(catalog):
    request = query(root="hierarchy", fields=[{"table": "hierarchy", "column": "id"},
                                             {"table": "facts", "column": "amount"}])
    forward = compile_preview(catalog, request)
    reverse = deepcopy(request)
    reverse["edges"][0].update(source="hierarchy", target="facts", sourceColumn="child_id", targetColumn="org_id")
    backward = compile_preview(catalog, reverse)
    assert 'FROM "main"."org_hier" AS t0' in forward["sql"]
    assert rows(forward["sql"]) == rows(backward["sql"]) == [(1, 100), (2, 100), (3, 200)]


@pytest.mark.parametrize("aggregate", ["sum", "count", "avg"])
def test_claimed_many_to_one_cannot_bypass_actual_hierarchy_multiplicity(catalog, aggregate):
    request = query(fields=[{"table": "facts", "column": "amount", "aggregate": aggregate},
                            {"table": "hierarchy", "column": "parent_id"}])
    request["edges"][0]["cardinality"] = "many_to_one"
    with pytest.raises(ModelValidationError, match="separate aggregate source"):
        compile_preview(catalog, request)
    catalog["tables"][1]["uniqueKeys"] = [["child_id"]]
    compile_preview(catalog, request)


@pytest.mark.parametrize("side,column", [("source", "org_id"), ("target", "child_id")])
def test_removed_logical_column_reports_edge_and_blocks_compilation(catalog, side, column):
    index = 0 if side == "source" else 1
    catalog["tables"][index]["columns"] = [c for c in catalog["tables"][index]["columns"] if c["name"] != column]
    issues = source_issues(catalog, query())
    assert any(issue.get("edgeId") == "hierarchy_link" and issue.get("column") == column for issue in issues)
    with pytest.raises(ValueError, match="Unknown model field"):
        compile_preview(catalog, query())


def test_raw_foreign_key_request_cannot_override_catalog_join_columns(catalog):
    request = query(edges=[{"id": "physical", "relationshipId": "org_fk", "source": "facts", "target": "organization",
                            "sourceColumn": "amount", "targetColumn": "id"}],
                    fields=[{"table": "facts", "column": "id"}, {"table": "organization", "column": "id"}])
    sql = compile_preview(catalog, request)["sql"]
    assert 't0."org_id" = t1."id"' in sql
    assert 't0."amount" = ' not in sql


@pytest.mark.parametrize("left,right,accepted", [
    ("integer", "bigint", True), ("numeric(12,2)", "real", True),
    ("varchar(40)", "text", True), ("uuid", "uuid", True),
    ("uuid", "integer", False), ("json", "json", False),
    ("", "integer", False), ("custom_type", "custom_type", False),
])
def test_logical_columns_require_comparable_live_types(catalog, left, right, accepted):
    catalog["tables"][0]["columns"][1]["dataType"] = left
    catalog["tables"][1]["columns"][1]["dataType"] = right
    if accepted:
        compile_preview(catalog, query())
    else:
        with pytest.raises(ValueError, match="comparable types"):
            compile_preview(catalog, query())
