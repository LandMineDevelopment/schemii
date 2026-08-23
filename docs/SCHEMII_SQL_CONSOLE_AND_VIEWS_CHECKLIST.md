# Schemii SQL Console And Views Checklist

This checklist governs the UI-first design and later implementation of two Schemii features: an independent raw SQL console and first-class PostgreSQL view/materialized-view management.

## Working Agreement

- [x] Create `feature/schemii-sql-console-views` from clean `main`.
- [x] Replace the completed Schemer implementation checklist with this focused roadmap.
- [x] Obtain explicit approval before starting each phase below.
- [x] During look-and-feel phases, use static or browser-local prototype state only; do not add execution or persistence APIs.
- [x] Complete browser-render automation for the implemented Console and Views workflows on desktop and mobile.
- [x] Run the complete required verification before completing each backend phase and before merging the branch.
- [x] Preserve saved canvas layout, profiles, database data, and unrelated user-owned state throughout implementation.

## Product Decisions To Lock

- [x] Approve where the SQL Console entry appears in the tool rail and how its independent workspace opens/closes.
- [x] Approve the console header, target identity, editor, result, error, history, and empty states on desktop and mobile.
- [x] Approve the write-mode toggle placement, wording, warning color, and confirmation interaction.
- [x] Decide whether one Run action may contain multiple SQL statements. Decision: allow up to 100 top-level statements, with a durable user-selected limit from 1 through 100.
- [x] Decide whether successful write statements auto-commit individually or run as one submitted transaction. Decision: one submitted script is one all-or-nothing server-owned transaction.
- [x] Approve the Tables / Views layer switch and independent persisted viewports.
- [x] Approve ordinary-view and materialized-view card treatments, dependency lines, empty states, and editor layouts. Selected direction: one graphical lineage canvas with a searchable right-side sibling pane.
- [x] Approve destructive materialized-view replacement review and explicitly defer refresh controls.

## Phase 1: SQL Console Look And Feel

Approval required before application code changes.

- [x] Add a dedicated SQL Console tool that is not attached to a selected table, inspector, or table-data pane.
- [x] Prototype a full workspace with an exact profile, database, and namespace identity in its header.
- [x] Prototype a raw SQL editor with Run, cancel, clear, and copy controls.
- [x] Prototype separate result-table, command-summary, loading, empty, and PostgreSQL error states.
- [x] Keep write mode off by default and visually distinguish the read-only state.
- [x] Add a write-mode toggle that requires deliberate confirmation before enabling.
- [x] Show a persistent high-visibility warning in the console header while write mode is enabled.
- [x] Show that write mode resets when the target changes, the console closes, or the page reloads.
- [x] Validate keyboard flow, focus treatment, scrolling, resizing, and responsive/mobile behavior in a rendered browser.
- [x] Use synthetic local data only; do not call PostgreSQL or save console state in this phase.

Acceptance criteria: the user approves the complete console workflow and all visible states before API or execution work begins.

## Phase 2: Views Layer Look And Feel

Approval required before application code changes.

- [x] Prototype a clear Tables / Views layer switch outside the existing table canvas.
- [x] Keep the existing table graphical view unchanged while the Views layer is active.
- [x] Give the Views layer its own draggable cards, directional dependency connectors, isolated viewport, zoom, pan, focus selection, and empty state.
- [x] Visually distinguish ordinary views from materialized views without relying on color alone.
- [ ] Prototype create, inspect, edit, duplicate, and delete entry points for ordinary views.
- [ ] Prototype create, inspect, replace, refresh, and delete entry points for materialized views.
- [ ] Prototype a view editor with name, namespace, SQL definition, output-column snapshot, dependencies, and bounded row preview.
- [ ] Show read-only definitions when ownership or PostgreSQL permissions do not permit editing.
- [ ] Show stale-definition, dependency, validation, destructive-replacement, and migration-preview states.
- [x] Validate desktop and mobile behavior with dense and empty catalogs.
- [x] Use synthetic local data only; do not introspect, migrate, refresh, or persist views in this phase.

