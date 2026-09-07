"""Design permissions follow the resulting state, never the submitted verb."""

from copy import deepcopy

import pytest

from schemii.schemii.ai.design_permissions import (
    TABLE_COLLECTIONS,
    TOP_LEVEL_COLLECTIONS,
    design_permission_ids,
)
from schemii.schemii.designs.models import DesignTable, SchemiiDesignContent


def table(identifier="table_a"):
    return {
        "id": identifier, "name": "projects",
        "columns": [{"id": "column_a", "name": "id", "data_type": "integer"},
                    {"id": "column_b", "name": "title", "data_type": "text"}],
        "keys": [{"id": "key_a", "column_ids": ["column_a"]}],
        "checks": [{"id": "check_a", "expression": "id > 0"}],
        "indexes": [{"id": "index_a", "column_ids": ["column_b"]}],
    }


def test_registry_covers_actual_design_model_collections():
    assert set(TOP_LEVEL_COLLECTIONS) == set(SchemiiDesignContent.model_fields)
    assert set(TABLE_COLLECTIONS) == set(DesignTable.model_fields) - {"id", "name"}


def test_whole_table_replacement_cannot_hide_child_deletions():
    before = {"tables": [table()]}
    after = deepcopy(before)
    after["tables"][0]["columns"].pop()
    after["tables"][0]["indexes"].clear()
    assert design_permission_ids(before, after) == ["columns.delete", "indexes.delete"]


@pytest.mark.parametrize("operation", ["create", "delete"])
def test_table_lifecycle_includes_every_owned_object(operation):
    empty, populated = {}, {"tables": [table()]}
    before, after = (empty, populated) if operation == "create" else (populated, empty)
    assert design_permission_ids(before, after) == sorted(
        f"{collection}.{operation}" for collection in ("tables", *TABLE_COLLECTIONS)
    )


def test_index_updates_do_not_require_table_update():
    before = {"tables": [table()]}
    after = deepcopy(before)
    after["tables"][0]["indexes"][0]["unique"] = True
    assert design_permission_ids(before, after) == ["indexes.update"]
    after["tables"][0]["indexes"].append({"id": "index_b", "column_ids": ["column_a"]})
    assert design_permission_ids(before, after) == ["indexes.create", "indexes.update"]


def test_table_rename_and_nested_column_change_are_independent():
    before = {"tables": [table()]}
    after = deepcopy(before)
    after["tables"][0]["name"] = "work"
    after["tables"][0]["columns"][1]["data_type"] = "integer"
    assert design_permission_ids(before, after) == ["columns.update", "tables.update"]


def test_column_moved_across_parents_requires_delete_and_create():
    before = {"tables": [table(), {"id": "table_b", "name": "other", "columns": []}]}
    after = deepcopy(before)
    after["tables"][1]["columns"].append(after["tables"][0]["columns"].pop())
    assert design_permission_ids(before, after) == ["columns.create", "columns.delete"]


def test_column_reordering_is_a_design_column_update():
    before = {"tables": [table()]}
    after = deepcopy(before)
    after["tables"][0]["columns"].reverse()
    assert design_permission_ids(before, after) == ["columns.update"]


def test_column_insertion_does_not_add_spurious_order_update():
    before = {"tables": [table()]}
    after = deepcopy(before)
    after["tables"][0]["columns"].insert(0, {"id": "column_c", "name": "other"})
    assert design_permission_ids(before, after) == ["columns.create"]


@pytest.mark.parametrize("collection", [name for name in TOP_LEVEL_COLLECTIONS if name != "tables"])
def test_all_top_level_object_lifecycles(collection):
    before = {collection: [{"id": "object_a", "name": "old"}]}
    after = {collection: [{"id": "object_a", "name": "new"}]}
    assert design_permission_ids({}, before) == [f"{collection}.create"]
    assert design_permission_ids(before, after) == [f"{collection}.update"]
    assert design_permission_ids(after, {}) == [f"{collection}.delete"]


def test_no_effect_and_unrelated_collection_order_need_no_permission():
    before = {"views": [{"id": "view_a"}, {"id": "view_b"}]}
    assert design_permission_ids(before, deepcopy(before)) == []
    assert design_permission_ids(before, {"views": list(reversed(before["views"]))}) == []


def test_actual_models_and_snake_case_dump_have_same_result():
    content = SchemiiDesignContent(tables=[{
        "id": "table_" + "a" * 32, "name": "items",
        "columns": [{"id": "column_" + "b" * 32, "name": "id", "data_type": "integer"}],
    }])
    assert design_permission_ids(SchemiiDesignContent(), content) == ["columns.create", "tables.create"]
    assert design_permission_ids(content, content.model_dump()) == []


def test_unknown_top_level_fields_fail_closed():
    with pytest.raises(ValueError, match="Unclassified"):
        design_permission_ids({}, {"future_objects": []})


@pytest.mark.parametrize("items", [[{"id": "same"}, {"id": "same"}], [{"name": "missing-id"}]])
def test_ambiguous_identity_fails_closed(items):
    with pytest.raises(ValueError, match="stable object IDs"):
        design_permission_ids({}, {"views": items})
