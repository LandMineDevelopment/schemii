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
    "raw_console": ActionPolicy("raw_console", "raw_console_approval_required", "Use database console", "CONSOLE ACTION COMPLETED", "Uses the exact database session; check the execution and transaction status."),
    "app_action": ActionPolicy("app_actions", "app_approval_required", "Use application features", "APPLICATION ACTION COMPLETED", "Uses the existing application service with the current user identity."),
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


def read_capabilities(action):
    """Infer diagnostics from SQL, retaining server-recorded replay origins.

    Replayed structured SQL must keep its structured authority rather than
    acquiring raw SQL permission. Diagnostic authority is always checked from
    SQL as well, including historical proposals without diagnostic markers.
    """
    from schemii.common.postgres.query_plans import query_authorities
    origins = list(action.get("replayCapabilities") or [])
    if action.get("structuredQueries") or action.get("structuredRead"):
        origins.append("structured_query")
    else:
        queries = action.get("queries") or [{"sql": action.get("sql", "")}]
        for query in queries:
            for authority in query_authorities(query["sql"]):
                if authority == "read":
                    if not action.get("replayCapabilities"):
                        origins.append("raw_sql_read")
                else:
                    origins.append({"explain": "explain_queries", "analyze": "analyze_queries"}[authority])
    return tuple(dict.fromkeys(origins))


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
    "query.explain": ("Explain query plans", "Queries", "explain_queries", "explain_approval_required"),
    "query.analyze": ("Run & analyze query plans", "Queries", "analyze_queries", "analyze_approval_required"),
    "query.read": ("Run read SQL", "Queries", "raw_sql_read", "read_approval_required"),
    "query.write": ("Execute write SQL", "Queries", "sql_write_execute", "write_approval_required"),
    "query.draft": ("Prepare SQL draft", "Queries", "raw_sql_write", None),
}


from .raw_actions import OPERATIONS
PERMISSIONS.update({f"console.{op}": (op.replace("_", " ").title() + " console", "Database console", "raw_console", "raw_console_approval_required") for op in OPERATIONS})
from .app_actions import APP_PERMISSIONS
PERMISSIONS.update(APP_PERMISSIONS)


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
        "query.read": "Run read-only SQL, including joins and aggregates. Query plans require their separate permissions.",
        "query.explain": "Request estimated PostgreSQL plans without running the query. Result inspection is controlled separately.",
        "query.analyze": "Execute a query to measure its PostgreSQL plan. Managed reads remain read-only; raw console analysis also requires console execution authority and can modify data. Result inspection is controlled separately.",
        "query.write": "Execute SQL in one managed transaction and commit on success. Can change data or schema independently of design-edit permissions; does not grant access to the human Console session or transaction controls.",
        "query.draft": "Prepare SQL in the Console without executing it.",
    }
    descriptions.update({key: entry[0] + ". Uses the existing application operation with your user identity and current resource revisions." for key, entry in APP_PERMISSIONS.items()})
    descriptions.update({key: descriptions[key] + " Passwords stay in the dedicated connection credential form and are never returned to the assistant." for key in APP_PERMISSIONS if key.startswith("connection.")})
    descriptions.update({f"console.{op}": "Use the existing user-owned database session. Session revisions protect reviewed work; returned row values need Analyze query results." for op in OPERATIONS})
    descriptions.update({"console.execute": "Execute exact SQL using the database credentials. Transaction controls, auto-commit and diagnostics require their separate permissions.", "console.close": "Close a database session and roll back its pending work.", "console.commit": "Commit the reviewed transaction or allow an explicitly selected automatic commit policy.", "console.rollback": "Roll back pending work in the reviewed transaction.", "console.copy_upload": "Prepare an authorized COPY FROM STDIN browser upload; the user selects a file.", "console.copy_download": "Prepare an authorized COPY TO STDOUT browser download; rows are not sent to the model."})
    return [{"id": key, "label": entry[0], "group": entry[1],
             "description": descriptions.get(key, "Saved design only. Contained object changes require their own permissions."),
             "destructive": key.endswith(".delete") or key in {"history.reset", "query.write", "migration.apply", "console.execute", "console.commit", "console.rollback", "console.close", "console.copy_upload"}}
            for key, entry in PERMISSIONS.items()]


def action_modes(capabilities):
    explicit = getattr(capabilities, "action_modes", None)
    if explicit is not None:
        return {key: explicit.get(key, "disabled") for key in PERMISSIONS}
    return {key: ("disabled" if not getattr(capabilities, enabled, False) else
                  "ask" if approval and getattr(capabilities, approval, True) else "automatic")
            for key, (_, _, enabled, approval) in PERMISSIONS.items()}


def required_action_ids(kind, action):
    if kind == "raw_console":
        from .raw_actions import required_actions
        return required_actions(action)
    if kind == "app_action":
        from .app_actions import permission_id
        return (permission_id(action),)
    if kind == "design_change":
        # Actual effects are classified by the server against the current design.
        return tuple(action.get("requiredActions", ()))
    if kind == "design_history":
        return ("history.reset" if action.get("reset") else f"history.{action.get('direction', 'undo')}",)
    if kind == "data_read":
        return tuple({"structured_query": "query.browse", "raw_sql_read": "query.read",
                      "explain_queries": "query.explain", "analyze_queries": "query.analyze"}[origin]
                     for origin in read_capabilities(action))
    return ({"migration_review": "migration.review", "migration_apply": "migration.apply",
             "migration_resolve": "migration.resolve", "migration_reconcile": "migration.reconcile",
             "structured_query": "query.browse", "sql_write": "query.write", "console_script": "query.draft"}[kind],)


def disabled_action_ids(capabilities, kind, action):
    modes = action_modes(capabilities)
    return tuple(key for key in required_action_ids(kind, action) if modes.get(key, "disabled") == "disabled")
