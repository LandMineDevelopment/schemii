"""Limits are visible in metadata across HTTP, tool and background boundaries."""
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from schemii.common.admin_config import AiPolicy
from schemii.common.ai.limits import limit_notice, record_ai_limit
from schemii.common.ai.pi import PiError
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.limit_events import InMemoryLimitEventRecorder, LimitEventNotice
from schemii.schemii.ai.repository import AiCapacityError
from schemii.schemii.ai.routes import _call, _resume
from schemii.schemii.ai.service import AiServiceError
from schemii.schemii.ai.models import SchemiiMessageCreate
from schemii.schemoo.ai_routes import Message
from test_schemoo_ai_conversations import setup, send


@pytest.mark.parametrize("code,name", [
    ("context_too_large", "context_bytes"), ("response_too_large", "response_bytes"),
    ("timeout", "provider_timeout_seconds"), ("ai_prompt_too_large", "prompt_bytes"),
    ("ai_read_batch_limit", "maximum_read_queries_per_batch"),
    ("ai_proposal_limit_reached", "maximum_proposals_per_turn"),
    ("ai_tool_round_limit", "maximum_tool_rounds"),
])
def test_known_ai_limits_record_once_without_diagnostics(code, name):
    policy = AiPolicy()
    error = PiError(code, "private query SELECT secret FROM credentials")
    recorder = InMemoryLimitEventRecorder()
    record_ai_limit(recorder, error, policy, "alice")
    record_ai_limit(recorder, error, policy, "alice")
    assert len(recorder.events()) == 1
    event = recorder.events()[0]
    assert event.notice.limit_name == "ai." + name
    assert event.notice.configured_limit == getattr(policy, name)
    assert "secret" not in repr(asdict(event))
    assert event.owner_id == "alice"


def test_recording_failure_preserves_actionable_error_and_never_logs_diagnostics(caplog):
    recorder = Mock()
    recorder.record.side_effect = RuntimeError("private storage diagnostic")
    error = PiError("context_too_large")
    record_ai_limit(recorder, error, AiPolicy(), "alice")
    assert "Start a new chat" in str(error)
    assert "could not be recorded" in caplog.text
    assert "private" not in caplog.text


def test_unknown_provider_error_is_not_mislabelled_as_config_limit():
    assert limit_notice(PiError("rate_limited"), AiPolicy()) is None
    assert limit_notice(PiError("provider_failed"), AiPolicy()) is None


def test_schemii_http_service_limit_keeps_notice_for_shared_handler():
    class Service:
        policy = AiPolicy()

        def send(self):
            raise AiServiceError(413, "ai_prompt_too_large", "Shorten the prompt")

    with pytest.raises(ApiProblem) as caught:
        _call(Service().send)
    assert caught.value.limit_event.limit_name == "ai.prompt_bytes"


def test_prompt_models_do_not_override_admin_budget_with_fixed_character_limit():
    text = "x" * 70_000
    assert Message(text=text, expectedRevision=1).text == text
    assert SchemiiMessageCreate(text=text, expected_chat_revision=1,
        expected_design_revision=1).text == text


def test_schemii_auto_resume_capacity_is_logged_without_replaying_resolved_actions():
    recorder = InMemoryLimitEventRecorder()
    service = SimpleNamespace(policy=AiPolicy(),
        services=SimpleNamespace(metadata=SimpleNamespace(limit_events=recorder)),
        repository=Mock(), ready_continuation=Mock(side_effect=AiCapacityError(
            "Wait for a turn to finish", limit_name="maximum_concurrent_turns_per_user",
            configured_limit=2, observed_value=2)))
    tasks = Mock()
    _resume(service, "alice", "chat", tasks)
    tasks.add_task.assert_not_called()
    assert len(recorder.events()) == 1
    assert recorder.events()[0].notice.limit_name == "ai.maximum_concurrent_turns_per_user"
    assert "Use Continue" in service.repository.add_event.call_args.args[-1]["message"]


def test_schemoo_chat_and_document_limit_errors_carry_numeric_notices():
    conversations, store, _, _, chat = setup([], policy=replace(AiPolicy(), maximum_chats_per_workspace=1))
    with pytest.raises(ApiProblem) as caught:
        conversations.create("alice", {"modelId": chat["modelId"], "providerId": "openai", "aiModelId": "test"})
    assert caught.value.limit_event == LimitEventNotice("schemoo_ai_chat", "ai.maximum_chats_per_workspace", 1, 1)
    with pytest.raises(ApiProblem) as caught:
        store.update("alice", chat["id"], lambda value: value.update(
            pending={"private-model-data": "x" * (store.policy.context_bytes + store.policy.proposal_bytes_per_turn)}))
    assert caught.value.limit_event.configured_limit == store.policy.context_bytes + store.policy.proposal_bytes_per_turn
    assert "private-model-data" not in repr(caught.value.limit_event)
    assert store.get("alice", chat["id"])["pending"] is None


def test_schemoo_background_runtime_limit_is_recorded_and_shown():
    conversations, _, _, _, chat = setup([PiError("context_too_large")])
    recorder = InMemoryLimitEventRecorder()
    conversations.services.metadata.limit_events = recorder
    finished = send(conversations, chat)
    assert finished["status"] == "failed"
    assert "Start a new chat" in finished["error"]
    assert len(recorder.events()) == 1
    assert recorder.events()[0].notice.limit_name == "ai.context_bytes"
