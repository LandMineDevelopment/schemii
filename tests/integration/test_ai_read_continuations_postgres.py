"""Exercise read-continuation constraints against the migrated PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from schemii.common.admin_config import AiPolicy
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiCapacityError, AiConflictError, AiNotFoundError, PostgresAiRepository
from schemii.schemii.workspaces.models import WorkspaceCreateRecord
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository


def setup_chat(harness, policy=None):
    workspace = PostgresWorkspaceRepository(harness.connection_factory).create(
        harness.owner_id, WorkspaceCreateRecord(name="AI continuation integration"))
    repo = PostgresAiRepository(harness.connection_factory, policy)
    chat = repo.create_chat(harness.owner_id, workspace.id, "Reads", "provider", "model", AiCapabilities(raw_sql_read=True))
    return repo, chat


def test_postgres_waiting_read_resume_is_atomic_and_history_cascades(postgres_metadata):
    harness = postgres_metadata
    owner = harness.owner_id
    repo, chat = setup_chat(harness)
    turn, _ = repo.create_turn(owner, chat.id, "Read", None, 1000, 2, 100)
    repo.claim_turn(owner, chat.id, turn.id)
    proposal = repo.create_proposal(owner, chat.id, turn.id, "raw_sql_read", "data_read", "Read", {"sql": "SELECT 1"}, "a" * 64, 1, 0, False, datetime.now(timezone.utc) + timedelta(minutes=5))
    operation = repo.create_operation(owner, chat.id, proposal.id, "data_read")
    run = repo.create_read_run(owner, chat.id, turn.id, operation.id, "SELECT 1", "Count", "execution", "result", 1, False)
    repo.save_continuation(owner, chat.id, turn.id, {"pendingProposalIds": [proposal.id], "runIds": [run.id]})
    repo.pause_turn(owner, chat.id, turn.id)
    # New adapter models process replacement without disturbing other users' work.
    repo = PostgresAiRepository(harness.connection_factory)
    assert repo.get_turn(owner, chat.id, turn.id).status == "waiting_approval"
    assert repo.continuation(owner, chat.id, turn.id)["runIds"] == [run.id]
    with pytest.raises(AiConflictError):
        repo.create_turn(owner, chat.id, "Second", None, 1000, 2, 100)
    with pytest.raises(AiNotFoundError):
        repo.get_read_run("another-owner", chat.id, run.id)
    with pytest.raises(ValueError, match="cannot contain query result rows"):
        repo.save_continuation(owner, chat.id, turn.id, {"result": {"rows": [["private"]]}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        resumed = list(pool.map(lambda _: repo.resume_turn(owner, chat.id, turn.id), range(4)))
    assert sum(item is not None for item in resumed) == 1
    repo.claim_turn(owner, chat.id, turn.id)
    repo.finish_turn(owner, chat.id, turn.id, "Done")
    repo.create_turn(owner, chat.id, "Next", None, 1000, 2, 1)
    assert repo.list_read_runs(owner, chat.id) == []
    with harness.connection_factory() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) AS count FROM schemii.ai_turn_continuations WHERE turn_id=%s", (turn.id,))
        assert cursor.fetchone()["count"] == 0


def test_postgres_resume_respects_owner_capacity_and_can_retry(postgres_metadata):
    owner = postgres_metadata.owner_id
    policy = AiPolicy(maximum_concurrent_turns=1000, maximum_concurrent_turns_per_user=1)
    repo, first = setup_chat(postgres_metadata, policy)
    second = repo.create_chat(owner, first.workspace_id, "Other", "provider", "model", AiCapabilities())
    waiting, _ = repo.create_turn(owner, first.id, "Read", None, 1000, 1, 100)
    repo.claim_turn(owner, first.id, waiting.id)
    repo.pause_turn(owner, first.id, waiting.id)
    active, _ = repo.create_turn(owner, second.id, "Busy", None, 1000, 1, 100)
    with pytest.raises(AiCapacityError) as error:
        repo.resume_turn(owner, first.id, waiting.id)
    assert error.value.limit_name == "maximum_concurrent_turns_per_user"
    assert repo.get_turn(owner, first.id, waiting.id).status == "waiting_approval"
    repo.cancel_turn(owner, second.id, active.id)
    assert repo.resume_turn(owner, first.id, waiting.id).status == "queued"
    repo.cancel_turn(owner, first.id, waiting.id)
    assert repo.get_chat(owner, first.id).status == "idle"
