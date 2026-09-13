import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from schemii.common.ai.pi import PiError, PiReply
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.read_workflow import run_read_workflow


def tool_reply(name="schemii_read_query", arguments=None):
    arguments = arguments or {"queries": [{"sql": "select 1", "label": "first"},
                                         {"sql": "select 2", "label": "second"}]}
    return PiReply("I will check.", ((name, arguments),), {
        "role": "assistant", "content": [{"type": "toolCall", "id": "call1", "name": name,
                                            "arguments": arguments}], "timestamp": 0,
    }, ("call1",))


def setup(approval=False, replies=None):
    capabilities = AiCapabilities(raw_sql_read=True, structured_data_read=True,
                                  read_approval_required=approval)
    chat = NS(id="chat", workspace_id="workspace", revision=1, provider_id="test", model_id="test", capabilities=capabilities)
    turn = NS(id="turn")
    repository = Mock()
    repository.continuation.return_value = None
    repository.list_operations.return_value = []
    repository.list_proposals.return_value = []
    repository.proposal_action.return_value = {"queries": [{"sql": "select 1"}]}
    service = NS(repository=repository, services=NS(metadata=NS(limit_events=Mock())), policy=NS(context_bytes=50_000, maximum_tool_rounds=8,
                                                tool_timeout_seconds=300), runtime=Mock(),
                 _save_tool_proposal=Mock(return_value=NS(id="proposal", revision=1, digest="digest", status="pending", action_type="data_read")),
                 execute=Mock(return_value=NS(id="operation", status="succeeded", proposal_id="proposal", kind="data_read")),
                 _refresh_tool_context=Mock(return_value="fresh system"),
                 _operation_read_context=Mock(return_value={"operationId": "operation", "results": [
                     {"rows": [[1]], "label": "first"}, {"rows": [[2]], "label": "second"}]}),
                 _read_tool_result=Mock())
    service.runtime.run.side_effect = replies or [tool_reply(), PiReply("The second is twice the first.", ())]
    return service, chat, turn


def run(service, chat, turn, **kwargs):
    return run_read_workflow(service, "owner", chat, turn, "system", "Compare the results",
                             Mock(), kwargs.get("is_authorized", lambda: True), Mock())


def test_batch_returns_one_tool_result_and_automatically_continues_analysis():
    service, chat, turn = setup()
    result = run(service, chat, turn)
    assert result.reply.text == "The second is twice the first."
    assert result.used_rows and not result.paused
    service.execute.assert_called_once()
    messages = service.runtime.run.call_args.kwargs["messages"]
    returned = [message for message in messages if message["role"] == "toolResult"]
    assert len(returned) == 1
    assert json.loads(returned[0]["content"][0]["text"])["results"][1]["rows"] == [[2]]
    service.repository.save_continuation.assert_not_called()


def test_approval_pauses_without_running_reads_or_persisting_provider_messages():
    service, chat, turn = setup(approval=True)
    result = run(service, chat, turn)
    assert result.paused
    service.execute.assert_not_called()
    service.runtime.run.assert_called_once()
    saved = service.repository.save_continuation.call_args.args[-1]
    assert saved["pendingProposalIds"] == ["proposal"]
    assert saved["usedOperationIds"] == saved["usedRunIds"] == []
    assert saved["toolRounds"] == 1
    assert saved["pendingCalls"] == [{"id": "call1", "name": "schemii_read_query",
                                       "proposalId": "proposal", "failed": False}]
    assert "rows" not in json.dumps(saved)
    service.repository.pause_turn.assert_called_once_with("owner", "chat", "turn")


