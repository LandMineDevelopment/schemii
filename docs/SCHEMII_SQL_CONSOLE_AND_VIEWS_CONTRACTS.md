# Schemii SQL Console And Views Contracts

Status: SQL Console read/write execution and the live Schemii Views catalog, inspection, ordinary-view lifecycle, and materialized-view creation/recreation/deletion are implemented. Kind conversion and materialized refresh remain unsupported.

This document defines the execution, catalog, persistence, conflict, and migration boundaries for Schemii's independent SQL Console and graphical Views layer. It extends focused shared infrastructure used by Schemii and Schemer without combining their application workflows or weakening their distinct revision guards.

## Design Standard

Choose the best coherent long-term design for the product outcome, constrained by simplicity, maintainability, reuse, capability isolation, migration cost, and operational safety. A smaller diff is not preferable when it creates duplicate connection logic, incompatible contracts, or a weaker user workflow.

Share stable foundations with multiple concrete consumers. Keep Schemii schema design, write authorization, migration, and layout ownership separate from Schemer dashboard revisions, widget projections, and analytic execution policy.

## Shared Boundaries

Extend these existing modules rather than adding Console- or Views-specific copies:

- `postgres_connections.py`: profile-backed PostgreSQL connections and value conversion.
- `postgres_common.py`: validation, identifier quoting, fingerprints, and structured service errors.
- `postgres_catalog.py`: exact relation inspection, semantic columns, definitions, and relation fingerprints.
- `postgres_service.py`: public PostgreSQL service facade and transactional execution.
- `postgres_http.py`: capability-scoped `/api/postgres/...` routes and per-application policies.
- `relation_source.py`: exact persisted relation identity and semantic column snapshot.
- `shared_web/session-client.js` and `shared_web/postgres-client.js`: authenticated browser transport.
- Dedicated Schemii `preview_view_mutation()` and `apply_view_mutation()`: saved-schema-bound view DDL planning and application, isolated from general schema plans.

Do not introduce another connection implementation, SQL parser, profile store, HTTP session mechanism, migration executor, or generic application framework.

### Capability Split

Schemii mounts the shared profile, catalog, schema, read-SQL, and Console capabilities, including its write-enabled Console policy. Schemer separately mounts relation analytics and its own revision-bound policies. The two view mutation routes are implemented directly by the Schemii server and are not mounted by Schemer. There is currently no `view_actions` capability or materialized-refresh route.

The existing `POST /api/postgres/profiles/{profileId}/sql` route remains a backward-compatible, single-statement, read-only adapter over the shared executor. Existing Schemii table tools, AI actions, and Schemer analytic SQL retain their application-specific policies and guards; the adapter cannot enter a Console write mode.

## Exact Target Contract

Every Console and Views request binds to:

- authenticated local HTTP session token and current server ID;
- saved `profileId` from the route;
- explicit `database` equal to the saved profile database;
- explicit `namespace` that exists on the connected database;
- connected `current_database()` equal to `database`;
- the saved PostgreSQL role, without privilege elevation.

Exact relation operations additionally carry canonical source identity:

```json
{
  "profileId": "local",
  "database": "bookstore",
  "namespace": "bookstore",
  "relation": "order_summary",
  "operation": "upsert",
  "kind": "view",
  "fingerprint": "64-lowercase-hex-characters",
  "columns": []
}
```

Unknown fields are rejected. The server re-inspects relation kind, semantic columns, and fingerprint before execution. It never substitutes an inferred profile, database, namespace, relation, or kind.

Credentials remain server-side. Browser responses, saved query history, logs, layout records, and schema records never contain passwords.

## Shared SQL Console

The Console implementation is shared by Schemii and Schemer. Both intentionally expose the human write-capable modes under independent durable application write intent and the selected PostgreSQL role's permissions. Schemii's separately proposal-bound AI write authority does not grant Schemer AI or either human Console additional authority. Every request binds a canonical execution or transaction UUID to application, HTTP session, server ID, `consoleId`, profile ID and fingerprint, database, namespace, and durable settings revision where applicable. Unknown fields and mismatched targets are rejected.

### Modes

