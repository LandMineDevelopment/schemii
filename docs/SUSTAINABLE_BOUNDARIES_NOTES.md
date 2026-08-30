# Sustainable Boundaries Notes

Durable working notes for `architecture/sustainable-boundaries`. Keep this file current during implementation and before context compaction or agent handoff.

## Current handoff

- **Branch:** `architecture/sustainable-boundaries`
- **Base checkpoint:** `main` at `692e6e6`, pushed to `origin/main`
- **Current phase:** Phase 8 complete
- **Next action:** commit the final verified operational hardening and deliver the branch
- **Active files:** credential deployment, read-only cross-domain dependency indexes, and final verification notes
- **Worktree:** all rewrite phases are implemented; final verified operational fixes are awaiting one focused commit
- **Invariants:** PostgreSQL, not SQLite, for transactional server metadata; browser and OpenCode never execute DB work; exact target/profile role; preserve schema/dashboard layout; stale conflicts fail closed; uncertain writes are never retried blindly
- **Latest verification:** 341 Python tests passed with 6 environment/platform skips; every JavaScript test and all three browser syntax checks passed; Python compile, POSIX shell syntax, eight Compose configurations, and `git diff --check` passed; disposable full-stack, target PostgreSQL, durable migration, and live metadata credential lifecycle checks passed
- **Blockers:** none

## Decisions

### 2026-08-13 — Authority persistence technology

The user explicitly rejected SQLite. If transactional persistence is required, use a PostgreSQL instance/database dedicated to server metadata. This database is application infrastructure and must remain distinct from user-selected target databases.

Questions the implementation must answer without weakening fail-closed behavior:

- Is metadata PostgreSQL a dedicated Compose service or a dedicated database/role in an included service?
- How do UI-only/external-target launch modes obtain metadata storage?
- What remains available when metadata PostgreSQL is down?
- How are bootstrap migrations versioned and serialized?
- What backup/retention expectations apply to chats, proposals, and receipts?

Decision after architecture research:

- Add a dedicated private `metadata-postgres` service and volume in every launcher mode. Do not store metadata in the optional user-target `postgres` service.
- Use database `schemii_metadata` with separate narrowly scoped Schemii and Schemer runtime roles, a no-login owner, and a migration role.
- Every mode uses the private metadata service name. Linux host-PostgreSQL modes keep the application in bridge mode and use a private Unix-socket relay, so metadata PostgreSQL is never published.
- Both application readiness checks require metadata connectivity and current migrations. Static UI and domain-document access may degrade, but authority-dependent actions fail with structured `503 metadata_unavailable`; there is no JSON fallback.
- Use packaged, checksummed SQL migrations serialized by a PostgreSQL advisory lock. No ORM or migration dependency is required.
- Treat metadata backups separately from user target backups. Metadata contains sensitive transient query-result payloads and authority history.

### 2026-08-13 — Rewrite freedom

This is a prototype architecture branch. Prefer the strongest coherent design over compatibility-only indirection. Preserve user data or provide an explicit migration/reset path, but do not retain weak title-bound or multi-file authority solely to minimize diffs.

### 2026-08-13 — Prototype cutover policy

- Preserve profiles, schemas, dashboards, migration history, example markers, PostgreSQL target data, and OpenCode volumes.
- Do not import active Console grants/executions or process-local migration/view plans.
- Archive existing JSON chats, proposals, operations, result references, and AI plans; do not activate executable legacy authority whose cross-file atomic state cannot be proven.
- Optionally import fully validated Schemii chats as read-only history after discarding grants and pending authority. Do not infer active Schemer authority from OpenCode titles.
- New chats use an internal application chat ID with the OpenCode session ID as an external transport reference.

### 2026-08-13 — Transactional authority model

The metadata schema will use relational ownership/state fields and JSONB only for bounded, versioned polymorphic payloads.

Core aggregates:

- Applications and migration ledger.
- Chats, external provider-session ownership, immutable targets, policy versions/capabilities, and grants.
- Proposals, operation approvals, operations, attempts/heartbeats, outcomes, receipts, and transition audit.
- Query-result references, separately scrub-able payloads, deliveries, and transitions.
- Migration plans, executions, transaction evidence, resource synchronization receipts, and transition audit.

Critical transaction boundaries:

