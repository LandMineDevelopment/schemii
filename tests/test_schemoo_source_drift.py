from copy import deepcopy
from types import SimpleNamespace

import pytest

from schemii.schemoo.catalog import catalog_contract, source_drift, source_issues


def inferred_lookup_fixture():
    from schemii.schemoo.catalog import model_source_contract
    source = catalog()
    source.update(namespace="public", database="warehouse", fingerprint="initial")
    source["tables"][0]["columns"].append({"name": "name", "dataType": "text", "nullable": False})
    rules = definition(source, [{"table": "child", "column": "id"}])
    rules.update(nodes=rules["nodes"][:1], edges=[], sourceContract={"scope": "model", "tables": [], "relationships": []})
    rules["scopes"] = [{"id": "parent", "kind": "conditional", "alternatives": [{"id": "pick",
        "inputs": [{"id": "value", "type": "uuid"}],
        "conditions": [{"table": "child", "column": "parent_id", "operator": "eq", "parameterId": "value"}]}]}]
    rules["sourceContract"] = model_source_contract(source, rules)
    return source, rules


def test_inferred_lookup_contract_is_server_derived_and_tracks_its_fk_and_label():
    source, rules = inferred_lookup_fixture()
    contract = rules["sourceContract"]
    assert [table["name"] for table in contract["tables"]] == ["parent", "child"]
    assert {column["name"] for column in contract["tables"][0]["columns"]} == {"id", "name"}
    assert [relationship["id"] for relationship in contract["relationships"]] == ["fk"]
    for change in ("retarget", "label-type", "label-removal", "table-removal", "fk-removal"):
        current = deepcopy(source)
        if change == "retarget":
            current["relationships"][0]["targetTable"] = "different_parent"
        elif change == "label-type":
            current["tables"][0]["columns"][1]["dataType"] = "integer"
        elif change == "label-removal":
            current["tables"][0]["columns"].pop()
        elif change == "table-removal":
            current["tables"].pop(0)
        else:
            current["relationships"] = []
        assert any(issue["severity"] == "breaking" for issue in source_issues(current, rules)), change


def test_inferred_lookup_runtime_rejects_changed_fk_before_source_query():
    from schemii.common.api.errors import ApiProblem
    from schemii.schemoo.models import ModelDefinition
    from schemii.schemoo.service import domain_query
    source, rules = inferred_lookup_fixture()
    model = SimpleNamespace(definition=ModelDefinition.model_validate(rules))
    body = SimpleNamespace(scope_id="parent", alternative_id="pick", parameter_id="value", search="")
    assert '"parent"' in domain_query(source, model, body)["sql"]
    current = deepcopy(source)
    current["relationships"][0]["targetTable"] = "different_parent"
    with pytest.raises(ApiProblem) as caught:
        domain_query(current, model, body)
    assert caught.value.code == "model_source_drift"


def test_new_foreign_keys_do_not_rebind_saved_implicit_lookups_or_suppress_new_dependencies():
    from schemii.schemoo.catalog import model_source_contract
    from schemii.schemoo.domain import parameter_domain
    source, rules = inferred_lookup_fixture()
    option = rules["scopes"][0]["alternatives"][0]
    other = {**source["relationships"][0], "id": "other", "targetTable": "other_parent"}
    current = {**source, "relationships": [other, *source["relationships"]]}
    lookup = parameter_domain(current, rules, option, option["inputs"][0])
    assert lookup["table"] == "parent" and lookup["relationshipId"] == "fk"
    assert [item["id"] for item in model_source_contract(current, rules, rules)["relationships"]] == ["fk"]
    current["relationships"] = []
    with pytest.raises(ValueError, match="lookup foreign key was removed"):
        model_source_contract(current, rules, rules)


def test_new_parameter_on_existing_scoped_model_adds_its_actual_lookup_dependencies():
    from schemii.schemoo.catalog import model_source_contract
    source, rules = inferred_lookup_fixture()
    previous = {**rules, "scopes": [], "sourceContract": None}
    previous["sourceContract"] = model_source_contract(source, previous)
    updated = {**rules, "sourceContract": previous["sourceContract"]}
    contract = model_source_contract(source, updated, previous)
    assert [item["id"] for item in contract["relationships"]] == ["fk"]
    assert {item["name"] for item in contract["tables"]} == {"child", "parent"}


def test_saved_no_fk_lookup_stays_local_when_the_first_foreign_key_appears():
    from schemii.schemoo.models import ModelDefinition
    from schemii.schemoo.routes import _scoped_definition
    from schemii.schemoo.service import domain_query
    source, rules = inferred_lookup_fixture()
    source["relationships"] = []
    saved = _scoped_definition(source, ModelDefinition.model_validate(rules), creating=True)
    model = SimpleNamespace(definition=saved)
    body = SimpleNamespace(scope_id="parent", alternative_id="pick", parameter_id="value", search="")
    before = domain_query(source, model, body)["sql"]
    current = deepcopy(source)
    current["relationships"] = catalog()["relationships"]
    assert domain_query(current, model, body)["sql"] == before
    resaved = _scoped_definition(current, saved, saved)
    assert resaved.sourceContract == saved.sourceContract
    assert saved.sourceContract.relationships == []


