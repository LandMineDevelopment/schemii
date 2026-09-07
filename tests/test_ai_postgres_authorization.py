"""Persisted capability aliases admit the same tools as in-memory policy."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock

import pytest

from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiConflictError, PostgresAiRepository
from schemii.schemii.ai.tools import TOOL_CAPABILITIES


PERMISSIONS = sorted(set(TOOL_CAPABILITIES.values()))


def repository_fixture(capability, enabled, *, revision=3):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    factory = Mock(return_value=connection)
    repository = PostgresAiRepository(factory)
    capabilities = AiCapabilities(**{capability: enabled}).model_dump(mode="json", by_alias=True)
    proposal = {"action_type": "design_change", "action": {"type": "batch", "actions": []}}
    cursor.fetchone.side_effect = [
        {"revision": revision, "capabilities": capabilities}, proposal, {"id": "operation"},
    ]
    repository._operation = Mock(return_value="operation")
    repository._proposal = Mock(return_value="proposal")
    return repository, factory, connection, cursor, proposal


def begin(repository, capability):
    return repository.begin_operation("owner", "chat", "proposal", 1, "digest", 3,
                                      capability, datetime.now(timezone.utc))


@pytest.mark.parametrize("capability", PERMISSIONS)
def test_every_tool_permission_admits_from_actual_persisted_json_alias(capability):
    repository, _, connection, cursor, proposal = repository_fixture(capability, True)
    assert begin(repository, capability) == ("operation", "proposal", proposal["action"])
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(statements) == 3
    assert "FOR UPDATE" in statements[0]
    assert "UPDATE schemii.ai_proposals" in statements[1]
    assert "INSERT INTO schemii.ai_operations" in statements[2]
    assert cursor.execute.call_args_list[0].args[1] == ("owner", "chat")
    assert cursor.execute.call_args_list[1].args[1][:5] == ("owner", "chat", "proposal", 1, "digest")
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("capability", PERMISSIONS)
def test_every_disabled_tool_permission_rejects_before_proposal_mutation(capability):
    repository, _, connection, cursor, _ = repository_fixture(capability, False)
    with pytest.raises(AiConflictError, match="required permission"):
        begin(repository, capability)
    assert cursor.execute.call_count == 1
    assert "SELECT revision,capabilities" in cursor.execute.call_args.args[0]
    repository._operation.assert_not_called()
    repository._proposal.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_policy_revision_change_rejects_even_when_capability_remains_enabled():
    capability = "migration_apply"
    repository, _, _, cursor, _ = repository_fixture(capability, True, revision=4)
    with pytest.raises(AiConflictError, match="permissions changed"):
        begin(repository, capability)
    assert cursor.execute.call_count == 1


def test_unknown_permission_never_opens_database_transaction():
    repository, factory, _, cursor, _ = repository_fixture("design_changes", True)
    with pytest.raises(AiConflictError, match="unknown permission"):
        begin(repository, "invented_permission")
    factory.assert_not_called()
    cursor.execute.assert_not_called()


def test_locked_action_policy_blocks_disabled_delete_despite_enabled_index_create():
    repository, _, connection, cursor, proposal = repository_fixture("design_changes", True)
    caps = AiCapabilities(action_modes={"indexes.create": "automatic"})
    cursor.fetchone.side_effect = [
        {"revision": 3, "capabilities": caps.model_dump(by_alias=True)}, proposal,
    ]
    with pytest.raises(AiConflictError, match="action permission is disabled"):
        repository.begin_operation("owner", "chat", "proposal", 1, "digest", 3,
                                   "design_changes", datetime.now(timezone.utc),
                                   required_actions=["indexes.create", "tables.delete"])
    assert not any("INSERT INTO schemii.ai_operations" in call.args[0] for call in cursor.execute.call_args_list)
    connection.rollback.assert_called_once()


def test_persisted_action_map_is_authoritative_without_legacy_flags():
    repository, _, _, cursor, proposal = repository_fixture("design_changes", True)
    cursor.fetchone.side_effect = [
        {"revision": 3, "capabilities": {"actionModes": {"indexes.create": "automatic"}}},
        proposal, {"id": "operation"},
    ]
    assert repository.begin_operation("owner", "chat", "proposal", 1, "digest", 3,
                                      "design_changes", datetime.now(timezone.utc),
                                      required_actions=["indexes.create"])[0] == "operation"
