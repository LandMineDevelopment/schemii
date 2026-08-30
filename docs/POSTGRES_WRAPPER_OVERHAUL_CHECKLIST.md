# PostgreSQL Wrapper Authority Overhaul

Status: implementation delivered and broadly verified; explicitly deferred residuals remain open below.

This checklist is the durable execution source for aligning Schemii and Schemer with the repository's PostgreSQL wrapper philosophy. Update it as work completes; do not mark an item complete before focused implementation checks pass. Run broad suites after the implementation converges.

## Invariants

- [x] PostgreSQL is authoritative for permissions, SQL semantics, native object behavior, and role/database/session operational policy.
- [x] Users explicitly control versioned AI capabilities, approval modes, and optional agent-specific bounds.
- [x] The AI cannot broaden its own policy; structured errors name the missing capability and local settings action.
- [x] Exact-target, stale-revision, destructive-review, layout/dashboard preservation, and uncertain-write recovery remain enforced.
- [ ] Application transport/resource bounds use continuation or explicit truncation throughout. Catalogs, snapshot-safe Console reads, and Schemer aggregate/detail results page; Console and Schemer browser exports drain retained resources within TTL and caps, while terminal Console spool truncation and several older bounded result routes remain non-recoverable.
- [x] Application limitations are labeled explicitly and direct users to Console/raw PostgreSQL where safe. A finite `rowsWritten` policy necessarily blocks arbitrary AI raw writes.

## 1. PostgreSQL Policy Ownership

- [x] Remove the Console-owned statement timeout and report PostgreSQL as timeout owner.
- [x] Remove default `statement_timeout` overrides from previews, catalog reads, Schemer analytics, AI reads, migrations, view operations, and example seeding.
- [x] Retain connection-establishment and external-service deadlines with accurate labels.
- [x] Make app-owned lock waits respect, never weaken, stricter PostgreSQL lock policy.
- [x] Ensure PostgreSQL timeout diagnostics identify PostgreSQL as the policy source.
- [x] Make every catalog/introspection/preview read transaction PostgreSQL read-only where applicable.

## 2. Permission And SQL Authority

- [x] Remove table-level `INSERT` vetoes that reject valid column-level grants.
- [x] Treat advisory privilege calculations as guidance, not browser authorization.
- [x] Let PostgreSQL return final permission diagnostics with SQLSTATE, message, detail, and hint.
- [x] Replace lexical read-SQL authorization with PostgreSQL read-only transaction authority while preserving one-statement/result contracts.
- [x] Support PostgreSQL-valid read commands such as `SHOW`.
- [x] Correct documentation that conflicts with actual privileged-role behavior.

## 3. Migration Correctness And Preservation

- [x] Classify executable steps and every unresolved difference as blocking; intentionally retained unrelated objects are excluded by touched-object analysis.
- [x] Prevent warning-only omitted differences from producing an apply-capable full-schema plan.
- [x] Preserve unresolved desired intent: incomplete preview is preview-only and does not synchronize a safe subset as full state.
- [x] Scope partitioned/unsupported-object blocking to touched objects and actual dependencies.
- [ ] Create one shared reconstruction preservation manifest for tables and materialized views.
- [x] Include opaque migration-relevant fingerprints in touched-table reconstruction inventory and full migration fingerprints.
- [x] Keep no-generated-`CASCADE`, destructive review, stale plans, transactional apply, and uncertain-commit reconciliation.

Residual: table reorder and materialized-view recreation use separate conservative manifests. No shared reconstruction contract exists, and unsupported reconstruction remains blocked rather than claimed.

## 4. Catalog Reach And Results

- [x] Expose system namespaces only through `scope=all`, with explicit classifications.
- [x] Represent foreign tables for supported catalog, source, structured-read, and lineage workflows.
- [x] Replace namespace/relation/lineage hard cutoffs with exact-context opaque keyset cursors and complete server-side fingerprints.
- [x] Support large full-introspection projections through batched complete catalog reads instead of the generic collection cutoff.
- [x] Permit verified cross-namespace read-only lineage inspection.
- [x] Derive and fingerprint structured type capabilities from PostgreSQL type/operator/aggregate catalogs; legacy v1 snapshots require explicit reselection.
- [ ] Add continuation/pagination/export paths for bounded result responses.

Residual: Console managed-read and explicit cursors are snapshot-safe, and Schemer aggregate/detail results have process-local continuation plus JSON/CSV export without query replay. Console committed spools remain finite and terminally truncated rows cannot be recovered. Universal continuation/export for all bounded APIs is not implemented.

## 5. Shared PostgreSQL Console

- [x] Share Console access and all four execution modes across Schemii and Schemer, with independent application-scoped write intent.
- [x] Preserve repeatable-read managed read with original-snapshot result continuation.
- [x] Preserve all-or-nothing managed transaction mode with pre-commit result spooling.
- [x] Add explicit transaction mode with visible transaction state and explicit commit/rollback resources.
- [x] Add autocommit/maintenance mode with disclosed partial-commit and uncertainty semantics.
- [x] Keep exact-target display, cancellation, durable receipts, and no automatic write replay.
- [x] Replace recurring pseudo-permission expiry with durable application-scoped human write intent and exact settings/target revalidation.
- [ ] Make statement/script/result limits user-configurable or pageable where they affect capability rather than transport safety.

Residual: statement count and row page size are user settings; hard SQL length, column, cell/row, response, spool, active-result, snapshot, and process limits remain operator/process safety ceilings. Console export remains browser JSON, while Schemer retained aggregate/detail resources export JSON or CSV.

