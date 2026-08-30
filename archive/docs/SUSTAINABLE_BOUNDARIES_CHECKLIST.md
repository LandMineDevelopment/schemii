# Sustainable Boundaries Rewrite

This document is the durable execution record for the `architecture/sustainable-boundaries` branch. Update it as work proceeds. Do not mark an item complete until implementation, focused tests, full relevant verification, and documentation are complete.

## Objective

Establish coherent, durable interfaces among the Schemii and Schemer browsers, application servers, OpenCode, server-owned persistence, and PostgreSQL. Preserve the two applications and their separate domain models while unifying authority, concurrency, recovery, and target-identity contracts.

## Governing decisions

- Keep Schemii and Schemer as separate processes and products.
- Keep schema and dashboard documents in their domain-owned stores; do not force them into a generic repository.
- PostgreSQL is authoritative for live catalog and data state.
- OpenCode remains an untrusted typed-proposal generator and never receives database credentials or execution authority.
- Browser validation is usability support, never authorization.
- Use PostgreSQL, not SQLite, if transactional server metadata is required.
- A dedicated server-metadata PostgreSQL database/service is acceptable. It must use a narrowly scoped role, versioned migrations, explicit backup/retention behavior, and must not be confused with a user-selected target database.
- Prefer one durable execution state machine for equivalent write operations.
- Persisted target identity is server-generated and cryptographic; browsers do not reconstruct authority fingerprints.
- Compatibility code may be removed when a clear migration or prototype reset path exists. Do not preserve weak boundaries solely for legacy shape compatibility.
- Never weaken layout preservation, stale-plan checks, exact source fingerprints, or PostgreSQL role inheritance.

## Required durable notes

Maintain `docs/SUSTAINABLE_BOUNDARIES_NOTES.md` throughout execution.

After every substantial milestone, record:

1. What changed and why.
2. Important contracts or schema migrations introduced.
3. Tests run and exact results.
4. Open questions, known risks, and next action.
5. Any behavior intentionally removed or migrated.

Before context compaction or handing work to another agent, update the **Current handoff** section with:

- Branch and latest commit.
- Worktree status.
- Current phase and exact next step.
- Files actively being changed.
- Invariants that must not be violated.
- Commands already run and their results.
- Blockers or decisions still needed.

## Subagent communication protocol

Every delegated task must state:

- Research-only or implementation authority.
- Exact ownership boundary and files.
- Contracts/invariants that must remain true.
- Whether schema/data migrations are permitted.
- Required tests or verification.
- Expected return format: findings, edits, risks, and next recommendation.

Agents must not duplicate another active agent's scope. Research findings that affect architecture must be summarized in `SUSTAINABLE_BOUNDARIES_NOTES.md`, not left only in ephemeral chat output.

## Phase 0 — baseline and target design

- [x] Checkpoint and push pre-rewrite work to `main` (`692e6e6`).
- [x] Create `architecture/sustainable-boundaries`.
- [x] Add durable checklist and handoff notes.
- [x] Inventory all browser/server/OpenCode/PostgreSQL routes and persisted records in a versioned interface matrix.
- [x] Define target module ownership and dependency direction.
- [x] Decide deployment topology for dedicated server-metadata PostgreSQL.
- [x] Define failure behavior when metadata PostgreSQL is unavailable.
- [x] Define migration/bootstrap/upgrade strategy and local development defaults.
- [x] Add architecture decision records for authority storage and migration execution.

## Phase 1 — transactional server authority

- [x] Design versioned PostgreSQL schema for chats, policies, grants, proposals, operations, query-result references, and execution receipts.
- [x] Add a narrowly scoped metadata database role and connection configuration.
- [x] Implement idempotent metadata migrations with startup verification.
- [x] Implement transactional chat creation/deletion and policy updates.
- [x] Implement atomic approval-grant plus operation creation.
- [x] Implement unique one-operation-per-proposal ownership.
- [x] Implement durable operation lifecycle and recovery without fixed unrenewed leases.
- [x] Implement query-result reserve/consume/release with explicit uncertain delivery semantics.
- [x] Add cleanup and retention for expired/terminal authority records and sensitive result payloads.
- [x] Migrate or intentionally retire JSON authority/chat records.
- [x] Add cross-process, crash-window, restart, and unavailable-metadata-DB tests.

## Phase 2 — common chat identity for both apps

- [x] Introduce application/resource-aware chat identity.
- [x] Move Schemer from title-bound authority to durable chat records.
- [x] Keep OpenCode titles display-only.
- [x] Add one-time import or explicit retirement for legacy Schemer sessions.
- [x] Use one server-owned cryptographic target fingerprint contract.
- [x] Remove browser-side authority fingerprint derivation.
- [x] Align history, activity, deletion, proposal, operation, and policy routes with chat identity.
- [x] Restore all still-valid pending server proposals from authority records.

