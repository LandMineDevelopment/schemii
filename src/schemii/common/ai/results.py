"""Bound a temporary query page before it reaches a provider."""
import json
from schemii.common.admin_config import AiPolicy
from schemii.common.api.errors import ApiProblem


def bounded_page(services, result):
    policy = getattr(getattr(services, "admin_config", None), "ai", None) or AiPolicy()
    rows = result["rows"]
    result["rows"] = []
    result["sampling"] = {"pageRows": len(rows), "returnedRows": 0,
        "maximumRows": policy.result_context_rows, "maximumBytes": policy.result_context_bytes,
        "truncated": bool(rows),
        "notice": "This is a bounded sample of one result page. Its next cursor advances past the whole page; omitted rows are not included in this sample."}
    def size():
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size() > policy.result_context_bytes:
        raise ApiProblem(413, "ai_result_metadata_too_large", "The result's column metadata exceeds the assistant sample limit. Select fewer fields.")
    for row in rows[:policy.result_context_rows]:
        result["rows"].append(row)
        result["sampling"]["returnedRows"] = len(result["rows"])
        if size() + 1 > policy.result_context_bytes:
            result["rows"].pop()
            result["sampling"]["returnedRows"] = len(result["rows"])
            break
    result["sampling"]["truncated"] = len(result["rows"]) < len(rows)
    return result