- `managed_read`: one repeatable-read, read-only transaction executes the script. Result cursors retain that original snapshot until exhausted, closed, cancelled, expired, or shutdown; SQL is never rerun for continuation. The transaction then rolls back.
- `managed`: one read-write transaction executes every statement and commits only after all statements succeed. Returned rows are copied to a bounded server spool before commit. Known failures and cancellation roll back the database transaction.
- `explicit`: a process-local exact-owner transaction resource spans multiple execution requests and reports PostgreSQL transaction state. Savepoint commands may run inside it; top-level transaction control remains resource-owned. Commit or rollback first closes all retained result resources deterministically. The application bounds active transaction count, idle time, and absolute lifetime, periodically rolls back expired resources, retains deterministic process-local tombstones, and rolls back during shutdown. These are connection lifecycle policies, not PostgreSQL statement policy; a stricter PostgreSQL `idle_in_transaction_session_timeout` remains authoritative. Browser loss, target switching, or abandoned tabs therefore cannot retain locks indefinitely.
- `autocommit`: maintenance mode enables PostgreSQL commands forbidden in transaction blocks. Each statement commits independently. Failure/cancellation reports `completedStatementIndexes`, `priorStatementsCommitted`, and `partial_committed` when appropriate; connection-class failures after dispatch may be uncertain. It never presents the script as atomic.

PostgreSQL functions, sequences, and external systems may have effects that read-only declarations or transaction rollback cannot reverse. Use a narrowly privileged saved role.

### Resources And Receipts

```text
POST   /api/postgres/profiles/{profileId}/console/executions
GET    /api/postgres/profiles/{profileId}/console/executions/{executionId}
DELETE /api/postgres/profiles/{profileId}/console/executions/{executionId}
POST   /api/postgres/profiles/{profileId}/console/transactions
GET    /api/postgres/profiles/{profileId}/console/transactions/{transactionId}
POST   /api/postgres/profiles/{profileId}/console/transactions/{transactionId}/executions
POST   /api/postgres/profiles/{profileId}/console/transactions/{transactionId}/commit
POST   /api/postgres/profiles/{profileId}/console/transactions/{transactionId}/rollback
GET    /api/postgres/profiles/{profileId}/console/executions/{executionId}/results/{resultId}
DELETE /api/postgres/profiles/{profileId}/console/executions/{executionId}/results/{resultId}
```

The browser generates execution IDs before dispatch but exposes **Stop** only after the execution POST is dispatched; preparatory result cleanup and transaction setup remain visibly busy without presenting an execution that could start later. A bounded, short-lived process-local cancellation reservation closes the DELETE-before-POST race, including managed, autocommit, and explicit write-capable execution: the matching POST records terminal pre-dispatch cancellation and sends no target SQL. Metadata PostgreSQL atomically reserves each ID globally before target connection or SQL dispatch, then transitions that same row through nonterminal and terminal states. A collision from any application, session, server, profile, or Console owner fails generically without revealing the existing owner; restart and context changes cannot make an ID reusable. Failures proven to precede target dispatch become terminal `not_started` evidence. Terminal, reserved, and running IDs are never replayed. Managed and explicit commit failures after commit starts return `execution_outcome_unknown`; no path automatically resubmits SQL.

Result resources carry exact execution, statement/result index, console/transaction, session/server, profile fingerprint, database, and namespace ownership. Opaque cursors advance once; stale cursors fail. Managed-read and explicit results continue the original cursor/snapshot. Managed-write and autocommit results continue only from a process-local spool capped at 10,000 rows and 8 MiB. Resources expire after five minutes and are also bounded to 32 active results and four retained snapshots. Browser JSON export drains that retained pageable cursor or bounded spool within its remaining resource TTL and operator caps; it is not an unbounded streaming export. Spool, width, response, or capacity truncation is explicit and terminal, and omitted spool data cannot be recovered by export.

### Durable Human Settings

`GET/PUT /api/postgres/console/settings` stores one application-scoped optimistic revision with `writeIntent`, `defaultMode`, `statementLimit`, and `rowPageSize`. Defaults are disabled write intent, `managed_read`, 100 statements, and 100 rows per page. Settings do not expire and do not inherit between Schemii and Schemer. A write execution binds the exact current settings revision and profile fingerprint. These settings express human Console intent only; they cannot authorize AI, elevate the selected PostgreSQL role, or bypass product policy. The retired write-grant endpoints return `410 console_write_grants_retired`.

