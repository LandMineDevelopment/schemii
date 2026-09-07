from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.tools import (
    authority_manifest, proposal_tool_arguments, tool_definitions,
    tool_enabled, tools_for_capabilities,
)


def test_migration_recovery_does_not_grant_apply_or_conflict_resolution():
    caps = AiCapabilities(action_modes={"migration.reconcile": "automatic"})
    tools = tools_for_capabilities(caps)
    assert tools["schemii_reconcile_migration"]
    assert tools["schemii_migration_status"]
    assert not tools["schemii_apply_migration"]
    assert not tools["schemii_resolve_migration"]
    assert not tools["schemii_review_migration"]
    assert not tools["schemii_design_change"]


def test_history_only_exposes_permitted_direction_without_reset():
    caps = AiCapabilities(action_modes={"history.undo": "ask"})
    history = next(tool for tool in tool_definitions(caps) if tool["name"] == "schemii_design_history")
    assert history["parameters"]["properties"]["direction"]["enum"] == ["undo"]
    assert tool_enabled("schemii_design_history", caps, {"direction": "undo"})
    assert not tool_enabled("schemii_design_history", caps, {"direction": "redo"})
    assert not tool_enabled("schemii_reset_design", caps)


def test_manifest_describes_independent_actions_and_does_not_imply_broad_execution():
    caps = AiCapabilities(action_modes={"indexes.create": "automatic", "tables.delete": "ask"})
    authority = authority_manifest(4, caps)
    assert authority["actionPermissions"]["indexes.create"]["mode"] == "automatic"
    assert authority["actionPermissions"]["tables.delete"]["mode"] == "ask"
    assert authority["actionPermissions"]["columns.delete"]["mode"] == "disabled"
    assert "executionPolicies" not in authority
    assert not authority["assistantCanSelfApprove"]
    assert tool_enabled("schemii_design_change", caps)
    assert not tool_enabled("schemii_execute_write", caps)


def test_native_replay_omits_server_only_permission_classification():
    action = {"type": "rename_table", "table_id": "table_a", "name": "renamed", "requiredActions": ["tables.update"]}
    assert proposal_tool_arguments("schemii_design_change", "Rename", action) == {
        "summary": "Rename", "action": {"type": "rename_table", "table_id": "table_a", "name": "renamed"},
    }
    assert "requiredActions" in action
