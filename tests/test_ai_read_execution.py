"""Read tools exercise the real service; only the Console execution boundary is fake."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from schemii.common.admin_config import AdminConfig
from schemii.common.postgres.console.models import ConsoleResultColumn, ConsoleResultPage
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService


class ReleasedResult(RuntimeError):
    code = "console_result_replay_required"


def test_unknown_read_reference_provides_safe_lookup_guidance(reads):
    with pytest.raises(Exception) as error:
        reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results", {"runIds": ["wrong-id"]}, reads.turn.id)
    assert error.value.code == "ai_read_run_not_found"
    assert "schemii_list_read_runs" in str(error.value)
    assert "wrong-id" not in str(error.value)


class ReadConsole:
    def __init__(self):
        self.requests = []
        self.released = set()

    def settings(self, owner):
        return SimpleNamespace(revision=1)

    def reserve(self, owner, workspace, request):
        self.requests.append(request)
        return SimpleNamespace(id=f"cex_{len(self.requests):032x}")

    def run(self, owner, execution):
        pass

    def get(self, owner, workspace, execution):
        return SimpleNamespace(id=execution, status="succeeded", results=[SimpleNamespace(
            id="res_" + execution[4:], row_count=1, has_more=False,
        )])

    def page(self, owner, workspace, execution, result, cursor):
        if execution in self.released:
            raise ReleasedResult()
        return ConsoleResultPage(execution_id=execution, result_id=result,
            columns=[ConsoleResultColumn(name="value", data_type="text")],
            rows=[["PRIVATE_RESULT_SENTINEL", int(execution[4:], 16)]],
            next_cursor=None, truncated=False,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))


@pytest.fixture
def reads():
    repo = InMemoryAiRepository()
    chat = repo.create_chat("owner", "ws_" + "a" * 32, "Read tools", "provider", "model",
        AiCapabilities(raw_sql_read=True, structured_data_read=True))
    turn, _ = repo.create_turn("owner", chat.id, "Compare both", None, 4, 2, 100)
    repo.claim_turn("owner", chat.id, turn.id)
    console = ReadConsole()
    services = SimpleNamespace(admin_config=AdminConfig(), console=console,
        workspaces=SimpleNamespace(get=lambda *_: SimpleNamespace(revision=1)),
        designs=SimpleNamespace(get=lambda *_: SimpleNamespace(revision=0)))
    return SimpleNamespace(repo=repo, chat=chat, turn=turn, console=console,
        service=AiService(repo, None, services))


def proposal(reads):
    return reads.service._save_tool_proposal("owner", reads.chat, reads.turn.id,
        "schemii_read_query", {"summary": "Compare two reads", "queries": [
            {"label": "Before", "sql": "SELECT 1 AS value"},
            {"label": "After", "sql": "SELECT 2 AS value"},
        ]})


def approve(reads, item):
    current = reads.repo.get_chat("owner", reads.chat.id)
    return reads.service.execute("owner", reads.chat.id, item.id,
        SimpleNamespace(expected_chat_revision=current.revision,
            expected_proposal_revision=item.revision, proposal_digest=item.digest))


def test_batched_read_returns_grouped_rows_and_only_persists_references(reads):
    operation = approve(reads, proposal(reads))
    assert [request.statements for request in reads.console.requests] == [["SELECT 1 AS value"], ["SELECT 2 AS value"]]
    assert all(request.mode == "managed_read" for request in reads.console.requests)
    result = reads.service._operation_read_context("owner", reads.chat.id, operation.id)
    assert result["authorization"] == {"mode": "user_approved",
        "policyRevision": reads.chat.revision, "proposalId": operation.proposal_id}
    assert result["results"][0]["authorization"] == result["authorization"]
    assert [entry["label"] for entry in result["results"]] == ["Before", "After"]
    assert [entry["rows"][0][1] for entry in result["results"]] == [1, 2]
    browser_result = reads.service.query_result("owner", reads.chat.id, operation.id, None)
    assert [entry["label"] for entry in browser_result.results] == ["Before", "After"]
    runs = reads.repo.list_read_runs("owner", reads.chat.id)
    assert len(runs) == 2
    persisted = json.dumps({"runs": [run.model_dump(mode="json") for run in runs],
        "operation": reads.repo.get_operation("owner", reads.chat.id, operation.id).model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in reads.repo.activity("owner", reads.chat.id, 0)]})
    assert "PRIVATE_RESULT_SENTINEL" not in persisted


def test_compare_retained_runs_returns_one_response_without_execution(reads):
    operation = approve(reads, proposal(reads))
    ids = [entry["runId"] for entry in operation.result_summary["results"]]
    compared = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results",
        {"runIds": list(reversed(ids))}, reads.turn.id)
    assert [entry["runId"] for entry in compared["results"]] == list(reversed(ids))
    assert len(reads.console.requests) == 2


def test_partial_batch_preserves_success_and_does_not_store_database_error_values(reads):
    original = reads.console.get
    def get(owner, workspace, execution):
        if execution.endswith("2"):
            raise RuntimeError("PRIVATE_ERROR_VALUE")
        return original(owner, workspace, execution)
    reads.console.get = get
    operation = approve(reads, proposal(reads))
    result = reads.service._operation_read_context("owner", reads.chat.id, operation.id)
    assert result["results"][0]["rows"]
    assert result["results"][1]["errorCode"] == "ai_query_failed"
    assert "PRIVATE_ERROR_VALUE" not in operation.model_dump_json()


def test_batch_shares_result_sample_budget_fairly(reads):
    from dataclasses import replace
    from schemii.schemii.ai.reads import _bounded_bundle
    reads.service.policy = replace(reads.service.policy, result_context_rows=4)
    bundle = _bounded_bundle(reads.service, [{"rows": [[n] for n in range(8)]}, {"rows": [[n] for n in range(8)]}])
    assert [len(result["rows"]) for result in bundle["results"]] == [2, 2]
    assert all(result["sampled"] for result in bundle["results"])


def test_released_runs_require_approval_and_resume_after_inline_approval(reads):
    operation = approve(reads, proposal(reads))
    ids = [entry["runId"] for entry in operation.result_summary["results"]]
    reads.console.released.update(entry["executionId"] for entry in operation.result_summary["results"])
    reply = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results", {"runIds": ids}, reads.turn.id)
    assert reply["approvalRequired"] is True
    assert len(reads.console.requests) == 2
    pending = reads.repo.get_proposal("owner", reads.chat.id, reply["proposalId"])
    reads.repo.save_continuation("owner", reads.chat.id, reads.turn.id, {"pendingProposalIds": [pending.id]})
    reads.repo.pause_turn("owner", reads.chat.id, reads.turn.id)
    assert reads.service.ready_continuation("owner", reads.chat.id) is None
    replay = approve(reads, pending)
    assert reads.service.ready_continuation("owner", reads.chat.id).id == reads.turn.id
    assert reads.service.ready_continuation("owner", reads.chat.id) is None
    assert len(reads.console.requests) == 4
    result = reads.service._operation_read_context("owner", reads.chat.id, replay.id)
    assert all(entry["rerun"] and "data may have changed" in entry["freshnessNotice"] for entry in result["results"])


def test_disabled_read_permission_cannot_replay_released_results(reads):
    operation = approve(reads, proposal(reads))
    ids = [entry["runId"] for entry in operation.result_summary["results"]]
    reads.console.released.update(entry["executionId"] for entry in operation.result_summary["results"])
    reads.repo.update_chat_policy("owner", reads.chat.id, reads.chat.revision,
        AiCapabilities(structured_data_read=True, raw_sql_read=False, read_approval_required=False))
    reply = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results", {"runIds": ids}, reads.turn.id)
    assert reply["permissionRequired"] == "Run read SQL"
    assert len(reads.console.requests) == 2
    displayed = reads.service.query_result("owner", reads.chat.id, operation.id, None)
    assert all(entry["released"] for entry in displayed.results)
    assert len(reads.console.requests) == 2


def test_released_runs_reexecute_automatically_when_approval_disabled(reads):
    operation = approve(reads, proposal(reads))
    ids = [entry["runId"] for entry in operation.result_summary["results"]]
    reads.console.released.update(entry["executionId"] for entry in operation.result_summary["results"])
    reads.repo.update_chat_policy("owner", reads.chat.id, reads.chat.revision,
        AiCapabilities(structured_data_read=True, raw_sql_read=True, read_approval_required=False))
    reply = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results", {"runIds": ids}, reads.turn.id)
    assert len(reply["results"]) == 2
    assert all(entry["rerun"] for entry in reply["results"])
    assert all(entry["authorization"]["mode"] == "automatic_read_policy" for entry in reply["results"])
    original = reads.service._operation_read_context("owner", reads.chat.id, operation.id)
    assert original["authorization"]["mode"] == "user_approved"
    assert len(reads.console.requests) == 4


@pytest.mark.parametrize("raw_ask,structured_ask", [(False, True), (True, False)])
def test_mixed_replay_preserves_all_origin_approval_policies(reads, monkeypatch, raw_ask, structured_ask):
    from schemii.schemii.ai import actions
    reads.chat = reads.repo.update_chat_policy("owner", reads.chat.id, reads.chat.revision,
        AiCapabilities(structured_data_read=True, raw_sql_read=True, structured_query=True,
                       read_approval_required=raw_ask, structured_query_approval_required=structured_ask))
    monkeypatch.setattr(actions, "relation_read_query", lambda *_: {"label": "Structured", "sql": "SELECT 3 AS value"})
    raw = approve(reads, proposal(reads))
    structured_proposal = reads.service._save_tool_proposal("owner", reads.chat, reads.turn.id,
        "schemii_browse_rows", {"queries": [{"relationRef": "rel_" + "a" * 16}]})
    structured = approve(reads, structured_proposal)
    entries = [*raw.result_summary["results"], *structured.result_summary["results"]]
    reads.console.released.update(entry["executionId"] for entry in entries)
    reply = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results",
        {"runIds": [entry["runId"] for entry in entries]}, reads.turn.id)
    assert reply["approvalRequired"] is True
    assert len(reads.console.requests) == 3
    pending = reads.repo.get_proposal("owner", reads.chat.id, reply["proposalId"])
    action = reads.repo.proposal_action("owner", reads.chat.id, pending.id)
    assert action["replayCapabilities"] == ["raw_sql_read", "structured_query"]
    replay = approve(reads, pending)
    assert replay.result_summary["authorization"]["mode"] == "user_approved"
    entries = replay.result_summary["results"]
    reads.console.released.update(entry["executionId"] for entry in entries)
    repeated = reads.service._read_tool_result("owner", reads.chat.id, "schemii_get_read_results",
        {"runIds": [entry["runId"] for entry in entries]}, reads.turn.id)
    assert repeated["approvalRequired"] is True
    assert len(reads.console.requests) == 6
    assert reads.repo.proposal_action("owner", reads.chat.id, repeated["proposalId"])["replayCapabilities"] == action["replayCapabilities"]


def test_replay_execution_checks_every_origin_not_only_proposal_capability(reads):
    from schemii.schemii.ai.reads import execute_batch
    from schemii.schemii.ai.service import AiServiceError
    # The primary envelope is raw SQL, but the replay also contains a structured
    # query whose capability is absent. Even an internal caller cannot run it.
    with pytest.raises(AiServiceError) as error:
        execute_batch(reads.service, "owner", reads.chat,
            SimpleNamespace(capability="raw_sql_read", turn_id=reads.turn.id), SimpleNamespace(id="unused"),
            {"queries": [{"sql": "SELECT 1", "label": "one"}],
             "replayCapabilities": ["raw_sql_read", "structured_query"]})
    assert error.value.code == "ai_permission_changed"
    assert reads.console.requests == []


def test_model_cannot_inject_internal_replay_capabilities():
    from schemii.schemii.ai.tools import normalize_tool_call
    proposal = normalize_tool_call("schemii_read_query", {
        "queries": [{"sql": "SELECT 1", "label": "one"}],
        "replayCapabilities": ["structured_query"], "structuredRead": True})
    assert proposal.capability == "raw_sql_read"
    assert "replayCapabilities" not in proposal.action and "structuredRead" not in proposal.action
