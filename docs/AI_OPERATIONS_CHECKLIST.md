# AI Operations Foundation Checklist

## Durable Authority

- [x] Store proposals, operations, and query-result references in application-scoped config directories.
- [x] Use inter-process locks, atomic replacement, directory synchronization, and restrictive permissions.
- [x] Hash claim and result-reservation tokens at rest.
- [x] Preserve consumed and uncertain tombstones until expiry.
- [x] Make expired claims and interrupted operations fail closed as `uncertain`.

## Server Execution

- [x] Canonicalize enabled actions before proposal issuance.
- [x] Key one durable operation to each proposal ID.
- [x] Guarantee one execution owner across concurrent requests.
- [x] Return existing operation state for duplicate execute requests.
- [x] Provide execute, status, and lost-response reconciliation routes.
- [x] Execute AI SQL only from the canonical proposal stored by the server.
- [x] Recompute each canonical action's capability and approval floor during authorization.
- [x] Separate SQL completion from optional model analysis.
- [x] Reject privileged PostgreSQL roles and `EXPLAIN` for AI SQL.
- [x] Keep uncertain model-result delivery reserved rather than redisclosing rows.
- [x] Keep Schemer query rows out of durable operation outcomes while retaining bounded execution evidence.
- [x] Propagate operation timeout and cancellation identity through complete-widget validation queries.
- [x] Run Schemer PostgreSQL work from revision snapshots without holding dashboard locks, then recheck the revision at final mutation.
- [x] Track new Schemer dashboard operations before mutation and archive receipts before rollover/deletion; leave unwitnessed legacy absence uncertain and never replay.
- [x] Report dashboard-record and receipt-archive corruption through Schemer readiness.

## Browser Boundary

- [x] Remove browser claim/finalize/release coordination from active execution paths.
- [x] Require explicit confirmation before every enabled operation.
- [x] Remove session-wide AI SQL approval.
- [x] Poll a concurrently running operation instead of repeating it.
- [x] Accept only allow-listed client commands and result kinds.
- [x] Flush or block pending Schemer edits and verify the persisted dashboard revision before AI execution.

## Temporarily Disabled Actions

- [x] Disable Schemii schema mutation, project creation, connection opening, and migration-preview tools.
- [x] Disable Schemer dashboard and widget mutation tools.
- [x] Keep ordinary application UI workflows available.

## Re-Enable Criteria

Each disabled action requires all of the following before its tool is restored:

- [ ] Strict server-side action normalization with unknown-field rejection.
- [ ] Application-owned execution against authoritative saved state.
- [ ] Exact revision and target binding; Schemii mutations also require layout-token binding.
- [ ] Deterministic generated IDs or another idempotent operation key.
- [ ] Atomic persistence with one resource revision increment.
- [ ] Lost-response reconciliation against exact intended state.
- [ ] An `uncertain` outcome when reconciliation cannot prove success or no effect.
- [ ] Focused concurrent-execute, restart, stale-binding, and response-loss tests.
- [ ] Byte-for-byte preservation tests for unrelated layout, viewport, widgets, and configuration.
- [ ] Updated user-facing capability documentation.

Schemii saved-schema mutation, local project creation, exact connection opening, durable migration preview, and separately confirmed transactional migration apply adapters satisfy these criteria. Schemer dashboard creation and widget create, rename, duplicate, and delete adapters also satisfy these criteria.

Schemii structured row insertion and expected-absent ordinary-view creation also satisfy this boundary: models emit preview proposals only, Schemii issues separate apply proposals, plans persist with restrictive permissions, apply rechecks the exact profile/database/namespace/relation and saved revision/layout binding, and uncertain commits reconcile through PostgreSQL transaction status without replaying the write. View synchronization is narrow and receipt-backed; inserts never mutate saved schema layout.
