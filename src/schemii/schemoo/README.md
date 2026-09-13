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

The copy icon beside a model in the model picker duplicates its saved definition
(including aliases, filters, and calculations), layout, working preview, and saved
previews atomically. Save pending edits first. Copies have independent model and
preview records, reuse the same owned connection, and contain no chats, credentials,
or query results. Existing model limits apply; stale source revisions require a
refresh instead of silently copying an unexpected version.

`catalog.py` resolves owner-authorized live sources. `models.py` defines bounded
contracts, `store.py` owns memory/PostgreSQL adapters and connection dependencies,
and `service.py` composes saved rules with Explore requests before compilation.
The application composes Schemoo's migration package into the existing metadata
migration history. Connection deletion/retargeting respects model dependencies.

API routes under `/api/v1/schemoo`:

- `GET /catalog?connection_id=...&namespace=...`: refresh schema and seed layout.
- `GET/POST /models`, `GET/PUT/DELETE /models/{id}`: owner-scoped model lifecycle.
- `POST /models/{id}/duplicate`: independent saved copy with a new name and checks
  against the source definition, layout, and Explore revisions.
- `PATCH /models/{id}`: atomic, revision-checked node/edge/scope upserts and removals
  by stable ID, plus name/root and field exposure changes. Omitted records and the
  server-owned source baseline stay unchanged; bindings are validated before save.
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

- Required scopes force their paths into every query, even when their fields
  are not selected. Conditional scopes activate only for participating sources,
  including intermediate paths and report-filter dependencies.
- Matching rows is independent: require matching returned records or prefilter
  sources while keeping unmatched parents. Existing required scopes require
  matches and existing conditional scopes keep unmatched parents by default.
- Report filters either restrict returned rows or test for matching/nonmatching
  related records using EXISTS/NOT EXISTS. Conditions in one existence group
  must hold on the same related record; separate groups are independent.

Select columns on the canvas, choose aggregates in Explore, inspect generated
SQL, and run a preview. Unneeded paths are omitted; no path guessing is used.

The catalog and query plan are server-derived from the live warehouse. The client
cannot replace saved model rules on an execution request or submit raw SQL.
The grid, viewport, DOM/HTTP utilities, icons and selectors live under `common`.
Rows are never put in browser storage or the metadata database.

Saved definitions include a bounded, schema-only `sourceContract` containing
column shapes, primary keys, and foreign-key signatures. It contains no source
values or query rows. Refreshing incorporates new tables and columns into the
author draft and creates new foreign-key paths disabled. Removed references stay
repairable. Primary-key, foreign-key, type, and nullability changes are labeled
for explicit review; accepting a changed foreign key disables its affected model
connections before adopting the new signature.

This is an interaction prototype, not a published semantic engine:

- Table sources and existing, single-column, same-schema foreign keys only.
- Aliases reuse physical sources; they do not create warehouse objects. No
  recursive hierarchy, composite keys, custom joins, latest-revision selection,
  model publishing. Organization tree tests use the
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

## Model assistant

The chat toolbar button opens the model assistant. It uses the same sidecar,
provider accounts, credential expiry, and model discovery as Schemii; there is no
second AI process or copy of provider credentials. Save author changes before
asking the assistant to edit: its tools operate on server-saved models. An open
unsaved draft is preserved if an assistant action changes the saved model.

`ai_tools.py` is the product adapter. Its typed `schemoo_actions` batch tool covers
all model/catalog routes, layout and Explore updates, validation, compilation,
preview execution, parameter/domain browsing, and common query status, paging,
cancellation and release. It dispatches through existing API handlers, retaining
ownership checks, optimistic revisions and query limits. Model filters, aliases,
calculated sources and exposed fields use the existing model definition contract;
there is no separate AI representation or unrestricted SQL tool.

Each action independently supports **Disabled**, **Ask**, or **Automatic**.
Inspection/planning is automatic by default; mutations, query execution and row
access require approval. A batch needs one review, not one dialog per action.
Actions run sequentially, not as an atomic transaction across endpoints; a failure
stops subsequent actions. Changing permissions invalidates pending approval and
stops the current assistant turn. Both products use the common turn-scoped query
cancellation boundary to interrupt active PostgreSQL reads, result fetches and
SQL writes. Only a connection actively used by that owner/chat/turn is signalled;
cancelled snapshots are released and require an explicit rerun. Later statements
and undispatched commits are fenced. An already-committed change cannot be undone,
and a non-query action already dispatched may finish, but later actions and
disclosure of its results are stopped. PostgreSQL permissions
remain authoritative regardless of the assistant setting.