def test_approval_resumes_native_tool_result_without_storing_rows_or_provider_transcript():
    initial = tool_reply()
    initial.assistant_message.update(api="openai-responses", provider="openai", model="model",
                                     responseId="must-not-persist")
    initial.assistant_message["content"].insert(0, {"type": "thinking", "thinking": "private reasoning",
                                                  "thinkingSignature": "private-signature"})
    service, chat, turn = setup(approval=True, replies=[initial, PiReply("Two results compared.", ())])
    service.policy.maximum_tool_rounds = 1
    assert run(service, chat, turn).paused
    state = service.repository.save_continuation.call_args.args[-1]
    serialized = json.dumps(state)
    assert all(value not in serialized for value in ("private reasoning", "private-signature", "must-not-persist", "select 1", "rows"))
    service.repository.continuation.return_value = state
    service.repository.list_proposals.return_value = [NS(id="proposal", summary="Compare reads", status="succeeded")]
    service.repository.proposal_action.return_value = {"queries": [{"sql": "select 1", "label": "first"},
                                                                  {"sql": "select 2", "label": "second"}]}
    service.repository.list_operations.return_value = [NS(id="operation", proposal_id="proposal", status="succeeded", kind="data_read")]
    result = run(service, chat, turn)
    assert result.reply.text == "Two results compared."
    assert service.runtime.run.call_args.args[6] == []
    messages = service.runtime.run.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == ["user", "user"]
    evidence = json.loads(messages[1]["content"])
    assert "call1" in evidence["receipts"]
    assert len(json.loads(evidence["results"][0]["content"][0]["text"])["results"]) == 2
    assert all(value not in json.dumps(messages) for value in
               ("private reasoning", "private-signature", "must-not-persist"))
    service.execute.assert_not_called()


def test_resume_includes_approved_result_and_previous_result_without_persisting_either():
    service, chat, turn = setup(approval=True, replies=[PiReply("Compared both.", ())])
    service.repository.continuation.return_value = {
        "pendingProposalIds": ["proposal"], "usedOperationIds": ["older"], "toolRounds": 1}
    service.repository.list_proposals.return_value = [NS(id="proposal", status="succeeded")]
    service.repository.list_operations.return_value = [NS(id="older", proposal_id="oldprop", status="succeeded", kind="data_read"),
                                                       NS(id="operation", proposal_id="proposal", status="succeeded", kind="data_read")]
    result = run(service, chat, turn)
    assert result.used_rows and not result.paused
    assert [call.args[-1] for call in service._operation_read_context.call_args_list] == ["older", "operation"]
    prompt = service.runtime.run.call_args.kwargs["messages"][0]["content"]
    assert "resumed read results" in prompt
    service.execute.assert_not_called()


def test_oversized_approval_resume_bounds_synthetic_results_not_user_request():
    service, chat, turn = setup(approval=True, replies=[PiReply("I need a narrower result page.", ())])
    service.repository.continuation.return_value = {
        "pendingProposalIds": ["proposal"], "usedOperationIds": ["older"], "toolRounds": 1}
    service.repository.list_proposals.return_value = [NS(id="proposal", status="succeeded")]
    service.repository.list_operations.return_value = [NS(id="older", proposal_id="oldprop", status="succeeded", kind="data_read"),
                                                       NS(id="operation", proposal_id="proposal", status="succeeded", kind="data_read")]
    service._operation_read_context.return_value = {"rows": [["private-value" * 10000]], "runId": "run1"}
    result = run(service, chat, turn)
    assert result.used_rows and not result.paused
    prompt = service.runtime.run.call_args.kwargs["messages"][0]["content"]
    assert prompt.startswith("Compare the results")
    assert "private-value" not in prompt
    assert "older" in prompt and "operation" in prompt and "run1" in prompt
    assert "row data was discarded" in prompt
    service.execute.assert_not_called()


def test_read_permission_denial_is_returned_to_model_without_execution():
    service, chat, turn = setup()
    chat.capabilities.action_modes["query.read"] = "disabled"
    run(service, chat, turn)
    service.execute.assert_not_called()
    service._save_tool_proposal.assert_not_called()
    result = service.runtime.run.call_args.kwargs["messages"][-1]
    assert result["isError"]
    assert json.loads(result["content"][0]["text"])["requiredPermission"] == "Run read SQL"