### Limits And Diagnostics

- Scripts contain 1-100 top-level statements, subject to the durable user-selected limit, and at most 100,000 characters.
- Pages are user-selectable within the operator maximum; results allow at most 100 columns, 64 KiB per cell, 256 KiB per row, 1 MiB response metadata, 50 notices, and 8 KiB notice text.
- One execution runs per `consoleId`; process admission separately enforces global, Console-class, and exact-target capacities.
- Explicit transactions default to at most four active resources, five idle minutes, and a 30-minute absolute lifetime. Operators may narrow or raise these within validated hard maxima using `SCHEMII_CONSOLE_TRANSACTION_MAXIMUM`, `SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS`, and `SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS`; idle must not exceed lifetime.
- PostgreSQL role/database/session `statement_timeout` is authoritative by default. A proposal-bound AI `operationTimeoutMs` can only narrow it and is reported as `policy_narrowing`.
- Cancellation requests the registered PostgreSQL connection and closes retained result resources. Completion/rollback is not claimed until execution reports it.
- PostgreSQL errors preserve bounded SQLSTATE, primary message, detail, hint, phase, rollback status, and safe retry/reconciliation guidance. Credentials, SQL text, values, stack traces, and unrestricted diagnostics are not returned or logged.

## Views Catalog And Inspection

The live PostgreSQL catalog is authoritative. Views first derives its target only from the active saved schema record:

- `schemaId` is the active record ID;
- `profileId`, `database`, and `namespace` exactly equal `schema.postgres.sourceProfileId`, `schema.postgres.database`, and `schema.postgres.namespace`;
- `expectedSchemaRevision` is the record's current positive integer `revision`;
- `layoutToken` is the record's current 64-character layout hash.

Without that complete binding, the Views workspace does not query PostgreSQL. Browser requests also reject responses whose profile, database, namespace, relation, or kind differs from the binding/request, and generation guards discard stale catalog and descriptor responses.

### GET APIs

```text
GET /api/postgres/profiles/{profileId}/namespaces?database={database}&scope=user|all&pageSize={n}&cursor={opaque}
GET /api/postgres/profiles/{profileId}/relations?database={database}&namespace={namespace}&kind={kind}&search={text}&pageSize={n}&cursor={opaque}
GET /api/postgres/profiles/{profileId}/relation?database={database}&namespace={namespace}&relation={relation}&expectedKind={kind}[&expectedFingerprint={fingerprint}]
GET /api/postgres/profiles/{profileId}/lineage?database={database}&namespace={namespace}&relation={relation}&direction=dependencies|dependents&expectedKind={kind}&expectedFingerprint={fingerprint}&pageSize={n}&cursor={opaque}
```

All routes require the authenticated local PostgreSQL session. Each page uses a repeatable-read, read-only transaction and verifies `current_database()` against `database`. Opaque keyset cursors are HMAC-bound to the exact profile fingerprint, database, namespace/scope, relation, kind, relation fingerprint, direction, filters, sort, page size, and complete catalog fingerprint. A changed catalog returns `catalog_cursor_stale`; a cursor from another context returns `catalog_cursor_mismatch`. `scope=user` excludes system namespaces; `scope=all` is explicit opt-in and labels `pg_catalog`, `information_schema`, temporary, toast, other system, and user namespaces.

The relation catalog response is:

```json
{
  "profileId": "local",
  "database": "bookstore",
  "namespace": "bookstore",
  "relations": [{"name": "order_summary", "kind": "view"}]
}
```

The shared catalog contains tables, partitioned tables, ordinary views, materialized views, and foreign tables; the Schemii Views browser retains ordinary and materialized views for mutation. The descriptor accepts every catalog relation kind for read/lineage workflows and returns exact target identity and kind plus:

- ordered semantic columns (`name`, PostgreSQL display `type`, `nullable`, `ordinal`, and advisory suggestions);
- a stable 64-character semantic fingerprint covering identity, ordered columns, catalog kind, and view definition;
- `definition`: `{status:"available", format:"query", sql}` or `{status:"unavailable", reason:"not_permitted"|"too_large"|"not_supported"}` with a 64 KiB cap;
- `owner`: an available role name or explicit `not_permitted` envelope;
- advisory current-role permissions: `canSelect`, `canAlter`, and materialized-only `canRefresh`;
- an initial page of direct dependencies and dependents plus the dedicated exact-source continuation route, including verified cross-namespace read-only identities;
- `materialized`: population and qualifying unique-index-based concurrent-refresh eligibility for materialized views, or `not_applicable` for other kinds;
- `columnProvenance`: a relation-fingerprint-bound `available` or `partial` envelope of ordered outputs, bounded expressions, derivation classifications, and exact direct-source column identities, or an explicit `unavailable` reason;
- `joinPredicates`: an independently fingerprinted, relation-fingerprint-bound `available` or `partial` envelope of ordered joins, normalized bounded conditions, join types, root-versus-nested query scope, and verified column-equality endpoints, or an explicit `unavailable` reason.
- `sqlStages`: a version-1, relation-fingerprint-bound envelope whose order semantics are `syntactic_dependency`. Available or partial envelopes contain at most 128 real query-local `cte`, `derived_table`, or `query_block` stages and exactly one unnamed, nonrecursive, parentless outer-SELECT `query_block`. Unavailable envelopes contain no stages and an explicit reason.

Each SQL stage has a stable `stageId`, contiguous `displayOrdinal`, kind, nullable parent stage, recursion flag, `lifetime:"query"`, stage dependencies, bounded SQL, ordered output expressions, ordered inputs, and ordered join/where/having predicates. An input points either to another declared stage or to an exact profile/database/namespace/relation/kind identity in the verified dependency snapshot; an optional SQL alias belongs to that input reference. The browser rejects unknown stage references, dependency mismatches, relation sources outside the verified dependencies, invalid fingerprints, non-contiguous order, multiple or malformed root query blocks, and breached bounds. Display order is dependency-aware presentation order and does not describe PostgreSQL execution order. CTEs, derived tables, and the outer SELECT query block are query-local syntax, not PostgreSQL temporary tables or a claim about runtime execution order.

`expectedKind` and optional `expectedFingerprint` cause `relation_changed` if live metadata differs. Foreign tables are inspectable read sources, but the dedicated view mutation route still accepts only ordinary and materialized views. PostgreSQL dependency metadata remains relation-level only. Schemii therefore analyzes the bounded `pg_get_viewdef` PostgreSQL query with SQLGlot and validates every resulting leaf and join endpoint against source columns read in the same repeatable-read transaction. It classifies outputs as `direct`, `expression`, `aggregate`, `window`, or `constant`. The join analyzer preserves normalized full conditions but promotes only verified column-to-column equality predicates; unsupported terms remain explicitly partial, and `NATURAL JOIN` is never presented as a verified inferred key. The analyzers are application capabilities, not PostgreSQL permissions or guarantees: unsupported SQL, unresolved leaves, or breached source, output, predicate, expression, definition, or envelope limits return partial/unavailable envelopes instead of guessed mappings. Each envelope has its own fingerprint and remains bound to the relation fingerprint; neither alters Schemer's saved source fingerprint contract.

The live frontend:

- lists exact live objects in a right-side sibling pane;
- applies case-insensitive typed search to already loaded cards without rerendering the workspace;
- combines search with All, Views, and Materialized kind filters;
- shows the selected view, exact output fields, direct lineage, and a compact impact summary;
- renders one focusable, pan/zoom graphical lineage canvas in semantic source-to-local-stage-to-root-query-block-to-final-view-to-consumer order;
- routes every verified physical input through its applicable CTE/derived stages and the available root query block; it never adds a source-to-final bypass or a conceptual fallback stage;
- presents the root query block as a compact join hub: visible alias and join summaries retain verified endpoints and partial reasons, while full WHERE/HAVING and selected-projection evidence remains available through its `More query logic` disclosure;
- uses generous deterministic fallback columns and vertically balanced lanes without saving fallback coordinates, while persisted Views positions remain authoritative;
- routes labels on their owning segments in a dedicated foreground SVG layer, converges every root input at one visible query-block port, and uses separately styled available, partial, active, and active-partial SVG arrowheads at destination endpoints;
- gives source, CTE/derived, query-block, final-view, and consumer cards titled drag surfaces while preserving clickable controls and column lists;
- makes source relations, verified join rows, the query block, mapped source columns, and output columns ordinary selectable controls; source, join, block, and output focus derives paths only from verified stage inputs, join endpoints, and projection contributors, visibly de-emphasizes unrelated context, and can be cleared without changing the viewport or saving layout;
- keeps read-only catalog and lineage state across layout-only revision/token advances for the same saved schema and exact PostgreSQL target, while mutation preview/apply continues to require the latest full revision and layout token;
- keeps the final view card's all-input projection detail and exact selected bounded expression;
- proactively loads supported same- or cross-namespace source descriptors and keeps their actual columns visible on each source card, labeling every mapped column with each output that consumes it;
- constrains source-column rows to the card width, keeps long identities and projections bounded, and permits vertical column scrolling without a horizontal scrollbar;
- opens an idempotent read-only relation inspector for supported relation kinds without turning lineage into write authority;
- makes a definition read-only when it is unavailable; advisory `canAlter` remains explanatory and PostgreSQL authorizes preview/apply.
- supports arrow-key pan, visible fit/zoom controls, reduced motion, crisp layout-rasterized text at fractional zoom, and invariant world coordinates on mobile rather than document reflow.

## Full-Schema Migration Completeness

Full-schema preview is apply-capable only when every requested live-to-desired difference is represented by executable reviewed steps. Destructive or replacement differences omitted by the user's preview choice, dedicated view-lifecycle work, touched partition relationships, dependent views, incomplete preservation inventory, and unsupported reconstruction are returned in `blockingDifferences` with a next action. Such a response has `complete:false`, `applyCapable:false`, no durable plan ID, and cannot apply a generated "safe subset" as if full synchronization succeeded. The saved desired design remains unresolved intent.

An apply-capable plan persists an explicit completeness proof over the reviewed live migration fingerprint and desired-schema fingerprint in both private and review payloads. Metadata creation, apply, and post-commit saved-schema synchronization each reject a missing/mismatched proof. Apply also binds profile fingerprint, database, namespace, schema revision/layout token, review digest, destructive choice, and, for AI work, the exact proposal-bound operation timeout. Null timeout inherits PostgreSQL `statement_timeout`; a finite AI value only narrows it. Apply is transactional, uses no generated `CASCADE`, rechecks stale state under the namespace lock, and reconciles uncertain commits from durable XID evidence without replay.

Preservation analysis is conservative and scoped to affected tables and actual dependencies. Physical table reorder is blocked unless touched-table inventory proves that ACLs, comments, RLS/policies, rules, publications, security labels, extension ownership, tablespace/access method/options/storage, owned sequences, identity/generated columns, statistics, indexes, constraints, triggers, partition relationships, unknown dependencies, owner, persistence, replica identity, and dependent views are neutral. Materialized-view recreation has its own supported preservation manifest. There is not yet one shared reconstruction manifest, and Schemii does not claim to reconstruct unsupported state; it blocks the preview instead.

## View Editing And Migration

Definition drafts remain browser-local and are not inserted into `schema.views` before preview. Duplicate regenerates the statement with the new identity. Commit means preview then apply; neither the Console nor `/plans/{id}/apply` accepts a view-mutation plan.

### Preview API

```text
POST /api/postgres/profiles/{profileId}/views/preview
```

The JSON object must contain exactly:

```json
{
  "schemaId": "schema_one",
  "expectedSchemaRevision": 7,
  "layoutToken": "64-lowercase-hex-characters",
  "database": "bookstore",
  "namespace": "bookstore",
  "relation": "order_summary",
  "expectation": {"kind": "view", "fingerprint": "64-lowercase-hex-characters"},
  "desired": {"kind": "view", "definition": "CREATE VIEW bookstore.order_summary AS SELECT 1"},
  "allowDestructive": false
}
```