Schemoo's `conversations.py` owns its model-specific tool continuation, approvals,
streaming, cancellation and bounded transient context. `conversation_store.py`
owns its owner/product-scoped metadata persistence and retention. The provider
runtime, credentials, model discovery and tool-schema normalization remain in
`common/ai`; product-specific lifecycle code is not a second provider runtime.
New messages and approvals check current runtime/model availability before work
is admitted; an unavailable provider leaves a pending review untouched. The UI uses the shared
model-assistant component, controls/icons and Schemii's extracted safe Markdown
renderer. `/api/v1/schemoo/ai` exposes settings and chat lifecycle routes; account
setup continues through the common AI provider routes.

Chat retention, message/activity counts, document/context size, concurrency and
transient memory use the existing `[ai]` configuration. Schemoo applies
`maximum_chats_per_workspace` as a stricter total chat cap per owner across models,
so deleting models cannot leave an unbounded chat collection. Maintenance prunes
expired chats. Restarted in-flight turns are marked interrupted, never replayed.
Configured limit rejections in HTTP requests and background tool loops use the
common privacy-safe metadata limit journal, including sidecar capacity limits.
Entries contain limit names, numeric budgets and error codes, not prompts, SQL,
rows or provider diagnostics; the journal's existing retention/count caps apply.
Only messages, pending action definitions and small replayable receipts are
durable. Query rows, native tool transcripts and answers derived from rows remain
bounded memory only. After eviction, the conversation shows an explicit notice;
the assistant must rerun reads and warn that data may have changed. No result rows
are written to the metadata database.

Both products use `common/ai/context.py` to compact completed native tool groups
into bounded factual receipts when the working context fills up. Original user
requests, current authority, and pending tool approvals are not summarized. Old
model copies, arguments, provider signatures, and row samples are discarded;
receipts retain action outcomes and reference IDs, explicitly forbid repeating
completed mutations, and require reinspection of omitted evidence. Compaction is
in-memory only. The tool loop reserves one final response with tools disabled so
reaching the step budget still produces an explanation of completed and remaining
work. This ordinary workflow does not require a separate agent process.

Focused checks: `tests/test_schemoo_ai_tools.py`,
`tests/test_schemoo_ai_conversations.py`, `tests/test_schemoo_ai_routes.py`, and
`tests/e2e/schemoo-ai.spec.js`. Route coverage is tested against the actual router;
adding a Schemoo route requires extending the action adapter or documenting its
exclusion.

## Saved previews

The Preview inspector can save named query setups and switch between them and a
working preview. Records contain only `ExploreState`: starting object, ordered
outputs and measures, parameter selections, report filters, and preview limit.
They never contain result rows or copies of the catalog/model. Saving a preview
does not change model, layout, or working-explore revisions.

Owner-scoped CRUD lives at `/api/v1/schemoo/models/{model_id}/previews` (GET/POST)
and `.../{preview_id}` (PUT/DELETE). Updates and deletes require the preview's
own revision; trimmed, case-insensitive names are unique within a model. The
combined serialized preview records for a model share the existing
`maximum_model_document_bytes` ceiling. Deleting a model cascades to its previews.
The model picker and header use the same confirmation dialog and guarded deletion.
AI actions mirror these routes with independent permissions and compact receipts.

When opened, saved queries reconcile through the same source-catalog path as the
working model. Removed or no-longer-exposed outputs are pruned without adding new
columns or changing output order. Invalid predicates and starting objects remain
visible for repair, rather than silently broadening a query. The saved record is
updated only when explicitly saved. Switching away from edited named previews
asks before discarding query edits and preserves model/layout changes.

## Model filter navigation

Model rule authoring lives in the **Filters** inspector, opened by the shared
funnel toolbar icon or Filters tab. **Model** owns starting-table/relationship
configuration; **Preview** owns parameter values and query-specific filters.
Table and summary inspectors link to potentially applicable rules (including
required model-wide rules) and can start a filter with their source preselected.
Selecting a rule highlights its bound model occurrences without moving the
viewport. The toolbar warning dot reflects structural authoring issues, not a
missing preview parameter value. Server validation remains authoritative.

## Calculated sources

Select a canvas table and choose **Add calculated source**. These are virtual
objects stored in the model definition, not physical tables or copied results.
The dialog prefills the selected table as the model connection. Fields have stable internal IDs and
author-facing labels; exposure and preview selections remain independent.

