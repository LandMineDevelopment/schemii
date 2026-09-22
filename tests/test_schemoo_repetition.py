"""Repetition diagnostics explain potential multiplicity without changing queries."""
from copy import deepcopy
import sqlite3

import pytest
from sqlglot import parse_one

from schemii.schemoo.prototype import compile_preview


def fixture():
    catalog = {
        "tables": [
            {"name": "orders", "primaryKey": ["id"], "columns": [{"name": "id"}, {"name": "amount"}]},
            {"name": "items", "columns": [{"name": "order_id"}, {"name": "category"}]},
            {"name": "notes", "columns": [{"name": "order_id"}, {"name": "body"}]},
        ],
        "relationships": [{"id": name, "sourceTable": name, "sourceColumn": "order_id", "targetTable": "orders", "targetColumn": "id"} for name in ("items", "notes")],
    }
    query = {"root": "orders", "relationships": ["items", "notes"], "fields": [
        {"table": "orders", "column": "amount", "aggregate": "sum"},
        {"table": "items", "column": "category"},
    ]}
    return catalog, query


@pytest.mark.parametrize("aggregate,expected", [("sum", 500), ("count", 4), ("avg", 125)])
def test_repetition_diagnostic_preserves_intentional_values_and_null_semantics(aggregate, expected):
    catalog, query = fixture()
    query["fields"][0]["aggregate"] = aggregate
    plan = compile_preview(catalog, query)
    diagnostic, = plan["repetitionDiagnostics"]
    assert diagnostic["code"] == "measure_repetition"
    assert diagnostic["outputIndex"] == 0
    assert diagnostic["measure"] == {"table": "orders", "column": "amount", "aggregate": aggregate, "label": f"{aggregate.upper()} of orders.amount"}
    relationship, = diagnostic["relationships"]
    assert relationship["id"] == "items"
    assert relationship["source"] == {"node": "items", "label": "items", "column": "order_id"}
    assert relationship["target"]["column"] == "id"
    assert [node["node"] for node in relationship["path"]] == ["orders", "items"]
    assert diagnostic["message"] in plan["warnings"]
    assert "DISTINCT" not in plan["sql"]
    with sqlite3.connect(":memory:") as db:
        db.executescript("""ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.orders(id INTEGER, amount REAL);
            CREATE TABLE public.items(order_id INTEGER, category TEXT);
            INSERT INTO public.orders VALUES (1,100),(2,100),(3,200),(4,NULL);
            INSERT INTO public.items VALUES (1,'A'),(1,'A'),(2,'A'),(3,'A'),(4,'A');""")
        assert db.execute(parse_one(plan["sql"], dialect="postgres").sql(dialect="sqlite")).fetchall() == [(expected, "A")]


def test_multiple_measures_report_each_relevant_branch_in_stable_order():
    catalog, query = fixture()
    query["fields"] += [{"table": "orders", "column": "id", "aggregate": "count"}, {"table": "notes", "column": "body"}]
    before = deepcopy(query)
    plan = compile_preview(catalog, query)
    assert query == before
    assert [item["outputIndex"] for item in plan["repetitionDiagnostics"]] == [0, 2]
    for item in plan["repetitionDiagnostics"]:
        assert [edge["id"] for edge in item["relationships"]] == ["items", "notes"]
    assert compile_preview(catalog, query)["repetitionDiagnostics"] == plan["repetitionDiagnostics"]


def test_sibling_path_starts_from_measure_source_and_keeps_alias_labels():
    catalog, query = fixture()
    query["nodes"] = [{"id": "orders", "table": "orders", "label": "Sales orders"},
                      {"id": "line", "table": "items", "label": "Order lines"},
                      {"id": "comment", "table": "notes", "label": "Comments"}]
    query["edges"] = [{"id": name, "relationshipId": relation, "source": name, "target": "orders"} for name, relation in [("line", "items"), ("comment", "notes")]]
    query["fields"] = [{"table": "line", "column": "order_id", "aggregate": "count"}, {"table": "comment", "column": "body"}]
    item, = compile_preview(catalog, query)["repetitionDiagnostics"]
    assert item["measure"]["label"] == "COUNT of Order lines.order_id"
    relationship, = item["relationships"]
    assert [node["label"] for node in relationship["path"]] == ["Order lines", "Sales orders", "Comments"]
    assert relationship["id"] == "comment"


@pytest.mark.parametrize("aggregate", ["min", "max", "count_distinct", "none"])
def test_duplicate_invariant_measures_and_raw_fields_need_no_measure_warning(aggregate):
    catalog, query = fixture()
    query["fields"][0]["aggregate"] = aggregate
    assert compile_preview(catalog, query)["repetitionDiagnostics"] == []


def test_unique_relationships_and_parent_lookups_preserve_measure_records():
    catalog, query = fixture()
    catalog["tables"][1]["primaryKey"] = ["order_id"]
    assert compile_preview(catalog, query)["repetitionDiagnostics"] == []
    catalog["tables"][1].pop("primaryKey")
    query["fields"] = [{"table": "items", "column": "order_id", "aggregate": "count"}, {"table": "orders", "column": "id"}]
    assert compile_preview(catalog, query)["repetitionDiagnostics"] == []