- Policy update locks the chat, creates an immutable next revision, and revokes incompatible grants atomically.
- Approval, once-per-chat grant creation, proposal claim, and unique operation creation occur in one transaction.
- One execution per proposal/plan is enforced by unique constraints.
- External OpenCode creation/deletion uses a durable provisioning/deleting saga because it cannot share a transaction.
- Target PostgreSQL execution never occurs while a metadata transaction is open. Write-ahead transaction evidence is persisted before target mutation.
- A stale execution heartbeat permits reconciliation, never replay.
- Query result delivery distinguishes pre-dispatch release from post-dispatch `delivery_uncertain` and scrubs uncertain payloads.

### 2026-08-13 — Durable migration model

- Normal UI, AI migration, and view mutation use one durable plan/execution lifecycle where semantics overlap.
- Plans bind exact saved resource ID/revision/layout token, saved target, profile and connected-target fingerprints, reviewed live state, desired state, destructive choice, canonical review payload, and SHA-256 review digest.
- Apply creates one durable execution and records target `xid8` evidence before DDL.
- Commit acknowledgement and saved-resource synchronization are independent outcomes. A proven commit remains committed even if schema sync conflicts or storage fails.
- Full-schema and narrow-view planning/locking/synchronization remain separate adapters.
- Materialized-view preservation remains specialized and must reject unsupported metadata.
- Legacy in-memory and JSON plan authority will be removed after cutover.

## Baseline findings

- Strong core trust model: OpenCode proposes; server authorizes and executes; PostgreSQL owns live truth; domain stores own intended state and layout.
- Highest-risk inconsistencies: non-transactional authority transitions, Schemer title-bound chats, normal migration uncertainty/binding, unsafe table reconstruction metadata loss, missing delete preconditions, and unbounded connection/result paths.
- Appropriate shared boundaries: local HTTP/session, profile/connection/catalog mechanics, relation source, widget query compiler, atomic persistence primitives, OpenCode transport, authority concepts, and shared browser shells.
- Domain boundaries to preserve: Schemii schema/migration semantics and Schemer dashboard/widget/query semantics.

## Milestone log

### Baseline checkpoint

- Committed all previous work on `main` as `692e6e6 Rework AI authority and PostgreSQL actions`.
- Pushed `main` to `origin/main`.
- Created `architecture/sustainable-boundaries`.
- Added this durable checklist and handoff protocol.

### Phase 0 design research

- Completed topology, authority-schema, migration-state-machine, and persistence/interface inventories with four specialized agents.
- Selected dedicated metadata PostgreSQL in every launch mode.
- Selected archive/reset rather than ambiguous executable-authority import.
- Defined transactional authority and migration boundaries above.

### Phase 1 metadata PostgreSQL foundation

- Added a dedicated metadata PostgreSQL service, volume, bootstrap roles, checksummed migrator gate, and runtime DSNs in all Compose modes.
- Added the packaged `schemii.metadata_migrate` entry point and exact-prefix migration-ledger validation.
- Added normalized metadata tables and a transactional repository foundation for policy, operation, and query-result workflows.
- Enforced Schemii/Schemer row isolation in PostgreSQL with runtime-role-derived RLS and denied runtime mutation of the migration ledger.
- Hardened repository validation, proposal expiry checks, policy/authorization lock ordering, active-chat result creation, payload scrubbing, and ambiguous commit reporting.
- Application server integration, lease/reconciliation behavior, transition audit writes, and durable migration execution remain subsequent work.

### Operational credential lifecycle

- Replaced known metadata defaults and `PGPASSWORD` injection with per-instance cryptographically random, file-mounted Compose secrets.
- Added metadata and OpenCode password-file consumers; OpenCode credentials now remain stable across launcher restarts.
- Added fail-safe legacy-volume detection, explicit compatibility warnings, owner-only storage intent, and coordinated credential backup, restore, and rotation commands.
- Expanded uninstall discovery to Schemer-only projects and included dashboard volumes, application images, and exact instance-marked credential directories.
- Replaced migration-role `CREATEROLE` and runtime-role administration with one bootstrap-owned `SECURITY DEFINER` password-rotation function having fixed role targets, fixed `pg_catalog` search path, bounded inputs, and migration-role-only execution.
- Added read-only cross-domain schema/dashboard indexes for profile-deletion impact. Compose mounts the other application's document volume read-only only when Schemer is enabled; neither application can mutate the other domain's records.

### Phase 8 integration completion

- Completed PostgreSQL-backed authority cutover for Schemii and Schemer; no JSON authority fallback remains.
- Completed operation leasing, stale-worker reconciliation, transition audit, query-result delivery uncertainty, and idempotent operation ownership.
- Completed durable normal/AI migration coordination with write-ahead target evidence and independent commit/synchronization outcomes.
- Finalized legacy handling as archive-only inert evidence rather than executable authority import.

