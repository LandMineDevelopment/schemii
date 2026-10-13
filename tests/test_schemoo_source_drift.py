from copy import deepcopy

from schemii.schemoo.catalog import catalog_contract, source_drift, source_issues


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
