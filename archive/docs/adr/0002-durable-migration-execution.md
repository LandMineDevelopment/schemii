# ADR 0002: One Durable Migration Execution Lifecycle

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

Normal migrations, AI migrations, and view mutations currently have different persistence, ownership, binding, commit-uncertainty, and reconciliation guarantees. Normal migrations are process-local and browser-schema-authored. AI migrations are durable but have weaker transaction evidence than structured writes. View mutations have strong operation-specific validation but are process-local.

## Decision

Use one PostgreSQL-backed plan and execution lifecycle where the operations share semantics:

```text
ready → applying → succeeded | failed | uncertain
```

Every apply-capable plan records:

- Exact saved resource ID, revision, and layout token.
- Exact saved profile/database/namespace binding.
- Server-generated profile and connected-target fingerprints.
- Reviewed live and desired fingerprints.
- Strictly validated private payload.
- Canonical public review payload and SHA-256 digest.
- Destructive classification and reviewed impact.

Execution rules:

- Unique one-execution-per-plan ownership.
- Confirmed review digest and destructive choice persisted before target connection.
- Target transaction ID and identity persisted before mutation.
- Intended result persisted before commit request.
- Lost commit acknowledgement becomes uncertain, never reported as rollback.
- Reconciliation uses transaction status and immutable evidence; it never replays SQL.
- PostgreSQL commit outcome and saved-resource synchronization outcome are recorded separately.

Full schema migration and view mutation retain separate planning, locking, preservation, and synchronization adapters. Materialized-view preservation remains specialized.

## Consequences

- Normal migrations can no longer apply an obsolete browser-supplied design.
- Lost responses and process restarts converge on one operation instead of replaying work.
- Existing in-memory normal/view plans and JSON AI plan authority are retired after cutover.
- Browser routes become plan/status/reconcile clients rather than post-commit authority owners.
