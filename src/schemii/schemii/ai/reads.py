"""Server-owned AI reads: durable query references, transient result values.

The console remains the single SQL safety/execution boundary. A bundle uses
separate managed read transactions so one invalid query does not discard others.
"""
from types import SimpleNamespace
import json
import time


def _error(status, code, message):
    from .service import AiServiceError
    return AiServiceError(status, code, message)


def execute_batch(service, owner, chat, proposal, operation, action):
    from .actions import RelationRead, relation_read_query
    from .action_policy import read_capabilities, requires_approval
    required = read_capabilities(action, proposal.capability)
    current = service.repository.get_chat(owner, chat.id)
    if current.revision != chat.revision or any(not getattr(current.capabilities, capability, False) for capability in required):
        raise _error(403, "ai_permission_changed", "Read permissions changed; no queries were run")
    queries = ([relation_read_query(service.services, owner, chat.workspace_id, RelationRead.model_validate(item))
                for item in action["structuredQueries"]] if action.get("structuredQueries")
               else action.get("queries") or [{"label": "Query", "sql": action["sql"]}])
    if len(queries) > service.policy.maximum_read_queries_per_batch:
        raise _error(422, "ai_read_batch_limit", "The read batch exceeds the configured query limit")
    results = []
    deadline = time.monotonic() + service.policy.tool_timeout_seconds
    for index, query in enumerate(queries):
        current = service.repository.get_chat(owner, chat.id)
        turn = service.repository.get_turn(owner, chat.id, proposal.turn_id)
        if current.revision != chat.revision or any(not getattr(current.capabilities, capability, False) for capability in required) or turn.status == "cancelled":
            raise _error(403, "ai_permission_changed", "Read permissions changed or the request was cancelled; remaining queries were not run")
        if time.monotonic() >= deadline:
            raise _error(429, "ai_read_time_limit", "The read batch reached its time limit; remaining queries were not run")
        try:
            workspace = service.services.workspaces.get(owner, chat.workspace_id)
            execution = service._run_query(owner, chat.workspace_id, workspace.revision, query["sql"])
            result = execution.results[0]
            rerun_ids = action.get("rerunOf", [])
            run = service.repository.create_read_run(owner, chat.id, proposal.turn_id, operation.id,
                query["sql"], query["label"], execution.id, result.id, result.row_count,
                result.has_more, rerun_ids[index] if index < len(rerun_ids) else None)
            results.append({"runId": run.id, "label": run.label, "executionId": execution.id,
                            "resultId": result.id, "rowCount": result.row_count, "hasMore": result.has_more})
        except Exception as error:
            # Database errors can contain actual row values. Persist a code only.
            code = getattr(error, "code", "ai_query_failed")
            results.append({"label": query["label"], "errorCode": code,
                            "errorMessage": "This read failed. Check its SQL, target and database permissions."})
        service.repository.add_event(owner, chat.id, "operation", {"turnId": proposal.turn_id,
            "operationId": operation.id, "completedQueries": len(results), "totalQueries": len(queries)})
    return {"results": results, "authorization": {
        "mode": "user_approved" if requires_approval(chat.capabilities, "data_read", action) else "automatic_read_policy",
        "policyRevision": chat.revision,
        "proposalId": proposal.id,
    }}


def _page(service, owner, chat, run):
    page = service.services.console.page(owner, chat.workspace_id, run.execution_id, run.result_id, None)
    rows = service._bounded_rows(page.rows)
    operation = service.repository.get_operation(owner, chat.id, run.operation_id)
    return {"runId": run.id, "operationId": run.operation_id, "label": run.label, "sql": run.sql,
            "authorization": (operation.result_summary or {}).get("authorization"),
            "executedAt": run.executed_at.isoformat(), "columns": [c.model_dump(by_alias=True) for c in page.columns],
            "rows": rows, "rowCount": run.row_count, "hasMore": run.has_more or bool(page.next_cursor),
            "sampled": bool(page.next_cursor) or len(rows) < len(page.rows),
            "rerun": run.rerun_of is not None, "rerunOf": run.rerun_of,
            "freshnessNotice": "This is a new execution of an earlier query; data may have changed. It is not the original snapshot." if run.rerun_of else None}


