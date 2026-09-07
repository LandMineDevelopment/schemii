# Schemoo semantic models

Open `/schemoo` on the unified HTTPS application. Choose an existing personal
PostgreSQL connection and schema, then create or open a saved model. Multiple
models may reference the same source without copying credentials or depending on
a Schemii design workspace. `/schemoo?model=<id>` opens one owner-private model.

The server persists current definitions, independent canvas layout, and Explore
inputs in `schemoo.models` in the metadata database. Each has its own optimistic
revision. Saves reject stale revisions rather than overwrite another tab. There
is no retained model-version history or result-row storage. Browser prototype
drafts can be explicitly imported; import does not delete the old browser copy.

`catalog.py` resolves owner-authorized live sources. `models.py` defines bounded
contracts, `store.py` owns memory/PostgreSQL adapters and connection dependencies,
and `service.py` composes saved rules with Explore requests before compilation.
The application composes Schemoo's migration package into the existing metadata
migration history. Connection deletion/retargeting respects model dependencies.

API routes under `/api/v1/schemoo`:

- `GET /catalog?connection_id=...&namespace=...`: refresh schema and seed layout.
- `GET/POST /models`, `GET/PUT/DELETE /models/{id}`: owner-scoped model lifecycle.
- `PUT /models/{id}/layout` and `/explore`: separately revisioned presentation/input state.
- `POST /models/{id}/validate`: diagnose unsaved author rules without execution.
- `POST /models/{id}/plan`: compile saved rules plus Explore selections.
- `POST /models/{id}/executions`: refresh live schema, revalidate, and reserve a managed read.
- `POST /models/{id}/parameter-values`: resolve saved parameter domain configuration and reserve a managed read.

Execution, cancellation, paging, result release and exports use the common
`/api/v1/common/query-executions/{id}` boundary and the existing Console runtime.
Machine-generated previews do not enter the user's saved SQL history. Rows remain
transient under the existing memory/session limits and receipt expiry policy.

The schema-only editing cache is bounded by `[schemoo] maximum_cached_catalogs`
and `catalog_refresh_seconds`. Preview execution always rechecks the live schema.
`[resources] maximum_models_per_user` bounds saved models; reaching it returns an
actionable error and records a privacy-safe metadata limit event.
`maximum_model_document_bytes` bounds each persisted definition, layout, or Explore
document (1 MiB by default). Model lists return summaries without loading all model
documents into memory.

The initial canvas imports every supported foreign key, including self-links.
Every connection belonging to a cycle is red, and execution is blocked until
enabled connections are cycle-free. Drag headers to arrange objects, click them
to create labeled aliases, and click connections to redirect their endpoints to
occurrences of the same physical table. Disabled connections remain visible.

**Staffing example** replaces only the current unsaved editor draft with an explicit acyclic
example: distinct organization and certification roles, a required organization
tree scope, and a conditional time scope. Under **Explore**, browse for a parent
organization and choose an as-of date, a period, or unrestricted history. As-of
defaults to today's UTC date, resolved once per compilation. Time ranges use
inclusive boundaries and explicitly allow null end dates. This example is not
an automatic cycle-resolution algorithm or a validated production HR model.

The **Model** inspector authors scopes with named OR alternatives containing
AND conditions, typed parameters, and defaults. **Explore** supplies values and
report filters:

- Required scopes force their paths into every query and restrict returned
  details, even when their fields are not selected.
- Conditional scopes apply only to sources participating in a query, including
  intermediate paths and report-filter dependencies. They prefilter those
  sources without eliminating unmatched parents of optional joins.
- Report filters either restrict returned rows or test for matching/nonmatching
  related records using EXISTS/NOT EXISTS. Conditions in one existence group
  must hold on the same related record; separate groups are independent.

Select columns on the canvas, choose aggregates in Explore, inspect generated
SQL, and run a preview. Unneeded paths are omitted; no path guessing is used.

The catalog and query plan are server-derived from the live warehouse. The client
cannot replace saved model rules on an execution request or submit raw SQL.
The grid, viewport, DOM/HTTP utilities, icons and selectors live under `common`.
Rows are never put in browser storage or the metadata database.

This is an interaction prototype, not a published semantic engine:

- Table sources and existing, single-column, same-schema foreign keys only.
- Aliases reuse physical sources; they do not create warehouse objects. No
  recursive hierarchy, composite keys, custom joins, latest-revision selection,
  calculated fields, or model publishing. Organization tree tests use the
  existing hierarchy closure table, not recursive SQL.
- All enabled paths must be acyclic. Disconnected objects may remain unused.
- LEFT JOINs, source filters, EXISTS/NOT EXISTS, basic aggregates and GROUP BY.
- These editable private model rules are modeling behavior, not an access-control
  system. PostgreSQL permissions remain authoritative. Value browsing queries
  the chosen physical source rather than enforcing unpublished draft scopes.
- Fanout is warned about, not automatically repaired. A sum can be multiplied by
  one-to-many joins; do not treat prototype measures as validated business metrics.
- Up to 100 unordered preview rows. No full export or materialization.
- Source changes preserve saved models and surface missing references for repair.
  Incomplete models may be saved but cannot execute invalid queries. Renames are
  not guessed. Unrelated changes warn without unnecessarily blocking valid queries.

Checks: `.venv/bin/python -m pytest -q tests/test_schemoo_prototype.py
tests/test_schemoo_filters.py tests/test_schemoo_routes.py` and
`npx playwright test tests/e2e/schemoo-prototype.spec.js` against a running stack.
Optional live SQL semantics: `SCHEMOO_LIVE_TESTS=1 .venv/bin/python -m pytest -q
tests/test_schemoo_sql_live.py` (synthetic read-only CTE fixtures, no warehouse writes).
Use only `./start.sh` to build/refresh the application.
