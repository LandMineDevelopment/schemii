"""Batch authorization binds exact reviews and uses shared transaction safety."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.schemii.ai.write_actions import execute_write


def batch_fixture():
    repository = Mock()
    chat = NS(id="chat", workspace_id="workspace", revision=3, capabilities=NS(raw_sql_write=True))
    repository.get_chat.return_value = chat
    design, workspace = NS(revision=5), NS(revision=2)
    proposals = {
        name: NS(id=name, turn_id="turn", revision=1, digest="digest-" + name, status="pending",
                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                 expected_design_revision=5, expected_workspace_revision=2,
                 capability="raw_sql_write", action_type="console_script")
        for name in ["first", "second", "third"]
    }
    repository.get_proposal.side_effect = lambda owner, chat_id, proposal_id: proposals[proposal_id]
    services = NS(admin_config=AdminConfig(), designs=Mock(), workspaces=Mock())
    services.designs.get.return_value = design
    services.workspaces.get.return_value = workspace
    service = AiService(repository, None, services)
    body = NS(items=[NS(proposal_id=name, expected_chat_revision=3,
                       expected_proposal_revision=1, proposal_digest="digest-" + name)
                     for name in proposals])
    return service, body, proposals, chat, design


def operation(kind="console_script", revision=None):
    receipt = {"kind": kind}
    return NS(kind=kind, result_summary={"designRevision": revision},
              model_dump=Mock(return_value=receipt))


@pytest.mark.parametrize("mutation", [
    lambda body, proposals, chat: setattr(body.items[1], "proposal_digest", "changed"),
    lambda body, proposals, chat: setattr(body.items[1], "expected_chat_revision", 2),
    lambda body, proposals, chat: setattr(proposals["second"], "status", "dismissed"),
    lambda body, proposals, chat: setattr(proposals["second"], "expires_at", datetime.now(timezone.utc) - timedelta(seconds=1)),
    lambda body, proposals, chat: setattr(proposals["second"], "expected_design_revision", 4),
    lambda body, proposals, chat: setattr(proposals["second"], "expected_workspace_revision", 1),
])
def test_entire_approval_is_checked_before_first_effect(mutation):
    service, body, proposals, chat, _ = batch_fixture()
    service.execute = Mock()
    mutation(body, proposals, chat)
    with pytest.raises(AiServiceError) as error:
        service.execute_batch("owner", "chat", body)
    assert error.value.code == "ai_batch_changed"
    service.execute.assert_not_called()


def test_disabled_permission_and_duplicate_proposal_rejected_before_effect():
    service, body, proposals, chat, _ = batch_fixture()
    service.execute = Mock()
    chat.capabilities.raw_sql_write = False
    with pytest.raises(AiServiceError) as error:
        service.execute_batch("owner", "chat", body)
    assert error.value.code == "ai_permission_required"
    chat.capabilities.raw_sql_write = True
    body.items.append(body.items[0])
    with pytest.raises(AiServiceError) as error:
        service.execute_batch("owner", "chat", body)
    assert error.value.code == "ai_duplicate_approval"
    service.execute.assert_not_called()


def test_mixed_batch_stops_after_failure_without_hiding_completed_effect():
    service, body, _, _, _ = batch_fixture()
    service.execute = Mock(side_effect=[operation(), AiServiceError(409, "changed", "changed")])
    result = service.execute_batch("owner", "chat", body)
    assert result["status"] == "partial"
    assert result["operations"] == [{"kind": "console_script"}]
    assert result["errorCode"] == "changed"
    assert result["notAttempted"] == ["third"]
    assert service.execute.call_count == 2


def test_failure_before_any_effect_is_not_reported_partial():
    service, body, _, _, _ = batch_fixture()
    service.execute = Mock(side_effect=AiServiceError(409, "changed", "changed"))
    result = service.execute_batch("owner", "chat", body)
    assert result["status"] == "failed"
    assert result["operations"] == []
    assert result["notAttempted"] == ["second", "third"]


def test_revision_rebind_uses_returned_own_revision_not_latest_external_revision():
    service, body, proposals, _, design = batch_fixture()
    original_execute = service.execute

    def effect(owner, chat_id, proposal_id, request, **kwargs):
        if proposal_id == "first":
            assert kwargs["_approved_design_revision"] == 5
            design.revision = 7  # Our save returned 6; another editor then saved 7.
            return operation("design_change", 6)
        assert kwargs["_approved_design_revision"] == 6
        return original_execute(owner, chat_id, proposal_id, request, **kwargs)

    service.execute = Mock(side_effect=effect)
    result = service.execute_batch("owner", "chat", body)
    assert result["status"] == "partial"
    assert result["errorCode"] == "ai_context_changed"
    service.repository.begin_operation.assert_not_called()


def write_fixture():
    console = Mock()
    console.settings.return_value = NS(revision=2)
    console.create_transaction.return_value = NS(id="txn", revision=1)
    console.reserve_transaction_execution.return_value = NS(id="execution")
    console.get.return_value = NS(id="execution", status="succeeded")
    console.get_transaction.return_value = NS(id="txn", revision=3, status="open")
    console.commit_transaction.return_value = NS(status="committed")
    return NS(console=console)


def write(services, **kwargs):
    return execute_write(services, "owner", "workspace", 5,
                         {"statements": ["UPDATE things SET active = true", "DELETE FROM old_things"]}, **kwargs)


def test_write_batch_uses_one_shared_transaction_and_exact_revisions():
    services = write_fixture()
    result = write(services)
    console = services.console
    assert result == {"transactionId": "txn", "executionId": "execution",
                      "commitOutcome": "committed", "liveDatabaseChanged": True}
    assert console.create_transaction.call_count == console.commit_transaction.call_count == 1
    assert console.create_transaction.call_args.args[2].expected_workspace_revision == 5
    assert len(console.reserve_transaction_execution.call_args.args[3].statements) == 2
    assert console.commit_transaction.call_args.args[3].expected_revision == 3
    console.rollback_transaction.assert_not_called()


def test_sql_failure_rolls_back_without_commit():
    services = write_fixture()
    services.console.get.return_value.status = "failed"
    with pytest.raises(AiServiceError, match="no commit"):
        write(services)
    services.console.commit_transaction.assert_not_called()
    services.console.rollback_transaction.assert_called_once()


def test_permission_revoked_before_commit_rolls_back():
    services = write_fixture()
    authorized = Mock(return_value=False)
    with pytest.raises(AiServiceError):
        write(services, is_authorized=authorized)
    authorized.assert_called_once()
    services.console.commit_transaction.assert_not_called()
    services.console.rollback_transaction.assert_called_once()


@pytest.mark.parametrize("sql", [
    "UPDATE things SET active = true; COMMIT",
    "ROLLBACK", "BEGIN", "PREPARE TRANSACTION 'escape'",
    "COPY things FROM STDIN", "COPY things TO '/tmp/things.csv'",
])
def test_ai_managed_write_cannot_inherit_human_session_transaction_access(sql):
    from test_console import console_client, target_workspace
    from schemii.schemii.console.service import ConsoleServiceError

    api, postgres = console_client()
    workspace = target_workspace(api)
    with pytest.raises(ConsoleServiceError):
        execute_write(api.app.state.services, "user_local_prototype", workspace["id"],
                      workspace["revision"], {"statements": [sql]})
    assert len(postgres.transactions) == 1
    transaction = postgres.transactions[0]
    assert transaction.executed == []
    assert transaction.rolled_back
    assert not transaction.committed


def test_cancellation_at_commit_gate_rolls_back_ai_owned_transaction():
    services = write_fixture()
    services.console.commit_transaction.side_effect = AiServiceError(
        409, "postgres_console_cancelled", "Stopped before COMMIT dispatch")
    with pytest.raises(AiServiceError):
        write(services)
    services.console.commit_transaction.assert_called_once()
    services.console.rollback_transaction.assert_called_once()


def test_uncertain_commit_is_never_retried_or_rolled_back():
    services = write_fixture()
    services.console.get_transaction.side_effect = [NS(revision=3, status="open"), NS(revision=4, status="uncertain")]
    services.console.commit_transaction.side_effect = RuntimeError("connection lost during commit")
    with pytest.raises(RuntimeError, match="connection lost"):
        write(services)
    services.console.commit_transaction.assert_called_once()
    services.console.rollback_transaction.assert_not_called()


def test_uncertain_commit_receipt_does_not_claim_live_changes():
    services = write_fixture()
    services.console.commit_transaction.return_value.status = "uncertain"
    result = write(services)
    assert result["commitOutcome"] == "uncertain"
    assert result["liveDatabaseChanged"] is None
    services.console.commit_transaction.assert_called_once()


def test_commit_error_does_not_attempt_rollback_even_if_metadata_still_says_open():
    services = write_fixture()
    # Console cannot always persist the uncertain terminal status if the
    # metadata database also fails. An old open receipt is not proof that the
    # PostgreSQL commit did not happen.
    services.console.commit_transaction.side_effect = RuntimeError("commit outcome uncertain")
    with pytest.raises(RuntimeError, match="commit outcome uncertain"):
        write(services)
    services.console.rollback_transaction.assert_not_called()


def test_cleanup_error_preserves_original_execution_failure():
    services = write_fixture()
    services.console.run_transaction.side_effect = RuntimeError("original execution error")
    services.console.rollback_transaction.side_effect = RuntimeError("cleanup also failed")
    with pytest.raises(RuntimeError, match="original execution error"):
        write(services)


def test_service_write_authorization_notices_turn_cancellation_without_policy_change(monkeypatch):
    service, body, proposals, chat, _ = batch_fixture()
    proposal = proposals["first"]
    proposal.action_type = "sql_write"
    proposal.capability = "sql_write_execute"
    chat.capabilities.sql_write_execute = True
    proposal.turn_id = "originating-turn"
    action = {"statements": ["UPDATE things SET active = true"]}
    service.repository.proposal_action.return_value = action
    service.repository.begin_operation.return_value = (NS(id="operation"), proposal, action)
    turn = NS(status="working")
    service.repository.get_turn.return_value = turn

    def execute_with_cancellation(*args, is_authorized, **kwargs):
        assert is_authorized() is True
        turn.status = "cancelled"
        assert service.repository.get_chat("owner", "chat").revision == chat.revision
        assert is_authorized() is False
        raise AiServiceError(409, "ai_write_cancelled", "Cancelled before commit")

    adapter = Mock(side_effect=execute_with_cancellation)
    monkeypatch.setattr("schemii.schemii.ai.service.execute_write", adapter)
    with pytest.raises(AiServiceError, match="Cancelled before commit"):
        service.execute("owner", "chat", "first", body.items[0])
    adapter.assert_called_once()
    assert service.repository.get_turn.call_args.args == ("owner", "chat", "originating-turn")
    assert service.repository.finish_operation.call_args.kwargs["status"] == "failed"
