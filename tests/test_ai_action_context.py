import json
from types import SimpleNamespace

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.action_context import action_context, intent_key
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.designs.store import InMemoryDesignRepository


def test_pending_batch_is_reused_and_action_context_distinguishes_draft():
    owner, workspace = "owner", "ws_" + "a" * 32
    table, column = "table_" + "b" * 32, "column_" + "c" * 32
    repo, designs = InMemoryAiRepository(), InMemoryDesignRepository()
    designs.replace(owner, workspace, SchemiiDesignReplace(expected_design_revision=0,
        content=SchemiiDesignContent.model_validate({"tables": [{"id": table, "name": "items",
            "columns": [{"id": column, "name": "code", "dataType": "text"}]}]})))
    chat = repo.create_chat(owner, workspace, "Batch", "provider", "model",
        AiCapabilities(design_changes=True, raw_sql_write=True))
    turn, _ = repo.create_turn(owner, chat.id, "Index it", None, 4, 2, 100)
    service = AiService(repo, None, SimpleNamespace(admin_config=AdminConfig(), designs=designs,
        workspaces=SimpleNamespace(get=lambda *_: SimpleNamespace(revision=1))))
    args = {"summary": "Add indexes once", "action": {"type": "batch", "actions": [
        {"type": "put_table_member", "table_id": table, "collection": "indexes",
         "object": {"name": "items_code_idx", "columnIds": [column]}}]}}
    first = service._save_tool_proposal(owner, chat, turn.id, "schemii_design_change", args)
    second = service._save_tool_proposal(owner, chat, turn.id, "schemii_design_change", args)
    assert first.id == second.id
    assert len(repo.list_proposals(owner, chat.id)) == 1
    draft = service._save_tool_proposal(owner, chat, turn.id, "schemii_open_console",
        {"summary": "SQL draft", "sql": "SELECT 1"})
    operation = service.execute(owner, chat.id, draft.id, SimpleNamespace(
        expected_chat_revision=chat.revision, expected_proposal_revision=draft.revision,
        proposal_digest=draft.digest))
    assert operation.result_summary["sqlExecuted"] is False
    context = action_context(repo, owner, chat.id, 16000)
    assert context["omittedCount"] == 0
    assert context["entries"][0]["status"] == "pending"
    assert context["entries"][1]["operation"]["resourceKind"] == "consoleDraft"
    assert "No SQL is executed" in context["entries"][1]["successMeaning"]
    assert len(json.dumps(context)) < 16000
    assert action_context(repo, owner, chat.id, 1)["omittedCount"] == 2


def test_intent_comparison_preserves_existing_identity_and_references():
    assert intent_key({"id": "new_a", "column_ids": ["column_a"]}, set()) == intent_key(
        {"id": "new_b", "column_ids": ["column_a"]}, set())
    assert intent_key({"id": "existing"}, {"existing"}) != intent_key({"id": "new"}, {"existing"})
