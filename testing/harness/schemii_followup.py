"""Generate the private, second-wave Schemii UI acceptance manifest.

This module only reads retained fixture files and writes a new manifest. It does
not provision accounts, seed designs, touch the application, or start a runner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from testing.harness.schemii_sweep import ACCOUNTS, ORACLE, ROOT, WRITERS


ID = re.compile(r"^(?:ws|pg|chat)_[0-9a-f]{32}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
PRESEEDED = {"qa_designer_005", "qa_designer_007", "qa_designer_008", "qa_designer_011"}
PRESEED_TABLES = ["qa_projects", "qa_tasks"]
ROW_SELECTOR = "#design-columns > .design-column-row:nth-child(N) [data-design-column-name]"

# Five broad, multistep scenarios per exact account. Each is expanded by the
# harness to independent desktop and 390x844 mobile results.
MISSIONS = {
    "qa_designer_004": ("Connection and workspace recovery", [
        ("connection-targets", "Reader and writer identity", "Compare the retained reader and isolated qa_write_designer_004 profiles in connection list, test each connection, open each exact workspace and verify database/schema/owner and permissions. Check reader write denial using a harmless attempted scratch CREATE, with no baseline mutation."),
        ("connection-disposable", "Disposable connection lifecycle", "Create a prefixed personal connection to the assigned writer target, test it, rename it, change a nonsecret setting, reject duplicate name and invalid port, then delete with confirmation. Inspect connection list after reload; never delete retained profiles."),
        ("workspace-switch", "Workspace target switching", "Open owned local, reader and writer workspace URLs in sequence. Create a disposable prefixed local workspace, rename/reload, then delete only that disposable workspace. Confirm selected target and catalog/design state never leak across workspaces."),
        ("workspace-errors", "Validation and recovery", "Try blank and duplicate workspace names, cancel deletion, test a temporarily invalid disposable connection setting, restore it, and confirm no extra objects or changed retained connection after reload."),
        ("connection-responsive", "Connection controls and mobile", "Traverse navigation, connection form, test feedback, workspace picker and delete dialog with keyboard and pointer. Capture desktop/mobile layout, focus, clipping and recovery; compare functionality and visual state separately."),
    ]),
    "qa_designer_005": ("Catalog, seeded design and export", [
        ("catalog-baseline", "Exact live catalog", "On reader workspace inspect customers 32, orders 513, customer_totals 32, fixture_version 1 and orders_id_seq in the live PostgreSQL catalog where available. The desired-design object browser does not list live sequences. Filter exact and absent objects, inspect columns, identity, FK, CHECK and nullability; clear filter and verify catalog recovery."),
        ("catalog-paging", "Rows, lineage and target", "Preview all 513 orders with real page progression, first/last IDs, status totals 171 each; inspect customer 1's 16 orders and 5962.24 total in customer_totals. Follow relationship/lineage and return to the same reader target."),
        ("seeded-export", "Seeded JSON and SQL downloads", "On local preseeded design, confirm both qa_projects and qa_tasks plus exact types/defaults/identity from oracle. Open export menu, use harness download for desired design JSON and PostgreSQL SQL, inspect actual files for names, FK, UNIQUE, CHECK, numeric precision and identity. Reload and repeat after one owned saved edit."),
        ("import-upload", "Owned import and round trip", "Export the seeded design JSON with harness download, inspect it, then use harness upload on the exact file input in a newly created prefixed local workspace. Confirm import summary, target, saved content and independent source design after reload; delete only the disposable import workspace."),
        ("export-recovery", "Empty, invalid and responsive export", "Exercise empty owned design export and a malformed owned JSON upload (<=1 MiB), verify clear error without partial save. Inspect menu, download controls, long SQL/JSON and catalog at desktop/mobile; report unsupported UI operations as blocked."),
    ]),
    "qa_designer_006": ("Empty local design and table creation", [
        ("empty-create", "Create table from empty canvas", "Confirm owned local design is empty. Open toolbar with #create-table-button, snapshot dialog, create a prefixed disposable probe table using row-specific selectors, submit with #save-design-table-button once, reload and confirm persistence. Delete only the probe table and verify empty state before continuing."),
        ("typed-projects", "Exact qa_projects columns", "On empty local design create qa_projects: project_id bigint GENERATED ALWAYS AS IDENTITY PK, code varchar(24) NOT NULL UNIQUE, title text NOT NULL, budget numeric(12,2) NOT NULL DEFAULT 0 CHECK budget>=0, due_on date NULL. Use row-specific selector for each column; inspect saved JSON and SQL, logging each unsupported property."),
        ("typed-tasks", "Exact dependent qa_tasks", "Create qa_tasks with task_id bigint identity PK, project_id bigint NOT NULL FK to qa_projects.project_id, state varchar(16) NOT NULL DEFAULT 'todo' CHECK allowed states, effort_hours numeric(6,2) NULL and notes text NULL. Verify relationship, persistence and export."),
        ("column-validation", "Repeated field and validation recovery", "Edit a specific middle row via row selector; test blank/duplicate name, numeric length/scale and nullability errors. Check no partial save, correct row keeps focus, then make one valid change and reload. Never retry an ambiguous mutating click."),
        ("empty-mobile", "Empty and populated mobile canvas", "At desktop and 390x844 mobile compare empty guidance, create dialog scrolling, all column rows, save feedback and populated table diagram; capture visual and functional evidence separately."),
    ]),
    "qa_designer_007": ("Seeded tables, constraints and canvas", [
        ("seeded-keys", "Inspect preseeded keys and FK", "On local seeded design verify qa_projects and qa_tasks exact oracle columns, bigint identity PKs, UNIQUE code and project_id FK. Compare inspector, canvas relationship and desired SQL after reload; report actual lossiness."),
        ("constraint-edit", "Checks, defaults and indexes", "Edit only seeded scratch tables: inspect budget>=0 and state allowed values, default 0/'todo', add then edit a qa_tasks(project_id,state) index and predicate state<>'done'. Confirm exported SQL, undo and reload."),
        ("row-specific-edit", "Target repeated column controls", "Open qa_tasks editor, snapshot first, then target a specific column name using #design-columns > .design-column-row:nth-child(N) [data-design-column-name] with actual N. Rename notes to detail, change effort_hours numeric(6,2) to numeric(8,2), save once and verify no other row changed."),
        ("canvas-history", "Layout, undo and redo", "Pan/zoom/fit and drag seeded tables; save/reload layout. Add scratch boolean flag default false, undo, redo, then remove it. Verify design revision and relationship endpoints at each committed state."),
        ("dependency-delete", "Dependency warning and mobile canvas", "Attempt deleting referenced qa_projects and indexed qa_tasks.project_id, cancel warnings, then inspect dependency order without destructive apply. Compare saved design, SQL and 390x844 canvas clipping/focus/scrolling."),
    ]),
    "qa_designer_001": ("Serialized chat-only acceptance", [
        ("chat-reader", "Reader chat and exact answers", "Use the precreated permissioned reader chat in Schemii AI only. Send exactly one prompt asking for baseline counts 32 customers, 513 orders, status counts 171 each and sum 180622.17. Wait for completed response before another prompt; compare target and answers to oracle, capture reply and any proposal."),
        ("chat-design", "Design chat proposal review", "Switch to the precreated permissioned local design chat. Send one prompt for exact qa_projects and qa_tasks oracle. Wait for completion, inspect every type/default/identity/FK/CHECK and target. Reject incomplete proposal; approve only one exact owned proposal after review; verify saved design by a later serialized chat readback."),
        ("chat-revision", "Serialized revision and persistence", "In the local chat, after previous turn is idle, request notes->detail and an index on qa_tasks(project_id,state). Review proposal scope and target, approve only if exact. Wait for operation receipt, reload chat, send a separate readback prompt and compare saved schema."),
        ("chat-history", "History and interrupted turn", "Rename only owned precreated conversation, switch reader/local chats, reload and verify messages and permissions. Start a disposable short prompt, cancel if streaming, wait for idle, and check no duplicate turn or proposal before continuing."),
        ("chat-boundary", "Permission boundary and mobile", "Send a single prompt requesting read of another lane and mutation of retained baseline. Verify denial or approval boundary; never approve out-of-scope actions. Inspect model/default reasoning, grants, long response, action cards, focus and overflow at both viewports."),
    ]),
    "qa_designer_008": ("Seeded views and advanced design", [
        ("seeded-view", "View from exact seeded tables", "Confirm local preseeded qa_projects/qa_tasks and exact oracle; create qa_project_totals ordinary view over code varchar(24) and budget numeric(12,2). Check SQL definition, dependency, save/reload/export, invalid query recovery."),
        ("materialized-view", "Materialized view lifecycle", "Create a test-owned materialized counterpart if UI supports it, inspect populate flag and dependencies, edit definition, save/reload/export and delete with confirmation. Record API-only or missing control as blocked, not passed."),
        ("types", "Enum and domain support", "Create qa_task_state enum todo/doing/done and qa_nonempty_code domain over varchar(24) with nonempty check if UI supports them. Apply to scratch fields, test duplicate/invalid input, inspect desired SQL and reload."),
        ("routine-trigger", "Routine and trigger dependency", "Create scratch numeric function qa_add_budget(numeric(12,2),numeric(12,2)) and a trigger against owned qa_projects if UI supports them. Validate body/timing, inspect dependency warning, saved SQL and delete cleanup. Do not execute on reader baseline."),
        ("advanced-responsive", "Search, error and long-form layout", "Search view/type/routine groups, clear and try invalid blank names or SQL, cancel and restore. Compare desktop/mobile long SQL body, focus order, scrolling and seeded object visibility; capture each unsupported control with evidence."),
    ]),
    "qa_designer_009": ("Read-only SQL and paging", [
        ("sql-oracle", "Known-result SQL reads", "On exact reader target run bounded SELECT counts for customers 32, orders 513, customer_totals 32, status groups 171 each, SUM(amount)=180622.17, customer 1 total 5962.24. Also record SELECT pg_database_size(current_database()) AS bytes against prior 33724083-byte (32.2 MiB) baseline; database relation size is not WAL or Docker volume usage. Verify target and result columns."),
        ("sql-page-grid", "513-row paging and horizontal scroll", "SELECT id,customer_id,ordered_on,status,amount,notes FROM orders ORDER BY id. Inspect page setting, move beyond 100, verify first ID 1/last 513/no duplicates, horizontally scroll to notes and back; pin/release and reload."),
        ("sql-export", "Result CSV download", "With 513 ordered rows, use result export via harness download; inspect actual CSV header and rows and whether export is displayed page versus full result. Use UI paging or documented COPY if needed and report exact limit."),
        ("sql-history", "Tabs, saved queries and replay", "Create two distinct query tabs, selected-statement versus run-all, save prefixed paid-orders query, verify 171 rows. Rename/reopen/delete only saved query, reload history and check result association and draft persistence."),
        ("sql-readonly", "EXPLAIN and safe failure states", "EXPLAIN a bounded customer_id=1 SELECT; cause syntax error and harmless denied CREATE on reader, then recover. Check cancellation, settings validation, grid scrolling and keyboard/mobile overflow without mutating baseline."),
    ]),
    "qa_designer_010": ("Isolated writer SQL, COPY and files", [
        ("writer-schema", "Writer and reader separation", "Open exact qa_write_designer_010 writer URL and verify empty isolated schema. Use SQL Console to create qa_write_items with exact writer oracle only there. Switch reader URL to confirm retained customers 32/orders 513 and denied writes, then return to writer."),
        ("writer-transaction", "Commit, rollback and totals", "Record SELECT pg_database_size(current_database()) AS bytes before bounded writes and compare prior baseline 33724083 bytes (32.2 MiB); this excludes WAL/volume usage. Insert A-100/B-200/C-300 with oracle values in explicit transaction; confirm precommit and durable count 3, qty 5, value 46.75. Update A-100 qty=99 then rollback; reload and verify qty=2. Record database bytes afterward; test negative qty CHECK and duplicate sku UNIQUE."),
        ("copy-upload", "Bounded COPY upload", "Use COPY FROM STDIN only for owned qa_write_items. With harness upload on exact COPY input file selector, send CSV for D-400/E-500/F-600 under 1 MiB; inspect status, then SELECT count/typed rows. Try malformed small CSV, verify no extra rows; total <=5,000."),
        ("copy-download", "COPY full download and grid", "Use COPY TO STDOUT on owned qa_write_items and harness download on the browser download link when shown. Inspect actual file header, six rows and typed values. Compare result grid horizontal scroll and displayed-result CSV to full COPY semantics."),
        ("writer-recovery", "Session, failure and mobile", "Exercise explicit/autocommit mode, bounded duplicate insert failure, transaction rollback, close/reopen session, and refresh. Verify no unintended repeat writes; inspect upload/download dialog, result grid and confirmations at desktop/mobile."),
    ]),
    "qa_designer_011": ("Isolated migration apply", [
        ("migration-seed", "Preseeded desired versus empty live", "On exact qa_write_designer_011 writer workspace verify live schema empty but desired preseeded qa_projects/qa_tasks exact oracle. Review migration target, source revision, SQL for identity, varchar lengths, numeric precision, defaults, PK/UNIQUE/CHECK/FK; cancel if any other schema appears."),
        ("migration-apply", "Apply exact owned migration", "Create a fresh migration plan for preseeded tables, inspect all steps and target qa_write_designer_011, then apply once with required confirmation. Wait for terminal receipt; inspect live catalog and constraints plus history. If outcome uncertain, inspect live schema before any retry."),
        ("migration-data", "Populated safe change", "After confirmed apply, insert at most three owned rows through writer Console, record values. In desired design add nullable reviewed_on date and increase budget precision from numeric(12,2) to numeric(14,2); preview conversion and row retention, apply only safe owned plan, compare all rows after reload."),
        ("migration-drift", "Owned drift and plan recovery", "Make one bounded scratch-only external change in qa_write_designer_011, inspect drift/unsafe conversion choices, cancel first, replan and reconcile only if exact affected objects are owned. Never alter retained baseline or another writer schema."),
        ("migration-final", "No-change review and mobile", "Confirm no-change plan after applied state, compare desired SQL, live catalog, history and revision. Inspect danger text, progress, long SQL, scrolling/focus at desktop/mobile. Leave schema for coordinator cleanup."),
    ]),
    "qa_designer_002": ("AI settings and navigation without shared inference", [
        ("ai-policy", "Active model and permission UI", "Open exact reader workspace and inspect Schemii AI settings: shared gpt-6-luna/default, context/action permissions and current target. Navigate settings tabs and help text, reload and confirm values. Do not send prompts while account 001 performs serialized inference."),
        ("ai-setting-edit", "Owned preference round trip", "On an owned disposable chat or account preference only, change one allowed model/reasoning or permission setting, inspect confirmation, reload, then restore original. Check denied admin-grant controls remain unavailable; no inference prompt."),
        ("ai-navigation", "Cross-feature target continuity", "Navigate reader catalog, local design, SQL Console and AI panel, switch workspace IDs and return. Verify AI header target/permissions track current workspace and no stale chat or result crosses to another target."),
        ("ai-history-empty", "Chat history and empty states", "Open history and settings without invoking model; inspect empty/list/loading behavior, rename/delete only a disposable chat if created via UI, cancel confirmation first, reload and confirm retained chats untouched."),
        ("ai-responsive", "Settings and navigation accessibility", "At desktop and 390x844 inspect AI settings, permission controls, model selector, history, long labels, panels, dialogs, focus order, scrolling/clipping and unavailable-model messaging without sending a model turn."),
    ]),
}


def _id(value: object, kind: str, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value) or not value.startswith(kind + "_"):
        raise ValueError(f"Invalid {label}")
    return value


def _source_lane(source: dict, username: str) -> dict:
    lane = source.get("lanes", {}).get(username)
    if not isinstance(lane, dict) or lane.get("persona") != "designer":
        raise ValueError(f"Missing exact designer fixture for {username}")
    resources = lane.get("resources", {})
    if not isinstance(resources, dict) or resources.get("schema") != username:
        raise ValueError(f"Missing exact reader schema for {username}")
    _id(resources.get("connectionId"), "pg", f"reader connection for {username}")
    if username in WRITERS:
        _id(resources.get("writableConnectionId"), "pg", f"writer connection for {username}")
        if resources.get("writableSchema") != "qa_write_designer_" + username[-3:]:
            raise ValueError(f"Invalid isolated writer schema for {username}")
        if resources["writableConnectionId"] == resources["connectionId"]:
            raise ValueError(f"Writer and reader profiles collide for {username}")
    if username in {"qa_designer_001", "qa_designer_002"}:
        policy = lane.get("chatProvider")
        if not isinstance(policy, dict) or policy.get("providerId") != "instance-codex" or policy.get("modelId") != "gpt-6-luna" or policy.get("reasoningEffort", "default") != "default":
            raise ValueError(f"Missing active shared chat policy for {username}")
    if lane.get("expectedCapabilities") != ["schemii:access"] or not isinstance(lane.get("deniedProducts"), list) or any(
        not isinstance(product, str) or product not in {"schemoo", "schemer"}
        for product in lane["deniedProducts"]
    ):
        raise ValueError(f"Unexpected effective product access for {username}")
    return lane


def build(source: dict, tag: str, workspaces: dict) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tag):
        raise ValueError("tag must be 1..32 alphanumeric, dash or underscore characters")
    if workspaces.get("tag") != tag or set(workspaces.get("lanes", {})) != set(ACCOUNTS):
        raise ValueError("Workspace map must have matching tag and exact ten accounts")
    preseed = workspaces.get("preseed")
    if not isinstance(preseed, dict) or set(preseed) != PRESEEDED:
        raise ValueError("Missing exact four preseeded design entries")
    chat_map = workspaces.get("chat")
    if not isinstance(chat_map, dict) or set(chat_map) != {"qa_designer_001"}:
        raise ValueError("Missing exact permissioned chat metadata for account 001")

    output = {"version": "schemii-followup-qa-v1", "tag": tag, "lanes": {}}
    seen_workspaces: set[str] = set()
    for username in ACCOUNTS:
        fixture = _source_lane(source, username)
        src = fixture["resources"]
        mapped = workspaces["lanes"][username]
        prefix = f"qa_{tag}_{username[-3:]}_"
        if not isinstance(mapped, dict) or mapped.get("prefix") != prefix:
            raise ValueError(f"Workspace prefix mismatch for {username}")
        if any(type(mapped.get(key)) is not bool for key in ("localCreated", "readerCreated", "writerCreated")):
            raise ValueError(f"Missing workspace creation ownership for {username}")
        if username not in WRITERS and mapped["writerCreated"]:
            raise ValueError(f"Unexpected writer ownership for {username}")
        local = _id(mapped.get("localWorkspaceId"), "ws", f"local workspace for {username}")
        reader = _id(mapped.get("readerWorkspaceId"), "ws", f"reader workspace for {username}")
        writer = None
        if username in WRITERS:
            writer = _id(mapped.get("writerWorkspaceId"), "ws", f"writer workspace for {username}")
        elif mapped.get("writerWorkspaceId") is not None:
            raise ValueError(f"Unexpected writer workspace for {username}")
        ids = [local, reader] + ([writer] if writer else [])
        if len(set(ids)) != len(ids) or any(item in seen_workspaces for item in ids):
            raise ValueError(f"Workspace IDs collide for {username}")
        seen_workspaces.update(ids)
        seed = preseed.get(username)
        if seed:
            expected_workspace = writer if username == "qa_designer_011" else local
            if (not isinstance(seed, dict) or seed.get("workspaceId") != expected_workspace
                or seed.get("marker") != prefix or seed.get("tables") != PRESEED_TABLES
                or not isinstance(seed.get("designHash"), str) or not HASH.fullmatch(seed["designHash"])):
                raise ValueError(f"Invalid preseed design metadata for {username}")
        chat = chat_map.get(username)
        if chat:
            if (chat.get("readerWorkspaceId") != reader or chat.get("localWorkspaceId") != local
                or not isinstance(chat.get("chats"), dict) or set(chat["chats"]) != {"reader", "design"}):
                raise ValueError("Chat workspace mapping is not exact for account 001")
            for key, workspace in (("reader", reader), ("design", local)):
                item = chat["chats"][key]
                if not isinstance(item, dict) or item.get("workspaceId") != workspace:
                    raise ValueError(f"Wrong {key} chat workspace")
                _id(item.get("id"), "chat", f"{key} chat")
            if chat["chats"]["reader"]["id"] == chat["chats"]["design"]["id"]:
                raise ValueError("Reader and design chats must differ")

        # Build a whitelist rather than copying potentially secret fixture fields.
        resources = {"schema": username, "connectionId": src["connectionId"],
                     "scratchPrefix": prefix, "workspaceTag": tag,
                     "seedOwnership": {key: mapped[key] for key in
                                       ("localCreated", "readerCreated", "writerCreated")},
                     "localWorkspaceId": local,
                     "readerWorkspaceId": reader, "oracle": ORACLE,
                     "rowSpecificColumnNameSelector": ROW_SELECTOR}
        if writer:
            resources.update(writableConnectionId=src["writableConnectionId"],
                             writableSchema=src["writableSchema"], writerWorkspaceId=writer)
        if seed:
            resources["preseed"] = {key: seed[key] for key in ("workspaceId", "marker", "designHash", "tables")}
        if chat:
            resources["chat"] = {"readerWorkspaceId": reader, "localWorkspaceId": local,
                                 "chats": {key: {"id": chat["chats"][key]["id"],
                                                 "workspaceId": chat["chats"][key]["workspaceId"]}
                                           for key in ("reader", "design")}}
        checks = [
            {"path": "/api/v1/auth/me", "status": 200, "equals": {"user.username": username}},
            {"path": f"/api/v1/schemii/workspaces/{local}", "status": 200,
             "equals": {"name": prefix + "seed", "connectionId": None}},
            {"path": f"/api/v1/schemii/workspaces/{reader}", "status": 200,
             "equals": {"connectionId": src["connectionId"], "namespace": username}},
        ]
        if writer:
            checks += [
                {"path": f"/api/v1/connections/{src['writableConnectionId']}", "status": 200,
                 "equals": {"host": "qa-postgres", "database": "schemii_qa", "username": src["writableSchema"]}},
                {"path": f"/api/v1/schemii/workspaces/{writer}", "status": 200,
                 "equals": {"connectionId": src["writableConnectionId"], "namespace": src["writableSchema"]}},
            ]
        if chat:
            checks += [{"path": f"/api/v1/schemii/ai/chats/{item['id']}", "status": 200,
                        "equals": {"workspaceId": item["workspaceId"], "providerId": "instance-codex",
                                   "modelId": "gpt-6-luna", "reasoningEffort": "default"}}
                       for item in chat["chats"].values()]

        default = writer if username in {"qa_designer_004", "qa_designer_010", "qa_designer_011"} else (
            local if username in {"qa_designer_006", "qa_designer_007", "qa_designer_008"} else reader)
        design_state = "exact qa_projects/qa_tasks design tables preseeded" if seed and seed["workspaceId"] == local else "empty test-owned design"
        owned = [f"owned local workspace {local}; {design_state}; "
                 f"all additional app object names prefixed {prefix}"]
        actions = ["create/edit/delete only own workspace/design objects", "download owned exports"]
        if username == "qa_designer_005":
            actions.append("upload <=1 MiB design JSON into a new owned local workspace")
        if username == "qa_designer_001":
            actions += ["send one AI prompt at a time in precreated reader/design chats",
                        "approve only exact proposals on owned local design"]
        if username == "qa_designer_002":
            actions.append("change and restore own AI preferences without model inference")
        if writer:
            owned.append(f"isolated writable schema {src['writableSchema']} via {src['writableConnectionId']}")
            actions += [f"SQL and migration writes only in {src['writableSchema']}",
                        "upload COPY CSV files <=1 MiB and <=5000 total generated writer rows",
                        "download COPY results from the same isolated writer schema"]
        mission, scenarios = MISSIONS[username]
        context = (f"Mission: {mission}. Start at exact URL /?workspace={default}; use harness navigate to "
                   f"/?workspace={local} for owned local, /?workspace={reader} for read-only baseline"
                   + (f", /?workspace={writer} for isolated writer" if writer else "")
                   + f". Confirm workspace identity before writes. Use exact qa_projects/qa_tasks table names "
                     f"only inside the owned workspace; prefix all additional object names with {prefix}. "
                   f"Column names repeat: snapshot first, then replace N in {ROW_SELECTOR} with actual one-based row. "
                   "Toolbar opener #create-table-button; dialog submit #save-design-table-button. "
                   "Run each scenario independently at 1280x800 desktop and 390x844 mobile. "
                   "Use harness actions only, screenshot actual state, verify persisted/reloaded result, "
                   "checkpoint functional and visual separately, and file a structured finding for reproducible defects. "
                   "Mark unsupported UI behavior blocked with evidence. ")
        if seed:
            context += (f"Preseed expected in workspace {seed['workspaceId']}: qa_projects and qa_tasks with the exact "
                        f"types/defaults/identity/FK/CHECK in resources.oracle; design hash {seed['designHash']}. ")
        if chat:
            context += (f"Precreated reader chat {chat['chats']['reader']['id']} and local design chat "
                        f"{chat['chats']['design']['id']}; do not use editor or Console for chat scenarios. "
                        "Wait for each turn to finish before sending the next; shared inference is serialized. ")
        output["lanes"][username] = {
            "url": f"/?workspace={default}", "persona": "designer",
            "expectedCapabilities": ["schemii:access"],
            "deniedProducts": list(fixture["deniedProducts"]),
            "checks": checks,
            "chatProvider": {"providerId": "instance-codex", "modelId": "gpt-6-luna",
                             "reasoningEffort": "default"} if username in {"qa_designer_001", "qa_designer_002"} else None,
            "resources": resources,
            "writeAuthorization": {"enabled": True, "resources": owned, "operations": actions},
            "scenarios": [{"id": scenario_id, "title": title, "product": "schemii",
                           "instructions": context + steps}
                          for scenario_id, title, steps in scenarios],
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ROOT / ".schemii/testing/fixtures.json")
    parser.add_argument("--workspace-fixtures", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must be new; use a unique run artifact")
    manifest = build(json.loads(args.fixtures.read_text()), args.tag,
                     json.loads(args.workspace_fixtures.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "lanes": len(manifest["lanes"]),
                      "baseScenarios": sum(len(lane["scenarios"]) for lane in manifest["lanes"].values())}))


if __name__ == "__main__":
    main()
