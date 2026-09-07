"""Product action authority and outcome vocabulary shared by API, tools and UI."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionPolicy:
    capability: str
    approval_field: str | None
    label: str
    completed: str
    scope: str


ACTION_POLICIES = {
    "design_change": ActionPolicy("design_changes", "design_approval_required", "Edit workspace design", "SAVED TO DESIGN", "Changes are saved to the workspace design. Live migration is a separate action."),
    "migration_review": ActionPolicy("design_changes", None, "Prepare migration review", "REVIEW READY", "A migration review does not apply changes to PostgreSQL."),
    "migration_apply": ActionPolicy("migration_apply", "migration_approval_required", "Apply & recover migrations", "MIGRATION SUBMITTED", "The exact reviewed plan is submitted to the migration worker. Check its execution receipt for commit outcome."),
    "migration_resolve": ActionPolicy("migration_apply", "migration_approval_required", "Apply & recover migrations", "CONFLICT CHOICES SAVED", "Updates the saved design and baseline with the chosen conflict resolutions. No live SQL is applied. A fresh migration review is required."),
    "migration_reconcile": ActionPolicy("migration_apply", "migration_approval_required", "Apply & recover migrations", "OUTCOME CHECKED", "Checks transaction evidence without replaying migration SQL. May synchronize the baseline; the receipt can still report an uncertain outcome or a design synchronization conflict."),
    "design_history": ActionPolicy("design_history", "history_approval_required", "Undo, redo & reset design", "DESIGN HISTORY UPDATED", "Changes the saved design, not the live database."),
    "structured_query": ActionPolicy("structured_query", "structured_query_approval_required", "Browse table rows", "READ COMPLETED", "Runs a structured read; returned values are temporary bounded samples."),
    "data_read": ActionPolicy("raw_sql_read", "read_approval_required", "Run read SQL", "READ COMPLETED", "Runs read-only SQL; returned values are temporary bounded samples."),
    "sql_write": ActionPolicy("sql_write_execute", "write_approval_required", "Execute write SQL", "WRITE COMPLETED", "Runs the approved SQL batch in one transaction and commits on success; failure rolls it back."),
    "console_script": ActionPolicy("raw_sql_write", None, "Prepare write SQL", "DRAFT READY", "Prepares a Console draft only. No SQL is executed."),
}


def policy_kind(action_type, action):
    return "structured_query" if action_type == "data_read" and (action.get("structuredQueries") or action.get("structuredRead")) else action_type


def requires_approval(capabilities, action_type, action):
    modes = action_modes(capabilities)
    if action_type == "design_change" and "requiredActions" not in action:
        # Previously saved proposals lack the effect classification. Never
        # infer automatic authority from an absent marker; execution rechecks.
        return True
    return any(modes[key] == "ask" for key in required_action_ids(action_type, action))


def read_capabilities(action, fallback=None):
    """Server-recorded replay origins survive subsequent result releases."""
    return tuple(dict.fromkeys(action.get("replayCapabilities") or [fallback or
        ACTION_POLICIES[policy_kind("data_read", action)].capability]))


def operation_receipt(operation):
    policy = ACTION_POLICIES[operation.kind]
    return {"operationId": operation.id, "kind": operation.kind, "status": operation.status,
            "resourceKind": operation.resource_kind, "resourceId": operation.resource_id,
            "outcome": operation.result_summary, "meaning": policy.scope,
            "error": operation.error_code if operation.status != "succeeded" else None}


def _camel(value):
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


def policy_descriptors():
    return [{"kind": kind, "capability": _camel(policy.capability),
             "approvalField": _camel(policy.approval_field) if policy.approval_field else None,
             "label": policy.label, "completed": policy.completed, "scope": policy.scope}
            for kind, policy in ACTION_POLICIES.items()]


DESIGN_COLLECTIONS = ("tables", "columns", "keys", "checks", "indexes", "relationships", "types", "functions", "views", "triggers")
PERMISSIONS = {
    **{f"{collection}.{verb}": (f"{verb.title()} {collection}", "Design", "design_changes", "design_approval_required")
       for collection in DESIGN_COLLECTIONS for verb in ("create", "update", "delete")},
    "migration.review": ("Prepare migration review", "Migrations", "design_changes", None),
    "migration.apply": ("Apply migration", "Migrations", "migration_apply", "migration_approval_required"),
    "migration.resolve": ("Resolve migration conflicts", "Migrations", "migration_apply", "migration_approval_required"),
    "migration.reconcile": ("Reconcile migration outcome", "Migrations", "migration_apply", "migration_approval_required"),
    **{f"history.{verb}": (f"{verb.title()} design", "History", "design_history", "history_approval_required") for verb in ("undo", "redo", "reset")},
    "query.browse": ("Browse table rows", "Queries", "structured_query", "structured_query_approval_required"),
    "query.read": ("Run read SQL", "Queries", "raw_sql_read", "read_approval_required"),
    "query.write": ("Execute write SQL", "Queries", "sql_write_execute", "write_approval_required"),
    "query.draft": ("Prepare SQL draft", "Queries", "raw_sql_write", None),
}


def permission_descriptors():
    descriptions = {
        "migration.review": "Prepare a plan without changing PostgreSQL.",
        "migration.apply": "Submit the reviewed plan to change the live database.",
        "migration.resolve": "Save external-change conflict choices; requires a fresh migration review.",
        "migration.reconcile": "Check an uncertain outcome and synchronize metadata without replaying SQL.",
        "history.undo": "Undo a saved design revision, including its object changes; does not undo live SQL.",
        "history.redo": "Restore an undone design revision, including its object changes; does not execute live SQL.",
        "history.reset": "Reset the saved design to its baseline; may discard all pending design edits.",
        "query.browse": "Run structured table reads using filters, sorting and counts.",
        "query.read": "Run read-only SQL, including joins and aggregates.",
        "query.write": "Execute and commit raw SQL. This broad authority can change data or schema independently of design-edit permissions.",
        "query.draft": "Prepare SQL in the Console without executing it.",
    }
    return [{"id": key, "label": entry[0], "group": entry[1],
             "description": descriptions.get(key, "Saved design only. Contained object changes require their own permissions."),
             "destructive": key.endswith(".delete") or key in {"history.reset", "query.write", "migration.apply"}}
            for key, entry in PERMISSIONS.items()]


def action_modes(capabilities):
    explicit = getattr(capabilities, "action_modes", None)
    if explicit is not None:
        return {key: explicit.get(key, "disabled") for key in PERMISSIONS}
    return {key: ("disabled" if not getattr(capabilities, enabled, False) else
                  "ask" if approval and getattr(capabilities, approval, True) else "automatic")
            for key, (_, _, enabled, approval) in PERMISSIONS.items()}


def required_action_ids(kind, action):
    if kind == "design_change":
        # Actual effects are classified by the server against the current design.
        return tuple(action.get("requiredActions", ()))
    if kind == "design_history":
        return ("history.reset" if action.get("reset") else f"history.{action.get('direction', 'undo')}",)
    if kind == "data_read":
        return tuple("query.browse" if origin == "structured_query" else "query.read" for origin in read_capabilities(action))
    return ({"migration_review": "migration.review", "migration_apply": "migration.apply",
             "migration_resolve": "migration.resolve", "migration_reconcile": "migration.reconcile",
             "structured_query": "query.browse", "sql_write": "query.write", "console_script": "query.draft"}[kind],)


def disabled_action_ids(capabilities, kind, action):
    modes = action_modes(capabilities)
    return tuple(key for key in required_action_ids(kind, action) if modes.get(key, "disabled") == "disabled")