Selected direction: the selected-view lineage and raw-definition focus, with the relation catalog/search hidden in a right-side sibling pane until requested. One graphical lineage canvas routes physical sources through applicable CTE/derived stages into the real outer-SELECT query block, then the final view and consumers. The query-block card owns same-SELECT joins, filters, partial reasons, and selected projection evidence. Segment labels stay in front of connection paths, source cards expose verified columns without expansion or horizontal scrolling, and layout zoom keeps text crisp. Destination arrowheads remain directional, and verified source/join/query-block/output focus can be cleared without moving or saving the isolated Views layout. Source/markup/style contract tests and rendered desktop/mobile automation cover canvas behavior, filtering, selection, layout isolation, and ordinary/materialized editor templates.

Acceptance criteria: the user approves layer navigation, card language, dependency presentation, and all editor states before catalog or migration work begins.

## Phase 3: Contracts And Safety Design

Approval required before backend implementation.

- [x] Define exact profile, database, namespace, role, and session binding for every console and view request.
- [x] Keep console execution server-side and credentials out of browser responses and saved history.
- [x] Enforce read-only PostgreSQL transactions while write mode is off, regardless of browser state.
- [x] Require an explicit write-enabled request contract while write mode is on; never infer write permission from SQL text alone.
- [x] Reset write authorization on target changes, console close, refresh, server restart, and session replacement.
- [x] Define statement timeout, cancellation, result-size, response-size, and concurrent-execution limits.
- [x] Define transaction, auto-commit, multi-statement, notices, row-count, and partial-failure semantics.
- [x] Rely on the selected PostgreSQL role's permissions; do not elevate privileges for the console.
- [x] Define safe audit metadata without recording credentials or sensitive SQL parameters unintentionally.
- [x] Define versioned saved records for view-layer layout and viewport state, separate from the table layer.
- [x] Treat the live PostgreSQL catalog as authoritative for view kind, definition, dependencies, ownership, and output columns.
- [x] Define stale-catalog fingerprints and conflict responses for view edits and materialized-view actions.
- [x] Route view changes through migration preview and confirmation rather than ad hoc DDL execution.

Contract source of truth: `docs/SCHEMII_SQL_CONSOLE_AND_VIEWS_CONTRACTS.md`.

Acceptance criteria: request contracts, transaction boundaries, permission behavior, destructive operations, and persisted layout ownership are documented and approved.

## Phase 4: SQL Console Backend

Approval required before implementation.

- [x] Add capability-scoped SQL console routes using the shared PostgreSQL profile and session infrastructure.
- [x] Verify the connected database before every execution.
- [x] Execute with read-only transaction enforcement unless the request carries current write authorization.
- [x] Allow PostgreSQL statements permitted by the selected role while write mode is explicitly enabled, including insert, update, delete, DDL, and function calls.
- [x] Implement cancellation, timeouts, bounded tabular results, command summaries, notices, and PostgreSQL error details.
- [x] Ensure failed multi-statement or transactional submissions follow the approved rollback semantics.
- [x] Invalidate or recheck linked catalog fingerprints after schema-changing statements.
- [x] Add focused service, route, authorization, transaction, timeout, cancellation, and browser tests for the read-only execution slice.
- [x] Run selected SQL or the caret statement by default, provide explicit Run all, and present ordered results as pinnable browser-local tabs.
- [x] Keep browser-local query views independently named and removable, with stable per-view `consoleId` values and uniquely named, renameable result tabs.
- [x] Make write mode and its grant per query view; new views start read-only, rename preserves authorization identity, and removal revokes the owning grant.
- [x] Revoke every query-view grant on target change or Console close, and ensure switching views cannot reuse another view's grant.
- [x] Apply the same current-grant requirement to selection/caret Run and Run all; pinned results, renamed tabs, query names, and history must not affect authorization.
- [x] Verify writes and rollback behavior against disposable PostgreSQL data and remove all test objects afterward.

Acceptance criteria: read-only mode rejects writes server-side; write mode executes only with explicit current authorization and selected-role permission; failures cannot leave an ambiguous transaction state.

