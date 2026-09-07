from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from schemii.schemii.ai.models import AiCapabilities
from schemii.common.admin_config import AiPolicy
from schemii.schemii.ai.repository import AiCapacityError, AiConflictError, AiNotFoundError, InMemoryAiRepository


def setup_turn():
    repo = InMemoryAiRepository()
    chat = repo.create_chat("owner", "ws_" + "a" * 32, "Reads", "provider", "model", AiCapabilities(raw_sql_read=True))
    turn, _ = repo.create_turn("owner", chat.id, "Compare", None, 4, 2, 100)
    repo.claim_turn("owner", chat.id, turn.id)
    return repo, chat, turn


def test_waiting_read_preserves_request_and_resumes_exactly_once():
    repo, chat, turn = setup_turn()
    repo.save_continuation("owner", chat.id, turn.id, {"tool_call_id": "call-1", "queries": [{"sql": "SELECT 1"}]})
    repo.pause_turn("owner", chat.id, turn.id)
    with pytest.raises(AiConflictError):
        repo.create_turn("owner", chat.id, "Other", None, 4, 2, 100)
    repo.recover_interrupted()
    assert repo.get_turn("owner", chat.id, turn.id).status == "waiting_approval"
    assert repo.continuation("owner", chat.id, turn.id)["tool_call_id"] == "call-1"
    with ThreadPoolExecutor(max_workers=4) as pool:
        resumed = list(pool.map(lambda _: repo.resume_turn("owner", chat.id, turn.id), range(4)))
    assert sum(result is not None for result in resumed) == 1
    assert repo.claim_turn("owner", chat.id, turn.id).status == "running"


def test_cancel_waiting_read_releases_chat_and_protects_continuation_ownership():
    repo, chat, turn = setup_turn()
    repo.pause_turn("owner", chat.id, turn.id)
    with pytest.raises(AiNotFoundError):
        repo.save_continuation("other", chat.id, turn.id, {})
    with pytest.raises(ValueError, match="cannot contain query result rows"):
        repo.save_continuation("owner", chat.id, turn.id, {"tool": {"result_rows": [["secret"]]}})
    cancelled, _ = repo.cancel_turn("owner", chat.id, turn.id)
    assert cancelled.status == "cancelled"
    assert repo.resume_turn("owner", chat.id, turn.id) is None
    assert repo.create_turn("owner", chat.id, "New", None, 4, 2, 100)[0].status == "queued"


def test_read_runs_are_scoped_references_and_expire_with_history():
    repo, chat, turn = setup_turn()
    proposal = repo.create_proposal("owner", chat.id, turn.id, "raw_sql_read", "data_read", "Read", {"sql": "SELECT 1"}, "a" * 64, 1, 0, False, datetime.now(timezone.utc) + timedelta(minutes=5))
    operation = repo.create_operation("owner", chat.id, proposal.id, "data_read")
    run = repo.create_read_run("owner", chat.id, turn.id, operation.id, "SELECT 1", "First", "execution-1", "result-1", 1, False)
    rerun = repo.create_read_run("owner", chat.id, turn.id, operation.id, "SELECT 1", "Again", "execution-2", "result-2", 1, False, run.id)
    assert repo.list_read_runs("owner", chat.id) == [rerun, run]
    assert "rows" not in run.model_dump()
    with pytest.raises(AiNotFoundError):
        repo.get_read_run("other", chat.id, run.id)
    repo.save_continuation("owner", chat.id, turn.id, {"run_ids": [run.id]})
    repo.finish_turn("owner", chat.id, turn.id, "Completed")
    repo.create_turn("owner", chat.id, "Next", None, 4, 2, 1)
    assert repo.list_read_runs("owner", chat.id) == []
    assert turn.id not in repo._continuations


def test_read_approval_defaults_to_required_for_old_capability_documents():
    assert AiCapabilities.model_validate({"rawSqlRead": True}).read_approval_required


@pytest.mark.parametrize("total,expected_limit", [(2, "maximum_concurrent_turns_per_user"), (1, "maximum_concurrent_turns")])
def test_resume_capacity_is_rechecked_and_request_stays_retryable(total, expected_limit):
    policy = AiPolicy(maximum_concurrent_turns=total, maximum_concurrent_turns_per_user=1)
    repo = InMemoryAiRepository(policy)
    first = repo.create_chat("owner", "ws_" + "b" * 32, "First", "p", "m", AiCapabilities())
    second = repo.create_chat("owner", first.workspace_id, "Second", "p", "m", AiCapabilities())
    waiting, _ = repo.create_turn("owner", first.id, "Read", None, total, 1, 100)
    repo.claim_turn("owner", first.id, waiting.id)
    repo.pause_turn("owner", first.id, waiting.id)
    busy, _ = repo.create_turn("owner", second.id, "Read", None, total, 1, 100)
    with pytest.raises(AiCapacityError) as error:
        repo.resume_turn("owner", first.id, waiting.id)
    assert error.value.limit_name == expected_limit
    assert repo.get_turn("owner", first.id, waiting.id).status == "waiting_approval"
    repo.cancel_turn("owner", second.id, busy.id)
    assert repo.resume_turn("owner", first.id, waiting.id).status == "queued"
