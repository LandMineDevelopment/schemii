from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.admin_config import AdminConfig
from schemii.common.ai.pi import PiError, PiReply
from schemii.common.api.errors import install_api_error_handlers
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiConflictError, InMemoryAiRepository
from schemii.schemii.ai.routes import router
from schemii.schemii.ai.service import AiService
from schemii.schemii.ai.tools import DESIGN_ACTION, TOOL_CAPABILITIES, tool_definitions, tool_enabled
from schemii.schemii.ai.action_policy import permission_descriptors


def setup_flow(callback, capabilities=None):
    owner = "alice"
    repo = InMemoryAiRepository()
    chat = repo.create_chat(owner, "ws_" + "a" * 32, "Streaming", "provider", "model",
                            capabilities or AiCapabilities())
    turn, _ = repo.create_turn(owner, chat.id, "Question", None, 4, 2, 100)
    cancelled = []
    runtime = SimpleNamespace(
        status=lambda owner: {"healthy": True, "providers": [{"id": "provider", "available": True,
                                                             "models": [{"id": "model", "status": "active"}]}]},
        run=callback, cancel=lambda owner, turn_id: cancelled.append((owner, turn_id)),
    )
    design = SimpleNamespace(revision=0, model_dump=lambda **kwargs: {"revision": 0, "tables": []})
    workspace = SimpleNamespace(revision=1, connection_id=None, namespace=None)
    service = AiService(repo, runtime, SimpleNamespace(admin_config=AdminConfig(),
        designs=SimpleNamespace(get=lambda *_: design), workspaces=SimpleNamespace(get=lambda *_: workspace)))
    return owner, repo, chat, turn, service, cancelled


def test_stream_is_owner_scoped_transient_and_only_saved_at_completion():
    def run(*args, **kwargs):
        kwargs["on_text"]("Partial ")
        kwargs["on_text"]("answer")
        assert all(message.role == "user" for message in repo.list_messages(owner, chat.id, 100))
        response = client.get(f"/ai/chats/{chat.id}/stream")
        assert response.json() == {"turnId": turn.id, "text": "Partial answer"}
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id="bob", authentication_source="local_prototype")
        assert client.get(f"/ai/chats/{chat.id}/stream").status_code == 404
        assert kwargs["is_authorized"]()
        return PiReply("Finished answer", ())

    owner, repo, chat, turn, service, _ = setup_flow(run)
    app = FastAPI()
    app.include_router(router)
    install_api_error_handlers(app)
    app.state.ai_service = service
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=owner, authentication_source="local_prototype")
    with TestClient(app) as client:
        service.run_turn(owner, chat.id, turn.id)
    assert repo.get_turn(owner, chat.id, turn.id).status == "succeeded"
    assert service.stream(owner, chat.id) == {}
    assert repo.list_messages(owner, chat.id, 100)[-1].text == "Finished answer"


def test_cancel_during_stream_stays_cancelled_and_discards_late_proposals():
    def run(*args, **kwargs):
        kwargs["on_text"]("Working")
        service.cancel_turn(owner, chat.id, turn.id)
        assert kwargs["is_authorized"]() is False
        # Simulate cancellation racing the adapter's final authority check.
        return PiReply("Late answer", (("schemii_read_query", {"summary": "Late query", "sql": "SELECT 1"}),))

    owner, repo, chat, turn, service, cancelled = setup_flow(run, AiCapabilities(raw_sql_read=True))
    service.run_turn(owner, chat.id, turn.id)
    assert repo.get_turn(owner, chat.id, turn.id).status == "cancelled"
    assert cancelled == [(owner, turn.id)]
    assert repo.list_proposals(owner, chat.id) == []
    assert all(message.role == "user" for message in repo.list_messages(owner, chat.id, 100))
    assert service.stream(owner, chat.id) == {}


def test_permission_callback_rejects_midstream_policy_change():
    def run(*args, **kwargs):
        kwargs["on_text"]("Provisional")
        assert kwargs["is_authorized"]()
        repo.update_chat_policy(owner, chat.id, chat.revision, AiCapabilities())
        assert not kwargs["is_authorized"]()
        raise PiError("permission_changed", status=409)

    owner, repo, chat, turn, service, _ = setup_flow(run, AiCapabilities(design_changes=True))
    service.run_turn(owner, chat.id, turn.id)
    assert repo.get_turn(owner, chat.id, turn.id).status == "failed"
    assert repo.get_turn(owner, chat.id, turn.id).error_code == "permission_changed"
    assert repo.list_proposals(owner, chat.id) == []
    assert service.stream(owner, chat.id) == {}
    assert all(message.role == "user" for message in repo.list_messages(owner, chat.id, 100))


def test_advertised_schema_is_derived_and_only_authorized_tools_are_present():
    assert tool_definitions(AiCapabilities()) == []
    full = tool_definitions(AiCapabilities(design_changes=True, raw_sql_read=True,
                                          raw_sql_write=True, structured_data_read=True,
                                          structured_query=True, design_history=True,
                                          migration_apply=True, sql_write_execute=True,
                                          explain_queries=True, analyze_queries=True,
                                          monitor_queries=True, raw_console=True, app_actions=True))
    assert {tool["name"] for tool in full} == set(TOOL_CAPABILITIES)
    derived = DESIGN_ACTION.json_schema()
    design = next(tool for tool in full if tool["name"] == "schemii_design_change")["parameters"]
    assert design["$defs"] == derived.pop("$defs")
    assert design["properties"]["action"] == derived
    for action in permission_descriptors():
        capabilities = AiCapabilities(action_modes={action["id"]: "ask"})
        names = {tool["name"] for tool in tool_definitions(capabilities)}
        assert names == {name for name in TOOL_CAPABILITIES if tool_enabled(name, capabilities)}
    review = {tool["name"] for tool in tool_definitions(AiCapabilities(action_modes={"migration.review": "automatic"}))}
    assert review == {"schemii_review_migration", "schemii_get_migration_plan", "schemii_migration_status"}


def test_repository_cancel_dismisses_pending_and_atomically_rejects_late_proposal():
    owner, repo, chat, turn, _, _ = setup_flow(lambda *_: None, AiCapabilities(raw_sql_read=True))
    repo.claim_turn(owner, chat.id, turn.id)

    def propose():
        return repo.create_proposal(owner, chat.id, turn.id, "raw_sql_read", "data_read",
            "Read one", {"sql": "SELECT 1"}, "a" * 64, 1, 0, False,
            datetime.now(timezone.utc) + timedelta(minutes=5), chat.revision)

    pending = propose()
    assert pending.status == "pending"
    repo.cancel_turn(owner, chat.id, turn.id)
    proposals = repo.list_proposals(owner, chat.id)
    assert len(proposals) == 1
    assert proposals[0].id == pending.id
    assert proposals[0].status == "dismissed"
    assert proposals[0].revision == pending.revision + 1
    with pytest.raises(AiConflictError, match="no longer active"):
        propose()
    assert len(repo.list_proposals(owner, chat.id)) == 1
