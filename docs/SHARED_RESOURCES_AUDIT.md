# Shared Resources Audit

Implementation status: completed on 2026-08-10 and reconciled with the final Schemer feature branch on 2026-08-11.

Schemii and Schemer share focused infrastructure modules rather than one common application framework. This keeps profile, transport, persistence, and visual contracts consistent while leaving schema-design and dashboard workflows independently understandable.

## Implemented Boundaries

### Relation Identity

- `relation_source.py` owns the canonical persisted relation identity and semantic column-snapshot contract.
- Dashboard storage, live source verification, query execution, and API boundaries use the same parser and 512-character PostgreSQL type limit.
- Schemer never silently rewrites a stale relation fingerprint or saved column snapshot.

### PostgreSQL Capabilities

- `postgres_http.py` mounts explicit profile, catalog, schema-design, relation-query, and read-SQL capabilities.
- Schemii mounts schema introspection, generic table preview, migration planning/apply, and its read-SQL policy.
- Schemer mounts exact-source catalog inspection, verified relation preview, aggregate/detail/temporal queries, and its stricter separately confirmed analytic-SQL policy.
- Schemer refresh uses exact-database relation listing followed by saved-source verification instead of full namespace introspection.

### Browser Clients And UI

- `shared_web/session-client.js` owns authenticated same-origin transport and one expired-session retry.
- `shared_web/postgres-client.js` restricts browser PostgreSQL requests to local `/api/postgres/...` routes.
- `shared_web/profile-manager.js` owns stable profile/form behavior while each application retains its own dialog layout and post-connect workflow.
- `shared_web/ui-components.js` owns SVG icons, icon buttons, delegated tooltips, truncation detection, status/loading controls, and details-menu behavior.
- `shared_web/theme.css` owns common control, focus, popup, dialog, danger, shadow, radius, and overlay tokens.
- All shared scripts extend the same merge-safe `window.SchemiiShared` namespace, removing script-order replacement hazards.

### Backend Structure

- `PostgresService` remains the public facade.
- `postgres_common.py`, `postgres_connections.py`, and `postgres_catalog.py` own shared validation, connection, and catalog behavior.
- Schemii-specific migration planning remains in the facade because Schemer does not consume it.
- `atomic_json.py` supplies durable same-directory JSON replacement; schema, dashboard, and profile stores retain their own applicable validation, locking, and serialization rules, while schema and dashboard records keep their separate revision semantics.
- `server_runtime.py` supplies strict environment parsing, static-directory validation, HTTP lifecycle, and response-before-thread shutdown behavior.

### Embedded AI

- Schemii and Schemer reuse one private pinned OpenCode service and provider credential store.
- Schemii sessions are restricted to read-only `/workspace`; Schemer sessions are restricted to read-only `/workspace-schemer`.
- The workspaces retain separate instructions, tools, skills, sessions, and action policies.
- Shared browser assistant components own provider setup, model discovery, history, activity streaming, and bounded text rendering.

### Tests

- `tests/http_test_support.py` supplies shared request-handler and service doubles.
- Direct JavaScript contracts cover the session client, profile manager, UI components, tooltips, and application adapters.
- PostgreSQL client behavior is covered through application and HTTP contract tests; there is no standalone `test_postgres_client.js` suite.
- Store, server-runtime, atomic-persistence, relation-source, catalog, and cross-application profile contracts have focused Python coverage.

## Historical Findings And Resolutions

### Priority 0: Correctness And Security

The original audit found duplicated relation-source validation, Schemer refresh through full introspection, and a shared HTTP router that exposed the union of both applications' capabilities. These were resolved by `relation_source.py`, lightweight exact-database verification, and per-application capability sets.

### Priority 1: Frontend Sharing

The original audit found duplicated profile forms, authenticated fetch/error handling, visual tokens, tooltip setup, and unsafe shared-namespace initialization. These were resolved by the shared session/profile clients, UI component registry, semantic theme, delegated tooltip controller, and merge-safe namespace initialization.

Application-specific copy remains intentional when workflows or safety consequences differ. Similar-looking dialogs are not generalized solely for visual similarity.

### Priority 2: Backend Structure

The original audit recommended splitting the PostgreSQL facade, extracting atomic JSON persistence and server lifecycle handling, and sharing test harnesses. Those boundaries now exist without changing the public `PostgresService` API or combining the two servers.

## Intentionally Application-Specific

- Schemii canvas state, layout-token conflict handling, schema persistence, migration planning/apply, SQL editor, and inspector.
- Schemer dashboard composition, fixed responsive tiles, drag-and-drop ordering, chart rendering, temporal window caching, drill-through reports, and dashboard revision semantics.
- Schemii and Schemer route assembly, application state, user workflows, and safety confirmations.
- A universal top bar, universal grid, shared global state container, or single combined server process.

Schemer persists one user-owned widget array order and vertical viewport state. Its uniform responsive version-3 cards have no saved geometry or breakpoint-specific order; those order-only dashboard semantics remain application-specific.

## Maintenance Rules

1. Add common PostgreSQL profile, identity, transport, or visual behavior to the existing shared module rather than copying it between applications.
2. Keep capability mounting least-privileged and test that each application rejects routes it does not own.
3. Preserve application-specific revision and layout guards when reusing lower-level helpers.
4. Add direct shared-module tests when introducing a new reusable contract.
5. Do not convert focused shared modules into a universal application framework.