## 6. User-Controlled AI Policy

- [x] Add versioned Schemii and Schemer application/agent settings in metadata PostgreSQL.
- [x] Persist capability mode per supported agent capability: disabled, every action, once per chat, automatic.
- [x] Support Schemii schema/structured read/structured write/raw read/raw write and Schemer structured/dashboard policy matrices without inventing unsupported product capabilities.
- [x] Add optional user bounds for rows disclosed/written, pages inspected, raw statements, operation timeout, and agent concurrency.
- [x] Default operation timeout to PostgreSQL policy inheritance; a bound only narrows it.
- [x] Bind every proposal and operation to immutable policy revision/snapshot, target, resource revision, disclosure, and applicable bounds.
- [x] Return structured missing-capability guidance and render an allowlisted local settings action.
- [x] Ensure policy changes revoke incompatible linked grants/proposals without modifying provider credentials or unrelated chats.
- [x] Preserve no natural-language authorization and no uncertain-write replay.

## 7. Structured PostgreSQL Operations

- [x] Allow PostgreSQL partition routing where exact catalog/effect evidence and outcome reconciliation remain sound.
- [x] Support domains, enums, custom types/operators, defaults, constraints, triggers, and RLS through PostgreSQL semantics.
- [x] Disclose detected secondary and potentially nontransactional effects instead of blanket rejection.
- [x] Follow PostgreSQL generated/identity behavior and preserve full bounded diagnostics.
- [x] Keep exact source snapshots, transactional execution, stale checks, durable operation receipts, effect digests, and bounded model disclosure.
- [x] Route raw writes with an unprovable finite row bound to Console or structured write with a precise `application_limitation`.

`rowsWritten` means primary rows submitted by structured insert. Trigger writes, partition internals, sequence changes, and external effects are disclosed but not counted.

## 8. Product And Error Experience

- [x] Distinguish AI provider status from PostgreSQL connection status.
- [x] Distinguish disconnected local designs from target-bound chats.
- [x] Distinguish suggested, selected, linked, and verified targets through the shared target presentation contract.
- [x] Rename profile timeout fields to **Connection timeout**.
- [x] Remove expiring Console write-grant confirmations; destructive preview/apply retain their distinct substantive reviews.
- [x] Keep destructive structured migration preview and final reviewed apply.
- [x] Standardize bounded PostgreSQL diagnostics across catalog, query, Console, migration, and AI routes.
- [x] Return explicit capability-unavailable and application-limitation errors with structured guidance.
- [x] Normalize Schemer dashboard version-1 and version-2 records deterministically in memory to version 3 with stable mobile order, vertical-only viewport, no persisted geometry, and no write-on-read.
- [x] Persist strict date-range slicers with explicit widget/source-column bindings, start-inclusive/end-exclusive ranges, temporal type and source-time-zone validation, DNF-preserving query composition, effective-query lineage, revision-guarded binding removal, and a complete browser editor. Every responsive breakpoint uses the same widget array order and uniform card size.
- [ ] Preserve responsive desktop/mobile behavior and accessible controls.

Evidence covers 1440x900 and 390x844 Chromium execution, responsive CSS, keyboard geometry and calendar navigation, focus trapping, ARIA/inert state, accessible chart tables, and reduced motion. Manual screen-reader/assistive-technology validation is not reported, so this remains open.

## 9. Operability And Recovery

- [x] Make global/class process admission capacities operator-configurable and exact-target aware.
- [x] Wire lifecycle-owned AI lease heartbeat, lost-lease handling, stale abandonment, result recovery, cleanup, and readiness health.
- [x] Separate configurable migration-plan TTL from temporal-manifest TTL.
- [x] Preserve one invalid-session retry only after explicit rejection.
- [x] Preserve reconciliation instead of replay after lost write responses.
- [x] Add packaging hygiene enforcement preventing stale importable/web build-tree copies.

## 10. Verification And Delivery

- [x] Focused Python and browser contract tests cover the changed implementation boundaries.
- [x] Full Python suite passes: 539 tests, 8 environment-gated skips.
- [x] Python compilation passes.
- [x] JavaScript syntax and all 31 browser contract suites pass.
- [x] Disposable PostgreSQL 17 matrix covers timeout inheritance/narrowing, column grants, RLS, triggers, partitions, enum/domain/composite types, generated/identity diagnostics, managed rollback, explicit transactions/savepoints, autocommit partial success, maintenance mode, and no execution-ID replay. Cleanup left zero test schemas or roles.
- [x] Rebuilt Schemii and Schemer containers are healthy; `/`, `/api/readiness`, session bootstrap, profiles, AI settings, Console settings, paged namespaces, and relation inspection pass in both applications.
- [x] No model-provider prompt was sent; verification used only local application/PostgreSQL routes.
- [x] Existing volumes were reused without deletion; five saved profile identities remained visible in both applications, PostgreSQL test objects were cleaned up, and schema/dashboard/provider/chat volumes were preserved.
- [x] All 11 schema records retained every established table object position/color, legacy table layout value, and views layout/viewport. Active browser sessions saved newer table viewports for the organization and local metadata designs after restart; those newer user-authored values were preserved. Mercury restoration changed only the six reserved default widget configurations; widget identities/order, all established layouts/viewports, and all unrelated custom widgets/configurations remained equal.
- [x] Documentation describes delivered behavior and explicitly records deferred reconstruction, export/continuation, and assistive-technology validation.
- [x] Documentation delivery passes `git diff --check` and includes no secrets or runtime/user data. Complete-worktree content review remains part of final implementation delivery.