- **Row calculations** combine two numeric source columns using add, subtract,
  multiply or divide. They compile inline into SELECT, without another join.
  Division returns NULL for a zero divisor; nullable inputs retain SQL semantics.
- **Grouped summaries** separate the records being summarized (`source`), the
  columns defining each group (`groupBy`), and the report connection
  (`connection.target`, with explicit source/target column mappings). For example,
  group certification records by `personnel_id` and connect to Personnel's `id`.
  The target mapping must identify a complete primary/nonnullable unique key.
  Source-only metrics need no inner joins. Needed many-to-one lookups can precede
  grouping; a multiplying lookup is rejected. Missing groups yield zero counts
  and NULL for other metrics. Existing definitions without `connection` retain
  their original owner-rooted semantics when reopened or edited in place.
  List, count, distinct count, sum, average, minimum and maximum are supported.
  Lists have deterministic value ordering and optional distinct values.
- Unused derived sources and unused summary outputs are omitted from SQL.
  Required and conditional model filters apply inside summaries. Independent
  contributing branches or incompatible grains are rejected instead of silently
  multiplying values. Use a separate summary for each independent branch.
- Each output can have its own AND conditions, including null checks, fixed
  domain values, IN/NOT IN and dynamic Today (UTC) date comparisons. Aggregates
  compile them as `FILTER (WHERE ...)`; row arithmetic uses `CASE WHEN` and
  returns NULL for a nonmatch. Conditions do not restrict sibling outputs or
  remove connected report rows. A non-expired certification count can use
  `expiration_date >= Today` with explicit NULL acceptance. Add
  `effective_date <= Today` for currently valid certifications. Today is resolved
  once by the server per compilation, not persisted as a frozen date. Output
  conditions reference the grouping source or sources on that output's existing path;
  they cannot silently add a multiplying join. Use model filters for parameters
  that should affect all fields, rather than per-field fixed conditions.
- Nested calculations and filters/domain lookups on calculated fields remain
  unsupported. Ordinary COUNT, SUM and AVG measures, including reaggregation of
  calculated fields, are rejected when a participating join can multiply their
  source records. Use separate grouped summaries at each measure's grain.
  Many-to-one lookups and reverse foreign keys proven unique preserve grain;
  MIN, MAX and COUNT DISTINCT are safe with repeated input values.

Create a separate demonstration against an existing `organization.public`
connection with `python dev/schemoo/derived-demo.py CONNECTION_ID`. It leaves
warehouse tables and existing models untouched. Its numeric calculation is an
illustrative minimum-pay × level expression, not a defined business metric.

### Independent filter activation and row matching

A scope's `kind` controls activation: `required` always includes its source paths;
`conditional` applies only when its bound sources participate. The separate
`rowBehavior` setting chooses `keep_unmatched` (prefilter sources before LEFT
JOIN, retaining unmatched parents) or `require_matching` (restrict returned rows
and exclude unmatched parents). A filter on the starting source always removes
nonmatching starting rows. Required scopes that keep unmatched parents include
the filtered source even if its fields are unselected, so their joins can change
result grain. The compiler applies the same aggregate safeguards to these joins.

Existing models with absent/null `rowBehavior` retain their prior behavior:
required scopes require matches; conditional scopes keep unmatched parents.
The filter editor exposes both choices and preserves row matching when activation
is changed. Saved models and AI model edits use the same contract.

Model-defined equality relationships use `kind: logical`, `source` and `target`
node IDs, and `sourceColumn` / `targetColumn`. They have no `relationshipId` and
never create database constraints. The API, editor and assistant persist these
through the existing model write operations and permissions. Columns are checked
against the live catalog, including on execution and source refresh. Cardinality
is authored documentation; aggregate safety relies on catalog uniqueness.

For closure hierarchies, a direct fact organization → hierarchy child connection
allows a required ancestor filter to compile as EXISTS without an intermediary
dimension join. Filter-only paths preserve fact grain. Selecting hierarchy fields
can return multiple ancestor rows; unsafe SUM/COUNT/AVG joins are rejected. Keep
needed dimension date predicates and separate role aliases when changing paths.

Date-range columns support the filter comparison **Contains date**
(`range_contains_date`). Bind a Date parameter with default `today` to generate
`active_range @> DATE 'YYYY-MM-DD'` using the date resolved on each run. Fixed
dates are also supported. The server validates the `daterange` source and date
value. Existing scope activation, unmatched-row behavior, and AI model-edit
permissions apply. Adding a range filter does not create columns or indexes.
