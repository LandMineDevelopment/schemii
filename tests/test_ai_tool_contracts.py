"""Model-visible schemas and normalized actions share real design contracts."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.schemii.ai.tools import (
    DESIGN_ACTION,
    AddTableColumn,
    ColumnChanges,
    ReadQuery,
    normalize_tool_call,
    tool_definitions,
)
from schemii.schemii.designs.models import (
    DesignColumn,
    DesignView,
    SchemiiDesignContent,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.store import DesignValidationError, InMemoryDesignRepository


TABLE_ID = "table_" + "a" * 32
COLUMN_ID = "column_" + "b" * 32


@pytest.mark.parametrize("sql", [
    "BEGIN; UPDATE things SET active = true; COMMIT;",
    "CREATE INDEX CONCURRENTLY things_idx ON things (id);",
    "SET search_path TO other_schema; VACUUM ANALYZE things;",
    "COPY things TO STDOUT WITH (FORMAT CSV);",
])
def test_human_console_drafts_preserve_raw_sql_without_granting_execution(sql):
    from schemii.schemii.ai.action_policy import disabled_action_ids
    from schemii.schemii.ai.tools import tool_enabled

    caps = AiCapabilities(action_modes={"query.draft": "automatic"})
    proposal = normalize_tool_call("schemii_open_console", {"sql": sql})
    assert proposal.action == {"sql": sql}
    assert proposal.action_type == "console_script"
    assert not disabled_action_ids(caps, proposal.action_type, proposal.action)
    assert tool_enabled("schemii_open_console", caps)
    assert not tool_enabled("schemii_execute_write", caps)
    assert not tool_enabled("schemii_read_query", caps)


def normalize(action):
    return normalize_tool_call("schemii_design_change", {"action": action}).action


def test_column_contracts_derive_fields_and_constraints_from_design():
    assert set(AddTableColumn.model_fields) == set(DesignColumn.model_fields) - {"id"}
    assert set(ColumnChanges.model_fields) == set(AddTableColumn.model_fields)
    for name, field in AddTableColumn.model_fields.items():
        assert field.annotation == DesignColumn.model_fields[name].annotation
        assert field.metadata == DesignColumn.model_fields[name].metadata
    with pytest.raises(ValidationError):
        AddTableColumn(name="x", dataType="")
    with pytest.raises(ValidationError):
        AddTableColumn(id=COLUMN_ID, name="x", dataType="text")


def test_add_column_normalizes_api_names_without_an_id():
    action = normalize({
        "type": "add_column", "table_id": TABLE_ID,
        "column": {"name": "title", "dataType": "text"},
    })
    assert action["column"]["data_type"] == "text"
    assert action["column"]["nullable"] is True
    assert "id" not in action["column"]


def test_partial_update_preserves_omitted_fields_and_explicit_null():
    action = normalize({
        "type": "update_column", "table_id": TABLE_ID, "column_id": COLUMN_ID,
        "changes": {"defaultExpression": None, "nullable": False},
    })
    assert action["changes"] == {"default_expression": None, "nullable": False}


def test_normalized_column_actions_apply_through_real_design_repository():
    owner, workspace = "owner", "ws_" + "c" * 32
    designs = InMemoryDesignRepository()
    initial = designs.replace(owner, workspace, SchemiiDesignReplace(
        expected_design_revision=0,
        content=SchemiiDesignContent.model_validate({"tables": [{
            "id": TABLE_ID, "name": "projects",
            "columns": [{
                "id": COLUMN_ID, "name": "title", "dataType": "text",
                "nullable": False, "defaultExpression": "'Untitled'::text",
            }],
            "indexes": [{
                "id": "index_" + "d" * 32, "name": "projects_title_idx",
                "columnIds": [COLUMN_ID],
            }],
        }]}),
    ))
    service = AiService(InMemoryAiRepository(), None, SimpleNamespace(
        admin_config=AdminConfig(), designs=designs,
    ))
    changed = service._apply_design(owner, workspace, initial, normalize({
        "type": "update_column", "table_id": TABLE_ID, "column_id": COLUMN_ID,
        "changes": {"dataType": "character varying(120)", "defaultExpression": None},
    }))
    column = changed.content.tables[0].columns[0]
    assert column.data_type == "character varying(120)"
    assert column.default_expression is None
    assert column.model_dump(exclude={"data_type", "default_expression"}) == (
        initial.content.tables[0].columns[0].model_dump(exclude={"data_type", "default_expression"})
    )
    assert changed.content.tables[0].indexes == initial.content.tables[0].indexes
    assert initial.content.tables[0].columns[0].data_type == "text"

    added = service._apply_design(owner, workspace, changed, normalize({
        "type": "add_column", "table_id": TABLE_ID,
        "column": {"name": "description", "dataType": "text"},
    }))
    new_column = added.content.tables[0].columns[1]
    assert new_column.id.startswith("column_") and len(new_column.id) == 39
    assert new_column.id != COLUMN_ID
    assert new_column.name == "description"
    assert added.content.tables[0].columns[0] == column
    assert added.content.tables[0].indexes == initial.content.tables[0].indexes
    assert designs.get(owner, workspace) == added
    assert added.revision == initial.revision + 2


@pytest.mark.parametrize("changes", [{}, {"id": COLUMN_ID}, {"name": None}, {"dataType": ""}, {"nullable": None}])
def test_invalid_or_identity_changing_partial_update_is_rejected(changes):
    with pytest.raises(ValueError):
        normalize({
            "type": "update_column", "table_id": TABLE_ID,
            "column_id": COLUMN_ID, "changes": changes,
        })


def test_new_put_object_id_is_server_generated_and_replacement_preserves_it():
    action = normalize({
        "type": "put_top_level_object", "collection": "views",
        "object": {"name": "active", "kind": "view", "definition": "SELECT 1"},
    })
    view = DesignView.model_validate(action["object"])
    assert view.id.startswith("view_")
    assert len(view.id) == 37
    assert normalize(action)["object"]["id"] == view.id


def test_collection_discriminator_rejects_wrong_object_shape():
    with pytest.raises(ValidationError):
        normalize({
            "type": "put_top_level_object", "collection": "views",
            "object": {"name": "bad", "columns": []},
        })
    with pytest.raises(ValidationError):
        normalize({
            "type": "put_table_member", "table_id": TABLE_ID, "collection": "keys",
            "object": {"name": "bad", "kind": "primary", "columnIds": []},
        })


def test_full_object_uses_source_design_validators():
    action = normalize({
        "type": "put_top_level_object", "collection": "types",
        "object": {"definition": "CREATE TYPE mood AS ENUM ('happy', 'sad')"},
    })
    assert action["object"]["name"] == "mood"
    assert action["object"]["enum_values"] == ["happy", "sad"]


def test_advertised_schemas_are_derived_and_full_objects_are_not_dictionaries():
    definitions = {
        tool["name"]: tool["parameters"]
        for tool in tool_definitions(AiCapabilities(design_changes=True, raw_sql_read=True))
    }
    assert definitions["schemii_read_query"]["properties"]["queries"]["items"] == ReadQuery.model_json_schema()
    schema = DESIGN_ACTION.json_schema()
    assert schema["$defs"]["AddColumn"]["properties"]["column"] == {"$ref": "#/$defs/AddTableColumn"}
    assert schema["$defs"]["PutDesignView"]["properties"]["object"] == {"$ref": "#/$defs/AiDesignView"}
    assert "id" not in schema["$defs"]["AiDesignView"]["required"]


def test_batch_normalizes_partial_updates_and_reports_destructive_children():
    proposal = normalize_tool_call("schemii_design_change", {"action": {
        "type": "batch", "actions": [
            {"type": "update_column", "table_id": TABLE_ID, "column_id": COLUMN_ID,
             "changes": {"defaultExpression": None}},
            {"type": "delete_object", "object_id": COLUMN_ID},
        ],
    }})
    assert proposal.action["actions"][0]["changes"] == {"default_expression": None}
    assert proposal.destructive
    with pytest.raises(ValueError, match="at least one changed field"):
        normalize({"type": "batch", "actions": [{
            "type": "update_column", "table_id": TABLE_ID,
            "column_id": COLUMN_ID, "changes": {},
        }]})


@pytest.mark.parametrize("actions", [[], [{"type": "batch", "actions": []}], [
    {"type": "rename_table", "table_id": TABLE_ID, "name": "renamed"}
] * 101])
def test_batch_rejects_empty_nested_and_oversized_actions(actions):
    with pytest.raises(ValidationError):
        normalize({"type": "batch", "actions": actions})


def batch_fixture():
    owner, workspace = "owner", "ws_" + "c" * 32
    designs = InMemoryDesignRepository()
    initial = designs.replace(owner, workspace, SchemiiDesignReplace(
        expected_design_revision=0,
        content=SchemiiDesignContent.model_validate({"tables": [{
            "id": TABLE_ID, "name": "projects", "columns": [{
                "id": COLUMN_ID, "name": "title", "dataType": "text",
            }, {"id": "column_" + "e" * 32, "name": "description", "dataType": "text"}],
        }]}),
    ))
    service = AiService(InMemoryAiRepository(), None, SimpleNamespace(
        admin_config=AdminConfig(), designs=designs,
    ))
    return service, designs, owner, workspace, initial


def index_action(name, column_id=COLUMN_ID):
    return {"type": "put_table_member", "table_id": TABLE_ID,
            "collection": "indexes", "object": {"name": name, "columnIds": [column_id]}}


def test_batch_saves_multiple_indexes_once_and_replaying_ids_is_a_noop():
    service, designs, owner, workspace, initial = batch_fixture()
    action = normalize({"type": "batch", "actions": [
        index_action("projects_title_idx"), index_action("projects_title_other_idx"),
    ]})
    changed = service._apply_design(owner, workspace, initial, action)
    assert changed.revision == initial.revision + 1
    assert [index.name for index in changed.content.tables[0].indexes] == [
        "projects_title_idx", "projects_title_other_idx",
    ]
    assert initial.content.tables[0].indexes == []
    assert designs.get(owner, workspace) == changed
    replayed = service._apply_design(owner, workspace, changed, action)
    assert replayed == changed


@pytest.mark.parametrize("last_action,error", [
    ({"type": "rename_table", "table_id": "table_" + "f" * 32, "name": "missing"}, AiServiceError),
    (index_action("bad_reference", "column_" + "f" * 32), DesignValidationError),
    (index_action("projects_title_idx"), DesignValidationError),
])
def test_batch_failure_leaves_saved_design_and_history_unchanged(last_action, error):
    service, designs, owner, workspace, initial = batch_fixture()
    history_before = dict(designs._history_state)
    action = normalize({"type": "batch", "actions": [
        index_action("projects_title_idx"), last_action,
    ]})
    with pytest.raises(error):
        service._apply_design(owner, workspace, initial, action)
    assert designs.get(owner, workspace) == initial
    assert designs._history_state == history_before


def test_batch_validates_dependencies_after_all_changes():
    service, designs, owner, workspace, initial = batch_fixture()
    with_index = service._apply_design(owner, workspace, initial, normalize(
        index_action("projects_title_idx")
    ))
    column_id = with_index.content.tables[0].columns[0].id
    index_id = with_index.content.tables[0].indexes[0].id
    # Removing a referenced column alone is invalid, but the complete batch
    # removes its dependency too, regardless of child-action ordering.
    changed = service._apply_design(owner, workspace, with_index, normalize({
        "type": "batch", "actions": [
            {"type": "delete_object", "object_id": column_id},
            {"type": "delete_object", "object_id": index_id},
        ],
    }))
    assert changed.revision == with_index.revision + 1
    assert [column.name for column in changed.content.tables[0].columns] == ["description"]
    assert changed.content.tables[0].indexes == []


@pytest.mark.parametrize("sql", ["DELETE FROM items", "SELECT 1; SELECT 2", "WITH deleted AS (DELETE FROM items RETURNING *) SELECT * FROM deleted"])
def test_explain_tool_rejects_writes_and_scripts(sql):
    from schemii.common.postgres.console.execution import ConsoleStatementValidationError
    with pytest.raises(ConsoleStatementValidationError):
        normalize_tool_call("schemii_explain_query", {"sql": sql, "analyze": True})


def test_diagnostic_tools_require_independent_permissions():
    def names(capabilities):
        return {item["name"] for item in tool_definitions(capabilities)}
    disabled = names(AiCapabilities())
    assert "schemii_explain_query" not in disabled
    assert "schemii_query_activity" not in disabled
    read = names(AiCapabilities(raw_sql_read=True))
    assert "schemii_explain_query" not in read
    assert "schemii_query_activity" not in read
    analyze = names(AiCapabilities(structured_data_read=True))
    assert "schemii_query_activity" not in analyze
    assert "schemii_explain_query" not in analyze
    assert "schemii_explain_query" in names(AiCapabilities(explain_queries=True))
    assert "schemii_explain_query" in names(AiCapabilities(analyze_queries=True))
    assert "schemii_query_activity" in names(AiCapabilities(monitor_queries=True))


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_explain_tool_requires_an_explicit_boolean_analyze_option(value):
    with pytest.raises(ValidationError):
        normalize_tool_call("schemii_explain_query", {"sql": "SELECT 1", "analyze": value})


def test_explain_tool_schema_matches_the_sql_size_limit():
    from schemii.schemii.ai.tools import ExplainQuery
    assert ExplainQuery.model_json_schema()["properties"]["sql"]["maxLength"] == 256 * 1024
    with pytest.raises(ValidationError):
        ExplainQuery(sql="x" * (256 * 1024 + 1))