## Phase 5: View And Materialized-View Backend

Approval required before implementation.

- [x] Extend catalog introspection for ordinary views and materialized views, including bounded definitions, ownership, output columns, dependencies, materialized metadata, and stable fingerprints.
- [ ] Add bounded read-only row preview for an exact view identity. The shared relation-preview implementation is not mounted by Schemii, and the current Views workspace has no row-preview control.
- [x] Add version-2 table/view layout-layer persistence and token/wholesale-change coverage without changing established table layout.
- [x] Preserve the complete saved layout during the implemented narrow post-apply view-item synchronization.
- [x] Add dedicated ordinary-view `CREATE VIEW` and `CREATE OR REPLACE VIEW` preview/apply planning.
- [ ] Detect output-column removal, rename, reorder, and type changes before replacement.
- [x] Add new materialized-view creation planning and apply.
- [x] Recreate existing materialized views transactionally with PostgreSQL 17 target locking, stale metadata checks, supported metadata restoration, population-intent preservation, and stored-row repopulation warnings.
- [x] Delete ordinary and materialized views through reviewed non-`CASCADE` plans with exact saved-item synchronization and stored-row deletion warnings.
- [ ] Convert view kinds. Current behavior: reject with `view_kind_conversion_unsupported`.
- [ ] Add explicit materialized-view refresh controls with permission, lock, and duration warnings.
- [x] Show live direct dependencies/dependents and affected-object counts; no view mutation emits `CASCADE`.
- [x] Derive bounded output-to-source column, join provenance, and real query-local CTE/derived/root-query-block stages from verified live view definitions; retain explicit partial/unavailable states; and render one pan/zoom lineage canvas without direct source bypasses or conceptual join-result nodes.
- [x] Require exact saved-schema revision/layout/target binding and stale profile/relation revalidation before preview and apply.
- [x] Keep view apply transactional with lock/statement timeouts, a namespace advisory transaction lock, ordinary-view access-share locking, rollback, and post-DDL reinspection.
- [x] Add focused catalog, route, mutation, stale-state, rollback, narrow-sync/layout-preservation, frontend-contract, and tutorial-v4 tests.
- [x] Complete disposable PostgreSQL 17 coverage for namespace contention, ordinary-view locking, and materialized-view `AccessExclusiveLock` acquisition/rollback. The suite remains opt-in through `SCHEMII_TEST_PG17_DSN` for environments without PostgreSQL 17.

Implemented acceptance boundary: users can inspect, create, edit, and delete ordinary views; create, transactionally recreate, and delete materialized views; and review stored-row consequences before destructive apply. Kind conversion, `CASCADE`, automatic dependent recreation, and materialized refresh remain unsupported.

## Phase 6: Integration And Delivery

Approval required before final integration.

- [x] Reconcile SQL-console schema changes with Schemii drift detection and hard-refresh requirements.
- [x] Verify Views-layer changes remain compatible with Schemer relation fingerprints and require explicit source reselection after breaking catalog changes.
- [x] Add documentation for write-mode risk, role permissions, transaction behavior, current view API/migration semantics, materialized-view lock limitations, saved-schema sync, and tutorial v4 objects.
- [x] Complete accessibility, keyboard, reduced-motion, desktop, and mobile verification.
- [x] Run focused Python and JavaScript tests.
- [x] Run the complete Python and JavaScript suites.
- [x] Run Python compilation, browser JavaScript syntax checks, and `git diff --check`.
- [x] Smoke-test `/`, `/api/session`, and the affected preview/apply routes on local servers.
- [x] Verify view lifecycle transactions, metadata restoration, locking, rollback, and deletion against disposable PostgreSQL 17 objects.
- [x] Compare the complete parsed persistent layout snapshot before and after lifecycle and rendered-browser verification.
- [x] Confirm no disposable PostgreSQL objects or rows remain and no runtime profiles or credentials were added to the repository.

Acceptance criteria: both approved features are documented, verified end to end, safe under stale state and restricted permissions, and ready for merge without altering unrelated user-owned data.
