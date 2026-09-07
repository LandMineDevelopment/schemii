"""Bounded durable action receipts, never query values or provider transcripts."""
import json
from .action_policy import ACTION_POLICIES


EFFECTS = {
    "design_change": "Saved to desired design only; live migration is separate.",
    "console_script": "Console draft prepared only; SQL has NOT been executed by this operation.",
    "migration_review": "Migration review prepared only; this operation does NOT apply it.",
    "data_read": "Read-only query execution; no database changes.",
}
EFFECTS.update({kind: policy.scope for kind, policy in ACTION_POLICIES.items()})


def intent_key(action, existing_ids):
    """Ignore generated creation IDs, never replacement IDs or references."""
    if isinstance(action, list):
        return [intent_key(item, existing_ids) for item in action]
    if isinstance(action, dict):
        return {key: ("<new>" if key == "id" and value not in existing_ids
                      else intent_key(value, existing_ids)) for key, value in action.items()}
    return action


def object_ids(value):
    if isinstance(value, dict):
        return ({value["id"]} if isinstance(value.get("id"), str) else set()).union(
            *(object_ids(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(object_ids(item) for item in value))
    return set()


def action_context(repository, owner, chat_id, byte_budget):
    proposals = repository.list_proposals(owner, chat_id)
    operations = {item.proposal_id: item for item in repository.list_operations(owner, chat_id)}
    entries = []
    used = 0
    for proposal in reversed(proposals):
        operation = operations.get(proposal.id)
        entry = {"proposalId": proposal.id, "status": proposal.status,
                 "kind": proposal.action_type, "summary": proposal.summary,
                 "expectedDesignRevision": proposal.expected_design_revision,
                 "expiresAt": proposal.expires_at.isoformat(),
                 "successMeaning": EFFECTS[proposal.action_type]}
        # Design actions and SQL drafts are durable intent, not result values.
        if proposal.action_type in {"design_change", "console_script"}:
            entry["action"] = repository.proposal_action(owner, chat_id, proposal.id)
        if operation:
            entry["operation"] = {"id": operation.id, "status": operation.status,
                                  "resourceKind": operation.resource_kind,
                                  "resourceId": operation.resource_id,
                                  "errorCode": operation.error_code}
        size = len(json.dumps(entry, default=str).encode())
        if used + size > byte_budget:
            continue
        entries.append(entry)
        used += size
    return {"entries": list(reversed(entries)), "omittedCount": len(proposals) - len(entries),
            "notice": "These are action receipts, not proof of current live schema. Check liveCatalog for current objects. Never treat a draft or saved design as an applied migration."}
