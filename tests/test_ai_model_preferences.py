from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from schemii.common.admin_config import AiPolicy
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiConflictError, InMemoryAiRepository, PostgresAiRepository


def conversation():
    repo = InMemoryAiRepository(AiPolicy(maximum_chats_per_workspace=1))
    caps = AiCapabilities(design_changes=True)
    chat = repo.create_chat("owner", "ws_" + "1" * 32, "Keep this title", "old", "model", caps)
    turn, _ = repo.create_turn("owner", chat.id, "Keep this question", None, 10, 10, 100)
    proposal = repo.create_proposal(
        "owner", chat.id, turn.id, "design_changes", "design_change", "Keep proposal", {},
        "a" * 64, 1, 1, False, datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return repo, chat, turn, proposal


@pytest.mark.parametrize("provider", ["old", "different-provider"])
def test_model_switch_preserves_conversation_and_pending_proposals_at_chat_limit(provider):
    repo, chat, turn, proposal = conversation()
    repo.finish_turn("owner", chat.id, turn.id, "Keep this answer")
    messages = repo.list_messages("owner", chat.id, 100)
    settings, saved, started_new = repo.save_preferences(
        "owner", chat.id, 1, 1, provider, "new-model", chat.capabilities,
    )
    assert not started_new
    assert saved.id == chat.id
    assert saved.title == chat.title
    assert saved.created_at == chat.created_at
    assert saved.revision == 2
    assert (saved.provider_id, saved.model_id) == (provider, "new-model")
    assert settings.default_model_id == "new-model"
    assert repo.list_messages("owner", chat.id, 100) == messages
    assert repo.list_proposals("owner", chat.id) == [proposal]
    assert len(repo.list_chats("owner")) == 1


def test_busy_model_switch_does_not_change_settings_chat_or_proposals():
    repo, chat, _, proposal = conversation()
    current = repo.get_chat("owner", chat.id)
    settings = repo.settings("owner")
    with pytest.raises(AiConflictError, match="finish"):
        repo.save_preferences("owner", chat.id, 1, 1, "other", "new", AiCapabilities())
    assert repo.settings("owner") == settings
    assert repo.get_chat("owner", chat.id) == current
    assert repo.list_proposals("owner", chat.id) == [proposal]


def test_permission_only_change_allowed_while_working_and_revokes_proposals():
    repo, chat, _, _ = conversation()
    _, saved, started_new = repo.save_preferences(
        "owner", chat.id, 1, 1, chat.provider_id, chat.model_id, AiCapabilities(),
    )
    assert not started_new
    assert saved.status == "working"
    assert repo.list_proposals("owner", chat.id)[0].status == "dismissed"


@pytest.mark.parametrize("settings_revision,chat_revision", [(0, 1), (1, 0)])
def test_model_change_preserves_optimistic_revision_checks(settings_revision, chat_revision):
    repo, chat, turn, _ = conversation()
    repo.finish_turn("owner", chat.id, turn.id, "Answer")
    with pytest.raises(AiConflictError):
        repo.save_preferences("owner", chat.id, settings_revision, chat_revision,
                              "new", "new", chat.capabilities)
    assert repo.settings("owner").revision == 1
    assert repo.get_chat("owner", chat.id).provider_id == "old"


@pytest.mark.parametrize("busy", [False, True])
def test_postgres_switch_updates_existing_row_or_rejects_busy_before_writes(busy):
    _, chat, _, _ = conversation()
    row = chat.model_dump()
    row["status"] = "working" if busy else "idle"
    updated = {**row, "revision": 2, "provider_id": "new", "model_id": "new"}
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [None, row, updated, {
        "revision": 2, "enabled": True, "default_provider_id": "new",
        "default_model_id": "new", "capabilities": chat.capabilities.model_dump(),
    }]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.transaction.return_value = nullcontext()
    repo = PostgresAiRepository(lambda: connection, AiPolicy(maximum_chats_per_workspace=1))
    repo._cleanup = lambda cursor: None
    if busy:
        with pytest.raises(AiConflictError, match="finish"):
            repo.save_preferences("owner", chat.id, 1, 1, "new", "new", chat.capabilities)
        connection.rollback.assert_called_once()
    else:
        _, saved, started_new = repo.save_preferences("owner", chat.id, 1, 1, "new", "new", chat.capabilities)
        assert not started_new
        assert saved.id == chat.id
        assert saved.title == chat.title
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any("INSERT INTO schemii.ai_chats" in statement for statement in statements)
    assert not any("count(*)" in statement for statement in statements)
    assert not any("ai_proposals" in statement for statement in statements)
    if busy:
        assert not any(statement.startswith(("UPDATE", "INSERT")) for statement in statements)


def test_diagnostic_preferences_roundtrip_chat_and_defaults_independently():
    from schemii.schemii.ai.models import SchemiiAiPreferencesUpdate, SchemiiAiSettings, SchemiiChat
    repo, chat, turn, _ = conversation()
    request = SchemiiAiPreferencesUpdate.model_validate({
        "expectedSettingsRevision": 1, "expectedChatRevision": chat.revision,
        "providerId": chat.provider_id, "modelId": chat.model_id,
        "capabilities": {"monitorQueries": True, "structuredDataRead": False,
            "actionModes": {"query.explain": "automatic", "query.analyze": "ask", "query.read": "disabled"}},
    })
    repo.save_preferences("owner", chat.id, request.expected_settings_revision,
        request.expected_chat_revision, request.provider_id, request.model_id, request.capabilities)
    settings = SchemiiAiSettings.model_validate_json(repo.settings("owner").model_dump_json(by_alias=True))
    current = SchemiiChat.model_validate_json(repo.get_chat("owner", chat.id).model_dump_json(by_alias=True))
    assert current.capabilities == settings.default_capabilities == request.capabilities
    for caps in [current.capabilities, settings.default_capabilities]:
        assert caps.explain_queries and not caps.explain_approval_required
        assert caps.analyze_queries and caps.analyze_approval_required
        assert caps.monitor_queries and not caps.structured_data_read
        assert not caps.raw_sql_read and caps.action_modes["query.read"] == "disabled"
    actions = {item["id"] for item in settings.permission_actions}
    assert {"query.explain", "query.analyze"} <= actions
