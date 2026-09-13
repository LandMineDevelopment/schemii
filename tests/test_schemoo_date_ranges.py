import pytest
from pglast import parse_sql

from schemii.schemoo.models import ModelDefinition
from schemii.schemoo.prototype import compile_preview, validate_definition


def setup_query(kind="required"):
    catalog = {"tables": [{"name": "slates", "columns": [
        {"name": "id", "dataType": "uuid"}, {"name": "active", "dataType": "daterange"}]}], "relationships": []}
    definition = {"root": "slates", "nodes": [{"id": "slates", "table": "slates", "label": "Slates"}],
        "fields": [{"table": "slates", "column": "id"}], "scopes": [{"id": "date", "kind": kind,
        "alternatives": [{"id": "as_of", "inputs": [{"id": "day", "type": "date", "defaultValue": "today"}],
            "conditions": [{"table": "slates", "column": "active", "operator": "range_contains_date", "parameterId": "day"}]}]}]}
    return catalog, definition


@pytest.mark.parametrize("kind", ["required", "conditional"])
def test_range_parameter_resolves_today_and_generates_typed_containment(kind):
    catalog, definition = setup_query(kind)
    ModelDefinition.model_validate({k: v for k, v in definition.items() if k != "fields"})
    sql = compile_preview(catalog, definition, _today="2026-09-12")["sql"]
    assert '@> DATE E\'2026-09-12\'' in sql
    assert "STRPOS" not in sql
    parse_sql(sql)
    assert "2027-01-01" in compile_preview(catalog, definition, _today="2027-01-01")["sql"]


@pytest.mark.parametrize("value", ["2026-02-30", "2026-09-12' OR TRUE --", "today", ["2026-09-12"], None, 42])
def test_invalid_range_date_rejected(value):
    catalog, definition = setup_query()
    definition["scopes"] = []
    definition["reportFilters"] = [{"mode": "rows", "conditions": [{"table": "slates", "column": "active",
        "operator": "range_contains_date", "value": value}]}]
    with pytest.raises(ValueError, match="valid date"):
        compile_preview(catalog, definition)


def test_wrong_source_type_rejected_at_model_validation():
    catalog, definition = setup_query()
    catalog["tables"][0]["columns"][1]["dataType"] = "text"
    with pytest.raises(ValueError, match="daterange"):
        validate_definition(catalog, definition)


def test_range_null_behavior_preserved():
    catalog, definition = setup_query()
    definition["scopes"][0]["alternatives"][0]["conditions"][0]["allowNull"] = True
    sql = compile_preview(catalog, definition)["sql"]
    assert 'OR t0."active" IS NULL' in sql
