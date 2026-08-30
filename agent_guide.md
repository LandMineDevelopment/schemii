# AI Agent Guide

## Scope

This repository contains Schemii, a PostgreSQL schema design and migration application, and Schemer, its dashboarding sibling. Keep shared PostgreSQL profiles, HTTP routes, browser clients, and visual tokens in common modules; do not copy connection implementations between the apps.

For installation, launch modes, Docker networking, persistent-volume safety, and setup verification, follow `docs/AI_AGENT_SETUP.md`.

Production launch modes publish applications only on loopback. A machine-local reverse proxy may be configured externally with exact HTTPS origins supplied through `SCHEMII_PUBLIC_ORIGINS` or `SCHEMER_PUBLIC_ORIGINS`; the repository must not own its identity, hostname, listener, routes, TLS, or access policy. Forwarded Host/Proto trust is request admission from the configured Docker ingress peer, not application user authorization or per-user privacy.

## Sources Of Truth

Use this order when behavior differs:

1. The user's explicit request.
2. The live PostgreSQL catalog for current database state.
3. For Schemii, the saved schema record selected for that exact profile, database, and namespace.
4. For Schemer, the saved dashboard revision and each widget's exact source fingerprint, column snapshot, structured query, and visualization projection.
5. Application code and tests.
6. `README.md` for operating guidance.

Never preview or apply a saved schema against an unverified database or namespace. Never execute or refresh a Schemer widget against an inferred profile, database, namespace, relation, or dashboard revision.

## PostgreSQL Wrapper Philosophy

Schemii and Schemer are PostgreSQL wrappers, not a parallel database authorization system.

- PostgreSQL owns role permissions, SQL semantics, constraints, triggers, defaults, generated values, row-level security, partition routing, types, operators, aggregates, extensions, transaction legality, and database/role/session timeout policy.
- Execute against the exact saved role and let PostgreSQL provide the authoritative permission and execution result. Advisory privilege checks may improve explanations but must not become hard authorization gates.
- The user owns which target is active, which capabilities the AI receives, each capability's approval mode, and optional agent-specific bounds. The AI must identify missing capability precisely and must never expand its own authority.
- The applications own exact-target identity, stale-state and revision guards, destructive intent review, preservation of layouts/dashboards/saved design intent, durable operation identity, uncertain-outcome recovery, transport pagination/chunking, and process stability.
- Do not impose application statement or lock policy by default when PostgreSQL can own it. Connection-establishment, external HTTP, startup, shutdown, and health deadlines remain application lifecycle concerns.
- Product modeling may be narrower than PostgreSQL, but limitations must be named as application limitations and an appropriate raw PostgreSQL surface should remain available. Never describe an app limitation as PostgreSQL denying or not supporting an operation.
- Bound individual responses for process/browser safety, but use pagination, cursors, streaming, or export instead of making otherwise valid PostgreSQL objects or results unreachable.
- Keep no-automatic-replay guarantees for uncertain writes. PostgreSQL permission does not make replay safe.

## Working Method

1. Inspect the relevant source, tests, saved schema or dashboard record, and `git status` before proposing changes.
2. State the smallest correct implementation and identify data, compatibility, locking, permission, and destructive-operation risks.
3. Ask before making a destructive schema or data change when intent is not explicit.
4. Implement the approved scope without modifying unrelated user changes.
5. Add or update focused tests for behavior changes.
6. Update documentation in the same change when setup, configuration, API behavior, migration semantics, or verification changes.
7. Run focused checks, the complete test suite, and `git diff --check` before delivery.

## PostgreSQL Design

- Treat PostgreSQL as authoritative for the live catalog.
- Prefer declarative primary keys, foreign keys, unique constraints, checks, and indexes over application-only validation.
- Quote identifiers and parameterize values. Do not interpolate user values into SQL.
- Preserve exact namespace and object ownership relationships during introspection and migration planning.
- Account for existing rows before type changes, `NOT NULL` additions, table rewrites, or constraint validation.
- Use explicit source and target time zones for timestamp conversions.
- Keep introspection and data-preview operations read-only.
- Require narrowly scoped database roles rather than broad administrative credentials.

## Layout Preservation

Canvas positions, colors, and viewport state are user-owned data. Introspection may update semantic schema content but must not regenerate or normalize established layout.

For any generated schema JSON write or introspection synchronization, load and follow `.opencode/skills/preserve-schemii-layout/SKILL.md`. Resolve the schema directory from `SCHEMII_SCHEMA_DIR`, falling back to `~/.local/share/schemii/schemas`; do not assume schemas live inside the repository.

Treat `layout_conflict` as a hard-refresh requirement. Never bypass the layout token guard or use a stale browser tab to overwrite a current layout.

## Schemer Dashboard Safety

- Dashboard records, widget array order, viewport state, source/query configuration, and unrelated custom widgets are user-owned data. Version-3 cards have no persisted geometry.
- Treat `dashboard_conflict`, `dashboard_changed`, and stale temporal-series errors as refresh requirements. Never bypass dashboard revision guards.
- Never rewrite a stale relation fingerprint or semantic column snapshot automatically. Require explicit source reselection after catalog changes.
- For Schemer dashboard work, send aggregate and detail execution with the exact dashboard ID/revision and complete verified profile, database, namespace, relation kind, fingerprint, source-column, and structured-query request. Draft queries may execute before they are saved. The base aggregate/detail API also accepts requests without dashboard context, so do not describe its revision guard as universal. Temporal execution additionally requires the exact saved widget ID and server-reconstructed line projection; never imply that stronger anti-tamper boundary exists on standard aggregate or detail requests.
- Keep persisted widgets single-relation and caller-SQL-free. Separately confirmed data-mode analytic SQL may join relations but must remain profile/database/namespace/revision-bound and must not mutate widget configuration.
- Mercury restoration may replace reserved default definitions only. Preserve established widget order, viewport, and unrelated widgets.
- Temporal line windows are separate read-only PostgreSQL snapshots. Cache them only within one refresh generation and never claim cross-window point-in-time consistency.

## Safe Migrations

- Identify the exact profile, database, namespace, and saved schema before preview.
- Review generated SQL, warnings, unsupported objects, data movement, locks, and destructive steps.
- Require an explicit destructive preview choice and apply confirmation for destructive plans.
- Re-preview after any design, profile, or live catalog change.
- Keep apply transactional and preserve stale-plan fingerprint checks.
- Verify rollback behavior for failed steps and test risky changes on disposable data first.
- Require zero unexpected steps and warnings when a saved schema is expected to match PostgreSQL.

## Verification

Run at least:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src
node --check src/schemii/web/app.js
node --check src/schemii/schemer_web/app.js
for test_file in tests/test_*.js; do node "$test_file" || exit 1; done
git diff --check
```

For server or API changes, also start one local server, fetch `/`, `/api/session`, and the affected API routes, then stop it. For PostgreSQL changes, verify against a disposable target and confirm no test objects or data remain.

For schema-file synchronization, compare parsed layout snapshots before and after the write and require equality for every pre-existing layout entry.

## Documentation And Commits

- Keep `README.md` aligned with current setup, environment variables, storage, API behavior, and safety guarantees.
- Do not claim planned behavior is implemented.
- Keep commits small and focused. Do not include secrets, local profiles, migration history, caches, virtual environments, or runtime schema data.
- Commit or push only when the user explicitly requests it.
- Before committing, inspect `git status`, the intended diff, recent commit style, and staged content; stage only intended files.
