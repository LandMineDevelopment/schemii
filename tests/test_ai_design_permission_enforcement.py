"""Granular authority is enforced at proposal creation and again at execution."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.designs.store import InMemoryDesignRepository


TABLE, COLUMN, INDEX = [kind + "_" + letter * 32 for kind, letter in
                        [("table", "a"), ("column", "b"), ("index", "c")]]


def fixture(modes):
    repository, designs = InMemoryAiRepository(), InMemoryDesignRepository()
    workspace_id = "ws_" + "d" * 32
    designs.replace("owner", workspace_id, SchemiiDesignReplace(expected_design_revision=0,
        content=SchemiiDesignContent(tables=[{"id": TABLE, "name": "items",
            "columns": [{"id": COLUMN, "name": "id", "data_type": "integer"}],
            "indexes": [{"id": INDEX, "name": "items_id_idx", "column_ids": [COLUMN]}]}])))
    chat = repository.create_chat("owner", workspace_id, "Permissions", "provider", "model",
                                  AiCapabilities(action_modes=modes))
    turn, _ = repository.create_turn("owner", chat.id, "Edit indexes", None, 4, 2, 100)
    services = NS(admin_config=AdminConfig(), designs=designs,
                  workspaces=NS(get=lambda *_: NS(revision=1)))
    return AiService(repository, None, services), chat, turn


def delete_index_by_replacement(service, chat):
    table = service.services.designs.get("owner", chat.workspace_id).content.tables[0].model_dump()
    table["indexes"] = []
    return {"type": "put_top_level_object", "collection": "tables", "object": table}


def save(service, chat, turn, action):
    return service._save_tool_proposal("owner", chat, turn.id, "schemii_design_change", {"action": action})


def approval(chat, proposal):
    return NS(expected_chat_revision=chat.revision, expected_proposal_revision=proposal.revision,
              proposal_digest=proposal.digest, confirmed=True, proposal_id=proposal.id)


def test_whole_table_replacement_cannot_bypass_disabled_index_deletion():
    service, chat, turn = fixture({"tables.update": "automatic", "indexes.create": "automatic"})
    with pytest.raises(AiServiceError) as error:
        save(service, chat, turn, delete_index_by_replacement(service, chat))
    assert error.value.details == {"requiredActions": ("indexes.delete",)}
    assert "indexes.delete" in str(error.value)
    assert service.repository.list_proposals("owner", chat.id) == []
    assert len(service.services.designs.get("owner", chat.workspace_id).content.tables[0].indexes) == 1


def test_proposal_records_only_actual_effects_not_put_table_verb():
    service, chat, turn = fixture({"indexes.delete": "ask"})
    proposal = save(service, chat, turn, delete_index_by_replacement(service, chat))
    action = service.repository.proposal_action("owner", chat.id, proposal.id)
    assert action["requiredActions"] == ["indexes.delete"]
    result = service.execute("owner", chat.id, proposal.id, approval(chat, proposal))
    assert result.status == "succeeded"
    assert service.services.designs.get("owner", chat.workspace_id).content.tables[0].indexes == []


@pytest.mark.parametrize("marker", [None, [], ["tables.update"]])
def test_execution_rederives_legacy_missing_or_incorrect_effect_marker(marker):
    service, chat, turn = fixture({"tables.update": "automatic"})
    action = delete_index_by_replacement(service, chat)
    if marker is not None:
        action["requiredActions"] = marker
    proposal = service.repository.create_proposal("owner", chat.id, turn.id, "design_changes",
        "design_change", "Replace table", action, "a" * 64, 1, 1, False,
        datetime.now(timezone.utc) + timedelta(minutes=5))
    with pytest.raises(AiServiceError, match="indexes.delete"):
        service.execute("owner", chat.id, proposal.id, approval(chat, proposal))
    assert service.repository.list_operations("owner", chat.id) == []


def test_batch_preflight_denies_later_disabled_effect_before_first_action():
    service, chat, turn = fixture({"tables.update": "automatic"})
    rename = save(service, chat, turn, {"type": "rename_table", "table_id": TABLE, "name": "renamed"})
    blocked = service.repository.create_proposal("owner", chat.id, turn.id, "design_changes",
        "design_change", "Replace table", delete_index_by_replacement(service, chat), "a" * 64,
        1, 1, False, datetime.now(timezone.utc) + timedelta(minutes=5))
    service.execute = Mock()
    with pytest.raises(AiServiceError, match="indexes.delete"):
        service.execute_batch("owner", chat.id, NS(items=[approval(chat, rename), approval(chat, blocked)]))
    service.execute.assert_not_called()


def test_pending_dedup_normalizes_server_only_effect_metadata():
    service, chat, turn = fixture({"indexes.delete": "ask"})
    action = delete_index_by_replacement(service, chat)
    first = save(service, chat, turn, action)
    second = save(service, chat, turn, action)
    assert first.id == second.id


def test_create_and_remove_same_new_index_requires_no_effect_permission():
    service, chat, turn = fixture({"tables.update": "automatic"})
    new_id = "index_" + "f" * 32
    proposal = save(service, chat, turn, {"type": "batch", "actions": [
        {"type": "put_table_member", "table_id": TABLE, "collection": "indexes",
         "object": {"id": new_id, "name": "new_idx", "column_ids": [COLUMN]}},
        {"type": "delete_object", "object_id": new_id},
    ]})
    assert service.repository.proposal_action("owner", chat.id, proposal.id)["requiredActions"] == []