For creation, `expectation` is exactly `{"absent":true}`. For replacement or deletion, it is exactly `kind` plus `fingerprint`. `operation` is `upsert` or `delete`. Upsert requires `desired` with exactly `kind` plus one non-empty, single SQL statement whose `CREATE [OR REPLACE] VIEW` or `CREATE MATERIALIZED VIEW` kind and namespace/name identity match the request. Delete omits `desired`. Unknown fields are rejected.

Before planning, the schema store checks the exact schema ID, revision, complete layout token, saved PostgreSQL target, and stable saved-view identity. Creation requires no matching saved item; replacement and deletion require exactly one matching item of the expected kind. Preview then opens a repeatable-read read-only transaction, verifies the database and live expectation, and rechecks the stored profile fingerprint after inspection. Plans are versioned, opaque, process-local, profile-bound, atomically claimed during apply, and expire after 15 minutes by default. The public plan includes the operation, reviewed steps, warnings, and whether it is destructive, but not its private saved-schema binding, profile fingerprint, or preservation manifest.

Implemented operations:

- absent ordinary view: normalized to `CREATE VIEW`;
- existing ordinary view: normalized to `CREATE OR REPLACE VIEW`;
- absent materialized view: `CREATE MATERIALIZED VIEW`;
- existing materialized view: reviewed transactional recreation preserving supported owner, ACL, relation/column comments, indexes/comments, reloptions, tablespace, access method, and populated/unpopulated intent;
- ordinary/materialized kind conversion in either direction: rejected with `view_kind_conversion_unsupported`;
- existing ordinary or materialized view deletion: reviewed non-`CASCADE` drop with exact identity/fingerprint revalidation.

PostgreSQL validates ordinary replacement output-column compatibility during apply. Preview returns a warning rather than claiming to pre-detect every output removal, rename, reorder, or type change. Generated view mutation SQL never adds `CASCADE`.

### Apply API

```text
POST /api/postgres/profiles/{profileId}/view-plans/{planId}/apply
```

The request body is exactly `{"reviewDigest":"<sha256>","confirmDestructive":false}`. Before opening PostgreSQL, the server atomically creates the plan's sole durable execution, persists the confirmed digest and destructive choice, and revalidates the schema revision/layout/target binding. It rejects expired/wrong-profile plans, changed profiles, digest mismatch, and missing destructive confirmation.

Apply uses one transaction and the saved PostgreSQL role. Schemii caps its namespace-lock wait at 5 seconds only when the current `lock_timeout` is zero or looser; it retains any stricter nonzero PostgreSQL setting. Statement duration inherits the selected role/database/session `statement_timeout`. Apply also takes a transaction-scoped advisory lock keyed to the namespace. Existing ordinary and materialized views use `SELECT * FROM qualified_view LIMIT 0` before catalog reinspection; this is non-mutating, takes access-share locks on the view and referenced relations, and blocks conflicting target DDL. While those locks are held, Schemii rechecks the semantic fingerprint, supported metadata fingerprint, and direct dependents, then persists the verified target identity and transaction ID before the first mutation. Recreation executes under the original owner and restores reviewed metadata before commit. Stored rows are discarded and repopulated when the original materialized view was populated; unpopulated views remain unpopulated. User triggers, extra rules, security labels, invalid indexes, non-permanent storage, non-owner grant histories, unavailable/truncated lineage, and any direct dependent block recreation. Delete verifies absence after the reviewed non-`CASCADE` drop. Any proven pre-commit failure rolls back all steps and returns `relation_changed`, the relevant conflict, or `apply_failed`. Lost commit acknowledgement and interrupted `applying` records become reconcile-only and can only use the persisted XID. A committed XID without a persisted intended result remains uncertain with manual recovery required.

### Post-Commit Saved-Schema Sync

After commit, the server appends a deterministic saved item for expected-absent creation, updates the exact stable saved item for replacement, or removes that exact semantic item for deletion. It preserves unrelated views and schema content and the complete layout byte-for-byte as parsed. Deletion intentionally retains any layout object formerly associated with the removed semantic item because layout is user-owned data. Sync increments the schema revision and returns the unchanged layout token.

PostgreSQL execution and saved-schema synchronization have separate durable states. A post-commit identity/revision conflict records sync state `conflict`; a write failure records `failed`. These statuses do not change the already committed execution's `succeeded` state. The UI says PostgreSQL committed, reloads the active schema/catalog, and never retries the plan automatically.

