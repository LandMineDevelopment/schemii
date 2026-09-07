"""Action permissions remain independent and old saved policies do not expand."""
import pytest
from pydantic import ValidationError

from schemii.schemii.ai.action_policy import PERMISSIONS, disabled_action_ids, requires_approval
from schemii.schemii.ai.models import AiCapabilities


def test_explicit_action_map_is_closed_by_default_and_round_trips():
    caps = AiCapabilities(action_modes={"indexes.create": "automatic", "tables.delete": "ask"})
    assert caps.action_modes["columns.delete"] == "disabled"
    assert len(caps.action_modes) == len(PERMISSIONS)
    assert AiCapabilities.model_validate(caps.model_dump(by_alias=True)) == caps
    assert caps.design_changes


def test_legacy_permission_projection_preserves_authority():
    caps = AiCapabilities(design_changes=True, design_approval_required=False, raw_sql_read=True)
    assert caps.action_modes["indexes.create"] == "automatic"
    assert caps.action_modes["tables.delete"] == "automatic"
    assert caps.action_modes["migration.review"] == "automatic"
    assert caps.action_modes["query.read"] == "ask"
    assert caps.action_modes["query.write"] == "disabled"


def test_explicit_map_overrides_legacy_enabled_flags():
    caps = AiCapabilities(design_changes=True, action_modes={"indexes.create": "automatic"})
    assert caps.action_modes["tables.delete"] == "disabled"


@pytest.mark.parametrize("modes", [{"unknown": "automatic"}, {"query.read": "sometimes"}])
def test_unknown_policy_rejected(modes):
    with pytest.raises(ValidationError):
        AiCapabilities(action_modes=modes)


def test_batch_asks_once_if_any_effect_requires_approval():
    caps = AiCapabilities(action_modes={"indexes.create": "automatic", "columns.update": "ask"})
    action = {"requiredActions": ["indexes.create", "columns.update"]}
    assert requires_approval(caps, "design_change", action)
    assert not disabled_action_ids(caps, "design_change", action)
    action["requiredActions"].append("tables.delete")
    assert disabled_action_ids(caps, "design_change", action) == ("tables.delete",)


def test_mixed_replay_preserves_both_individual_read_policies():
    caps = AiCapabilities(action_modes={"query.read": "automatic", "query.browse": "ask"})
    action = {"replayCapabilities": ["raw_sql_read", "structured_query"]}
    assert requires_approval(caps, "data_read", action)
    assert not disabled_action_ids(caps, "data_read", action)


def test_history_and_migration_are_independent():
    caps = AiCapabilities(action_modes={"history.undo": "automatic", "history.redo": "ask", "migration.review": "ask"})
    assert not requires_approval(caps, "design_history", {"direction": "undo"})
    assert requires_approval(caps, "design_history", {"direction": "redo"})
    assert disabled_action_ids(caps, "design_history", {"reset": True}) == ("history.reset",)
    assert requires_approval(caps, "migration_review", {})
    assert disabled_action_ids(caps, "migration_apply", {}) == ("migration.apply",)