## Phase 3 — durable migration execution

- [x] Bind normal migration preview to exact saved schema ID/revision/layout token and target.
- [x] Load intended schema server-side instead of accepting an authoritative browser schema document.
- [x] Unify normal, AI, and view migration plan state where semantics overlap.
- [x] Add durable single-owner `ready → applying → terminal/uncertain` execution.
- [x] Record PostgreSQL transaction evidence before mutation.
- [x] Handle lost commit responses without claiming rollback.
- [x] Reconcile uncertain outcomes without replay.
- [x] Add canonical review digests and strict durable-plan validation.
- [x] Make plan retention/redaction explicit.
- [x] Test concurrent apply, restart, commit-response loss, stale schema, stale catalog, and reconciliation.

## Phase 4 — PostgreSQL catalog and write safety

- [x] Run full introspection in one read-only repeatable-read snapshot.
- [x] Reject missing namespaces distinctly from empty namespaces.
- [x] Inventory table metadata affected by reconstruction: owner, ACLs, comments, RLS/policies, rules, replica identity, statistics, storage, tablespace/access method, publications, security labels, and extension-owned state.
- [x] Preserve supported metadata transactionally.
- [x] Reject reconstruction when unsupported metadata would be lost.
- [x] Improve role capability reporting, including inheritance and `SET ROLE` ability.
- [x] Fix post-commit local-history/receipt error handling.
- [x] Replace weak advisory-lock keys with a collision-resistant contract.
- [x] Add live disposable PostgreSQL tests for all risky paths and cleanup assertions.

## Phase 5 — browser/server contracts

- [x] Standardize structured API error envelopes.
- [x] Add conditional deletion for schemas, dashboards, and profiles.
- [x] Add explicit Schemii conflict quarantine and recovery UX.
- [x] Separate Schemer draft query execution from exact saved-widget execution.
- [x] Define focused successful-response validators in shared browser clients.
- [x] Consolidate session bootstrap through one shared client contract.
- [x] Tighten route-family path predicates to segment boundaries/templates.
- [x] Add behavioral browser tests for retries, conflicts, stale responses, uncertain operations, and malformed success responses.

## Phase 6 — bounds, performance, and operability

- [x] Add per-cell, per-row, total-result, nesting, and catalog-definition limits across all data paths.
- [x] Add global PostgreSQL execution concurrency classes and backpressure.
- [x] Measure before deciding on connection pooling.
- [x] Batch bounded Schemer catalog hydration to avoid N+1 connections.
- [x] Deduplicate identical dashboard queries within one refresh generation where safe.
- [x] Preserve separate temporal snapshots; never claim cross-window consistency.
- [x] Add summary-list and exact-resource endpoints where full-library reloads are wasteful.
- [x] Use distinct PostgreSQL `application_name` values for Schemii, Schemer, and metadata authority.
- [x] Add operational health/readiness for metadata DB, target DB degradation, and OpenCode separately.

## Phase 7 — module ownership and cleanup

- [x] Split shared AI primitive validation from Schemii and Schemer action vocabularies.
- [x] Introduce declarative per-application tool contract registries and parity tests.
- [x] Extract Schemii and Schemer AI executors from HTTP handlers.
- [x] Extract explicit PostgreSQL route policies and guards from stringly mixin hooks.
- [x] Decompose `PostgresService` internally while preserving a deliberate facade where useful.
- [x] Remove dead compatibility functions and stale title/fingerprint paths.
- [x] Remove false Compose coupling between Schemer and Schemii health.
- [x] Correct documentation to exactly match implemented capabilities.

## Phase 8 — final verification and delivery

- [x] Run focused tests for every changed boundary.
- [x] Run complete Python and JavaScript suites.
- [x] Run formatting/static/syntax checks and `git diff --check`.
- [x] Run Schemii and Schemer server/API smoke checks.
- [x] Run disposable metadata PostgreSQL bootstrap/migration/restart checks.
- [x] Run disposable target PostgreSQL read/write/migration/reconciliation checks.
- [x] Verify no test objects or data remain.
- [x] Verify saved layout snapshots remain equal for every pre-existing entry touched by synchronization tests.
- [x] Review security, permissions, retention, backup, deployment, and rollback documentation.
- [x] Review the complete branch diff and commits against `main`.

## Completion criteria

The rewrite is complete only when:

- Equivalent operations use coherent authority and recovery state machines.
- No browser or OpenCode field establishes server authority.
- Schemer and Schemii both use application-owned chat identity.
- Normal migrations cannot apply an obsolete saved design or falsely report rollback after uncertain commit.
- Destructive reconstruction preserves all supported PostgreSQL metadata or refuses to run.
- Server metadata transitions are transactional and restart-safe in PostgreSQL.
- All result paths have explicit memory/concurrency bounds.
- Documentation and behavior agree.
- Full verification and disposable PostgreSQL checks pass.