### Materialized Refresh

No materialized-view refresh endpoint or UI control is implemented. `canRefresh` and `concurrentRefreshEligible` are inspection metadata only; they do not authorize or initiate refresh.

## Views Layout Persistence

Keep one Schemii schema record, revision, and layout token. Do not create a second store or independent revision that could commit semantic and visual state out of order.

The implemented storage serializer normalizes saved layouts to version 2 while preserving existing extension fields:

```json
{
  "version": 2,
  "layers": {
    "tables": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}},
    "views": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}}
  }
}
```

Version-1 `tables` and `view` fields migrate into `layers.tables`. Existing version-2 `layers.views`, including object records, viewport, and extension fields, is preserved by browser table-layout serialization. The live Views workspace reads and writes only its own viewport and explicitly dragged object positions through the existing layout-token queue. Deterministic fallback positions and selection changes are browser presentation state and are never saved.

All stored layout is user-owned. The schema `revision`, protocol-2 layout token, `schema_conflict`, and `layout_conflict` cover the complete version-2 layout. Either conflict requires refresh; no stale tab may overwrite either layer. View mutation preview/apply requires the complete current token, and post-commit synchronization does not regenerate, normalize, or otherwise change layout.

`schema_layout_token()` hashes the complete layout. `is_wholesale_layout_change()` checks established objects and viewports in both `layers.tables` and `layers.views`; tests cover table-only, view-only, combined, and viewport replacement attempts. Existing version-1 records remain readable, and the next browser save writes version 2 without changing the parsed table layout.

## Schemer Compatibility

- Shared catalog additions are backward-compatible and do not rewrite saved Schemer source fingerprints or column snapshots.
- The legacy read-only `/sql` route remains available with Schemer's current database, profile-fingerprint, dashboard-revision, role, and result-limit policy.
- Breaking view changes alter the existing relation fingerprint. Schemer then returns `relation_changed` and requires explicit source reselection.
- Schemer retains `dashboardId`, expected revision, widget projection, temporal manifest, and non-privileged-role guards.
- Schemer intentionally mounts the write-capable human Console under Schemer's independent durable write intent and PostgreSQL role permissions. Schemii human intent and AI write capability cannot broaden it.
- Schemer's dashboard analytics and AI query paths retain their separate read-only, revision-bound policies; human Console write intent does not broaden those paths.

## Tutorial v4 Coverage

The Mercury Books seed's v4 reconciliation adds four ordinary views (`book_catalog`, `order_summary`, `low_stock_books`, and `customer_order_totals`) and one materialized view (`monthly_sales`) with a qualifying unique `sales_month` index. It verifies the nine base tables, compares live definitions with temporary canonical definitions, creates missing reserved objects, and does not use `CREATE OR REPLACE`, refresh, or drop in the v4 reconciliation block. Recognized v4 objects with modified definitions are preserved; index restoration is skipped when modified `monthly_sales` is not compatible.

For an exact legacy-v3 upgrade, canonical `order_summary` with its legacy reserved comment is adopted and relabeled for v4. A legacy-v3 `order_summary` carrying that old reserved comment but a modified definition instead triggers the reserved-object collision error. This is intentional collision safety, not modified-object preservation.

## Acceptance Decisions

- Shared foundations are extended once and scoped through capabilities and policies.
- Console mode explicitly determines managed-read rollback, all-or-nothing managed write, multi-request explicit transaction, or per-statement autocommit semantics.
- Human write mode requires durable application-scoped write intent plus exact settings/target binding; AI write mode requires its own immutable proposal/policy binding. PostgreSQL remains final permission authority.
- Real cancellation uses an execution registry and PostgreSQL cancellation.
- Live catalog fingerprints guard Views inspection, replacement preview, and apply; expected absence guards creation.
- View DDL uses only the dedicated Schemii view preview/apply plan resource.
- Views layout is a separate layer inside the existing versioned Schemii layout and conflict guard.
- Schemer keeps its stronger dashboard and source boundaries.
- Materialized creation/recreation/deletion are implemented. Kind conversion, generated `CASCADE`, and materialized refresh are not implemented.