## Verification log

- Final full Python verification: 341 passed, 6 skipped (four opt-in PostgreSQL tests, one opt-in Docker credential test, and one unavailable PowerShell-platform test). The opt-in Docker credential lifecycle was then run separately and passed.
- Final JavaScript verification: every `tests/test_*.js` file passed; Schemii, Schemer, and shared assistant syntax checks passed.
- Opt-in PostgreSQL 17 safety suite: 16 view/catalog/lock tests passed against disposable PostgreSQL 17, including cleanup assertions.
- Opt-in metadata credential lifecycle: bootstrap privilege checks plus backup, rotate, restore, restart, and authentication passed.
- Fresh full-stack Compose smoke: metadata migrations 1–4, Schemii `/`, `/api/session`, `/api/readiness`, Schemer `/`, `/api/session`, `/api/readiness`, target PostgreSQL seed, and both application health checks passed.
- Durable HTTP migration smoke: exact saved schema preview produced zero unexpected steps/warnings; apply committed with xid evidence; plan status returned `succeeded/committed`; saved-schema synchronization returned `succeeded` after restart.
- Fresh-stack review found and fixed two integration defects before delivery: the rotation SQL init mount/order and read-only cross-domain dependency stores. The complete fresh stack passed after both fixes.
- Final security review additionally fixed recoverable credential file/database transitions, bind-mounted secret identity preservation, exact credential formatting, cross-process launcher locking, timestamp JSON serialization, metadata restore ownership guidance, and label-verified uninstall ownership. POSIX launcher/uninstaller tests include macOS Bash 3.2 compatibility guards.
- Disposable target inspection found no remaining `schemii_*_test` namespaces. Final containers, networks, volumes, images, and generated credential files were removed.
- POSIX shell syntax, Python compilation, eight Compose render combinations, and `git diff --check` passed. Native PowerShell parsing/runtime was unavailable in the final shell; CI retains Windows parser coverage.

- Phase 8 final verification: 328 Python tests passed with 6 expected skips; all JavaScript suites and browser syntax checks passed; Python compile, shell syntax, both PowerShell parsers, eight Compose combinations, and `git diff --check` passed.
- Phase 8 live credential verification: fresh metadata bootstrap proved migration `rolcreaterole=false`, bootstrap `rolcanlogin=false`, bootstrap function ownership, `SECURITY DEFINER`, fixed `search_path=pg_catalog`, no PUBLIC execute, migration-only execute, bounded-input rejection, and successful backup/rotate/restore with disposable resources removed.

- Phase 7 focused verification: 107 AI action/registry/executor/server/PostgreSQL route/migration tests passed.
- Phase 7 full verification: 324 Python tests passed with 5 expected skips; every `tests/test_*.js` file passed; Python compile, all three browser syntax checks, eight supported Compose config combinations, and `git diff --check` passed.
- Phase 7 ownership: shared AI primitives and app vocabularies are separate; declarative tool contracts match OpenCode registrations; app executors own action execution/reconciliation; PostgreSQL routes use explicit app policies; durable migration compatibility is behind `PostgresMigrationFacade`.
- Retired `ai_authority.py`, `ai_chat_store.py`, their JSON-only tests, browser authority fingerprint derivation, and proposal claim/finalize/release routes. Legacy records remain archive-only migration inputs.

- Pre-branch full Python: 286 passed, 5 skipped.
- Pre-branch all `tests/test_*.js`: passed.
- Pre-branch Python compile, JavaScript syntax, and `git diff --check`: passed.
- Live `schemii-schemii-1`: rebuilt from checkpoint worktree and healthy on `127.0.0.1:8080`.
- Metadata-focused unit/Compose checks: 20 passed after reconciliation.
- Disposable PostgreSQL integration: built the wheel-backed image, bootstrapped roles, applied migration 1, verified runtime migration-ledger writes are denied, verified Schemii rows are invisible to Schemer, reran migrations after restart, and confirmed metadata persisted.

## Open risks

- Legacy metadata volumes created before the scoped rotation function require a reviewed one-time bootstrap-owned installation before credential rotation or restore; launchers fail closed until it exists.
- Windows ACL enforcement, parser validation, and the PowerShell live credential lifecycle still require a native Windows CI/runtime runner; `pwsh` was unavailable in the final local shell.
- Metadata backups contain authority history and transient sensitive payloads and must remain protected and restore-tested independently from user target backups.
