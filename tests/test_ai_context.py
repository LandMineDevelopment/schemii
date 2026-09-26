import copy
import json

from schemii.common.ai.context import bounded_tool_receipt, compact_tool_context, context_size, final_response_messages


def test_final_synthesis_keeps_rows_without_native_tool_protocol_or_reasoning():
    initial={"role":"user","content":"How many authors?"}
    messages=[initial]+group(result={"runId":"run1","rows":[[8]],"status":"succeeded"},bulk=100_000)
    output=final_response_messages("answer only",messages,5000)
    assert output[0] is initial
    assert all(message["role"] == "user" for message in output)
    text=json.dumps(output)
    assert "private reasoning" not in text and "thinkingSignature" not in text
    assert "rows" in text and "8" in text and "run1" in text
    assert context_size("answer only",output,[]) < 5000


def test_final_synthesis_bounds_old_models_but_retains_latest_answer_evidence():
    messages=[{"role":"user","content":"Summarize the result."}]+group()+group("latest",result={"runId":"run2","rows":[[42]]},bulk=0)
    output=final_response_messages("answer only",messages,8000)
    assert context_size("answer only",output,[]) <= 8000
    text=json.dumps(output)
    assert "invalid_column" in text and "succeeded" in text
    assert "rows" in output[-1]["content"] and "42" in output[-1]["content"]


def group(identifier="call1", result=None, arguments=None, bulk=12000):
    return [{"role": "assistant", "timestamp": 1, "content": [
        {"type": "thinking", "thinking": "private reasoning", "thinkingSignature": "s" * bulk},
        {"type": "toolCall", "id": identifier, "name": "actions", "arguments": arguments or {
            "modelId": "model1", "actions": [{"action": "update_model", "definition": {"bulk": "x" * bulk}}]}}]},
        {"role": "toolResult", "toolCallId": identifier, "toolName": "actions",
         "isError": False, "content": [{"type": "text", "text": json.dumps(result or {
             "results": [{"action": "update_model", "status": "succeeded", "revision": 4},
                         {"action": "validate_model", "error": "invalid_column", "message": "Select a source column."}],
             "model": {"bulk": "m" * bulk}})}]}]


def test_preserves_original_request_and_receipts_without_bulk_or_provider_reasoning():
    initial = {"role": "user", "content": "Add historical slots, then validate; do not change other objects."}
    messages = [initial] + group()
    before = copy.deepcopy(messages)
    result = compact_tool_context("authority", messages, [], 5000)
    assert context_size("authority", result, []) < 5000
    assert messages == before
    assert result[0] is initial
    assert [message["role"] for message in result] == ["user", "user"]
    summary = result[-1]["content"]
    for expected in ("call1", "model1", "succeeded", "invalid_column", "Select a source column.", "revision", "do not repeat completed mutations"):
        assert expected in summary
    assert "private reasoning" not in summary
    assert "s" * 100 not in summary
    assert "m" * 100 not in summary
    assert "x" * 100 not in summary


def test_latest_results_stay_native_when_older_compaction_is_enough():
    old = group()
    recent = group("call2", {"runId": "run2", "rows": [["evidence"]]}, bulk=0)
    messages = [{"role": "user", "content": "compare"}] + old + recent
    result = compact_tool_context("system", messages, [], 6000)
    assert result[-2:] == recent
    assert result[1]["role"] == "user"


def test_fresh_oversized_rows_become_explicit_missing_evidence_not_false_summary():
    messages = [{"role": "user", "content": "compare"}] + group(
        result={"runIds": ["run1", "run2"], "rows": [["private-cell" * 5000]]}, bulk=0)
    result = compact_tool_context("system", messages, [], 5000)
    text = result[-1]["content"]
    assert "private-cell" not in text
    assert "run1" in text and "run2" in text
    assert "row data were discarded" in text
    assert "not a full result" in text