def test_changed_permissions_prevent_execution_after_provider_returns():
    service, chat, turn = setup()
    authorized = iter([True, False])
    with pytest.raises(PiError) as caught:
        run(service, chat, turn, is_authorized=lambda: next(authorized))
    assert caught.value.code == "permission_changed"
    service.execute.assert_not_called()


def test_unadvertised_design_call_returns_exact_server_permission_denial():
    from schemii.schemii.ai.service import AiServiceError
    service, chat, turn = setup(replies=[
        tool_reply("schemii_design_change", {"action": {"type": "delete_object", "object_id": "table_a"}}),
        PiReply("Enable Delete tables in Assistant settings.", ()),
    ])
    service._save_tool_proposal.side_effect = AiServiceError(
        403, "permission_denied", "Enable these actions in Assistant settings: tables.delete",
        {"requiredActions": ["tables.delete"]},
    )
    run(service, chat, turn)
    service._save_tool_proposal.assert_called_once()
    service.execute.assert_not_called()
    returned = json.loads(service.runtime.run.call_args.kwargs["messages"][-1]["content"][0]["text"])
    assert returned["requiredActions"] == ["tables.delete"]
    assert returned["error"] == "permission_denied"


def test_result_replay_can_pause_for_approval():
    service, chat, turn = setup(replies=[tool_reply("schemii_get_read_results", {"runIds": ["oldrun"]})])
    service._read_tool_result.return_value = {"approvalRequired": True, "proposalId": "replay"}
    result = run(service, chat, turn)
    assert result.paused
    assert service.repository.save_continuation.call_args.args[-1]["pendingProposalIds"] == ["replay"]


def test_oversized_tool_results_compact_without_losing_original_request_or_repeating_actions():
    service, chat, turn = setup()
    service._operation_read_context.return_value = {"rows": [["private-value" * 10_000]]}
    run(service, chat, turn)
    assert service.runtime.run.call_count == 2
    service.execute.assert_called_once()
    messages = service.runtime.run.call_args.kwargs["messages"]
    assert messages[0]["content"] == "Compare the results"
    assert "row data were discarded" in messages[-1]["content"]
    assert "private-value" not in json.dumps(messages)
    assert "private-value" not in str(service.repository.add_event.call_args)


def test_last_tool_round_gets_one_tools_disabled_summary_with_all_results():
    service, chat, turn = setup()
    service.policy.maximum_tool_rounds = 1
    result = run(service, chat, turn)
    assert result.reply.text == "The second is twice the first."
    assert result.used_rows
    assert service.runtime.run.call_count == 2
    request = service.runtime.run.call_args
    assert request.args[6] == []
    assert "Answer the user's original question now" in request.args[4]
    assert "incomplete checks" in request.args[4]
    evidence = json.loads(request.kwargs["messages"][-1]["content"])
    assert json.loads(evidence["results"][0]["content"][0]["text"])["results"][1]["rows"] == [[2]]
    service.execute.assert_called_once()
    assert "rows" not in str(service.repository.add_event.call_args_list)


@pytest.mark.parametrize("reply", [tool_reply(), PiReply("", ())])
def test_final_summary_cannot_execute_extra_actions_or_loop(reply):
    service, chat, turn = setup(replies=[tool_reply(), reply])
    service.policy.maximum_tool_rounds = 1
    with pytest.raises(PiError) as caught:
        run(service, chat, turn)
    assert caught.value.code == "ai_tool_round_limit"
    assert service.runtime.run.call_count == 2
    service.execute.assert_called_once()
    service._save_tool_proposal.assert_called_once()


def test_final_summary_still_checks_permission_revocation():
    service, chat, turn = setup()
    service.policy.maximum_tool_rounds = 1
    allowed = True
    def response(*args, **kwargs):
        nonlocal allowed
        if args[6] == []:
            allowed = False
            return PiReply("Do not publish after revocation", ())
        return tool_reply()
    service.runtime.run.side_effect = response
    with pytest.raises(PiError) as caught:
        run(service, chat, turn, is_authorized=lambda: allowed)
    assert caught.value.code == "permission_changed"
    service.execute.assert_called_once()


