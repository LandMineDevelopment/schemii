from __future__ import annotations

import pytest

from schemii.schemii.designs.deletion_impact import design_deletion_impact
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.designs.store import (
    DesignValidationError,
    InMemoryDesignRepository,
)


TABLE = "table_" + "1" * 32
CHILD_TABLE = "table_" + "2" * 32
ID_COLUMN = "column_" + "3" * 32
EMAIL_COLUMN = "column_" + "4" * 32
LABEL_COLUMN = "column_" + "5" * 32
CHILD_ID_COLUMN = "column_" + "6" * 32
CHILD_ACCOUNT_COLUMN = "column_" + "7" * 32
PRIMARY = "key_" + "8" * 32
EMAIL_INDEX = "index_" + "9" * 32
RELATIONSHIP = "relationship_" + "a" * 32
VIEW = "view_" + "b" * 32
TYPE = "type_" + "c" * 32
ROUTINE = "function_" + "d" * 32
TRIGGER = "trigger_" + "e" * 32


def dependent_content() -> SchemiiDesignContent:
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": TABLE,
                    "name": "accounts",
                    "columns": [
                        {"id": ID_COLUMN, "name": "id", "dataType": "bigint", "nullable": False},
                        {"id": EMAIL_COLUMN, "name": "email", "dataType": "text", "nullable": False},
                        {
                            "id": LABEL_COLUMN,
                            "name": "label",
                            "dataType": "text",
                            "nullable": False,
                            "generatedExpression": "lower(email)",
                            "generatedSourceColumnIds": [EMAIL_COLUMN],
                        },
                    ],
                    "keys": [
                        {"id": PRIMARY, "name": "accounts_pkey", "kind": "primary", "columnIds": [ID_COLUMN]},
                    ],
                    "indexes": [
                        {
                            "id": EMAIL_INDEX,
                            "name": "accounts_email_idx",
                            "method": "btree",
                            "columnIds": [EMAIL_COLUMN],
                        }
                    ],
                },
                {
                    "id": CHILD_TABLE,
                    "name": "sessions",
                    "columns": [
                        {"id": CHILD_ID_COLUMN, "name": "id", "dataType": "bigint", "nullable": False},
                        {"id": CHILD_ACCOUNT_COLUMN, "name": "account_id", "dataType": "bigint", "nullable": False},
                    ],
                },
            ],
            "relationships": [
                {
                    "id": RELATIONSHIP,
                    "name": "sessions_account_id_fkey",
                    "sourceTableId": CHILD_TABLE,
                    "sourceColumnIds": [CHILD_ACCOUNT_COLUMN],
                    "targetTableId": TABLE,
                    "targetColumnIds": [ID_COLUMN],
                }
            ],
            "views": [
                {
                    "id": VIEW,
                    "name": "account_directory",
                    "kind": "view",
                    "definition": "SELECT id, email FROM accounts",
                }
            ],
        }
    )


def _flatten(nodes):
    return [node for item in nodes for node in [item, *_flatten(item.children)]]


def test_deletion_impact_is_recursive_and_source_derived() -> None:
    content = dependent_content()

    table_impact = design_deletion_impact(content, 7, TABLE)
    assert table_impact.blocked is True
    assert table_impact.design_revision == 7
    table_nodes = _flatten(table_impact.dependents)
    assert {(node.kind, node.name) for node in table_nodes} >= {
        ("key", "accounts_pkey"),
        ("relationship", "sessions_account_id_fkey"),
        ("index", "accounts_email_idx"),
        ("view", "account_directory"),
    }
    key_node = next(node for node in table_impact.dependents if node.object_id == PRIMARY)
    assert [node.object_id for node in key_node.children] == [RELATIONSHIP]

    column_impact = design_deletion_impact(content, 7, EMAIL_COLUMN)
    assert {(node.kind, node.name) for node in column_impact.dependents} == {
        ("column", "label"),
        ("index", "accounts_email_idx"),
        ("view", "account_directory"),
    }


def test_type_and_routine_dependencies_are_derived_recursively() -> None:
    document = dependent_content().model_dump(mode="json", by_alias=True)
    document["types"] = [
        {
            "id": TYPE,
            "definition": "CREATE TYPE account_state AS ENUM ('active', 'disabled')",
        }
    ]
    document["tables"][0]["columns"][1]["dataType"] = "account_state"
    document["functions"] = [
        {
            "id": ROUTINE,
            "definition": (
                "CREATE FUNCTION touch_account() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NEW; END $$"
            ),
        }
    ]
    document["triggers"] = [
        {
            "id": TRIGGER,
            "definition": (
                "CREATE TRIGGER account_touch BEFORE UPDATE OF email ON accounts "
                "FOR EACH ROW EXECUTE FUNCTION touch_account()"
            ),
        }
    ]
    content = SchemiiDesignContent.model_validate(document)

    type_impact = design_deletion_impact(content, 8, TYPE)
    assert [node.object_id for node in type_impact.dependents] == [EMAIL_COLUMN]
    assert {(node.kind, node.name) for node in _flatten(type_impact.dependents)} >= {
        ("column", "email"),
        ("column", "label"),
        ("index", "accounts_email_idx"),
        ("trigger", "account_touch"),
        ("view", "account_directory"),
    }

    routine_impact = design_deletion_impact(content, 8, ROUTINE)
    assert [(node.kind, node.name) for node in routine_impact.dependents] == [
        ("trigger", "account_touch")
    ]


def test_repository_rejects_a_removal_that_leaves_dependents() -> None:
    repository = InMemoryDesignRepository()
    saved = repository.replace(
        "owner",
        "ws_" + "c" * 32,
        SchemiiDesignReplace(expected_design_revision=0, content=dependent_content()),
    )
    revised = saved.content.model_copy(deep=True)
    revised.tables[0].columns = [
        column
        for column in revised.tables[0].columns
        if column.id not in {EMAIL_COLUMN, LABEL_COLUMN}
    ]
    revised.tables[0].indexes = []

    with pytest.raises(DesignValidationError, match="before deleting column"):
        repository.replace(
            "owner",
            saved.workspace_id,
            SchemiiDesignReplace(expected_design_revision=1, content=revised),
        )


def test_explicitly_removing_a_target_and_all_dependents_is_atomic() -> None:
    repository = InMemoryDesignRepository()
    workspace_id = "ws_" + "d" * 32
    repository.replace(
        "owner",
        workspace_id,
        SchemiiDesignReplace(expected_design_revision=0, content=dependent_content()),
    )
    revised = dependent_content().model_copy(deep=True)
    revised.tables[0].columns = [
        column
        for column in revised.tables[0].columns
        if column.id not in {EMAIL_COLUMN, LABEL_COLUMN}
    ]
    revised.tables[0].indexes = []
    revised.views = []

    saved = repository.replace(
        "owner",
        workspace_id,
        SchemiiDesignReplace(expected_design_revision=1, content=revised),
    )
    assert [column.name for column in saved.content.tables[0].columns] == ["id"]
