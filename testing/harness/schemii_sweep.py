"""Build a private, prescriptive ten-lane Schemii manual QA manifest.

The generated file contains resource IDs but no passwords. Agents receive only
their own lane brief. The source persona fixtures remain the authority for
effective-access checks and retained connection identities.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS = (
    "qa_designer_004", "qa_designer_005", "qa_designer_006", "qa_designer_007",
    "qa_designer_001", "qa_designer_008", "qa_designer_009", "qa_designer_010",
    "qa_designer_011", "qa_designer_002",
)
WRITERS = {"qa_designer_004", "qa_designer_010", "qa_designer_011"}
CHAT = {"qa_designer_001", "qa_designer_002"}

ORACLE = """Retained PostgreSQL QA baseline (read-only; never edit/reset during a run):
customers: id integer PRIMARY KEY, name text NOT NULL, region text NOT NULL CHECK region IN ('east','west'); exactly 32 rows, ids 1..32, name 'Customer 001'..'Customer 032', even ids east and odd ids west.
orders: id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, customer_id integer NOT NULL REFERENCES customers(id), ordered_on date NOT NULL, status text NOT NULL CHECK status IN ('pending','paid','cancelled'), amount numeric(10,2) NOT NULL CHECK amount>=0, notes text NULL; exactly 513 rows, ids 1..513, each status 171 rows, SUM(amount)=180622.17, customer 1 has 16 orders totaling 5962.24.
customer_totals: ordinary view with id integer, name text, region text, order_count bigint, total numeric; exactly 32 result rows. The version marker table has one row (version=1). The 120 reader roles cannot CREATE, INSERT, UPDATE or DELETE in these schemas.
Disposable design oracle: qa_projects(project_id bigint identity PK, code varchar(24) NOT NULL UNIQUE, title text NOT NULL, budget numeric(12,2) NOT NULL DEFAULT 0 CHECK budget>=0, due_on date NULL); qa_tasks(task_id bigint identity PK, project_id bigint NOT NULL FK qa_projects(project_id), state varchar(16) NOT NULL DEFAULT 'todo' CHECK state IN ('todo','doing','done'), effort_hours numeric(6,2) NULL, notes text NULL). Use a unique test-owned name prefix for every new workspace, design, saved query, view and connection. If the UI cannot express an oracle property, report that precise gap; do not silently substitute a different type.
Writer-row oracle for an explicitly assigned qa_write_* schema only: qa_write_items(item_id bigint identity PK, sku varchar(20) NOT NULL UNIQUE, qty integer NOT NULL CHECK qty>=0, price numeric(10,2) NOT NULL CHECK price>=0, active boolean NOT NULL DEFAULT true, note text NULL). Insert A-100/qty 2/price 12.50/active true, B-200/qty 3/price 7.25/active false, C-300/qty 0/price 0.00/active true. Expected row count 3, SUM(qty)=5, SUM(qty*price)=46.75. A rolled-back update to qty=99 must leave A-100 at qty=2. Limit COPY payloads to 1 MiB and total generated rows to 5,000 per writer schema; no unbounded SQL.
The page has two controls named 'Create table': use selector #create-table-button for the toolbar opener and #save-design-table-button for the dialog submit after checking the current snapshot. Do not retry an ambiguous mutating click. Start each scenario at the exact pre-opened workspace URL in the lane brief, then switch by workspace ID only when the scenario requires a different target.
The create-table editor repeats the accessible label 'Column name' for every row. Target row N (1-based) with selector #design-columns > .design-column-row:nth-child(N) [data-design-column-name] after confirming the row order in a fresh snapshot; the harness does not support a label index. For exports use the header Download menu and the harness download action, then inspect the returned private file. Scroll a 100-row preview or a horizontally overflowing result grid before calling later rows or columns unavailable.
For every scenario, assess functionality and visual behavior separately at the assigned desktop or 390x844 mobile viewport. Capture actual result and persisted/reloaded state, not just a click. File a structured finding for a reproducible defect with exact input, expected/actual outcome and screenshot. An unsupported or API-only capability is blocked, never passed by assumption."""

PAGING_ORACLE = """Use SELECT id,customer_id,ordered_on,status,amount,notes FROM orders ORDER BY id.
Set Console Rows per page to 100 through its settings UI (restore the prior preference afterward).
513 rows exceed five complete pages: page starts 1,101,201,301,401,501; ends 100,200,300,400,500,513.
Require the full ordered id sequence 1..513 exactly once in the full exported file, not a displayed-page count.
For id n: customer_id=(n%32)+1; date=2025-01-01+(n%90) days; status cycles paid,cancelled,pending;
amount=((n*137)%100000)/100; notes is NULL for multiples of 7, otherwise 'Fixture order n'.
If catalog row preview does not guarantee order, use the explicit ordered Console query for this oracle;
report preview ordering separately rather than assuming its first row is id 1."""


def scenario_contract(scenario_id: str, title: str, instructions: str) -> dict:
    """Preserve meaningful mobile coverage without replaying stateful creation."""
    mobile = (f"Saved-state readback of desktop scenario {scenario_id} at 390x844 mobile. "
              "The matching desktop functional result must pass first; otherwise mark blocked prerequisite. "
              "Inspect its exact assigned resources and recorded final state through visible UI; reopen/reload "
              "and verify saved values, target identity, navigation, focus, scrolling and visible feedback. "
              "Do not create/edit/delete/import, execute SQL writes, approve actions or send another AI turn. "
              "Record expected versus actual and fresh screenshots. This result covers mobile readback, "
              "not independent mobile creation, upload or mutation. "
              f"Desktop workflow for expected results: {instructions}")
    return {"id": scenario_id, "title": title, "product": "schemii", "instructions": instructions,
            "viewportContracts": {
                "desktop": {"mode": "write", "instructions": instructions},
                "mobile": {"mode": "readback", "dependsOnDesktop": True, "instructions": mobile}}}


def empty_design_check(workspace_id: str) -> dict:
    return {"path": f"/api/v1/schemii/workspaces/{workspace_id}/design", "status": 200, "phase": "initial",
            "equals": {"content." + key + ".length": 0 for key in
                       ("tables", "relationships", "types", "functions", "views", "triggers")}}

# Each mission is deliberately broader than a single component. A scenario is a
# multistep acceptance contract, expanded by the harness into desktop and mobile.
MISSIONS = (
    ("Connection and workspace lifecycle", (
        ("connection-list", "Connections, permissions and target identity", "Inspect assigned managed and personal connection lists, target host/database/schema, availability and ownership. Test connection success and a deliberately invalid host/port without changing the retained managed profile. Verify clear error and recovery, then reopen the original target."),
        ("connection-edit", "Disposable connection create/edit/delete", "Using only the assigned qa_write_designer_004 target, exercise create, test, rename, edit nonsecret settings, duplicate-name validation, SSL/credential validation, and delete-impact confirmation on a disposable connection. Never delete the retained qa_designer_004 reader profile. Verify the disposable connection disappears after deletion."),
        ("workspace-local", "Local workspace lifecycle", "Create a local workspace named with the scratch prefix; rename, switch away/back, reload, and verify owner, revision and target identity. Create qa_projects with the exact column oracle, save, reopen, and check the column types/defaults persist. Exercise delete confirmation only on this workspace."),
        ("workspace-backed", "Database-backed workspace lifecycle", "Open the assigned reader profile and inspect the live qa_designer_004 schema without changing it. Verify the 32 customers, 513 orders and customer_totals view are associated with that exact target; switch to local workspace and back, checking no cross-target data leakage."),
        ("workspace-errors", "Conflicts, empty and recovery states", "Exercise blank and duplicate workspace names, a stale revision if safely reachable, unavailable target handling, cancel/confirm paths, quick-start guidance and empty state. Reload after validation errors and confirm no unintended workspace or profile was created."),
        ("workspace-responsive", "Responsive workspace controls", "Traverse workspaces, connection controls, inspector and dialogs by keyboard and pointer; verify labels, focus, scrolling, no clipping or overlap, and clear destructive-action wording at exact viewport."),
    )),
    ("Import, catalog and export", (
        ("catalog-list", "Live catalog inventory and filtering", "Open assigned qa_designer_005 reader target. Verify customers, orders, customer_totals, fixture_version and orders_id_seq appear in appropriate object groups; search/filter exact and absent names, clear filter, inspect counts and empty state."),
        ("catalog-detail", "Column and relationship detail", "Inspect customers.id integer PK, customers.region text CHECK; orders.id bigint identity PK, orders.customer_id integer FK, orders.amount numeric(10,2), orders.notes nullable. Verify relationship target and constraint detail, search and breadcrumb navigation."),
        ("catalog-preview", "Live rows, paging and lineage", "Preview exactly 32 customers and 513 orders with paging; verify first customer is Customer 001/west and order ids are stable. Inspect customer_totals lineage and customer 1 count 16/total 5962.24; check table-scoped SQL action and return navigation."),
        ("import-provenance", "Import and edit provenance", "Import the assigned reader catalog into a test-owned design/workspace through UI. Inspect import summary, any lossiness notices and target identity. Save a test-only addition with exact qa_projects types, reload and ensure imported objects and new design objects remain distinguishable."),
        ("export-files", "JSON and desired SQL download", "Download the test-owned design as JSON and desired SQL using harness download. Inspect file names and actual contents for qa_projects bigint identity, varchar(24), numeric(12,2), date, UNIQUE and CHECK. Test export from empty design and after saved change."),
        ("catalog-recovery", "Catalog error and responsive recovery", "Use safe missing-object search, unavailable-state affordances and retry without modifying reader data. Verify catalog, preview, lineage, export controls and long names remain usable at exact viewport; record clipping separately."),
    )),
    ("Table and canvas design", (
        ("table-create", "Typed multi-table creation", "Create a test-owned local design with qa_projects and qa_tasks exactly as the oracle specifies. Set bigint identity PKs, varchar lengths, numeric precision/scale, nullability, defaults and date; save and reload, then inspect exported desired SQL for each property."),
        ("column-edit", "Column edit, ordering and validation", "Rename qa_tasks.notes to detail, reorder physical versus display columns, and change effort_hours numeric(6,2) to numeric(8,2). Try duplicate/blank names, unsupported precision and illegal nullability; confirm validation and no partial save."),
        ("canvas-layout", "Canvas pan, zoom and layout", "Position both tables, zoom in/out/fit, pan, drag and save layout. Reload and check positions and relationship endpoints. Inspect overlap, clipped inspector, text legibility and keyboard access in both viewports."),
        ("undo-redo", "Undo, redo and reset", "Add a disposable column qa_tasks.flag boolean NOT NULL DEFAULT false, undo, redo, save, reload, then reset/cancel/reconfirm using UI controls. Verify revision and exact surviving columns after each committed step."),
        ("save-conflict", "Dirty state and revision conflict", "Edit several fields before saving; switch screens and observe unsaved-change warning. Exercise save error/stale revision if reachable, recover without data loss and verify only authorized test design changed."),
        ("table-delete", "Dependency-aware delete and export", "Delete qa_tasks only after inspecting FK/dependency warning; cancel first, then remove the test-owned dependency and confirm. Verify qa_projects survives, reload and inspect JSON/SQL export. Do not touch imported baseline tables."),
    )),
    ("Constraints and dependencies", (
        ("keys-fks", "Composite design and foreign keys", "Create qa_projects and qa_tasks from oracle in a local design; add FK qa_tasks.project_id -> qa_projects.project_id. Check valid key targets, mismatched type rejection, nullable/required behavior, relationship canvas and saved desired SQL."),
        ("uniques-checks", "Unique and check constraints", "Add UNIQUE on qa_projects.code varchar(24), CHECK budget>=0 for numeric(12,2), and CHECK state IN ('todo','doing','done') for varchar(16). Test malformed expression, duplicate name, edit/delete and recovery. Reload and inspect exact predicates."),
        ("indexes", "Indexes and predicates", "Add an index on qa_tasks(project_id,state) and a partial predicate state<>'done'; inspect order, expression/predicate editing, invalid column handling, rename/delete and desired SQL after reload."),
        ("renames", "Dependency-aware renames", "Rename qa_projects.project_id to id only within scratch design. Inspect FK/index references and migration plan before save; cancel if plan would affect retained baseline. Restore project_id and verify references remain valid."),
        ("deletion", "Safe dependency deletion", "Try deleting a referenced qa_projects table and an indexed qa_tasks column. Check warning, cancel, remove dependencies in order, commit scratch deletion, reload and verify no dangling objects or silent cascade."),
        ("constraint-crossview", "Inspector/canvas/export agreement", "For the same saved design, compare inspector, relationship canvas, JSON export and desired SQL: names, integer/bigint, varchar lengths, numeric scales, required/nullable, defaults, key and predicates must agree. Check mobile readability."),
    )),
    ("Chat-only end-to-end", (
        ("chat-read", "Model readiness and read context", "From Schemii AI only, select active shared gpt-6-luna/default reasoning. Ask for assigned workspace target and read-only baseline summary: customers 32, orders 513, each status 171, sum amount 180622.17. Compare response to the fixed oracle in resources; do not use designer or Console controls."),
        ("chat-design", "Create exact typed design through chat", "Through chat alone request local scratch qa_projects and qa_tasks with every type/default/constraint in the oracle. Review the assistant proposal line by line. Reject any missing bigint identity, varchar length, numeric scale, FK or CHECK; ask correction and explicitly approve only a matching proposal."),
        ("chat-edit", "Revise and verify through chat", "Through chat request qa_tasks.detail text NULL instead of notes, plus index on project_id/state. Check proposal target and approval scope, approve, then ask the assistant to read back saved schema. Reload the chat and ask it to read the saved schema again to verify persistence; do not use direct editor or Console actions."),
        ("chat-history", "Conversation management and recovery", "Use chat UI to inspect history, rename conversation, switch away/back, reload and verify messages/proposals. Exercise cancellation or interrupted streaming, failed prompt recovery and delete confirmation on a disposable conversation."),
        ("chat-permissions", "Chat proposal permission boundaries", "Ask for an action outside authorized scope (modify another workspace or retained baseline). Confirm clear denial or approval boundary; never approve a proposal against unowned data. Test batch proposal review and dismiss paths using scratch objects."),
        ("chat-mobile", "Chat responsive and accessibility", "With a long typed prompt and a multi-action response, inspect header model/reasoning/permissions values, streaming region, message overflow, action cards, dialogs and focus order at exact viewport. Capture actual model output and visible UI state."),
    )),
    ("Views and advanced objects", (
        ("views", "Ordinary and materialized views", "In test-owned design, define qa_project_totals as a view of qa_projects(code varchar(24), budget numeric(12,2)); create a materialized counterpart if UI supports it. Inspect populate flag, SQL definition, dependencies, save/reload/export and invalid-query validation."),
        ("lineage", "View lineage and catalog agreement", "For baseline customer_totals, confirm lineage from customers.id integer and orders.customer_id integer FK, preview 32 rows and customer 1 total 5962.24. For scratch views, inspect relation/source lineage, search, detail and empty/invalid states."),
        ("enums-domains", "Enum and domain types", "Create qa_task_state enum with todo/doing/done and qa_nonempty_code domain over varchar(24) with nonempty check if UI exposes these. Apply the types to scratch columns, test duplicate labels/invalid base type, save/reload and inspect desired SQL."),
        ("routines", "Functions and procedures", "Create a scratch numeric function qa_add_budget(numeric(12,2),numeric(12,2)) returning numeric, plus a procedure if UI supports it. Check signature, language/body validation, edit/rename/delete, save/reload and export; never execute against retained baseline."),
        ("triggers", "Trigger definitions and dependencies", "Attach a test-owned trigger to qa_projects and a test-owned function, inspect timing/events/arguments, invalid missing-function handling, dependency deletion warning, save/reload and desired SQL."),
        ("advanced-ux", "Search, recovery and responsive forms", "Search all advanced object groups and clear search; test invalid/blank names, cancel and restore, long SQL bodies, keyboard focus, mobile forms and no content clipping. Mark an API-only or unavailable control blocked with evidence."),
    )),
    ("SQL Console reads", (
        ("sql-known", "Known-result read queries", "On assigned qa_designer_009 reader target run SELECT count(*) FROM customers (32), count(*) FROM orders (513), count(*) FROM customer_totals (32), and SUM(amount) FROM orders (180622.17). Verify displayed values and exact target identity."),
        ("sql-tabs", "Drafts, selection and run modes", "Create at least two Console tabs with different SQL. Test cursor statement, selected statement and run-all semantics; edit unsaved draft, switch tabs/reload, and confirm each result belongs to its SQL. Include one syntax error and recovery."),
        ("sql-pages", "Paging, sorting, pin and CSV", "Query all 513 orders ordered by id; confirm first id 1, last id 513, real next-page progression beyond page size 100, row count semantics and no duplicates. Pin/release result and download CSV; inspect header, first/last rows and 513 data rows or documented export limit."),
        ("sql-history", "Saved query and history lifecycle", "Save a named query for paid orders; expect exactly 171 rows. Rename/edit/reopen/delete only that saved query, check history ordering/replay after reload, and verify original SQL/draft is preserved appropriately."),
        ("sql-explain", "EXPLAIN, cancel and errors", "Run EXPLAIN on SELECT * FROM orders WHERE customer_id=1 and inspect plan display. Exercise a safely cancellable bounded read, malformed SQL, read-only write denial, clear error, retry and session recovery."),
        ("sql-responsive", "Console settings and responsive behavior", "Inspect result page setting/revision, valid and invalid page sizes, multi-tab controls, long numeric/text cells, grid scrolling, CSV affordance, focus, keyboard and mobile layout. Confirm settings persist after reload without enabling writes on reader role."),
    )),
    ("SQL Console writes and bulk", (
        ("sql-writer-identity", "Exact writer target and read-only separation", "Use only qa_write_designer_010. Verify target name/schema before any SQL. Confirm retained qa_designer_010 reader schema still has 32 customers and 513 orders and rejects writes; create qa_write_items with exact oracle types only in writable schema."),
        ("sql-transaction", "Explicit transaction commit and rollback", "Insert A-100, B-200 and C-300 with oracle values through explicit transaction; confirm visible before commit and persisted after commit, count 3/qty 5/value 46.75. Update A-100 qty=99 then roll back; reload and verify qty=2. Test validation failure for negative qty."),
        ("sql-autocommit", "Autocommit and partial failure", "Exercise explicit versus autocommit mode and confirmation boundaries using scratch rows only; insert then inspect durable state, cause duplicate sku UNIQUE error, verify preceding committed rows and exact failure receipt. Avoid uncertain retry without inspecting state."),
        ("sql-copy", "COPY upload and download", "Upload a <=1 MiB CSV containing exactly 3 new sku rows D-400/E-500/F-600 to the assigned scratch table through UI; verify row count and typed values, then download COPY/CSV and inspect header/data. Reject malformed CSV and verify no unexpected rows; never upload to retained tables."),
        ("sql-bulk", "Bulk job progress and recovery", "Use test-owned rows for bulk job start/progress/result/cancel or failure if UI exposes it. Verify persisted row counts, job status and no silent duplicate execution after reload. Keep total writer rows <=5,000."),
        ("sql-session", "Open session, timeout and mobile UI", "Inspect active transaction/session state, navigation warning, rollback/close/reopen, cancellation and visible error recovery. Verify editor, mode selector, result grid, upload/download and confirmations remain usable at exact viewport."),
    )),
    ("Migration lifecycle", (
        ("migration-baseline", "Target, empty plan and drift", "Open only qa_write_designer_011 target. Confirm empty marked writer schema and no-change plan before editing, target identity and revision. Inspect any unexpected drift/external conflict without approving broad changes."),
        ("migration-create", "Create typed schema and apply", "Design qa_projects and qa_tasks exactly as oracle, inspect generated SQL for bigint identity, varchar(24)/varchar(16), numeric(12,2)/numeric(6,2), defaults, PK/UNIQUE/CHECK/FK. Confirm exact target and apply once; verify live catalog tables and constraints."),
        ("migration-data", "Populated change and conversion", "Insert a few bounded scratch rows into applied tables, then plan nullable due_on date addition, budget numeric precision increase, and an order change. Inspect type conversion, rebuild/data-retention warning and generated steps; apply only safe confirmed plan, then verify rows unchanged."),
        ("migration-conflicts", "Planner blockers and drift choices", "Create a deliberate scratch-only external schema change or conflicting design change; inspect drift resolution options, unsafe conversion/blocker, cancellation and replan. Never resolve by altering another lane's objects or retained baseline."),
        ("migration-recovery", "Danger, progress and uncertain outcomes", "Test danger confirmation text, cancel and approve on owned target; inspect progress, attempt history, failure receipts and reconciliation path. If outcome is uncertain, inspect live schema before any repeat to avoid duplicate DDL."),
        ("migration-final", "Post-apply consistency and responsive UI", "After a successful apply, verify no-change review, live catalog, design, desired SQL and migration history agree. Inspect step details, long identifiers, mobile dialog/scrolling/focus and disabled Apply semantics. Leave target for coordinator reset after run."),
    )),
    ("AI controls and cross-feature UX", (
        ("ai-settings", "Provider, model and permission settings", "Confirm shared gpt-6-luna/default active; inspect model/reasoning and granular context/action permissions. Change only test-owned settings, reload and verify persistence, then restore original values. Inspect unavailable-model and denied-action states without changing administrator grants."),
        ("ai-reading", "AI read proposals and exact data", "Ask AI for assigned baseline counts 32 customers/513 orders, 171 paid, total 180622.17 and customer 1 total 5962.24. Compare response and UI query results; inspect context boundary and whether read-result citations target the correct schema."),
        ("ai-design", "AI design proposals and batch review", "Ask AI for qa_projects/qa_tasks exact types, defaults, keys and FK. Compare each proposed action to oracle, reject one intentionally incomplete proposal, request correction, approve a matching batch only against scratch design, reload and inspect saved schema."),
        ("ai-sql", "AI-to-SQL and migration boundaries", "Ask AI to draft a known-result SELECT and a migration review; inspect SQL, target, permission prompt and whether any mutation requires explicit approval. Do not run write SQL or apply migration on reader schema. Test denial of a request outside owned scope."),
        ("ai-history", "Streaming, cancel and history", "Inspect streaming and cancellation, send a follow-up, rename and reopen disposable conversation, reload, compare messages and action receipts, then delete only that conversation. Check error/retry behavior without duplicate approval."),
        ("ai-responsive", "Cross-screen responsive/accessibility sweep", "Traverse workspace, catalog, design, Console, migration and AI screens at assigned viewport. Inspect navigation, keyboard focus, labels, dialogs, long text/JSON, empty/loading/error states, model settings and permissions overflow. Document every reproducible clipped/overlapped control."),
    )),
)


def build(source: dict, tag: str, workspaces: dict) -> dict:
    if not tag or not tag.replace("-", "").replace("_", "").isalnum() or len(tag) > 32:
        raise ValueError("tag must be 1..32 alphanumeric, dash or underscore characters")
    output = {"version": "schemii-full-qa-v1", "lanes": {}}
    if workspaces.get("tag") != tag:
        raise ValueError("Workspace fixture tag differs from this sweep")
    for username, (mission, scenarios) in zip(ACCOUNTS, MISSIONS, strict=True):
        fixture = source["lanes"].get(username)
        if not fixture or fixture.get("persona") != "designer" or fixture.get("resources", {}).get("schema") != username:
            raise ValueError(f"Missing exact designer fixture for {username}")
        if username in CHAT and not fixture.get("chatProvider"):
            raise ValueError(f"Missing active chat policy for {username}; run ./test.sh provision-chat")
        resources = {key: fixture["resources"][key] for key in (
            "schema", "connectionId", "connectionOwnerId", "database", "host", "fixtureVersion",
            "writableConnectionId", "writableSchema") if key in fixture["resources"]}
        checks = list(fixture["checks"])
        prefix = f"qa_{tag}_{username[-3:]}_"
        seeded = workspaces.get("lanes", {}).get(username)
        if not seeded or seeded.get("prefix") != prefix or any(
            not re.fullmatch(r"ws_[0-9a-f]{32}", seeded.get(key) or "")
            for key in ("localWorkspaceId", "readerWorkspaceId")
        ):
            raise ValueError(f"Missing exact pre-opened local and reader workspaces for {username}")
        if username in WRITERS and not re.fullmatch(r"ws_[0-9a-f]{32}", seeded.get("writerWorkspaceId") or ""):
            raise ValueError(f"Missing exact pre-opened writer workspace for {username}")
        ownership = {key: seeded.get(key) for key in ("localCreated", "readerCreated", "writerCreated")}
        if any(not isinstance(value, bool) for value in ownership.values()):
            raise ValueError(f"Missing workspace ownership flags for {username}")
        resources.update({"scratchPrefix": prefix, "mission": mission, "oracle": ORACLE + "\n" + PAGING_ORACLE,
                          "workspaceTag": tag, "seedOwnership": ownership,
                          "localWorkspaceId": seeded["localWorkspaceId"],
                          "readerWorkspaceId": seeded["readerWorkspaceId"]})
        checks += [
            {"path": "/api/v1/auth/me", "status": 200, "equals": {"user.username": username}},
            {"path": "/api/v1/schemii/workspaces/" + seeded["localWorkspaceId"],
             "status": 200, "equals": {"name": prefix + "seed", "connectionId": None}},
            {"path": "/api/v1/schemii/workspaces/" + seeded["readerWorkspaceId"],
             "status": 200, "equals": {"connectionId": resources["connectionId"], "namespace": username}},
        ]
        checks.append(empty_design_check(seeded["localWorkspaceId"]))
        if username in WRITERS and not resources.get("writableConnectionId"):
            raise ValueError(f"Missing exact writable profile for {username}; provision writer target first")
        if username in WRITERS:
            resources["writerWorkspaceId"] = seeded["writerWorkspaceId"]
            checks.append({"path": "/api/v1/connections/" + resources["writableConnectionId"],
                           "status": 200, "equals": {"host": "qa-postgres", "database": "schemii_qa",
                                                      "username": resources["writableSchema"]}})
            checks.append({"path": "/api/v1/schemii/workspaces/" + seeded["writerWorkspaceId"],
                           "status": 200, "equals": {"connectionId": resources["writableConnectionId"],
                                                      "namespace": resources["writableSchema"]}})
            checks.append({"path": f"/api/v1/schemii/workspaces/{seeded['writerWorkspaceId']}/catalog",
                           "status": 200, "equals": {"catalog.namespace": resources["writableSchema"],
                                                      "catalog.tables.length": 0, "catalog.views.length": 0}})
        operations = ["create/edit/delete own prefixed app workspaces/designs/queries/chats", "download own exports"]
        if username == "qa_designer_005":
            operations.append("upload test-owned design JSON into own scratch workspace")
        if username in CHAT:
            operations += ["send AI chat on own workspace", "review/reject/approve proposals against own scratch design"]
        if username in WRITERS:
            operations += ["create/edit own writable connection", "write SQL only in " + resources["writableSchema"], "apply migration only in " + resources["writableSchema"], "upload CSV only into " + resources["writableSchema"]]
        resources_owned = ["app objects with prefix " + prefix]
        if username in WRITERS:
            resources_owned += [resources["writableSchema"], resources["writableConnectionId"]]
        default_workspace = seeded["writerWorkspaceId"] if username in WRITERS else (
            seeded["localWorkspaceId"] if username in {"qa_designer_006", "qa_designer_007"}
            else seeded["readerWorkspaceId"])
        output["lanes"][username] = {
            "url": "/?workspace=" + default_workspace, "persona": "designer", "expectedCapabilities": fixture["expectedCapabilities"],
            "deniedProducts": fixture["deniedProducts"], "checks": checks,
            "chatProvider": fixture.get("chatProvider"), "resources": resources,
            "writeAuthorization": {"enabled": True, "resources": resources_owned, "operations": operations},
            "scenarios": [scenario_contract(scenario_id, title,
                           f"Mission: {mission}. {steps} Default workspace is {default_workspace}; local workspace {seeded['localWorkspaceId']}; reader workspace {seeded['readerWorkspaceId']}; writer workspace {seeded.get('writerWorkspaceId')}. Navigate to /?workspace=WORKSPACE_ID when switching targets and confirm identity before writes. Use test-owned prefix {prefix}. Apply the exact oracle in resources, record expected versus actual and verify persistence. File documented findings immediately.")
                          for scenario_id, title, steps in scenarios],
        }
    return output


def build_pilot(source: dict, tag: str, workspaces: dict,
                accounts=("qa_designer_005", "qa_designer_006"), reviewer=None) -> dict:
    """A bounded two-account native adoption fixture, using existing seed receipts."""
    if (len(accounts) != 2 or len(set(accounts)) != 2
            or any(not re.fullmatch(r"qa_designer_[0-9]{3}", account) for account in accounts)
            or reviewer is not None and (reviewer in accounts or not re.fullmatch(r"qa_designer_[0-9]{3}", reviewer))):
        raise ValueError("Pilot needs two distinct retained designers and an optional separate reviewer")
    testers = tuple(accounts)
    accounts = testers + ((reviewer,) if reviewer else ())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tag) or workspaces.get("tag") != tag:
        raise ValueError("Pilot needs an exact matching workspace tag")
    result = {"version": "schemii-full-qa-v1", "tag": tag, "lanes": {}}
    ids = []
    for username in accounts:
        seeded = workspaces.get("lanes", {}).get(username, {})
        local = seeded.get("localWorkspaceId")
        if not isinstance(local, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", local) or seeded.get("localCreated") is not True:
            raise ValueError(f"Pilot requires a fresh owned local workspace for {username}")
        ids.append(local)
    if len(set(ids)) != len(ids):
        raise ValueError("Pilot workspaces collide")
    for index, (username, local) in enumerate(zip(accounts, ids, strict=True)):
        peer = ids[1] if index == 0 else ids[0]
        fixture = source.get("lanes", {}).get(username, {})
        if (fixture.get("persona") != "designer" or fixture.get("expectedCapabilities") != ["schemii:access"]
                or fixture.get("resources", {}).get("schema") != username):
            raise ValueError(f"Pilot needs exact effective designer grants for {username}")
        mapped = workspaces["lanes"][username]
        prefix = f"qa_{tag}_{username[-3:]}_"
        if mapped.get("prefix") != prefix:
            raise ValueError("Pilot workspace marker differs")
        resources = {key: fixture["resources"][key] for key in ("schema", "connectionId")}
        resources.update(workspaceTag=tag, scratchPrefix=prefix, localWorkspaceId=local,
                         readerWorkspaceId=mapped["readerWorkspaceId"],
                         seedOwnership={key: mapped[key] for key in ("localCreated", "readerCreated", "writerCreated")},
                         cleanupOwner="coordinator: exact seed receipts; extra app objects require cleanupReceipts")
        context = (f"Use only owned local workspace {local} at /?workspace={local}; prefix {prefix}. "
                   "Your peer owns a different workspace: never enter or mutate it. Initial design is empty. "
                   "The saved table oracle is qa_pilot_items with id integer NOT NULL primary key, label text NOT NULL, "
                   "qty integer NULL. Use #create-table-button then #save-design-table-button once; "
                   "column names repeat, so use row selectors from a fresh snapshot. "
                   "Preserve each first attempt outcome. Inspect saved state before repeating an uncertain write. ")
        cases = [
            ("pilot-create", "Create, save and reopen", "Create the exact table, save once, switch away/back and reload. Verify all three exact names/types/nullability and PK in inspector and exported desired SQL. Leave table for subsequent cases."),
            ("pilot-files", "Real JSON/SQL download and owned upload", "Requires the saved pilot-create table; block prerequisite if creation failed. Open summary[aria-label=\"Download\"] before #download-catalog-button or #export-design-sql-button. Download JSON and SQL, inspect actual private bytes and exact three columns/PK. Create a new prefixed import workspace through UI; record exact creation ID/time receipt before further work. Upload the downloaded <=1 MiB JSON through its file input. Save/reload and compare content; original remains unchanged. Inspect downloads; an unsupported picker is blocked browser capability, not a pass."),
            ("pilot-recovery", "Validation, keyboard save and recovery", "Requires the saved pilot-create table; block prerequisite if creation failed. On owned table attempt a blank or duplicate column name and verify clear validation with no partial saved change. Restore valid input, make label->description, Tab to the visible Save control and activate it with Enter once, switch away/back and reload. Verify the renamed column persists once, peers remain separate and the browser session can recover after reload. Leave exact final state and receipts for coordinator cleanup."),
        ]
        checks = list(fixture.get("checks", [])) + [
            {"path": "/api/v1/auth/me", "status": 200, "equals": {"user.username": username}},
            {"path": f"/api/v1/schemii/workspaces/{local}", "status": 200,
             "equals": {"id": local, "name": prefix + "seed", "connectionId": None}},
            {"path": f"/api/v1/schemii/workspaces/{peer}", "status": 404},
            empty_design_check(local),
        ]
        result["lanes"][username] = {
            "url": f"/?workspace={local}", "persona": "designer", "expectedCapabilities": ["schemii:access"],
            "independentReviewer": username == reviewer,
            "deniedProducts": list(fixture.get("deniedProducts", [])), "checks": checks, "resources": resources,
            "writeAuthorization": {"enabled": True, "resources": [f"owned seed workspace {local}", f"own prefixed import workspace {prefix}import"],
                                   "operations": ["create/edit/save own local design", "download own design JSON and SQL", "upload <=1 MiB own JSON into own prefixed workspace"]},
            "scenarios": [scenario_contract(sid, title, context + steps) for sid, title, steps in cases],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ROOT / ".schemii/testing/fixtures.json")
    parser.add_argument("--workspace-fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True, help="Unique short label for test-owned app object names")
    parser.add_argument("--pilot", action="store_true", help="Only two owned designer adoption lanes; no provider inference")
    parser.add_argument("--pilot-accounts", default="qa_designer_005,qa_designer_006")
    parser.add_argument("--pilot-reviewer", help="A separate retained designer with its own fresh review workspace")
    args = parser.parse_args()
    source = json.loads(args.fixtures.read_text())
    workspace_map = json.loads(args.workspace_fixtures.read_text())
    manifest = (build_pilot(source, args.tag, workspace_map, tuple(args.pilot_accounts.split(",")), args.pilot_reviewer)
                if args.pilot else build(source, args.tag, workspace_map))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "lanes": len(manifest["lanes"]),
                      "baseScenarios": sum(len(lane["scenarios"]) for lane in manifest["lanes"].values())}))


if __name__ == "__main__":
    main()
