"""Shared, privacy-safe accounting for limits outside the HTTP error boundary.

Only known error codes and numeric limits are recorded. In particular, provider
diagnostics, prompts, queries, rows and model definitions never enter this log.
"""
import logging

from schemii.common.metadata.limit_events import LimitEventNotice, new_limit_event

LOGGER = logging.getLogger("schemii.ai.limits")

_POLICY_LIMITS = {
    "ai_prompt_too_large": "prompt_bytes",
    "ai_prompt_limit": "prompt_bytes",
    "ai_context_too_large": "context_bytes",
    "context_too_large": "context_bytes",
    "ai_tool_context_limit": "context_bytes",
    "response_too_large": "response_bytes",
    "timeout": "provider_timeout_seconds",
    "ai_tool_timeout": "tool_timeout_seconds",
    "ai_read_time_limit": "tool_timeout_seconds",
    "ai_read_batch_limit": "maximum_read_queries_per_batch",
    "ai_tool_round_limit": "maximum_tool_rounds",
    "ai_proposal_limit_reached": "maximum_proposals_per_turn",
    "ai_proposal_size_limit_reached": "proposal_bytes_per_turn",
}


def limit_notice(error, policy, resource="ai_turn"):
    """Resolve an explicit limit or a known application/runtime policy failure."""
    notice = getattr(error, "limit_event", None)
    if isinstance(notice, LimitEventNotice):
        return notice
    # Existing repositories carry capacity facts without importing HTTP errors.
    if hasattr(error, "configured_limit") and hasattr(error, "limit_name"):
        name = error.limit_name
        return LimitEventNotice(
            getattr(error, "resource", resource),
            name if "." in name else "ai." + name,
            error.configured_limit, getattr(error, "observed_value", None),
        )
    name = _POLICY_LIMITS.get(getattr(error, "code", None))
    if name is not None and policy is not None:
        return LimitEventNotice(resource, "ai." + name, getattr(policy, name))
    return None


def record_ai_limit(recorder, error, policy, owner, resource="ai_turn", *, workspace_id=None):
    """Record swallowed/background failures once; logging cannot mask recovery."""
    notice = limit_notice(error, policy, resource)
    if notice is None or getattr(error, "_ai_limit_recorded", False):
        return
    try:
        recorder.record(new_limit_event(
            notice, error_code=getattr(error, "code", "ai_capacity_reached"),
            owner_id=owner, workspace_id=workspace_id,
        ))
        error._ai_limit_recorded = True
    except Exception:
        # Avoid exception text/tracebacks, which could expose provider payloads.
        LOGGER.warning("AI limit event could not be recorded in metadata")
