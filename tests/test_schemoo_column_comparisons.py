"""Column operands retain same-row semantics across the shared predicate paths."""

from copy import deepcopy
import sqlite3

import pytest
from pydantic import ValidationError
from sqlglot import parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.catalog import catalog_contract, source_issues
from schemii.schemoo.models import Condition, DerivedCondition, ExploreState, ModelCreate, ModelDefinition
from schemii.schemoo.prototype import compile_preview
from schemii.schemoo.service import plan_query
from schemii.schemoo.store import InMemoryModelRepository


CATALOG = {"namespace": "main", "tables": [{"name": "orders", "primaryKey": ["id"], "columns": [
    {"name": name, "dataType": kind, "nullable": True}
    for name, kind in [("id", "integer"), ("actual", "numeric"), ("target", "integer"), ("label", "text")]
]}], "relationships": []}
CONDITION = {"table": "order_alias", "column": "actual", "operator": "gt", "compareColumn": "target"}


def query(**changes):
    return {"root": "order_alias", "nodes": [{"id": "order_alias", "table": "orders", "label": "Orders"}],
            "fields": [{"table": "order_alias", "column": "id"}], **changes}


def execute(sql):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE orders(id INTEGER, actual NUMERIC, target INTEGER, label TEXT);
            INSERT INTO orders VALUES (1, 12, 10, 'late'), (2, 8, 10, 'early'), (3, 10, 10, 'equal'),
                                      (4, NULL, 10, 'missing actual'), (5, 12, NULL, 'missing target');
        """)
        return connection.execute(parse_one(sql, dialect="postgres").sql(dialect="sqlite")).fetchall()


@pytest.mark.parametrize("location", ["rows", "exists", "not_exists", "required", "conditional"])
@pytest.mark.parametrize("allow_null", [False, True])
def test_same_row_alias_comparisons_execute_on_every_predicate_path(location, allow_null):
    condition = {**CONDITION, "allowNull": allow_null}
    if location in {"required", "conditional"}:
        options = {"scopes": [{"id": "late", "kind": location, "alternatives": [{"id": "one", "conditions": [condition]}]}]}
    else:
        options = {"reportFilters": [{"mode": location, "conditions": [condition]}]}
    result = compile_preview(CATALOG, query(**options))
    expected = [1, 4] if allow_null else [1]
    if location == "not_exists":
        expected = [value for value in range(1, 6) if value not in expected]
    assert sorted(row[0] for row in execute(result["sql"])) == expected
    assert "JOIN" not in result["sql"]


@pytest.mark.parametrize("operator,expected", [("eq", [3]), ("ne", [1, 2]), ("gt", [1]), ("gte", [1, 3]), ("lt", [2]), ("lte", [2, 3])])
def test_all_six_operators(operator, expected):
    result = compile_preview(CATALOG, query(filters=[{**CONDITION, "operator": operator}]))
    assert sorted(row[0] for row in execute(result["sql"])) == expected


def test_optional_scope_activation():
    scope = {"id": "late", "kind": "required", "requirement": "optional", "alternatives": [{"id": "one", "conditions": [CONDITION]}]}
    assert len(execute(compile_preview(CATALOG, query(scopes=[scope]))["sql"])) == 5
    assert execute(compile_preview(CATALOG, query(scopes=[scope], selections={"late": {"active": True}}))["sql"]) == [(1,)]


@pytest.mark.parametrize("change", [{"operator": "in"}, {"operator": "is_null"}, {"operator": "contains"},
    {"value": ""}, {"value": 0}, {"value": False}, {"parameterId": "p"}, {"domain": {}},
    {"compareColumn": ""}, {"compareColumn": []}])
def test_contract_and_direct_compiler_reject_ambiguous_or_malformed_operands(change):
    condition = {**CONDITION, **change}
    with pytest.raises(ValidationError):
        Condition.model_validate(condition)
    with pytest.raises(ValueError):
        compile_preview(CATALOG, query(filters=[condition]))


@pytest.mark.parametrize("column", ["missing", "label", "orders.target", 'target"; DROP TABLE orders; --'])
def test_unknown_cross_source_or_incompatible_columns_rejected(column):
    with pytest.raises(ValueError, match="comparison column|comparable types"):
        compile_preview(CATALOG, query(filters=[{**CONDITION, "compareColumn": column}]))


def test_column_names_are_quoted_as_identifiers():
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["columns"][2]["name"] = 'target"value'
    sql = compile_preview(catalog, query(filters=[{**CONDITION, "compareColumn": 'target"value'}]))["sql"]
    assert 't0."actual" > t0."target""value"' in sql
    parse_one(sql, dialect="postgres")


@pytest.mark.parametrize("kind", ["row", "aggregate"])
def test_derived_conditions_reuse_column_operand(kind):
    request = query()
    output = {"id": "result", "label": "Result", "operation": "subtract" if kind == "row" else "sum", "column": "actual", "conditions": [CONDITION]}
    if kind == "row":
        output["operand"] = "target"
    request["nodes"].append({"id": "calculated", "table": "orders", "label": "Calculated", "derivation": {
        "kind": kind, "source": "order_alias", "groupBy": ["id"] if kind == "aggregate" else [], "outputs": [output]}})
    request["fields"].append({"table": "calculated", "column": "result"})
    rows = execute(compile_preview(CATALOG, request)["sql"])
    assert sorted(rows) == [(1, 2 if kind == "row" else 12), (2, None), (3, None), (4, None), (5, None)]
    with pytest.raises(ValidationError, match="Today"):
        DerivedCondition.model_validate({**CONDITION, "valueSource": "today"})


def definition():
    request = query()
    return {"root": request["root"], "nodes": request["nodes"], "exposedFields": request["fields"],
            "scopes": [{"id": "late", "kind": "required", "alternatives": [{"id": "one", "conditions": [CONDITION]}]}]}


def test_missing_rhs_and_type_drift_are_reported_even_when_rhs_not_exposed():
    saved = definition()
    saved["sourceContract"] = catalog_contract(CATALOG)
    changed = deepcopy(CATALOG)
    changed["tables"][0]["columns"][2]["dataType"] = "text"
    assert any(issue.get("column") == "target" and issue["severity"] == "breaking" for issue in source_issues(changed, saved))
    changed["tables"][0]["columns"].pop(2)
    assert any(issue.get("column") == "target" and issue["kind"] == "removed_column" for issue in source_issues(changed, saved))


def test_report_rhs_must_be_exposed():
    saved = definition()
    saved["exposedFields"].append({"table": "order_alias", "column": "actual"})
    with pytest.raises(ApiProblem) as error:
        plan_query(CATALOG, ModelDefinition.model_validate(saved), ExploreState(fields=query()["fields"], reportFilters=[{"mode": "rows", "conditions": [CONDITION]}]))
    assert error.value.code == "field_not_exposed"


def test_repository_round_trip_preserves_operand():
    repository = InMemoryModelRepository()
    saved = repository.create("owner", ModelCreate(name="Comparisons", connection_id="pg_" + "a" * 32,
        database="warehouse", namespace="main", definition=definition()))
    loaded = repository.get("owner", saved.id)
    restored = ModelDefinition.model_validate_json(loaded.definition.model_dump_json())
    condition = restored.scopes[0].alternatives[0].conditions[0]
    assert condition.compareColumn == "target" and condition.value is None


@pytest.mark.parametrize("mode,expected", [("rows", [(1,)]), ("exists", [(1,)]), ("not_exists", [(2,), (3,)])])
def test_related_record_comparison_uses_the_existing_alias_and_same_related_row(mode, expected):
    catalog = deepcopy(CATALOG)
    catalog["tables"].append({"name": "customers", "columns": [{"name": "id", "dataType": "integer"}]})
    catalog["tables"][0]["columns"].append({"name": "customer_id", "dataType": "integer"})
    catalog["relationships"] = [{"id": "customer_fk", "sourceTable": "orders", "sourceColumn": "customer_id", "targetTable": "customers", "targetColumn": "id"}]
    request = query(root="customers", fields=[{"table": "customers", "column": "id"}],
        reportFilters=[{"mode": mode, "conditions": [CONDITION]}])
    request["nodes"].append({"id": "customers", "table": "customers", "label": "Customers"})
    request["edges"] = [{"id": "orders_for_customer", "relationshipId": "customer_fk", "source": "order_alias", "target": "customers"}]
    sql = compile_preview(catalog, request)["sql"]
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE customers(id INTEGER);
            INSERT INTO customers VALUES (1), (2), (3);
            CREATE TABLE orders(id INTEGER, actual NUMERIC, target INTEGER, customer_id INTEGER);
            INSERT INTO orders VALUES (1, 12, 10, 1), (2, 8, 10, 1),
                                      (3, 10, 12, 2), (4, 1, 2, 2);
        """)
        assert sorted(connection.execute(parse_one(sql, dialect="postgres").sql(dialect="sqlite")).fetchall()) == expected


@pytest.mark.parametrize("left,right,compatible", [("date", "date", True),
    ("timestamp(3) with time zone", "timestamptz", True), ("varchar(20)", "text", True),
    ("boolean", "boolean", True), ("date", "text", False), ("json", "json", False),
    ("date", "timestamp", False), ("", "", False)])
def test_compatibility_reuses_existing_conservative_no_cast_rules(left, right, compatible):
    catalog = deepcopy(CATALOG)
    catalog["tables"][0]["columns"][1]["dataType"] = left
    catalog["tables"][0]["columns"][2]["dataType"] = right
    if compatible:
        assert 't0."actual" > t0."target"' in compile_preview(catalog, query(filters=[CONDITION]))["sql"]
    else:
        with pytest.raises(ValueError, match="comparable types"):
            compile_preview(catalog, query(filters=[CONDITION]))