@pytest.mark.parametrize("already_bound", [False, True])
def test_new_input_on_same_column_cannot_steal_an_unchanged_inputs_domain(already_bound):
    from schemii.schemoo.models import ModelDefinition
    from schemii.schemoo.routes import _scoped_definition
    from schemii.schemoo.service import document, domain_query
    source, rules = inferred_lookup_fixture()
    if not already_bound:
        source["relationships"] = []
    saved = _scoped_definition(source, ModelDefinition.model_validate(rules), creating=True)
    body = SimpleNamespace(scope_id="parent", alternative_id="pick", parameter_id="value", search="")
    old_sql = domain_query(source, SimpleNamespace(definition=saved), body)["sql"]
    current = deepcopy(source)
    other = deepcopy(current["tables"][0]); other["name"] = "other_parent"
    current["tables"].append(other)
    current["relationships"].insert(0, {**catalog()["relationships"][0], "id": "new_fk",
                                         "targetTable": "other_parent"})
    updated_rules = document(saved)
    new_scope = deepcopy(updated_rules["scopes"][0]); new_scope["id"] = "new_scope"
    updated_rules["scopes"].append(new_scope)
    updated = _scoped_definition(current, ModelDefinition.model_validate(updated_rules), saved)
    assert domain_query(current, SimpleNamespace(definition=updated), body)["sql"] == old_sql
    body.scope_id = "new_scope"
    assert '"other_parent"' in domain_query(current, SimpleNamespace(definition=updated), body)["sql"]
    bindings = updated.sourceContract.parameterDomains
    assert len(bindings) == 2
    assert {item.scopeId for item in bindings} == {"parent", "new_scope"}
    assert source_issues(current, document(updated)) == []


def catalog():
    return {"tables": [
        {"name": "parent", "primaryKey": ["id"], "columns": [{"name": "id", "dataType": "uuid", "nullable": False}]},
        {"name": "child", "primaryKey": ["id"], "columns": [
            {"name": "id", "dataType": "uuid", "nullable": False},
            {"name": "parent_id", "dataType": "uuid", "nullable": True},
            {"name": "unused", "dataType": "text", "nullable": True},
        ]},
    ], "relationships": [{"id": "fk", "name": "child_parent", "sourceTable": "child",
                           "sourceColumn": "parent_id", "targetTable": "parent", "targetColumn": "id"}]}


def definition(source, exposed=None):
    return {"root": "child", "nodes": [{"id": "child", "table": "child", "label": "Child"},
                                          {"id": "parent", "table": "parent", "label": "Parent"}],
            "edges": [{"id": "edge", "relationshipId": "fk", "source": "child", "target": "parent", "enabled": True}],
            "scopes": [], "exposedFields": exposed, "sourceContract": catalog_contract(source)}


def test_additive_source_changes_do_not_create_drift():
    before = catalog()
    current = deepcopy(before)
    current["tables"][0]["columns"].append({"name": "new_value", "dataType": "text", "nullable": True})
    current["tables"].append({"name": "new_table", "primaryKey": [], "columns": []})
    current["relationships"].append({"id": "new_fk", "name": "new_fk", "sourceTable": "new_table",
                                     "sourceColumn": "id", "targetTable": "parent", "targetColumn": "id"})
    assert source_drift(current, definition(before)) == []


def test_primary_key_column_contract_and_foreign_key_changes_are_explicit():
    before = catalog()
    current = deepcopy(before)
    current["tables"][1]["primaryKey"] = ["id", "parent_id"]
    current["tables"][1]["columns"][1]["dataType"] = "text"
    current["relationships"][0]["sourceColumn"] = "id"
    issues = source_drift(current, definition(before, [{"table": "child", "column": "parent_id"}]))
    assert [issue["kind"] for issue in issues] == ["changed_primary_key", "changed_column", "changed_relationship"]
    assert all(issue["severity"] == "breaking" and issue["acknowledge"] for issue in issues)


def test_removed_column_without_explicit_references_is_a_warning():
    before = catalog()
    current = deepcopy(before)
    current["tables"][1]["columns"] = current["tables"][1]["columns"][:2]
    restricted = source_drift(current, definition(before, [{"table": "child", "column": "id"}]))
    assert restricted[0]["kind"] == "removed_column" and restricted[0]["severity"] == "warning"
    unrestricted = source_drift(current, definition(before))
    assert unrestricted[0]["severity"] == "warning"
    explicit = source_drift(current, definition(before, [{"table": "child", "column": "unused"}]))
    assert explicit[0]["severity"] == "breaking"


def test_referenced_column_removal_is_reported_once_with_review_context():
    before = catalog()
    current = deepcopy(before)
    current["tables"][1]["columns"] = [column for column in current["tables"][1]["columns"]
                                             if column["name"] != "parent_id"]
    issues = source_issues(current, definition(before, [{"table": "child", "column": "parent_id"}]))
    matching = [issue for issue in issues if issue.get("table") == "child" and issue.get("column") == "parent_id"]
    assert len(matching) == 1
    assert matching[0]["kind"] == "removed_column"
    assert matching[0]["severity"] == "breaking"
    assert matching[0]["acknowledge"] is False