def _bounded_bundle(service, results):
    # One context budget across the entire bundle, not one budget per query.
    budget = service.policy.result_context_bytes
    remaining_rows = service.policy.result_context_rows
    for index, result in enumerate(results):
        values = result.get("rows", [])
        kept = []
        share_rows = remaining_rows // max(1, len(results) - index)
        share_bytes = budget // max(1, len(results) - index)
        for row in values:
            size = len(json.dumps(row, default=str).encode())
            if len(kept) >= share_rows or size > share_bytes:
                break
            kept.append(row)
            remaining_rows -= 1
            budget -= size
            share_bytes -= size
        if len(kept) != len(values):
            result["sampled"] = True
        if "rows" in result:
            result["rows"] = kept
        result["returnedRows"] = len(kept)
    return {"results": results, "snapshot": "Each query is a separate read-only transaction.",
            "sampleNotice": "Values are bounded samples. Use aggregate queries for complete counts or comparisons."}


def operation_context(service, owner, chat_id, operation_id, *, for_model=True):
    operation = service.repository.get_operation(owner, chat_id, operation_id)
    chat = service.repository.get_chat(owner, chat_id)
    results = []
    for item in (operation.result_summary or {}).get("results", []):
        if "runId" not in item:
            results.append(dict(item))
            continue
        if for_model and not chat.capabilities.structured_data_read:
            results.append({**item, "permissionRequired": "Analyze query results", "message": "Enable Analyze query results in Assistant settings to let the assistant see query values."})
            continue
        run = service.repository.get_read_run(owner, chat_id, item["runId"])
        try:
            results.append(_page(service, owner, chat, run))
        except Exception as error:
            if getattr(error, "code", "") != "console_result_replay_required":
                raise
            results.append({"runId": run.id, "label": run.label, "sql": run.sql,
                            "released": True, "message": "Result released. Use schemii_get_read_results to request a new execution under current approval policy."})
    return {**_bounded_bundle(service, results),
            "authorization": (operation.result_summary or {}).get("authorization")}


def tool_result(service, owner, chat_id, name, arguments, turn_id):
    chat = service.repository.get_chat(owner, chat_id)
    if not chat.capabilities.structured_data_read:
        raise _error(403, "ai_permission_required", "Enable Analyze query results in Assistant settings")
    if name == "schemii_list_read_runs":
        return {"runs": [run.model_dump(by_alias=True, mode="json") for run in service.repository.list_read_runs(owner, chat_id)]}
    ids = arguments.get("runIds", [])
    if not isinstance(ids, list) or not ids or len(ids) > service.policy.maximum_read_queries_per_batch or any(not isinstance(i, str) for i in ids):
        raise _error(422, "ai_read_batch_limit", f"Choose between 1 and {service.policy.maximum_read_queries_per_batch} known run IDs")
    results, released = [], []
    for run_id in dict.fromkeys(ids):
        from .repository import AiNotFoundError
        try:
            run = service.repository.get_read_run(owner, chat_id, run_id)
        except AiNotFoundError:
            raise _error(404, "ai_read_run_not_found", "A read reference is not available in this conversation. Call schemii_list_read_runs and use its exact run IDs; do not guess IDs or substitute execution/result IDs.") from None
        if arguments.get("refresh", False):
            released.append(run)
            continue
        try:
            results.append(_page(service, owner, chat, run))
        except Exception as error:
            if getattr(error, "code", "") != "console_result_replay_required":
                raise
            released.append(run)
    if released:
        from .action_policy import read_capabilities, requires_approval
        from .tools import permission_label
        origins = set()
        for run in released:
            original = service.repository.get_proposal(owner, chat_id,
                service.repository.get_operation(owner, chat_id, run.operation_id).proposal_id)
            origins.update(read_capabilities(service.repository.proposal_action(owner, chat_id, original.id), original.capability))
        missing = next((capability for capability in sorted(origins) if not getattr(chat.capabilities, capability, False)), None)
        if missing:
            return {**_bounded_bundle(service, results),
                    "permissionRequired": permission_label(missing),
                    "message": f"Earlier results were released. Enable {permission_label(missing)} to rerun them; historical values cannot be recovered."}
        proposal = service._save_tool_proposal(owner, chat, turn_id, "schemii_read_query",
            {"summary": "Rerun earlier reads; data may have changed", "queries": [{"label": run.label, "sql": run.sql} for run in released]},
            rerun_of=[run.id for run in released], replay_capabilities=origins)
        replay_action = service.repository.proposal_action(owner, chat_id, proposal.id)
        if requires_approval(chat.capabilities, "data_read", replay_action):
            return {**_bounded_bundle(service, results), "approvalRequired": True, "proposalId": proposal.id}
        operation = service.execute(owner, chat_id, proposal.id, SimpleNamespace(expected_chat_revision=chat.revision,
            expected_proposal_revision=proposal.revision, proposal_digest=proposal.digest))
        results.extend(operation_context(service, owner, chat_id, operation.id)["results"])
    return _bounded_bundle(service, results)