def test_nested_result_page_cursor_survives_compaction_without_rows():
    cursor = "c" * 512
    result = {"results": [{"operation": "get_result_page", "result": {
        "executionId": "cex_" + "a" * 32, "resultId": "res_" + "b" * 32,
        "nextCursor": cursor, "rows": [["private-page-cell"]],
        "columns": [{"name": "private-column"}]}}]}
    receipt = bounded_tool_receipt(result)
    facts = {item["path"]: item["value"] for item in receipt["facts"]}
    assert facts["results[0].result.nextCursor"] == cursor
    assert "private-page-cell" not in json.dumps(receipt)
    assert "private-column" not in json.dumps(receipt)

    messages = [{"role": "user", "content": "Continue the result"}] + group(result=result)
    compacted = compact_tool_context("system", messages, [], 5000)
    assert len(compacted) == 2
    text = compacted[-1]["content"]
    assert cursor in text
    assert "private-page-cell" not in text and "private-column" not in text


def test_approval_pending_or_mismatched_native_call_groups_are_never_compacted():
    for messages in (group()[:1], group()[:1] + [group("different")[1]]):
        assert compact_tool_context("system", messages, [], 2000) == messages


def test_batched_call_group_never_leaves_orphan_tool_results():
    messages = group()
    second = group("call2", {"status": "denied", "requiredActions": ["tables.delete"]})
    messages[0]["content"].append(second[0]["content"][-1])
    messages.append(second[1])
    compacted = compact_tool_context("system", messages, [], 6000)
    assert len(compacted) == 1
    assert "call1" in compacted[0]["content"] and "call2" in compacted[0]["content"]
    assert "denied" in compacted[0]["content"] and "tables.delete" in compacted[0]["content"]


def test_does_not_silently_trim_protected_prompt_or_oversized_tool_definitions():
    messages = [{"role": "user", "content": "user request" * 1000}]
    assert compact_tool_context("system", messages, [], 100) == messages
    messages = [{"role": "user", "content": "small"}]
    tools = [{"name": "tool", "description": "d" * 10000}]
    assert compact_tool_context("system", messages, tools, 500) == messages


def test_small_context_is_unchanged_including_signatures():
    messages = group(bulk=0)
    assert compact_tool_context("system", messages, [], 50000) is messages


def test_compaction_can_recur_without_forgetting_previous_receipts():
    messages = [{"role": "user", "content": "original request"}] + group()
    first = compact_tool_context("system", messages, [], 6000)
    second = compact_tool_context("system", first + group("call2"), [], 6000)
    assert second[0] == messages[0]
    assert second[1] == first[1]
    assert context_size("system", second, []) < 6000
    assert "call2" in second[-1]["content"]


def test_plain_text_rejected_approval_receipt_is_retained():
    messages = group()
    messages[-1]["content"] = [{"type": "text", "text": "The user declined the action batch. Do not retry it without a new request."}]
    assert "The user declined" in compact_tool_context("system", messages, [], 4000)[0]["content"]


def test_six_snapshot_rounds_keep_exact_action_outcomes_and_result_references():
    messages = [{"role": "user", "content": "Mirror historical slots and organizations, then validate."}]
    for index in range(6):
        result = {"results": [
            {"operation": "update_model", "result": {"status": "succeeded", "modelId": "model1", "revision": index + 2,
                                                        "model": {"definition": "bulk" * 15000}}},
            {"operation": "validate_model", "errorCode": "missing_source_column", "errorMessage": "Choose a source column."},
            {"operation": "query", "result": {"resultId": f"result{index}", "status": "succeeded", "rows": [["private"]]}}]}
        messages += group(f"call{index}", result=result, arguments={"actions": [{"operation": "update_model", "modelId": "model1"}]})
        messages = compact_tool_context("current authority", messages, [], 20000)
        assert context_size("current authority", messages, []) <= 20000
    text = json.dumps(messages)
    assert "Mirror historical slots" in text
    assert "private" not in text
    for index in range(6):
        assert f"call{index}" in text and f"result{index}" in text
    assert "update_model" in text and "validate_model" in text and "missing_source_column" in text


def test_schemii_operation_receipt_preserves_resource_outcome_and_error():
    receipt = bounded_tool_receipt({"operationId": "operation1", "kind": "design_change", "status": "failed",
        "resourceKind": "design", "resourceId": "design1", "outcome": "Saved changes; validation failed", "error": "missing_source"})
    facts = {item["path"]: item["value"] for item in receipt["facts"]}
    assert facts["kind"] == "design_change"
    assert facts["resourceId"] == "design1"
    assert facts["outcome"] == "Saved changes; validation failed"
    assert facts["error"] == "missing_source"