def test_scoped_contract_retains_hidden_keys_foreign_keys_alias_filters_and_domains():
    from schemii.schemoo.catalog import source_dependencies
    source = catalog()
    source["tables"].append({"name": "lookup", "primaryKey": ["code"], "uniqueKeys": [["label"]], "columns": [
        {"name": "code", "dataType": "uuid", "nullable": False}, {"name": "label", "dataType": "text", "nullable": False}]})
    rules = definition(source, [{"table": "child", "column": "unused"}])
    rules["nodes"].append({"id": "role", "table": "child", "label": "Role"})
    rules["scopes"] = [{"id": "filter", "alternatives": [{"conditions": [{"table": "role", "column": "unused", "compareColumn": "parent_id"}],
        "inputs": [{"domain": {"table": "lookup", "column": "code", "labelColumn": "label"}}]}]}]
    selected, relationships = source_dependencies(source, rules)
    assert selected == {"parent": {"id"}, "child": {"id", "parent_id", "unused"}, "lookup": {"code", "label"}}
    assert relationships == {"fk"}
    contract = catalog_contract(source, rules)
    assert contract["scope"] == "model" and len(contract["tables"]) == 3
    assert contract["tables"][2]["uniqueKeys"] == [["label"]]
    rules["sourceContract"] = contract
    current = deepcopy(source); current["tables"][1]["columns"][1]["nullable"] = False
    assert any(issue["kind"] == "changed_column" and issue["column"] == "parent_id" for issue in source_drift(current, rules))
    current = deepcopy(source); current["tables"][2]["columns"][1]["dataType"] = "uuid"
    assert any(issue["table"] == "lookup" and issue["kind"] == "changed_column" for issue in source_drift(current, rules))
    current = deepcopy(source); current["tables"][2]["uniqueKeys"] = []
    assert any(issue["kind"] == "changed_unique_key" for issue in source_drift(current, rules))
    current = deepcopy(source); current["tables"][2]["uniqueKeys"].append(["code", "label"])
    assert source_drift(current, rules) == []


def test_scoped_derived_dependencies_include_operands_groups_connections_conditions_and_domain_labels():
    from schemii.schemoo.catalog import source_dependencies
    source = catalog(); rules = definition(source, [{"table": "summary", "column": "total"}])
    rules["nodes"].append({"id": "summary", "table": "child", "derivation": {"source": "child", "kind": "aggregate", "groupBy": ["parent_id"],
        "connection": {"target": "parent", "columns": [{"source": "parent_id", "target": "id"}]},
        "outputs": [{"id": "total", "nodeId": "child", "column": "unused", "operand": "id", "conditions": [{"table": "child", "column": "id", "compareColumn": "parent_id"},
            {"table": "child", "column": "unused", "domain": {"nodeId": "child", "column": "unused", "labelColumn": "parent_id"}}]}]}})
    selected, _ = source_dependencies(source, rules)
    assert selected["child"] == {"id", "parent_id", "unused"}
    assert "total" not in selected["child"]


def test_scoped_hidden_key_and_logical_join_drift_block_but_unselected_columns_do_not():
    source = catalog(); rules = definition(source, [{"table": "child", "column": "id"}])
    rules["edges"] = [{"id": "logical", "kind": "logical", "source": "child", "target": "parent", "sourceColumn": "parent_id", "targetColumn": "id"}]
    rules["sourceContract"] = catalog_contract(source, rules)
    current = deepcopy(source); current["tables"][1]["columns"][2]["dataType"] = "integer"
    assert source_drift(current, rules) == []
    current["tables"][1]["columns"][1]["dataType"] = "text"
    assert source_drift(current, rules)[0]["kind"] == "changed_column"
    current = deepcopy(source); current["tables"][0]["columns"] = []
    assert any(issue["kind"] == "removed_column" and issue["severity"] == "breaking" for issue in source_drift(current, rules))


def test_incomplete_domain_drafts_do_not_invent_source_dependencies():
    import pytest
    from schemii.schemoo.catalog import source_dependencies
    source = catalog()
    rules = definition(source, [{"table": "child", "column": "id"}])
    rules["edges"] = []
    rules["scopes"] = [{"id": "picker", "alternatives": [{"conditions": [
        {"table": "child", "column": "id", "domain": {"table": "", "column": "", "labelColumn": ""}}],
        "inputs": [{"domain": {"table": "", "column": "", "labelColumn": ""}}]}]}]
    assert source_dependencies(source, rules) == ({"parent": {"id"}, "child": {"id"}}, set())
    domain = rules["scopes"][0]["alternatives"][0]["inputs"][0]["domain"]
    domain.update(table="child", column="parent_id")
    assert source_dependencies(source, rules)[0]["child"] == {"id", "parent_id"}
    domain.update(column="missing")
    with pytest.raises(ValueError, match="child.missing is unavailable"):
        source_dependencies(source, rules)
    domain.update(table="inaccessible", column="")
    with pytest.raises(ValueError, match="Source inaccessible is unavailable"):
        source_dependencies(source, rules)
