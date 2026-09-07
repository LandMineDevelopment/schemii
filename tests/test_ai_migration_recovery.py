"""Recovery tools retain shared migration authority and truthful outcomes."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai import actions
from schemii.schemii.ai.action_policy import requires_approval, operation_receipt
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository, AiConflictError
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.schemii.ai.tools import normalize_tool_call, proposal_tool_arguments, tool_definitions
from schemii.schemii.migrations.errors import MigrationServiceError
from schemii.schemii.migrations.models import MigrationDriftResolutionRequest, MigrationReconciliationRequest

OWNER = "owner"
WORKSPACE = "ws_" + "a" * 32
PLAN = "mpl_" + "b" * 32
EXECUTION = "mex_" + "c" * 32
CONFLICT = "mcf_" + "d" * 32
DIGEST = "e" * 64
RESOLVE = {"plan_id": PLAN, "expected_design_revision": 3, "review_digest": DIGEST,
           "resolutions": [{"conflict_id": CONFLICT, "resolution": "pull_live"}]}
RECONCILE = {"execution_id": EXECUTION, "expected_execution_revision": 4}


@pytest.fixture
def recovery():
    repo = InMemoryAiRepository()
    chat = repo.create_chat(OWNER, WORKSPACE, "Recovery", "provider", "model", AiCapabilities(migration_apply=True))
    turn, _ = repo.create_turn(OWNER, chat.id, "Recover", None, 4, 2, 100)
    migrations = Mock()
    migrations.get_plan.return_value = NS(workspace_id=WORKSPACE, review_digest=DIGEST,
        conflicts=[NS(id=CONFLICT, object_path="tables.projects.columns.title", summary="Changed externally")])
    migrations.get_execution.return_value = NS(workspace_id=WORKSPACE)
    services = NS(admin_config=AdminConfig(), migrations=migrations,
        designs=NS(get=lambda *_: NS(revision=3)), workspaces=NS(get=lambda *_: NS(revision=1)))
    return NS(repo=repo, chat=chat, turn=turn, migrations=migrations, service=AiService(repo, None, services))


def propose(f, name, arguments):
    return f.service._save_tool_proposal(OWNER, f.chat, f.turn.id, name, arguments)


def execute(f, proposal):
    return f.service.execute(OWNER, f.chat.id, proposal.id, NS(expected_chat_revision=f.chat.revision,
        expected_proposal_revision=proposal.revision, proposal_digest=proposal.digest))


@pytest.mark.parametrize("name,arguments,kind", [
    ("schemii_resolve_migration", RESOLVE, "migration_resolve"),
    ("schemii_reconcile_migration", RECONCILE, "migration_reconcile"),
])
def test_recovery_uses_existing_migration_policy_and_native_contract(name, arguments, kind):
    proposal = normalize_tool_call(name, arguments)
    assert proposal.action_type == kind
    assert proposal.capability == "migration_apply"
    assert requires_approval(AiCapabilities(migration_apply=True), kind, arguments)
    assert not requires_approval(AiCapabilities(migration_apply=True, migration_approval_required=False), kind, arguments)
    assert name not in {tool["name"] for tool in tool_definitions(AiCapabilities())}
    assert proposal_tool_arguments(name, "Recovery", {**proposal.action, "reviewContext": []}) == proposal.action


def test_conflict_bundle_uses_exact_shared_request_and_metadata_only_receipt(recovery):
    f = recovery
    proposal = propose(f, "schemii_resolve_migration", RESOLVE)
    assert proposal.details["reviewContext"][0]["path"] == "tables.projects.columns.title"
    f.migrations.resolve_drift.return_value = NS(id="resolution", plan_id=PLAN, design_revision=4, baseline_revision=2)
    operation = execute(f, proposal)
    owner, plan_id, request = f.migrations.resolve_drift.call_args.args
    assert (owner, plan_id) == (OWNER, PLAN)
    assert type(request) is MigrationDriftResolutionRequest
    assert request.model_dump() == {k: v for k, v in RESOLVE.items() if k != "plan_id"}
    assert operation.result_summary["freshReviewRequired"] is True
    assert operation.result_summary["liveDatabaseChanged"] is False
    f.migrations.create_execution.assert_not_called()
    with pytest.raises(AiConflictError):
        execute(f, proposal)
    assert f.migrations.resolve_drift.call_count == 1


@pytest.mark.parametrize("outcome,sync,required", [("uncertain", None, True), ("committed", "conflict", True), ("rolled_back", None, False)])
def test_reconciliation_preserves_real_outcome_without_executing_sql(recovery, outcome, sync, required):
    f = recovery
    proposal = propose(f, "schemii_reconcile_migration", RECONCILE)
    result = {"commitOutcome": outcome, "syncStatus": sync, "reconcileRequired": required}
    f.migrations.reconcile_execution.return_value = NS(id=EXECUTION, model_dump=lambda **_: result)
    operation = execute(f, proposal)
    owner, execution_id, request = f.migrations.reconcile_execution.call_args.args
    assert (owner, execution_id) == (OWNER, EXECUTION)
    assert type(request) is MigrationReconciliationRequest
    assert request.expected_execution_revision == 4
    assert operation_receipt(operation)["outcome"] == result
    f.migrations.create_execution.assert_not_called()


def test_stale_review_cannot_produce_misleading_conflict_labels(recovery):
    recovery.migrations.get_plan.return_value.review_digest = "f" * 64
    with pytest.raises(MigrationServiceError):
        propose(recovery, "schemii_resolve_migration", RESOLVE)
    assert recovery.repo.list_proposals(OWNER, recovery.chat.id) == []


@pytest.mark.parametrize("name,arguments,lookup", [
    ("schemii_resolve_migration", RESOLVE, "get_plan"),
    ("schemii_reconcile_migration", RECONCILE, "get_execution"),
])
def test_other_workspace_resources_never_mutate(recovery, name, arguments, lookup):
    getattr(recovery.migrations, lookup).return_value.workspace_id = "another"
    with pytest.raises(MigrationServiceError):
        execute(recovery, propose(recovery, name, arguments))
    recovery.migrations.resolve_drift.assert_not_called()
    recovery.migrations.reconcile_execution.assert_not_called()


@pytest.mark.parametrize("name,arguments", [("schemii_resolve_migration", RESOLVE), ("schemii_reconcile_migration", RECONCILE)])
def test_changed_permissions_block_saved_recovery_proposal(recovery, name, arguments):
    proposal = propose(recovery, name, arguments)
    recovery.repo.update_chat_policy(OWNER, recovery.chat.id, recovery.chat.revision, AiCapabilities())
    with pytest.raises(AiServiceError, match="policy changed"):
        execute(recovery, proposal)
    recovery.migrations.resolve_drift.assert_not_called()
    recovery.migrations.reconcile_execution.assert_not_called()


def test_model_cannot_inject_review_labels_or_duplicate_conflict_choices():
    with pytest.raises(ValidationError):
        normalize_tool_call("schemii_resolve_migration", {**RESOLVE, "reviewContext": []})
    with pytest.raises(ValidationError):
        actions.MigrationResolveAction.model_validate({**RESOLVE, "resolutions": RESOLVE["resolutions"] * 2})


@pytest.mark.parametrize("approval", [True, False])
@pytest.mark.parametrize("name,arguments", [("schemii_resolve_migration", RESOLVE), ("schemii_reconcile_migration", RECONCILE)])
def test_recovery_tool_loop_pauses_or_runs_then_returns_receipt_once(recovery, approval, name, arguments):
    from schemii.common.ai.pi import PiReply
    from schemii.schemii.ai.read_workflow import run_read_workflow
    f = recovery
    f.chat = f.repo.update_chat_policy(OWNER, f.chat.id, f.chat.revision,
        AiCapabilities(migration_apply=True, migration_approval_required=approval))
    f.repo.claim_turn(OWNER, f.chat.id, f.turn.id)
    f.migrations.resolve_drift.return_value = NS(id="resolution", plan_id=PLAN, design_revision=4, baseline_revision=2)
    f.migrations.reconcile_execution.return_value = NS(id=EXECUTION, model_dump=lambda **_: {
        "commitOutcome": "uncertain", "reconcileRequired": True})
    reply = PiReply("Checking", ((name, arguments),), {"role": "assistant", "content": [
        {"type": "toolCall", "id": "call1", "name": name, "arguments": arguments}], "timestamp": 0}, ("call1",))
    f.service.runtime = Mock()
    f.service.runtime.run.side_effect = [reply, PiReply("The receipt is ready.", ())]
    f.service._refresh_tool_context = Mock(return_value="fresh context")
    def run():
        return run_read_workflow(f.service, OWNER, f.chat, f.turn, "context", "Recover",
            Mock(), lambda: True, Mock())
    result = run()
    if approval:
        assert result.paused
        f.migrations.resolve_drift.assert_not_called()
        f.migrations.reconcile_execution.assert_not_called()
        proposal = f.repo.list_proposals(OWNER, f.chat.id)[0]
        execute(f, proposal)
        f.repo.resume_turn(OWNER, f.chat.id, f.turn.id)
        f.repo.claim_turn(OWNER, f.chat.id, f.turn.id)
        result = run()
    assert not result.paused
    assert result.reply.text == "The receipt is ready."
    messages = f.service.runtime.run.call_args.kwargs["messages"]
    returned = [m for m in messages if m["role"] == "toolResult"]
    assert len(returned) == 1
    receipt = json.loads(returned[0]["content"][0]["text"])
    assert receipt["status"] == "succeeded"
    assert f.migrations.resolve_drift.call_count + f.migrations.reconcile_execution.call_count == 1
    if name == "schemii_reconcile_migration":
        assert receipt["outcome"]["commitOutcome"] == "uncertain"
    else:
        assert receipt["outcome"]["freshReviewRequired"] is True
