# Sustainable Boundaries Interface Matrix

This is the Phase 0 inventory of authority-relevant persistence and wire contracts. It records migration disposition for the architecture rewrite.

## Persistence

| Current record | Location | Current owner | Rewrite disposition |
| --- | --- | --- | --- |
| PostgreSQL profiles | `postgres_profiles.json` | Shared profile service | Preserve; version and migrate losslessly. Keep credentials outside browser/OpenCode. |
| Migration history | `migration_history.json` | PostgreSQL service | Preserve as non-authoritative audit history; move to metadata PostgreSQL later. |
| Legacy Schemii chats/policies/grants | `ai_chats/v1/*.json` | Retired JSON authority | Archived as inert evidence; never imported as executable records. |
| Chats, policies, grants, proposals, operations, query-result references | Metadata PostgreSQL | Application-scoped metadata authority | Active source of truth with transactional transitions, retention, and RLS isolation. |
| Legacy AI proposals/operations/results | `ai_authority/v1/{app}/*.json` | Retired JSON authority | Archived as inert evidence; never imported or replayed. |
| Legacy AI migration/write plans | `ai_migration_plans/*.json` | Retired PostgreSQL-service compatibility | Archived as inert evidence; durable coordinator plans are the only executable plans. |
| Normal migration/view plans | Process memory | PostgreSQL service | Reset; replaced by durable plan store. |
| Console settings/execution receipts | Metadata PostgreSQL | Shared Console metadata authority | Durable application-scoped optimistic settings and exact-owner terminal receipts; retired write grants are not authority. |
| Active Console executions/transactions/results | Process memory | Shared Console service | Cancellation, explicit transactions, retained snapshots, and result cursors are intentionally process-lifetime resources; restart rolls back/closes them while durable receipts prevent write replay. |
| Schemii schemas | schema directory | Schemii domain store | Preserve exactly, including unknown fields, semantic IDs, receipts, and complete layout. |
| Schemer dashboards | dashboard directory | Schemer domain store | Preserve exactly, including widget order, source snapshots, queries, viewport, and layouts. |
| OpenCode sessions/credentials | OpenCode volumes | OpenCode | Preserve opaque upstream data. Store only verified external session references in metadata DB. |

## AI wire contracts

Current shared routes include status/auth, versioned agent settings GET/PUT, session creation/list/history/activity/delete, durable conversation-title rename through `PUT /api/ai/sessions/{chatId}/title`, messages, proposal execute/reconcile, proposal-bound query cancellation, and operation status. Query cancellation is `DELETE /api/ai/sessions/{chatId}/proposals/{proposalId}/execution`; it durably records intent before signalling the process-local Psycopg connection so pre-attachment races fail closed. Claim/finalize/release proposal routes no longer exist.

Target changes:

- Application chat ID becomes distinct from external OpenCode session ID.
- Both applications use durable chat ownership; titles are display-only.
- Browser does not resend resource, target, capability, or policy authority after chat creation.
- Each chat owns an immutable version-2 snapshot of agent policy revision, effective capability modes/floors, bounds, disclosure, and target verification. Settings changes revoke only incompatible linked future authority.
- Proposal execution and reconciliation are the only proposal transitions exposed to browsers.
- Query-result delivery adds explicit pre-dispatch reservation and post-dispatch uncertain states.
- Pending valid proposals are restored from metadata authority, never reconstructed from model history.
- Lifecycle maintenance heartbeats active operation leases, abandons stale attempts, recovers result reservations/delivery, and performs retention cleanup; lost or uncertain work is reconciled and never replayed.

## Migration wire contracts

Normal preview accepts the exact saved schema ID, revision, layout token, namespace, and destructive-preview choice; the server loads the desired schema. Normal, view, and overlapping AI writes persist UUID plans in metadata PostgreSQL. Apply accepts only `reviewDigest` and `confirmDestructive`; resource and target authority comes from the durable plan.

Target contract:

```json
{
  "schemaId": "schema_...",
  "expectedRevision": 7,
  "layoutToken": "...",
  "profileId": "pg_...",
  "database": "organization",
  "namespace": "public",
  "allowDestructive": false
}
```

The server loads intended state, persists a canonical reviewed plan, and returns `planId`, `reviewDigest`, and bounded review data. Apply submits only plan identity, matching digest, and destructive confirmation. Status/reconcile routes expose the one durable execution.

Plan status is available at `GET /api/postgres/migration-plans/{planId}/status`; execution status and explicit reconciliation use `GET /api/postgres/migration-executions/{executionId}/status` and `POST /api/postgres/migration-executions/{executionId}/reconcile`. Reconcile has an empty JSON body and checks `pg_xact_status` without replaying SQL. Interrupted `applying` records are first durably promoted to reconcile-only `uncertain`; committed XIDs require the persisted intended result for automatic success, otherwise status remains `manual_required`.

## Resource deletion

Delete contracts use optimistic preconditions:

- Schema: revision plus layout token.
- Dashboard: revision.
- Profile: context fingerprint plus server-generated dependency impact.

Schema deletion sends `expectedRevision` and `layoutToken`; dashboard deletion sends `expectedRevision`. Profile deletion first fetches `GET /api/postgres/profiles/{id}/deletion-impact`, reviews the server-visible schemas, dashboards, active chats, plans, and operations, then sends the returned `profileFingerprint` and `impactFingerprint` in the `DELETE` body. Either digest changing produces a stale conflict. Profile deletion never deletes dependent resources.

## Schemer query execution

The `relation/query` and `relation/detail` routes remain caller-structured draft execution with optional dashboard revision context. `saved-widgets/aggregate` and `saved-widgets/detail` require dashboard ID, revision, and widget ID; the server loads the saved source, structured query, detail configuration, and visualization projection. Temporal execution retains its exact saved line-widget guard.

Target distinction:

- Draft relation execution remains caller-structured and target-verified.
- Saved-widget execution requires dashboard/widget identity and server reconstruction.
- Documentation and route names do not imply saved-widget authority for draft execution.

## HTTP errors and browser contracts

Every API failure, including unknown routes and legacy handler failures, is normalized to `{ "error": { "code": "...", "message": "...", "retryable": false, "details": {} } }`; `retryable` and `details` are omitted when not applicable. Unexpected failures use generic text rather than exception or credential material. Shared browser validators reject malformed session, profile, catalog, plan, operation, schema, dashboard, deletion-impact, aggregate, and detail successes. Session bootstrap is single-flight, aborting one waiter does not cancel other waiters, and an invalid session response is retried at most once without allowing stale responses to clear a newer token.