def test_tool_failure_is_an_error_result_and_does_not_leak_private_diagnostics():
    service, chat, turn = setup()
    service.execute.side_effect = ValueError("duplicate employee secret@example.com")
    run(service, chat, turn)
    result = service.runtime.run.call_args.kwargs["messages"][-1]
    assert result["isError"]
    assert "secret@example.com" not in json.dumps(result)


def test_nonread_proposals_pause_until_approved():
    service, chat, turn = setup(replies=[tool_reply("schemii_design_change", {"action": {}}), PiReply("Proposal ready.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    service._save_tool_proposal.return_value.action_type = "design_change"
    result = run(service, chat, turn)
    assert result.paused
    service._save_tool_proposal.assert_called_once()
    service.execute.assert_not_called()


def test_separate_design_calls_become_one_atomic_proposal_and_one_approval():
    actions = [{"type": "rename_table", "table_id": "table_" + "a" * 32, "name": "first"},
               {"type": "rename_table", "table_id": "table_" + "b" * 32, "name": "second"}]
    calls = tuple(("schemii_design_change", {"summary": "Rename table", "action": action})
                  for action in actions)
    reply = PiReply("", calls, {"role": "assistant", "content": [
        {"type": "toolCall", "id": str(index), "name": name, "arguments": arguments}
        for index, (name, arguments) in enumerate(calls)]}, ("0", "1"))
    service, chat, turn = setup(replies=[reply])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    service._save_tool_proposal.return_value.action_type = "design_change"
    result = run(service, chat, turn)
    assert result.paused
    service._save_tool_proposal.assert_called_once()
    assert service._save_tool_proposal.call_args.args[-1]["action"] == {"type": "batch", "actions": actions}
    state = service.repository.save_continuation.call_args.args[-1]
    assert state["pendingProposalIds"] == ["proposal"]
    assert [call["proposalId"] for call in state["pendingCalls"]] == ["proposal", "proposal"]
    service.execute.assert_not_called()


def test_invalid_design_batch_cannot_partially_execute_valid_siblings():
    calls = (("schemii_design_change", {"action": {"type": "rename_table", "table_id": "table_" + "a" * 32, "name": "valid"}}),
             ("schemii_design_change", {"action": {"type": "unknown"}}))
    reply = PiReply("", calls, {"role": "assistant", "content": []}, ("0", "1"))
    service, chat, turn = setup(replies=[reply, PiReply("Please correct the invalid action.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    result = run(service, chat, turn)
    assert not result.paused
    service._save_tool_proposal.assert_not_called()
    service.execute.assert_not_called()
    results = service.runtime.run.call_args.kwargs["messages"][-2:]
    assert all(item["isError"] for item in results)


def design_operation():
    return NS(id="operation", status="succeeded", proposal_id="proposal", kind="design_change",
              resource_kind="design", resource_id="design", result_summary={"revision": 2}, error_code=None)


def test_automatic_design_execution_continues_with_fresh_context_and_exact_receipt():
    service, chat, turn = setup(replies=[tool_reply("schemii_design_change", {"action": {}}),
                                       PiReply("Saved the design, not migrated.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    chat.capabilities.action_modes["tables.update"] = "automatic"
    service._save_tool_proposal.return_value.action_type = "design_change"
    service.execute.return_value = design_operation()
    result = run(service, chat, turn)
    assert not result.paused and not result.used_rows
    service.execute.assert_called_once()
    service._refresh_tool_context.assert_called_once_with("owner", chat, "system")
    assert service.runtime.run.call_args.args[4] == "fresh system"
    returned = json.loads(service.runtime.run.call_args.kwargs["messages"][-1]["content"][0]["text"])
    assert returned["kind"] == "design_change"
    assert returned["outcome"] == {"revision": 2}
    assert "Live migration is a separate action" in returned["meaning"]
    service._operation_read_context.assert_not_called()


def test_design_approval_resumes_with_saved_outcome_without_reexecuting():
    service, chat, turn = setup(replies=[tool_reply("schemii_design_change", {"action": {}}),
                                       PiReply("The approved design was saved.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    service._save_tool_proposal.return_value.action_type = "design_change"
    assert run(service, chat, turn).paused
    service.repository.continuation.return_value = service.repository.save_continuation.call_args.args[-1]
    service.repository.list_proposals.return_value = [NS(id="proposal", summary="Change design", status="succeeded")]
    service.repository.proposal_action.return_value = {"type": "rename_table", "name": "renamed", "table_id": "table_" + "a" * 32}
    service.repository.list_operations.return_value = [design_operation()]
    result = run(service, chat, turn)
    assert not result.paused and not result.used_rows
    messages = service.runtime.run.call_args.kwargs["messages"]
    assert messages[-1]["role"] == "toolResult"
    returned = json.loads(messages[-1]["content"][0]["text"])
    assert returned["kind"] == "design_change" and returned["status"] == "succeeded"
    service.execute.assert_not_called()
    service._operation_read_context.assert_not_called()


def test_automatic_design_permission_never_grants_raw_write_authority():
    service, chat, turn = setup(replies=[tool_reply("schemii_execute_write", {"sql": "DELETE FROM records"}),
                                       PiReply("Write access is not enabled.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    chat.capabilities.action_modes["tables.update"] = "automatic"
    result = run(service, chat, turn)
    assert not result.paused
    service._save_tool_proposal.assert_not_called()
    service.execute.assert_not_called()
    returned = json.loads(service.runtime.run.call_args.kwargs["messages"][-1]["content"][0]["text"])
    assert returned["error"] == "permission_denied"


def test_multiple_automatic_design_calls_execute_one_batch_once():
    actions = [{"type": "rename_table", "table_id": "table_" + value * 32, "name": value}
               for value in ("a", "b")]
    calls = tuple(("schemii_design_change", {"action": action}) for action in actions)
    reply = PiReply("", calls, {"role": "assistant", "content": []}, ("0", "1"))
    service, chat, turn = setup(replies=[reply, PiReply("Saved both together.", ())])
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    chat.capabilities.action_modes["tables.update"] = "automatic"
    service._save_tool_proposal.return_value.action_type = "design_change"
    service.execute.return_value = design_operation()
    assert not run(service, chat, turn).paused
    service._save_tool_proposal.assert_called_once()
    service.execute.assert_called_once()
    service._refresh_tool_context.assert_called_once()
    returned = service.runtime.run.call_args.kwargs["messages"][-2:]
    assert returned[0]["content"] == returned[1]["content"]


def test_direct_tools_inspect_without_creating_action_proposals():
    service, chat, turn = setup(replies=[tool_reply("schemii_list_relations", {"search": "projects"}),
                                       PiReply("Found projects.", ())])
    chat.capabilities.action_modes["query.browse"] = "ask"
    service._read_tool_result.return_value = {"relations": [{"name": "projects"}]}
    assert not run(service, chat, turn).paused
    service._save_tool_proposal.assert_not_called()
    service.execute.assert_not_called()
    service._read_tool_result.assert_called_once_with("owner", "chat", "schemii_list_relations",
                                                     {"search": "projects", "cursor": None, "pageSize": 100}, "turn")


def test_direct_tool_missing_capability_is_denied_without_dispatch():
    service, chat, turn = setup(replies=[tool_reply("schemii_list_relations", {"search": "projects"}),
                                       PiReply("Enable Browse table rows.", ())])
    assert not run(service, chat, turn).paused
    service._read_tool_result.assert_not_called()
    service._save_tool_proposal.assert_not_called()
    returned = json.loads(service.runtime.run.call_args.kwargs["messages"][-1]["content"][0]["text"])
    assert returned["requiredPermission"] == "Browse table rows"


def test_structured_query_resumes_with_native_browse_arguments_and_read_rows():
    service, chat, turn = setup(replies=[tool_reply("schemii_browse_rows", {"queries": []}),
                                       PiReply("The returned rows show the answer.", ())])
    chat.capabilities.action_modes["query.browse"] = "ask"
    service.repository.proposal_action.return_value = {"queries": [{"sql": "select 1"}],
                                                       "structuredQueries": [{"relationRef": "reference"}]}
    assert run(service, chat, turn).paused
    service.repository.continuation.return_value = service.repository.save_continuation.call_args.args[-1]
    service.repository.list_proposals.return_value = [NS(id="proposal", summary="Browse rows", status="succeeded")]
    service.repository.list_operations.return_value = [service.execute.return_value]
    result = run(service, chat, turn)
    assert result.used_rows and not result.paused
    calls = service.runtime.run.call_args.kwargs["messages"][1]["content"]
    assert calls[0]["arguments"] == {"queries": [{"relationRef": "reference"}]}
    assert "select 1" not in json.dumps(calls)


def test_direct_inspection_mixed_with_approval_replays_request_not_stored_results():
    calls = (("schemii_list_relations", {"search": "projects"}),
             ("schemii_design_change", {"action": {}}))
    reply = PiReply("", calls, {"role": "assistant", "content": []}, ("0", "1"))
    service, chat, turn = setup(replies=[reply, PiReply("The design is saved.", ())])
    chat.capabilities.action_modes["query.browse"] = "ask"
    chat.capabilities.action_modes["tables.update"] = "ask"
    service.repository.proposal_action.return_value = {"requiredActions": ["tables.update"]}
    service._save_tool_proposal.return_value.action_type = "design_change"
    service._read_tool_result.return_value = {"relations": [{"name": "not-persisted-result"}]}
    assert run(service, chat, turn).paused
    state = service.repository.save_continuation.call_args.args[-1]
    assert "not-persisted-result" not in json.dumps(state)
    assert state["pendingCalls"][0]["request"]["search"] == "projects"
    service.repository.continuation.return_value = state
    service.repository.list_proposals.return_value = [NS(id="proposal", summary="Design edit", status="succeeded")]
    service.repository.list_operations.return_value = [design_operation()]
    assert not run(service, chat, turn).paused
    assert service._read_tool_result.call_count == 2
    assert service._read_tool_result.call_args_list[0] == service._read_tool_result.call_args_list[1]
    service.execute.assert_not_called()


def test_explain_analyze_uses_approval_workflow_without_execution():
    service, chat, turn = setup(approval=True, replies=[tool_reply(
        "schemii_explain_query", {"sql": "SELECT 1", "analyze": True})])
    chat.capabilities = AiCapabilities(analyze_queries=True)
    service.repository.proposal_action.return_value = {"queries": [{"sql": "EXPLAIN ANALYZE SELECT 1"}]}
    result = run(service, chat, turn)
    assert result.paused
    service.execute.assert_not_called()
    saved = service.repository.save_continuation.call_args.args[-1]
    assert saved["pendingCalls"][0]["name"] == "schemii_explain_query"
    assert saved["pendingProposalIds"] == ["proposal"]


@pytest.mark.parametrize("tool,kind,action,permission", [
    ("schemii_raw_console", "raw_console", {"operation":"create"}, "console.create"),
    ("schemii_app_action", "app_action", {"operation":"list_saved_queries","args":{}}, "query.saved.read"),
])
def test_non_design_action_returns_success_without_reopening_catalog(tool,kind,action,permission):
    service, chat, turn = setup(replies=[tool_reply(tool, action), PiReply("Action completed.", ())])
    chat.capabilities = AiCapabilities(action_modes={permission:"automatic"})
    service.repository.proposal_action.return_value = action
    service._save_tool_proposal.return_value.action_type = kind
    operation = design_operation()
    operation.kind = kind
    operation.result_summary = {"id":"raw_"+"a"*32,"status":"open"}
    service.execute.return_value = operation
    service._refresh_tool_context.side_effect = AssertionError("Session/library actions do not change saved design")
    result = run(service, chat, turn)
    assert not result.paused
    returned = json.loads(service.runtime.run.call_args.kwargs['messages'][-1]['content'][0]['text'])
    assert returned['outcome'] == operation.result_summary
    assert not returned.get('error')
    service._refresh_tool_context.assert_not_called()
