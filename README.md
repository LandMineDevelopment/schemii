# Schemii

Schemii is a visual PostgreSQL workspace for programmers who want to understand and change a database without losing sight of either the diagram or the SQL. It combines schema design, live database inspection, data exploration, migration planning, and an optional AI assistant in one local browser application.

## What Makes Schemii Different

- **Design and reality stay separate and explicit.** PostgreSQL remains authoritative for live database state, while saved Schemii designs describe the intended schema.
- **Migrations are reviewed, not guessed.** Schemii compares the selected design with an exact verified database and namespace, shows the generated SQL and warnings, and requires separate confirmation before apply.
- **Your diagram remains yours.** Table positions, colors, and viewport state are saved independently and preserved when live schema semantics are refreshed.
- **Live tools are built into the canvas.** Inspect PostgreSQL objects, preview table rows, and run bounded transactional SQL without leaving the design workspace. The standalone Console is read-only by default; write modes require independent durable application write intent and use only the saved PostgreSQL role's permissions.
- **PostgreSQL features are represented directly.** Work with primary and foreign keys, composite keys, unique and check constraints, indexes, views, functions, triggers, identity columns, generated columns, and more.
- **AI is constrained and reviewable.** The private OpenCode sidecar can explain designs and propose structured actions, but it has no shell or filesystem access and cannot bypass Schemii confirmations or migration safety.
- **It is useful immediately.** The default stack includes a populated relational PostgreSQL tutorial and a separate local-only design with deliberately organized layouts.

Schemii runs locally, binds its UI to host loopback, and stores designs, profiles, database data, and AI state in installation-specific Docker volumes. It has no account requirement, CDN assets, or telemetry built into the application.

## Install Docker

Docker is the only software required to run Schemii. Python, Node.js, PostgreSQL tools, and Git are not required.

- **Windows:** Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Use the WSL 2 backend and Linux containers, then start Docker Desktop.
- **macOS:** Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) for Apple silicon or Intel, then start Docker Desktop.
- **Linux:** Install [Docker Engine](https://docs.docker.com/engine/install/) and the [Docker Compose plugin](https://docs.docker.com/compose/install/linux/). Start the Docker service. If Docker reports a socket permission error, follow Docker's [Linux post-install steps](https://docs.docker.com/engine/install/linux-postinstall/) or use rootless Docker. Docker access is effectively administrator-level access.

Confirm both commands work in a new terminal:

```bash
docker version
docker compose version
```

If either command fails, finish the matching Docker installation above before starting Schemii.

## Download Schemii

### Without Git

1. Open the [Schemii releases page](https://github.com/LandMineDevelopment/schemii/releases) and choose one immutable `vX.Y.Z` release.
2. Record the full 40-character commit SHA embedded in every release filename.
3. Download `schemii-X.Y.Z-<full-sha>-source.tar.gz`, the three matching `linux-amd64` image archives, `release-manifest.json`, and `SHA256SUMS` from that exact release.
4. Verify every checksum and attestation, then load the three images as described under [Release Integrity](#release-integrity).
5. Extract the generated source archive and open a terminal in its version-and-SHA-named directory.

Keep the extracted directory in the same location. Its path identifies this Schemii installation and its Docker volumes.

### With Git

The default branch is for development. For an immutable installation, fetch and check out the exact protected release tag and verify that it peels to the intended full commit SHA:

```bash
git clone https://github.com/LandMineDevelopment/schemii.git
cd schemii
git checkout --detach vX.Y.Z
test "$(git rev-parse 'vX.Y.Z^{commit}')" = "<full-sha>"
```

Git supplies the reviewed orchestration source, not the three application images. Download, verify, and load the matching release image archives before continuing.

## Start Schemii

Linux or macOS:

```bash
export SCHEMII_IMAGE="schemii:X.Y.Z-<full-sha>"
export SCHEMII_METADATA_IMAGE="schemii-metadata-postgres:X.Y.Z-<full-sha>"
export SCHEMII_OPENCODE_IMAGE="schemii-opencode:X.Y.Z-<full-sha>"
bash ./start.sh
```

Windows PowerShell:

```powershell
$env:SCHEMII_IMAGE = "schemii:X.Y.Z-<full-sha>"
$env:SCHEMII_METADATA_IMAGE = "schemii-metadata-postgres:X.Y.Z-<full-sha>"
$env:SCHEMII_OPENCODE_IMAGE = "schemii-opencode:X.Y.Z-<full-sha>"
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The launcher checks Docker and the three selected immutable application images, starts the complete private Compose stack with `--no-build`, waits for health checks, and prints the local URL. It opens the browser automatically unless opening is disabled.

The first start downloads pinned dependency images that are not included in the release set. It requires internet access and may take several minutes. The selected application, metadata, and OpenCode images are never rebuilt by the launcher.

No account is required to start Schemii. Anonymous AI models may be available, but AI use can require provider authentication when anonymous models are unavailable. No model request is made until the user sends a chat message.

### Preview tutorials without deployment

Source checkouts with Node.js can preview both tutorials without Docker or a database:

```bash
node scripts/tutorial-preview.js
```

Open Schemer at `http://127.0.0.1:18080/` and Schemii at `http://127.0.0.1:18080/schemii/`. Set `SCHEMII_TUTORIAL_PREVIEW_PORT` to use another loopback port. This development preview serves current source assets with synthetic in-memory records; it never contacts PostgreSQL, OpenCode, Docker, deployed services, or saved application data, and all preview edits reset when the process stops. Use the normal launcher for backend, persistence, database, AI, or ingress verification.

Schemii and Schemer require the dedicated metadata PostgreSQL schema to be current before either serves requests. Each app's `/api/readiness` reports its configured HTTP access boundary, metadata, optional OpenCode, observed target-database degradation, and PostgreSQL execution admission counters separately. Metadata is required in both products, and Schemer additionally requires its dashboard/AI-receipt store to be readable and internally valid. A `503` readiness response preserves the complete structured component report instead of collapsing it to a generic API error. Optional target and OpenCode degradation remains visible without taking static UI or saved-resource access out of service. Compose health checks use this endpoint. AI chat UUIDs, policies, grants, proposals, operation attempts, and bounded one-use query-result references are stored in metadata PostgreSQL; OpenCode session IDs are opaque server-only external references and titles are display-only. Legacy Schemii JSON chat authority and Schemer JSON/title authority are moved or marked idempotently under `retired-json-authority` and are never imported as executable records.

PostgreSQL owns statement duration by default: Schemii and Schemer do not install an application `statement_timeout` for Console, catalog, preview, analytics, migration, or seed work. Connection establishment and external HTTP/provider calls retain lifecycle deadlines. An explicitly configured AI `operationTimeoutMs` may only narrow the active PostgreSQL timeout, and namespace mutation lock waits are narrowed to five seconds only when PostgreSQL's current `lock_timeout` is zero or looser. PostgreSQL remains authoritative for permissions and execution; privilege calculations are advisory, and failures preserve bounded SQLSTATE, primary message, detail, hint, phase, rollback, and safe retry/reconciliation diagnostics.

All PostgreSQL result paths enforce shared 64 KiB cell, 256 KiB row, 1 MiB response, eight-level nesting, and 1000-item collection transport limits, with narrower route limits where documented. Structured `limitEvents` identify safe truncation; unsafe cell, row, nesting, definition, and catalog overflows return structured errors. Process-wide admission control separates short catalog, bounded read, Console, and migration/write work, applies global, class, and exact-target capacities, returns retryable `429` under backpressure and `503` while stopping, and releases capacity after errors and cancellation. These process ceilings are not PostgreSQL policy or user settings. Schemii, Schemer, and metadata connections use distinct PostgreSQL `application_name` values.

Connection pooling is intentionally not enabled. A 2026-08-13 measurement inside the default Compose network ran 50 connect-plus-`SELECT 1` operations in 343.7 ms (6.44 ms median, 9.69 ms p95), versus 2.1 ms for 50 `SELECT 1` operations on one connection. The measured setup cost is small relative to interactive bounded queries and does not yet justify pool lifecycle, credential/profile invalidation, transaction-state reset, and shutdown complexity. Reconsider pooling if production telemetry shows connection setup is a material share of request latency or PostgreSQL connection churn becomes an operational constraint.

### Default Stack

The no-argument launcher starts:

- Schemii UI and local API
- A dedicated private PostgreSQL 17 metadata database and one-shot schema migrator
- A private PostgreSQL 17 tutorial database
- A private OpenCode agent sidecar
- A one-shot tutorial seed service

Only the hardened `schemii-ingress` reverse proxy publishes the Schemii loopback port. The application backend has no host port and shares a dedicated internal HTTP network only with that ingress. The ingress alone also joins a singleton bridge required for Docker loopback publication; no dependency container joins it. PostgreSQL, metadata PostgreSQL, OpenCode, and the application backend remain private in every mode. An externally managed machine-local reverse proxy may forward an HTTPS origin to the loopback ingress, but the repository does not configure that proxy, its TLS identity, hostname, listener, routes, or access policy. Linux local-database modes keep the application on its normal private networks and carry PostgreSQL traffic to host `127.0.0.1:5432` through a private Unix-socket bridge; they do not publish metadata PostgreSQL or the application backend.

### Other Modes

| Goal | Linux or macOS | Windows PowerShell |
| --- | --- | --- |
| Complete default stack | `bash ./start.sh` | `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| Local design only | `bash ./start.sh ui` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ui` |
| Tutorial PostgreSQL without AI | `bash ./start.sh docker-db` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode docker-db` |
| AI without included PostgreSQL | `bash ./start.sh ai` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ai` |
| Linux host PostgreSQL without AI | `bash ./start.sh local-db` | Not supported; use `ui` and `host.docker.internal` |
| Linux host PostgreSQL with AI | `bash ./start.sh ai-local-db` | Not supported; use `ai` and `host.docker.internal` |
| Schemer and tutorial PostgreSQL, explicitly without AI | `bash ./start.sh schemer` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode schemer` |
| Schemer and tutorial PostgreSQL with shared AI | `bash ./start.sh schemer-ai` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode schemer-ai` |

Set `SCHEMII_NO_OPEN=1` on Linux/macOS or use `-NoOpen` on PowerShell to suppress browser opening.

## First Steps

The first default start creates two saved examples:

- **Mercury Books: PostgreSQL tutorial** is linked to the live `bookstore` namespace. Its nine tables include 80 books, 150 customers, 500 orders, and more than 1,200 linked order items for realistic exploration, alongside generated and identity columns, composite keys, checks, JSONB, B-tree and GIN indexes, functions, and a trigger. Tutorial v4 adds the canonical `book_catalog`, `order_summary`, `low_stock_books`, and `customer_order_totals` views plus the `monthly_sales` materialized view and its qualifying unique `sales_month` index. Reconciliation creates missing v4 objects and preserves recognized reserved objects whose definitions were modified; it skips dependent index restoration when a modified `monthly_sales` definition is incompatible. Upgrade caveat: a legacy-v3 `order_summary` with the old reserved comment but a modified definition is treated as a reserved-object collision and stops reconciliation rather than being overwritten.
- **Event Studio: Local design example** is a seven-table design that demonstrates local modeling, relationships, checks, indexes, composite keys, and SQL/JSON export without a database connection.

Use the folder button to switch designs, the disk button to save, and the PostgreSQL tool to inspect data or preview migration SQL. Click the Tables canvas and use the arrow keys to pan by one grid cell; hold Shift with an arrow to move four cells at a time. Schemii's seven-page workflow introduction covers modeling, canvas layout, inspection, Views and SQL workspaces, PostgreSQL synchronization, AI review, and saved-design recovery; reopen it from the **?** menu.

### Schemii Views

The shared Tables / Views / SQL Console selector remains available across those workspaces. The left tool rail keeps Undo, Redo, fit/zoom, PostgreSQL sync, functions, and the AI assistant available while replacing table-only tools with Views or SQL actions. Views provides Browse, Create, Refresh, and permission-gated Delete controls. The shared Console in both applications provides managed read, managed all-or-nothing write transaction, explicit multi-request transaction, and autocommit/maintenance modes. Schemer's write-capable human Console is intentional and uses its own durable application-scoped write intent; it does not inherit Schemii intent or AI authority, and the selected PostgreSQL role remains authoritative for permissions. Human write intent, default mode, statement limit, and row page size are durable application-scoped settings with optimistic revisions, not expiring pseudo-permissions and not AI authority. Views opens a live view browser for the PostgreSQL target saved in the active schema record and is available only when that record has an exact `sourceProfileId`, `database`, and `namespace`, a current schema revision, and a current layout token. It loads ordinary and materialized views with ordered columns, bounded definitions, owner and advisory privileges, stable fingerprints, pageable direct lineage across namespaces, and materialized population/concurrent-refresh eligibility. One focusable graphical canvas renders verified physical sources, query-local CTE and derived-table stages where applicable, exactly one real outer-SELECT `query_block`, the final view, and verified consumers. The root query-block card owns that SELECT's verified inputs and aliases, complete join conditions and partial reasons, filters, and selected projection summary. A physical source never bypasses an available root query block, CTEs and derived tables are not temporary tables, and syntactic dependency order is not PostgreSQL runtime execution order. High-contrast destination arrowheads and segment-local labels make direction explicit. Every source, stage, query block, final view, and consumer has a titled drag surface. Deterministic fallback columns and balanced lanes are never saved automatically; persisted Views positions and viewport remain authoritative and isolated from Tables layout. Source, verified join, query-block, and output focus modes emphasize only endpoint-verified paths while retaining unrelated context, and Clear focus or an empty-canvas click removes emphasis without moving or saving the canvas. Foreign tables are first-class read/catalog sources and may appear in lineage, although the dedicated Views mutation lifecycle remains limited to ordinary and materialized views.

An imported table's inspector also provides a table-prefilled, permanently read-only Console. Its single Run action executes highlighted SQL or, when there is no selection, the statement under the cursor. It uses the same revision-bound exact-target managed-read execution, cancellation, retained-result paging, export, and persistent PostgreSQL diagnostics as the standalone SQL workspace. Results land in the existing Data view, which temporarily becomes the Console results drawer; Clear releases those results and restores the live table preview. Its gold accent indicates that it remains scoped to the Tables workspace, and it cannot enable writes.

Creating, editing, or deleting a view always uses Schemii's dedicated preview and opaque-plan apply APIs; it is not sent through the Console or the full-schema migration route. Ordinary views support `CREATE VIEW`, PostgreSQL-validated `CREATE OR REPLACE VIEW`, and reviewed deletion. Materialized views support creation, reviewed transactional recreation, and deletion. Recreation warns that stored rows are discarded and repopulated when the view was previously populated; deletion warns that the relation and all rows stored in it are permanently removed. Source-table rows are never deleted by these operations. Kind conversion remains unsupported, no generated step uses `CASCADE`, and direct dependents block destructive operations. There is no materialized-view refresh endpoint or refresh control yet. Apply runs in one transaction with the saved role, namespace advisory locking, narrowed lock waiting, inherited PostgreSQL statement policy, profile/relation stale checks, and target locking before authoritative reinspection. PostgreSQL validates ordinary replacement output compatibility during apply; Schemii does not claim to reconstruct or pre-detect every unsupported object or output rename, reorder, removal, or type change.

After PostgreSQL commits, Schemii narrowly appends a deterministic saved item for creation, updates the exact stable saved item for replacement, or removes that semantic item for deletion. It preserves custom fields where applicable, all unrelated schema content, and the complete parsed layout, including any layout object associated with a deleted view. If exact identity/revision reconciliation or storage fails after commit, the response reports the PostgreSQL commit separately from a `schemaSync` conflict/storage error; the browser refreshes and never retries the DDL plan.

Deleting an example remains respected across restarts. Use **? > Restore examples** to reinstall missing examples. Existing saved designs and layouts are not replaced. In included-database mode, restoration can refresh the reserved tutorial connection password from current `.env` settings, so re-preview any open migration afterward.

### Schemer Dashboard Workspace

The current dashboard-list compatibility contract preserves complete `dashboards` and `summaries` arrays for unparameterized requests. Callers opt into bounded keyset paging by sending `pageSize` and following the signed `page.nextCursor`. Dashboard records now normalize version 1 and version 2 in memory to order-only version 3 without write-on-read; this and the source-upgrade contract below supersede older version-2 geometry and source-reselection-only descriptions later in this section.

Schemer is supported by both launchers. Use `schemer` for the explicit no-AI stack or `schemer-ai` for one private OpenCode service shared by both applications. These modes preserve the same stable instance identity and five-credential lifecycle as Schemii modes, select and reuse free loopback ports, wait for every selected application and sidecar health check, print both URLs, and open only the primary Schemer URL. They use the complete sibling stack so the shared tutorial profile remains available, but Schemer's health check and readiness do not depend on Schemii health. The direct Compose combinations below remain available to advanced operators; they use fixed ports and do not provide launcher lifecycle safeguards.

All launch modes retain loopback-only host publication. For optional external HTTPS, an operator may configure a machine-local reverse proxy independently and set `SCHEMII_PUBLIC_ORIGINS` and/or `SCHEMER_PUBLIC_ORIGINS` to comma-separated exact, path-free HTTPS origins. The proxy must send exactly one `X-Forwarded-Host` matching the public authority and exactly one `X-Forwarded-Proto: https`; the backend accepts those headers only from its configured Docker ingress peer. This boundary is proxy admission, not application user authorization or per-user privacy.

Current dashboards use version-3 array order only. Cards form three equal columns above 900 pixels, two equal columns from 601 through 900 pixels, and one column at 600 pixels and below, always with a 12-pixel gap and a 260-pixel card height. Edit mode reorders that one shared sequence by dragging a widget header or using its keyboard-accessible earlier/later controls. Schemer persists no widget coordinates, dimensions, or breakpoint-specific order.

The Date ranges dialog now edits the persisted slicer contract, including exact widget/temporal-column bindings, half-open dates, and source time zones for timestamp-without-time-zone columns. Saved ranges refresh bound widgets through revision-guarded server reconstruction; the browser never guesses or silently rebinds a stale source. Detail selections use one generation-bound retained-result workflow with retry, opaque continuation, JSON/CSV export, SQL provenance, and explicit resource release. A superseded dispatched request is still observed so any returned snapshot can be released; browser cancellation never abandons a server-created result. The older note below that browser slicer editing is deferred is superseded.

Schemer is an analytics workspace served separately from Schemii while reusing the same Python `PostgresService`, capability-scoped PostgreSQL HTTP router, authenticated browser session client, PostgreSQL browser client, profile form/repository contracts, profile store, visual tokens, SVG icon registry, icon-button factory, delegated tooltip behavior, status controls, loading controls, and menu behavior. Common actions such as Close are instantiated from the same shared component rather than copied between apps. The bundled Mercury dashboard contains six functioning widgets backed by the live `bookstore.order_summary` view; it does not embed preview values or rows. **Restore Mercury demo** rebuilds those definitions from the verified included profile while preserving existing widget array order, vertical viewport, and unrelated custom widgets. Dashboard widgets render as uniform responsive tiles. Clicking a tile expands it from its dashboard position into an app-wide detail view using the widget's own header. Activating a KPI, chart mark, or table row opens its live detail report with the matching filters. `View SQL` shows a readable, copyable equivalent with bound values populated while server execution remains parameterized. Edit mode supports persisted header drag-and-drop ordering, keyboard-accessible earlier/later movement, widget creation, duplication, and deletion within the one shared order-only version-3 sequence.

Schemii and Schemer use the same responsive introduction shell, server-start/opt-out policy, page controller, reduced-motion playback rules, and animated cursor implementation. Each application shows its introduction once after a new local server process starts, not again on an ordinary browser refresh. Selecting **Do not show on future server starts** disables that application's automatic introduction for the current browser origin; **Show introduction** in the application menu remains available and allows the option to be cleared. Schemer's seven workflow pages cover dashboard lifecycle, widget ordering, exact source selection, query and visualization drafts, date ranges, retained detail and lineage, and the distinct AI, Console, and refresh boundaries. Both seven-page introductions use synthetic local markup and never create or save records, execute PostgreSQL queries, send AI prompts, refresh sources, or alter user-owned layout.

Dashboard management includes create, open/switch, rename, duplicate, archive/unarchive, active/archived filtering, and delete workflows. Dashboard and widget edits autosave with revision checks; navigation stops when pending persistence fails instead of discarding local work. Dashboard records have an explicit 5 MiB normalized JSON ceiling aligned with the HTTP request-body ceiling; oversized requests return `413`, and an already persisted valid oversized legacy record remains readable but is not overwritten. Version-1 and version-2 records normalize deterministically in memory to version 3 without write-on-read. Their widgets sort by saved `mobile.order` with original array index as the stable tie-breaker, persisted widget geometry is removed, and desktop/mobile viewport state retains only vertical `y`; the first explicit mutation writes version 3 and advances the revision once. `GET /api/dashboards` and `GET /api/dashboards/summary` return at most 50 records by default and 100 when `pageSize=100`, with a signed `page.nextCursor`; tampered, cross-list/page-size, and stale cursors fail closed. Existing callers can continue reading the `dashboards` or `summaries` arrays, while callers that need every record follow `page.hasMore`. Summary reads use validated revision-bound sidecar metadata when current and fall back to the authoritative dashboard JSON. A dashboard is limited to 100 widgets. New manual widgets may begin as unconfigured placeholders and gain live data only after a verified source and structured query are applied. Confirmed AI dashboard/widget mutation proposals execute through Schemer's durable operation authority and dashboard-owned idempotent receipts while preserving unrelated order, viewport, and configuration. Each new mutation first directory-syncs an operation tracking witness, and its receipt is then written atomically with the dashboard; before rollover or dashboard deletion the receipt is promoted into immutable archived evidence. Reconciliation can therefore prove an applied or witnessed-not-applied outcome after restart without replay or treating cache eviction as proof of no effect. Unwitnessed legacy absence remains uncertain rather than being mislabeled. Version-3 records may persist strict date-range slicers with explicit widget/source-column bindings, and the Date ranges dialog edits those bindings and values.

The Data sources dialog manages shared PostgreSQL connection profiles only. In dashboard Edit mode, each tile has an **Edit** action that opens that widget's configuration editor, where its name can be edited independently of source or query setup. Names and source assignments save automatically; query drafts remain local until **Apply query & run** succeeds. Connected keyboard-navigable tabs provide separate Source, Visualization, Filters, Sort & Limit, and Detail Report views inside one scrolling content pane. The Source view browses tables, partitioned tables, views, materialized views, and foreign tables for that widget alone. Namespace and relation catalogs use exact-target, filter-, sort-, page-size-, profile-, and catalog-fingerprint-bound opaque keyset cursors; system namespaces require explicit `scope=all` opt-in and carry system classifications. The server verifies `current_database()` before returning identities and rejects changed profiles, databases, catalogs, and cursor contexts. Selecting a relation loads its normalized kind, ordered semantic columns, PostgreSQL display types, nullability, full deterministic relation fingerprint, and catalog-derived type/operator/aggregate capabilities. New version-2 source snapshots persist those capabilities. Legacy version-1 snapshots remain display-compatible but cannot run or be edited until the source is reselected or explicitly upgraded from **Review legacy sources**. That review reads no rows, binds the exact dashboard revision and widget IDs, requires exact live legacy fingerprints and columns, validates saved queries against current capabilities, expires after a signed short-lived review, re-inspects on confirmed apply, and atomically upgrades only compatible widgets in one revision while preserving incompatible widgets. The Sort & Limit view accepts multiple result fields and preserves their top-to-bottom priority in generated `ORDER BY`. Narrow layouts retain dashboard switching and creation controls, and reduced-motion preferences suppress workspace transitions.

Legacy-source apply first acquires exact target-relation `ACCESS SHARE` guards in separate read-committed transactions, then reads the authoritative capability descriptors from separate PostgreSQL 17 repeatable-read snapshots. The service's in-process and cross-process profile locks and the target-relation guards remain held through the revision-guarded dashboard write. That is the cross-system linearization boundary: the saved capability descriptor is authoritative at the guarded repeatable-read snapshot, not a claim that every dependency catalog remains immutable through or after the HTTP response. DDL on a dependent type, collation, operator, aggregate, or implementation catalog after that snapshot is a subsequent source change even when it does not conflict with the guarded target-relation lock. Apply performs one observational post-write source verification after releasing the guards and reports `current`, `changed`, or `unavailable`; this check never rolls back, rewrites, or replays the already durable dashboard revision. A detected change remains visibly blocked in Schemer, and `verify_relation_source` plus execution's own repeatable-read reinspection reject a changed fingerprint before row execution. DDL can still commit after the post-write check, so no response claims the catalog remains frozen.

Each legacy-source batch is limited to four unique profile/database pairs and at most eight simultaneous PostgreSQL connections; the existing 100-target request ceiling remains available within that fanout. Preview's `deferredWidgetIds` is the sole continuation list. An all-incompatible batch can advance to those deferred IDs without a write; every compatible batch requires a separate explicit confirmation, increments the dashboard revision once, reloads that new revision, and previews only its deferred IDs without automatically applying them. The signed digest accepts all 100 maximum-length widget IDs: its derived 28,604-character ceiling replaces the former 8,192-character decoder limit, and the preview exposes that ceiling. Both legacy routes use the corresponding derived 50,027-byte request-body ceiling; over-bound or tampered input fails closed.

In a widget editor, a verified relation can be assigned only to that widget. Version-1 and version-2 dashboard records remain read-compatible through in-memory version-3 normalization, while sourced widgets persist exactly one `source` object containing profile, database, namespace, relation, kind, fingerprint, and an optional semantic column snapshot. A configured aggregate stores a version-2 `query` with ordered `dimensions`, one or more ordered `measures`, parameterized filter groups, `sort`, and a row `limit`. Conditions inside a filter group are combined with `AND`; groups are combined with `OR`. Available operators follow PostgreSQL catalog-derived column capabilities: ordered comparisons and `between` where supported, text matching for compatible operators, and null checks where broader comparison is unavailable. Date and timestamp values use typeable, Schemer-themed calendar controls that close on outside click or Escape, while `between` provides explicit From and To bounds. Existing version-1 flat filters load as one AND group. Measures support row count, column count, count distinct, sum, average, minimum, and maximum when exposed by the saved capabilities, with stable IDs and lineage. The validator rejects source arrays, joins, caller SQL, incomplete identities, unsupported kinds, malformed fingerprints, stale or unknown fields, invalid operator/type or aggregate/type combinations, and unknown configuration fields. Assignments can be cleared, and sourced tiles display their exact database, namespace, and relation.

Applying a query creates a first-class Aggregate Report widget. Its versioned `table` presentation lists every dimension and measure target without changing generated SQL. Dimensions always render before measures, while each group can be reordered independently. Presentation settings persist display labels, widths from 64 to 1024 pixels, hidden state, left pinning, and a bounded page size of 10, 25, 50, or 100 rows. Hidden columns remain in both the query and presentation record, so showing them again restores their configuration. Pagination operates only over the server-bounded aggregate response; truncation remains visible when the query limit excludes additional groups. Aggregate rows and measure cells retain source-column, filter-group, dimension-value, and measure lineage hooks for the Phase 7 drill-through drawer. Pivots, subtotals, and grand totals remain intentionally deferred.

Aggregate Report editors place table, KPI, grouped bar, line, and donut controls on the Visualization tab. The selector includes a decorative, explicitly data-free sample of the selected mode. Each mode exposes bespoke role blocks: table uses all configured dimensions and measures, KPI uses no dimensions and one or more measures, bar and line use one dimension and one or more measures, and donut uses one dimension and one measure. Compatible roles carry forward when the mode changes; narrower modes truncate only their active selection while retained query fields remain available to wider modes. Empty or invalid required blocks receive a stronger highlight. Execution projects the authoritative query to the active mode's roles, so KPI can run ungrouped without deleting chart dimensions and donut can use one measure without deleting the others. An optional version-1 `visualization` presentation stores these per-mode selections. Existing reports without this object continue in table mode. Measure formatting shows currency and decimal-place inputs only when the selected number format uses them. Chart marks preserve query lineage and each chart includes a keyboard-accessible data table. Expanded line charts backed by a PostgreSQL date or timestamp use a proportional UTC timeline rather than equal row spacing. A server manifest selects a fixed resolution that keeps the complete filtered domain within the widget's saved result limit; the browser then lazy-loads aligned half-open time windows as they enter the horizontal viewport and caches every loaded window until refresh, source/query change, revision change, or dashboard navigation. Scrolling back reuses browser-cached points without querying PostgreSQL, every cached point remains selectable, and unloaded windows remain visual gaps rather than invented connections. Selecting a bucket drills through its complete half-open UTC range instead of incorrectly matching only the bucket-start value. Visualization and query edits remain local drafts until **Apply query & run** verifies the projected query and saves the dashboard. Dashboard tiles display only the resulting visualization, keeping configuration controls inside the widget editor.

Each Aggregate Report also stores a version-1 detail-report presentation tied to the same verified source relation. It configures ordered source columns, labels, widths, visibility, number formats, default sort, row identifier, and a page size capped at 100. Activating a KPI value, chart mark, aggregate row, or measure cell opens a shared vertical workspace with the effective widget and dashboard-slicer filters, clicked dimension values, optional measure lineage, matching-row count, live query time, bounded server pagination, sorting, and search actions on every column header. Detail responses retain an opaque process-local result resource and expose Previous/Next cursors, so navigation and JSON/CSV export use the original PostgreSQL snapshot without rerunning or skipping rows. A search action smoothly widens its column and reveals the input beside the field name. Only one input expands at a time; opening another shrinks the previous column and preserves its active value as a compact header badge. Multiple column searches combine with `AND`, debounce while typing, replace the retained result, cast the selected PostgreSQL values to text for matching, and keep every search value parameterized. Either pane header swaps which pane is expanded while leaving the other available as a compact header. The server re-verifies the exact database, relation kind, fingerprint, and live columns, then executes parameterized count and detail statements in one read-only repeatable-read transaction. Its retained cursor holds an `ACCESS SHARE` lock until exhaustion, export, cancellation, five-minute expiry, shutdown, or restart; conflicting DDL can wait while that snapshot is open. The configured row identifier provides a stable tie-breaker. Detail SQL and bound parameters remain separately inspectable from the detail header. Slicer editing, record editing, joins, and cross-widget drill-through remain deferred.

Every sourced widget and active detail report includes a **Data Lineage** action. One reusable dialog shows the redacted profile label and ID, exact database/namespace/relation identity, relation kind, fingerprint, ordered PostgreSQL columns, verification state, effective query, separately identified slicer predicates, clicked dimensions, selected measure, column searches and sort, query duration, row counts, truncation, and refresh time. Views and materialized views show their bounded PostgreSQL query definition when available; tables show their authoritative ordered catalog columns and explicitly state that PostgreSQL does not expose one complete creation statement. Aggregation SQL, detail-page SQL, detail-count SQL, and each parameter list remain separate. Copy controls operate only on the explicitly selected statement or parameter JSON and never interpolate values or include connection credentials. Read-only `EXPLAIN` remains a separately approved future extension.

Schemer can use the same private OpenCode service and provider subscriptions as Schemii while retaining its own `/workspace-schemer` chat history, instructions, skills, and proposal tools. Provider API keys and OAuth/subscription credentials remain in the existing `schemii-opencode-data` volume, so connecting through either app makes that provider available to both without credential re-entry. Both apps use the same left-side assistant drawer, activity timeline, reasoning/tool cards, provider settings, and history controls. A confirmed proposal card displays a visible running state and live elapsed timer, then retains the final success, failure, or cancellation duration. Running AI query cards expose **Stop**; the server durably binds the cancellation to the exact proposal, signals the active PostgreSQL connection, and waits for read-only rollback. History and proposals are server-bound to the exact application, chat, resource, disclosure level, saved revision, and data target. Confirmed actions create persistent, idempotent operation records so retries and reconnects observe one execution. Metadata and dashboard disclosure modes remain row-free. Data mode requires an exact profile, database, and namespace to enable the inert `schemer_read_query` proposal tool; every query requires browser confirmation and only a server-owned, bounded, one-use result reference is returned to the model. Schemer may also propose opening an exact listed dashboard and creating or changing dashboards/widgets through its dashboard-owned executor. Analytic SQL may join relations when necessary, while persisted widget configuration remains single-relation and caller-SQL-free.

Schemer verifies every persisted widget source against live PostgreSQL when a dashboard opens and when the catalog is refreshed. Refresh uses the lightweight exact-database relation listing rather than Schemii's full namespace introspection, then verifies each saved relation kind, fingerprint, and column snapshot. Mismatches return `relation_changed` and block the widget rather than silently adopting new metadata. Missing or unreachable sources are also blocked. The strict singular source shape has no join or cross-relation column-reference fields, and the dashboard validator rejects attempts to add them.

Relation columns include advisory role suggestions derived from PostgreSQL type categories. Numeric values are suggested as measures, temporal values as dates, text/enums/booleans as dimensions, and UUID or conservatively named `id`/`*_id` values as identifiers. Suggestions are displayed as labels only: they are not persisted, do not select a role, and are excluded from relation fingerprints.

The relation detail pane can request a 20-row source preview. The dedicated preview API requires the complete verified source identity, rechecks kind and fingerprint in the same read-only transaction used for selection, inherits PostgreSQL statement policy, quotes every identifier, selects only verified columns from one relation, parameterizes offset and limit, and caps requests at 50 rows. It never accepts joins or caller SQL; preview order is explicitly reported as unstable.

New source assignments persist a version-2 semantic column snapshot containing name, PostgreSQL display type, nullability, ordinal, and fingerprinted catalog-derived capabilities. Live verification compares that snapshot with PostgreSQL and reports missing relations plus named missing, added, and changed columns. Legacy version-1 snapshots remain readable but require explicit source reselection to acquire current capabilities before structured queries run. Changed sources stay blocked until the user reselects the live relation; Schemer never rewrites a saved fingerprint or snapshot automatically.

Aggregate execution uses dedicated endpoints for both lower-level structured requests and dashboard-bound browser requests. The browser sends unsaved sourced drafts through `dashboard-widgets/preview` with the exact dashboard ID/revision/widget ID; the server reconstructs the saved source authority, accepts the draft structured query, composes every applicable slicer, and rejects stale-dashboard execution. Persisted execution uses `saved-widgets/aggregate` and reconstructs both source and query. The lower-level `relation/query` endpoint still accepts a complete verified relation snapshot and validated single-relation query without dashboard context, so revision and slicer guarantees are not universal to every structured execution API. The server quotes all identifiers, binds every filter and limit value, acquires an access-share lock before taking the catalog snapshot, and executes in a repeatable-read, read-only transaction with a 500-row maximum. Generated SQL is formatted across clauses and grouped predicates so `View SQL` mirrors the AND/OR structure. Responses include a bounded generic result table, truncation state, exact generated SQL, bound parameters, dimension/measure/filter-group lineage, the complete `effectiveQuery`, and `slicerLineage` identifying the applied slicer predicates. The temporal-series companion route requires the exact saved line widget ID and dashboard revision, reconstructs its source and visualization projection, and composes slicers into manifest and aligned-window requests. Date slicers use start-inclusive/end-exclusive bounds; `sourceTimeZone` is required only when a binding targets `timestamp without time zone`, while PostgreSQL `date` and `timestamp with time zone` bindings reject it. Temporal execution rejects expired HMAC-signed manifests, stale refresh generations, misaligned ranges, unknown fields, or windows denser than one row per server-derived bucket. Each request remains independently read-only and repeatable-read; cached windows are refresh-coherent but are not claimed to share one long-lived PostgreSQL snapshot. Schemer does not accept joins or caller-authored SQL through these endpoints.

In the preceding analytics contract, "with a statement timeout" means the selected PostgreSQL role/database/session setting; Schemer does not install an application default.

Detail responses and truncated or response-oversized aggregate responses add an exact-owner `resultResource`. Complete aggregate responses that fit within one response page are not retained and export locally from the returned columns and rows. Retained aggregate rows use bounded memory; detail paging retains the original repeatable-read PostgreSQL cursor and caches fetched rows for Previous and export. Retained continuation and JSON/CSV export never rerun the query. The resource is bound to the browser session, server process, application, profile and source fingerprints, query, and any dashboard/widget authority claim. It expires after five minutes, is explicitly released when replaced or closed, and cannot survive server restart; expiry, cancellation, stale authority, and restart never trigger automatic replay. Aggregate spools and detail results have independent capacity: up to 100 aggregate spools share 64 MiB and use inactive least-recently-used/expiry eviction, while up to eight detail results share a separate 64 MiB budget and at most four live snapshots. Live detail resources are never evicted for aggregate admission. Every resource remains limited to 16 MiB, detail retention to 10,000 rows, response pages to 1 MiB, and exports to 20 MiB. The browser schedules at most three aggregate requests per available exact profile/database target and six globally, leaving default target admission headroom for interactive detail work. Busy or expired work remains explicit and user-retryable; there are no automatic aggregate HTTP retries. These are application process-safety limits, not PostgreSQL policy.

Direct Compose also consumes preloaded immutable images and never builds them. Set `SCHEMII_IMAGE`, `SCHEMII_METADATA_IMAGE`, and, when AI is enabled, `SCHEMII_OPENCODE_IMAGE` to the verified release references, then set a stable instance and collision-free ports before running either command. In a POSIX shell:

```bash
export SCHEMII_INSTANCE=my-schemii SCHEMII_HOST_PORT=18080 SCHEMER_HOST_PORT=18081
```

In PowerShell:

```powershell
$env:SCHEMII_INSTANCE = "my-schemii"
$env:SCHEMII_HOST_PORT = "18080"
$env:SCHEMER_HOST_PORT = "18081"
```

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.schemer.yaml up --no-build -d
```

To run both applications with one shared OpenCode service, set `SCHEMII_CREDENTIAL_DIR` to the absolute owner-only five-file credential directory described below and include both AI overrides:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.ai.yaml -f compose.schemer.yaml -f compose.schemer.ai.yaml up --no-build -d
```

With the example overrides above, open Schemii at `http://127.0.0.1:18080/` and Schemer at `http://127.0.0.1:18081/`; without overrides, direct Compose defaults to ports 8080 and 8081. Both services run distinct `schemii`/`schemer` commands from one application image. They share the dedicated metadata service but connect as distinct runtime roles; `metadata-migrate` must finish successfully before either application starts. After `example-seed`, one `example-profile-init` service writes or verifies the reserved tutorial profile in `schemii-config`; both applications depend on that one-shot owner, not on Schemii health. Saved PostgreSQL profiles are shared through the same instance-scoped volume; passwords remain server-side and are never returned to either browser. Versioned dashboard records are stored separately in the owner-only `schemer-dashboards` volume and survive container replacement or restart. Deleting that volume permanently deletes the saved dashboards. Direct native launches use `SCHEMER_DASHBOARD_DIR`, which defaults to `~/.local/share/schemer/dashboards`.

Schemer saves edits automatically using revision checks. If another browser tab saves the same dashboard first, the stale tab enters a modal quarantine: autosave and mutation controls stop, open editors are dismissed, and an immutable capture of the local dashboard/editor draft remains exportable. Only the explicit discard-and-refresh action loads the exact server-authoritative dashboard; the stale tab never retries or overwrites the newer record automatically.

## Everyday Use

Rerun the same launcher command to start or update an installation. The launcher reuses its saved designs, profiles, database, AI credentials, and chats.

The launcher prints an **Instance** name and URL. Separate installation directories receive separate instance names, ports, containers, credentials, and volumes while safely sharing the same verified release images. Do not move or rename an installation directory unless you intentionally want a new derived instance or have set a stable `SCHEMII_INSTANCE` environment variable.

When upgrading an older installation that has legacy volumes but no remaining container, the launcher stops instead of opening an empty-looking instance. Follow its displayed command to reuse the legacy `schemii` data, or choose a unique `SCHEMII_INSTANCE` for a separate installation.

### Adopt Historical Unlabeled Volumes

Historical default installations can have the exact volumes `schemii_schemii-config` and `schemii_schemii-schemas` without Docker Compose labels. Normal new volumes still require exact Compose ownership labels. Recovery and uninstall accept this one historical pair only after an explicit local attestation; no other project, volume name, or logical resource can use this path.

First start the historical installation normally with `SCHEMII_INSTANCE=schemii` if it has no remaining container, review that the expected profiles, designs, and layouts are present, and then stop every container in project `schemii`. From the same repository directory and exact owner-only credential directory used by that installation, run:

```bash
export SCHEMII_INSTANCE=schemii
export SCHEMII_CREDENTIAL_DIR=/absolute/path/to/existing/credentials
bash ./start.sh legacy-volume-adopt ADOPT:schemii
```

PowerShell:

```powershell
$env:SCHEMII_INSTANCE = "schemii"
$env:SCHEMII_CREDENTIAL_DIR = "C:\absolute\path\to\existing\credentials"
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode legacy-volume-adopt -ConfirmInstance ADOPT:schemii
```

Adoption does not mount-write, copy, relabel, recreate, or remove either volume. It requires the exact `instance` credential marker; verifies both volumes are unlabeled, local, and expose stable Docker identity; inspects every current/exited container mount; requires at least one exact Compose project/service/repository-working-directory witness for each volume; and rejects any foreign or unexpected consumer. Only then does it atomically publish two owner-only manifests under `legacy-volume-adoptions.v1` in the credential directory.

Do not edit, delete, copy, or restore those manifests manually. They are local Docker-host and repository-path evidence, not portable backup content. Recovery and uninstall recompute both volume identities and require the complete manifests byte-for-byte; a missing/tampered manifest, moved repository, replaced volume, changed identity, extra evidence file, or permission drift fails closed. Expected containers may be recreated or removed after adoption because container IDs are not part of the durable identity. Re-run the same adoption command only to verify unchanged evidence; it refuses to replace conflicting evidence.

Use **? > Shut down Schemii** to save pending design changes and stop the UI process. PostgreSQL and OpenCode may remain running so the next UI start is fast. To stop every container, use Docker Desktop's Containers view, or stop containers with the printed instance label:

```bash
docker stop $(docker ps -q --filter "label=com.docker.compose.project=<instance>")
```

PowerShell:

```powershell
docker ps -q --filter "label=com.docker.compose.project=<instance>" | ForEach-Object { docker stop $_ }
```

Starting Schemii again restores those containers without deleting data.

### Update An Immutable Installation

Do not update a detached release checkout with `git pull` and do not overlay an installation with an automatically generated repository snapshot. Choose the next published protected release tag, record its full 40-character commit SHA, and download that release's actual source, Python-package, and architecture-qualified image artifacts plus `SHA256SUMS`. Verify every checksum and attestation as described below before stopping or changing the current installation.

For an existing Git checkout, fetch only the reviewed next tag into `FETCH_HEAD`, verify its peeled commit, and detach at that exact commit:

```bash
next_tag=vX.Y.Z
next_sha='<full-40-character-sha>'
git fetch --no-tags origin "$next_tag"
test "$(git rev-parse 'FETCH_HEAD^{commit}')" = "$next_sha"
git checkout --detach "$next_sha"
```

For a source-archive installation, extract the verified `${prefix}-source.tar.gz` beside the old directory instead of overwriting it, then enter the new tag-and-SHA-named directory. In either case, first take the coordinated and external backups described below, load the three verified published image archives, preserve the exact existing `SCHEMII_INSTANCE` and owner-only `SCHEMII_CREDENTIAL_DIR`, select the published image addresses, and launch without rebuilding them:

```bash
export SCHEMII_INSTANCE='<existing-exact-instance>'
export SCHEMII_CREDENTIAL_DIR=/absolute/path/to/existing/credentials
export SCHEMII_IMAGE="schemii:${next_tag#v}-${next_sha}"
export SCHEMII_METADATA_IMAGE="schemii-metadata-postgres:${next_tag#v}-${next_sha}"
export SCHEMII_OPENCODE_IMAGE="schemii-opencode:${next_tag#v}-${next_sha}"
bash ./start.sh
```

PowerShell uses the same verified next tag, SHA, credential directory, and published image addresses before running `start.ps1`. Keep the previous source directory, image archives, and backups until the updated instance passes health and data review.

### Release Integrity

`VERSION` is the single practical release version source; standard Python package metadata reads it through setuptools. Release changes are recorded in `CHANGELOG.md` and the operator procedure is in `docs/RELEASE_CHECKLIST.md`. Main-branch CI embeds the exact 40-character commit revision, builds one wheel/sdist and one application, metadata, and OpenCode image set, validates the packages, smokes that same image set in all four product modes with `--no-build`, inspects the source, nested packages, and every saved filesystem layer in all three image archives for private/runtime data, and uploads one hash-bound, provenance-attested candidate. CI does not publish it.

An operator must explicitly dispatch the promotion workflow with the successful main-branch CI run ID, exact version, and `PROMOTE` confirmation. The protected `production-release` environment then downloads that candidate, re-verifies hashes, attestations, build identity, and image IDs without rebuilding or repeating the smoke suite, publishes exact version/revision GHCR tags with OCI provenance, and creates `v<VERSION>`. Release archives contain the version and full commit SHA, include `release-manifest.json` and `SHA256SUMS`, refuse to replace an existing release/tag, and never publish a mutable `latest` artifact. Verify all downloaded files from one release directory before extracting or loading them:

```bash
release_tag=vX.Y.Z
release_sha='<full-40-character-sha>'
release_version=${release_tag#v}
prefix="schemii-${release_version}-${release_sha}"
base="https://github.com/LandMineDevelopment/schemii/releases/download/${release_tag}"
curl --fail --location --remote-name-all \
  "$base/SHA256SUMS" \
  "$base/release-manifest.json" \
  "$base/published-images.json" \
  "$base/${prefix}-source.tar.gz" \
  "$base/${prefix}-python-packages.tar.gz" \
  "$base/${prefix}-application-linux-amd64.tar.gz" \
  "$base/${prefix}-metadata-linux-amd64.tar.gz" \
  "$base/${prefix}-opencode-linux-amd64.tar.gz"
if command -v sha256sum > /dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi
gh attestation verify "${prefix}-source.tar.gz" --repo LandMineDevelopment/schemii
gh attestation verify published-images.json --repo LandMineDevelopment/schemii
```

`linux-amd64` image archives run only on a Linux/AMD64 Docker engine; the architecture is part of each image archive name and is checked before release. Do not load one on an incompatible engine. Load the three exact archives and select their immutable tags before using the extracted source:

```bash
docker load --input "${prefix}-application-linux-amd64.tar.gz"
docker load --input "${prefix}-metadata-linux-amd64.tar.gz"
docker load --input "${prefix}-opencode-linux-amd64.tar.gz"
export SCHEMII_IMAGE="schemii:${release_tag#v}-${release_sha}"
export SCHEMII_METADATA_IMAGE="schemii-metadata-postgres:${release_tag#v}-${release_sha}"
export SCHEMII_OPENCODE_IMAGE="schemii-opencode:${release_tag#v}-${release_sha}"
bash ./start.sh
```

PowerShell uses the same archive and image addresses:

```powershell
$releaseTag = "vX.Y.Z"
$releaseSha = "<full-40-character-sha>"
$releaseVersion = $releaseTag.Substring(1)
$prefix = "schemii-$releaseVersion-$releaseSha"
Get-FileHash "$prefix-*" -Algorithm SHA256
docker load --input "$prefix-application-linux-amd64.tar.gz"
docker load --input "$prefix-metadata-linux-amd64.tar.gz"
docker load --input "$prefix-opencode-linux-amd64.tar.gz"
$env:SCHEMII_IMAGE = "schemii:$($releaseTag.Substring(1))-$releaseSha"
$env:SCHEMII_METADATA_IMAGE = "schemii-metadata-postgres:$($releaseTag.Substring(1))-$releaseSha"
$env:SCHEMII_OPENCODE_IMAGE = "schemii-opencode:$($releaseTag.Substring(1))-$releaseSha"
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

`Get-FileHash` output must be compared with every matching line in `SHA256SUMS`; a matching algorithm alone is not verification. GitHub CLI users can run `gh attestation verify <archive> --repo LandMineDevelopment/schemii` for each archive.

Python, PostgreSQL, Node, OpenCode, direct Python dependencies, build backend, actionlint, and release-workflow actions are pinned to concrete versions or verified multi-platform image-index digests. Managed GitHub runner labels, the selected Python 3.12 patch installed by GitHub, OS packages preinstalled on that runner, and Docker Desktop/Engine itself remain externally managed residuals; do not invent digests for them. Re-verify all pinned image indexes and organizational tag-protection/immutable-release settings before each release.

## Docker Data And Backups

The default stack stores data in instance-scoped Docker volumes:

- `schemii-config`: PostgreSQL profiles, stored profile passwords, migration history, and example state
- `schemii-schemas`: saved designs and canvas layouts
- `schemii-postgres`: included PostgreSQL data
- `schemii-metadata-postgres`: server authority, migration records, and other application metadata; independent from every user-selected target database
- `schemii-opencode-data`: provider credentials and chat sessions
- `schemii-opencode-config` and `schemii-opencode-state`: OpenCode configuration and state
- `schemii-opencode-cache`: recreatable cache
- `schemer-dashboards`: saved Schemer dashboards, widget configuration, layout, and viewport state when Schemer is enabled
- `schemii-recovery`: durable coordinated-restore rollback or committed-forward-cleanup evidence; normally empty and retained whenever automatic recovery cannot finish

List the exact volumes for the launcher-printed instance:

```bash
docker volume ls --filter "label=com.docker.compose.project=<instance>"
```

Back up the config, schemas, Schemer dashboards, both PostgreSQL databases, and non-cache OpenCode volumes before upgrades or migration work. Use `pg_dump` for important PostgreSQL data. Metadata can contain sensitive authority history and transient query-result payloads, so protect and retain its backups separately from user target backups.

The launchers generate five cryptographically random credentials per instance: one metadata bootstrap initialization secret, three metadata PostgreSQL login-role passwords, and one internal OpenCode password. Every credential file has one optional LF newline after a single value of 16-256 characters from `[A-Za-z0-9_-]`; all launchers, container entrypoints, and server-side readers enforce that same format, while the metadata rotation function validates the three database passwords it accepts. They persist outside the repository at `$XDG_DATA_HOME/schemii/credentials/<instance>` (defaulting to `~/.local/share/schemii/credentials/<instance>`) on Linux/macOS and `%LOCALAPPDATA%\Schemii\credentials\<instance>` on Windows. Directories are restricted to the owner and files to the owner on POSIX. On Windows, the launcher removes inheritance and applies and verifies owner/current-user-only ACLs recursively on reused and new credential content, transaction staging, and backups; an ACL application or verification error stops the operation. Container entrypoints briefly retain `CHOWN`, `DAC_OVERRIDE`, `SETGID`, `SETPCAP`, and `SETUID` to copy owner-only mounted files, drop to the application UID, and clear the capability bounding set before the application starts. Do not commit, print, email, or include this directory in an unencrypted general-purpose backup.

The coordinated launcher backup covers shared profile/config files, byte-exact Schemii schema records and layouts, Schemer dashboards/layouts, the owner-managed `public` metadata schema and data, the exact instance marker, and all five credentials. It does not include target PostgreSQL data, including the bundled tutorial database, or OpenCode provider/chat/config/state volumes; those require separate backups. Every instance container must be stopped so file-backed config, schemas, and dashboards have no writer. The destination `<directory>/<instance>` must not already exist.

Historical-volume adoption manifests are deliberately excluded because their Docker identity and repository binding are host-local and must not authorize a restored or replacement volume. A restored volume created by current Compose receives normal ownership labels; otherwise perform a new explicit adoption only when the exact historical volumes and witness contract still apply.

```bash
bash ./start.sh instance-backup <protected-directory>
```

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode instance-backup -Path <protected-directory>
```

The command starts only metadata PostgreSQL while taking its consistent custom-format dump, validates the shared profile document plus every schema and dashboard record, records checksums, application version, and metadata version, requires the checksum manifest to contain exactly every expected critical path once, and verifies the complete version-specific role-membership, role-attribute, migration-history, database/schema/default/object/function ACL, policy, row-security, authority foreign-key, inventory, and owner matrix. Metadata versions 10 through the current packaged version 13 are supported; older, newer, gapped, renamed, or checksum-mismatched histories fail closed. It copies the completed backup with owner-only host permissions and stops metadata again. Tar archives preserve stored bytes, numeric ownership, and modes. Backup output contains plaintext PostgreSQL profile passwords, metadata/OpenCode credentials, and sensitive metadata. Store it as a password vault, not as a general unencrypted archive.

Restore is destructive and only targets the exact marker-matched instance. Review and separately back up the destination, stop every instance container, and pass `RESTORE:<instance>` as the explicit destructive confirmation:

```bash
bash ./start.sh instance-restore <protected-directory> RESTORE:<exact-instance-name>
```

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode instance-restore -Path <protected-directory> -ConfirmInstance RESTORE:<exact-instance-name>
```

Restore rejects a mismatched marker, incomplete or structurally invalid checksum manifest, newer or incompatible application/metadata version, invalid credential, missing or foreign destination volume, running destination, unsafe archive path, malformed schema/dashboard/profile document, or incorrect metadata ownership/ACL. Before replacement it writes config, schema/layout, dashboard, and metadata rollback snapshots into an unpublished staging directory, validates their archives and hashes, fully syncs them, publishes the complete set, syncs again, and publishes the transaction marker last. No destination mutation begins before that marker and rollback trap exist. An interrupted or disk-full unpublished phase is removed before retry instead of being mistaken for rollback evidence. Commit first atomically publishes and syncs an instance-bound `committed` marker before deleting any rollback file. While that marker exists, rollback is forbidden: every cleanup step is idempotent, and the launchers retain it until data evidence and the atomically transitioned credential cleanup are both complete.

The destination must match the exact current metadata catalog and security matrix before its rollback snapshot is accepted; unknown destination objects or security drift stop restore without mutation. Metadata restore then drops and recreates the owner-managed `public` schema, resets the global default-function privilege to the recorded source version, loads the archive, and verifies the exact source-version history and security matrix before migration. The metadata migrator validates history and applies the remaining contiguous packaged migrations, after which the exact current version 13 matrix must pass before commit is allowed. File ownership/modes and the destination instance marker remain preserved. Before committed-marker publication, a failure or hard interruption rolls back application data, metadata, and credentials before retry. After publication, the same reviewed restore command completes forward cleanup without replaying or rolling back the committed restore. Success leaves the instance stopped for review. Target PostgreSQL and OpenCode data remain untouched and excluded.

### Full Disaster Recovery Order

The coordinated backup is intentionally not a complete machine backup. For a full recovery, use this order and keep every artifact under the same protected incident identifier:

1. Record the exact instance, release tag, full SHA, image addresses, Docker architecture, target PostgreSQL user/database, and backup checksums.
2. Stop Schemii and Schemer writers, keep the included target PostgreSQL running only long enough to take its custom-format dump, then stop it.
3. With OpenCode stopped, archive the non-cache `schemii-opencode-data`, `schemii-opencode-config`, and `schemii-opencode-state` volumes with numeric ownership and verify each archive. The cache volume is intentionally recreatable.
4. Stop every remaining instance container and run `instance-backup` for config, layouts, dashboards, metadata, and credentials.
5. On the replacement host, verify/load the exact architecture-qualified release artifacts, extract the exact tag/SHA source, set the same `SCHEMII_INSTANCE`, and start the desired mode once only to create label-owned empty volumes. Stop every instance container.
6. Restore the clean target PostgreSQL database, restore the three OpenCode volumes, run coordinated `instance-restore`, and start the desired mode only after every restore and checksum check succeeds.

For the default included target, back up while its application writers are stopped but its `postgres` container remains running:

```bash
instance='<exact-instance>'
mkdir -p disaster-recovery
chmod 700 disaster-recovery
umask 077
postgres_id=$(docker ps -q --filter "label=com.docker.compose.project=$instance" --filter "label=com.docker.compose.service=postgres")
docker exec "$postgres_id" pg_dump --username schemii --dbname schemii --format=custom --create > disaster-recovery/target-postgres.dump
docker exec -i "$postgres_id" pg_restore --list < disaster-recovery/target-postgres.dump > /dev/null
docker stop "$postgres_id"
```

Archive each stopped OpenCode volume with the exact loaded release image, not a floating helper image:

The loop produces `schemii-opencode-data.tar.gz`, `schemii-opencode-config.tar.gz`, and `schemii-opencode-state.tar.gz`.

```bash
for logical in schemii-opencode-data schemii-opencode-config schemii-opencode-state; do
  docker run --rm --user 0:0 --entrypoint sh \
    --mount "type=volume,source=${instance}_${logical},target=/source,readonly" \
    "$SCHEMII_OPENCODE_IMAGE" -c 'tar --numeric-owner -czf - -C /source .' \
    > "disaster-recovery/${logical}.tar.gz"
  tar -tzf "disaster-recovery/${logical}.tar.gz" > /dev/null
done
rm -f disaster-recovery/SHA256SUMS
if command -v sha256sum > /dev/null 2>&1; then
  sha256sum disaster-recovery/* > disaster-recovery/SHA256SUMS
else
  shasum -a 256 disaster-recovery/* > disaster-recovery/SHA256SUMS
fi
bash ./start.sh instance-backup disaster-recovery
```

On a reviewed empty replacement instance, restore the target database and stopped OpenCode volumes before coordinated restore:

```bash
release_tag='<exact-protected-release-tag>'
release_sha='<full-40-character-sha>'
credential_dir='/absolute/path/to/restored-owner-only-credentials'
export SCHEMII_INSTANCE='<exact-instance>'
export SCHEMII_CREDENTIAL_DIR="$credential_dir"
export SCHEMII_IMAGE="schemii:${release_tag#v}-${release_sha}"
export SCHEMII_METADATA_IMAGE="schemii-metadata-postgres:${release_tag#v}-${release_sha}"
export SCHEMII_OPENCODE_IMAGE="schemii-opencode:${release_tag#v}-${release_sha}"
export SCHEMII_POSTGRES_DB=schemii
export SCHEMII_POSTGRES_USER=schemii
export SCHEMII_POSTGRES_PASSWORD='<exact-recorded-tutorial-password>'
if command -v sha256sum > /dev/null 2>&1; then sha256sum -c disaster-recovery/SHA256SUMS; else shasum -a 256 -c disaster-recovery/SHA256SUMS; fi
SCHEMII_INSTANCE="$SCHEMII_INSTANCE" \
SCHEMII_CREDENTIAL_DIR="$SCHEMII_CREDENTIAL_DIR" \
SCHEMII_IMAGE="$SCHEMII_IMAGE" \
SCHEMII_METADATA_IMAGE="$SCHEMII_METADATA_IMAGE" \
SCHEMII_OPENCODE_IMAGE="$SCHEMII_OPENCODE_IMAGE" \
SCHEMII_POSTGRES_DB="$SCHEMII_POSTGRES_DB" \
SCHEMII_POSTGRES_USER="$SCHEMII_POSTGRES_USER" \
SCHEMII_POSTGRES_PASSWORD="$SCHEMII_POSTGRES_PASSWORD" \
docker compose --project-name "$SCHEMII_INSTANCE" -f compose.yaml -f compose.postgres.yaml up --no-build -d postgres
postgres_id=$(docker ps -q --filter "label=com.docker.compose.project=$SCHEMII_INSTANCE" --filter "label=com.docker.compose.service=postgres")
docker exec -i "$postgres_id" pg_restore --username schemii --dbname postgres --clean --if-exists --create --exit-on-error < disaster-recovery/target-postgres.dump
docker stop "$postgres_id"
for logical in schemii-opencode-data schemii-opencode-config schemii-opencode-state; do
  docker run --rm --user 0:0 --entrypoint sh \
    --mount "type=volume,source=${SCHEMII_INSTANCE}_${logical},target=/destination" \
    --mount "type=bind,source=$PWD/disaster-recovery,target=/backup,readonly" \
    "$SCHEMII_OPENCODE_IMAGE" -c "find /destination -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar --numeric-owner -xzf /backup/${logical}.tar.gz -C /destination"
done
bash ./start.sh instance-restore disaster-recovery "RESTORE:${SCHEMII_INSTANCE}"
bash ./start.sh
```

These commands are destructive only on the reviewed empty replacement volumes/database. For a non-default target profile, use that PostgreSQL installation's own tested physical or logical backup procedure and exact role instead of the tutorial commands. Never infer a target from Schemii profile labels during disaster recovery.

Back up the included PostgreSQL database separately on Linux/macOS, using the printed instance:

```bash
postgres_id=$(docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres")
docker exec "$postgres_id" pg_dump -U schemii -d schemii > schemii-postgres.sql
```

PowerShell:

```powershell
$postgresId = docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres"
docker exec $postgresId pg_dump -U schemii -d schemii > schemii-postgres.sql
```

If `.env` changes the user or database, substitute those values. Archive Schemii designs and the non-cache OpenCode data/config/state volumes only while their writers are stopped, preserve numeric owners and modes, and verify each archive before relying on it. No floating helper image is prescribed: use an internally approved, digest-pinned multi-platform archive tool and record that digest with the backup. Keep backups outside the installation directory before replacing source files.

Never run `docker compose down --volumes` or remove project volumes unless permanent deletion is intended. Doing so can delete saved designs, Schemer dashboards and widget layouts, profiles and passwords, migration history, PostgreSQL data, provider credentials, chats, and AI state.

Back up, restore, or rotate the instance credential set with `bash ./start.sh credentials-backup <protected-directory>`, `bash ./start.sh credentials-restore <protected-directory>`, and `bash ./start.sh credentials-rotate`. PowerShell equivalents use `-Mode credentials-backup -Path <directory>`, `-Mode credentials-restore -Path <directory>`, and `-Mode credentials-rotate`. Backup output contains plaintext credentials, and restore requires its `instance` marker to exactly match the selected instance. One exact-instance credential lock beside the external owner-only credential directory serializes initialization, interrupted-transaction cleanup and recovery, backup, restore, rotation, and coordinated instance backup/restore. It is released before ordinary Compose startup and is not an uninstall or repository lifecycle lock. Contention waits for at most 60 seconds and then fails explicitly. PowerShell uses an owner-only exclusive OS file handle, which the OS releases after a crash. POSIX uses an owner-only atomic owner-PID lock directory and removes a structurally valid lock whose owner process has exited. Unsafe paths, links, ownership, permissions, malformed metadata, and instance collisions fail closed. Rotation and restore stage old and new sets in an owner-only transaction directory, wait for PostgreSQL readiness before the first password update, update PostgreSQL through the migration login and narrow `SECURITY DEFINER` function, replace active file contents without changing the file identities mounted by existing containers, restart consumers, wait again, and verify the resulting migration login. Standalone credential-operation failures resume deterministic rollback while retaining both sets if automatic recovery cannot authenticate. Coordinated restore credentials instead follow the data transaction's durable state: rollback before commit publication, or an atomic forward-cleanup rename after publication. Back up metadata and credentials together before rotation.

An existing metadata volume created by an older release is never reset. On first launcher use, credentials are recovered from its existing container when possible; otherwise the historical local credentials are recorded with an explicit warning. Back up immediately. The bootstrap password is initialization-only: after first-cluster setup installs the bootstrap-owned function, the bootstrap role becomes `NOLOGIN`; rotation deliberately retains rather than regenerates that file. Credential restore may carry its archived value for disaster recovery but does not re-enable or change the role. Rotation and restore require the narrowly scoped bootstrap-owned `schemii_admin.rotate_metadata_passwords` function. If a legacy volume lacks it, the launcher fails before changing active files. A database administrator must use a reviewed maintenance connection as `schemii_metadata_bootstrap` to install the exact repository file `docker/metadata/002_rotation_function.sql`, then verify its owner, `SECURITY DEFINER` flag, fixed search path, ACL, and the bootstrap role's `NOLOGIN` state. Do not edit that file for the installation, grant `CREATEROLE`, grant runtime-role administration, or reset the volume. If the volume used custom credentials and its old container no longer exists, recover the exact credentials from backup before attempting the one-time installation.

Direct `docker compose` use intentionally has no credential defaults. Create an absolute owner-only directory containing `metadata_bootstrap_password`, `metadata_migration_password`, `metadata_schemii_password`, `metadata_schemer_password`, and `opencode_password` files in the single-line format above, set `SCHEMII_CREDENTIAL_DIR` to it, and then render or start Compose. Keep those files stable across restarts. Prefer the launchers because they also handle legacy detection, permissions, backup, restore, and rotation.

To remove only the included PostgreSQL database, stop the instance, remove only `<instance>_schemii-postgres`, and use explicit `ui` or `ai` mode afterward. The default launcher recreates and reseeds a missing included database.

## Uninstall Schemii

Back up anything important first. The uninstaller permanently removes verified Schemii instances owned by this repository, including their containers, default networks, Schemii designs and layouts, profiles and passwords, migration history, PostgreSQL data, provider credentials, chats, state volumes, and safely attributable project-scoped images. It then removes the repository containing the uninstall script.

It does not uninstall Docker and does not use broad Docker prune commands or remove unrelated Docker projects.

Linux or macOS:

```bash
bash ./uninstall.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

The script discovers both Schemii and Schemer-only projects from exact repository Compose labels, or orphaned instances from multiple correctly named and labeled persistent resources. It lists detected instances and requires typing `UNINSTALL`, then removes verified application containers, networks, volumes, attributable images, credentials, and the repository. It never inspects or changes an externally managed reverse proxy, TLS configuration, listener, route, hostname, or access policy; the operator must remove application routes from that external system separately. For deliberate unattended application removal, pass `--yes` on Linux/macOS or `-Yes` on PowerShell.

## PostgreSQL Connections

Open the PostgreSQL tool, create a connection, and use **Save & test** before selecting a namespace or introspecting.

| PostgreSQL location | Launch mode | Profile host |
| --- | --- | --- |
| Included tutorial container | Default or `docker-db` | `postgres` |
| Linux host bound to loopback | `local-db` or `ai-local-db` | `127.0.0.1` |
| Windows/macOS host through Docker Desktop | `ui` or `ai` | `host.docker.internal` |
| Existing container on the same private Docker network | Custom Compose override | Service name or network alias |
| Remote or managed PostgreSQL | Any bridge mode | Server DNS name or IP address |

Inside normal Docker bridge mode, `127.0.0.1` refers to the Schemii container, not the host. Base Compose does not add a Linux `host.docker.internal` mapping. The Linux-only `local-db` overrides preserve the profile host `127.0.0.1` by adding two capability-free nginx stream relays: one shares Schemii's network namespace and one exposes only a PostgreSQL Unix socket from host networking. No relay publishes a host TCP port.

For remote databases, prefer `sslmode=verify-full` with trusted certificates and use a narrowly privileged role. Inspection needs catalog and target-schema access. Migration apply additionally needs only the DDL privileges required by the reviewed plan.

### Included Database Settings

After the tutorial seed completes, the shared one-shot initializer creates the included profile before either application starts. Its evaluation defaults are:

| Field | Value |
| --- | --- |
| Host | `postgres` |
| Port | `5432` |
| Database | `schemii` |
| User | `schemii` |
| Password | `schemii-local` |
| SSL mode | `disable` |

To customize these values before the first database start:

Linux/macOS:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit `.env`, then run the normal Schemii launcher. Do not replace the launcher with a partial Compose command.

## Embedded AI Assistant

The default stack starts the pinned OpenCode sidecar and waits for its authenticated health check. Provider authentication, model selection, disclosure levels, confirmation boundaries, chat persistence, and limitations are documented in [`docs/AI_ASSISTANT.md`](docs/AI_ASSISTANT.md).

OpenCode runs in a read-only workspace with shell, filesystem, web, dynamic MCP, and unrelated tools denied. Proposal tools produce inert actions that the server replaces with expiring, context-bound, one-use proposal envelopes. Schemii validates actions and requires UI confirmation before writes, navigation, database contact, or migration workflows. AI can preview migrations, structured row insertion, and expected-absent ordinary-view creation, but only Schemii can issue each separate durable apply proposal. Inserts use parameterized structured values, and uncertain commits reconcile by PostgreSQL transaction ID rather than retrying.

## Troubleshooting

### Docker is not found

Install Docker using the operating-system link in [Install Docker](#install-docker), reopen the terminal, and run `docker version` and `docker compose version`.

### Docker is installed but unavailable

Start Docker Desktop or the Linux Docker service. If `docker info` reports permission denied on Linux, follow Docker's post-install instructions or configure rootless Docker.

### Startup fails

1. Read the launcher error immediately above the failure.
2. Confirm `docker info` and `docker compose version` work.
3. Rerun the same launcher command. A new instance chooses an installation-specific free UI port unless `SCHEMII_HOST_PORT` is fixed. An existing instance reuses its prior port; if another process took it, stop that process or set a new `SCHEMII_HOST_PORT`.
4. In Docker Desktop, inspect the containers under the launcher-printed instance.

First startup needs internet access to download missing pinned dependency images. Registry, proxy, DNS, or firewall failures can prevent image downloads.

### PostgreSQL connection fails

Confirm that the profile host matches the table in [PostgreSQL Connections](#postgresql-connections), then use **Test** in Schemii. Do not expose PostgreSQL to the LAN merely to make container networking work.

### Agent is unavailable

The default launcher includes OpenCode. Explicit `ui`, `local-db`, and `docker-db` modes do not. Rerun the default launcher and confirm the `opencode` container is healthy in Docker Desktop.

## Configuration

Most users do not need configuration. These launcher and Compose variables are the commonly useful overrides:

| Variable | Purpose |
| --- | --- |
| `SCHEMII_INSTANCE` | Stable lowercase instance name; keep unique between installations |
| `SCHEMII_HOST_PORT` | Fixed loopback browser port instead of automatic selection |
| `SCHEMER_HOST_PORT` | Fixed Schemer loopback port; the launcher otherwise selects and reuses an instance-specific free port, direct Compose defaults to `8081` |
| `SCHEMII_IMAGE` | Shared application image containing both `schemii` and `schemer` entry points; generated release source defaults to its embedded version-and-revision tag |
| `SCHEMII_NO_OPEN` | Set to `1` to suppress browser opening on Linux/macOS |
| `SCHEMII_PUBLIC_ORIGINS` | Optional comma-separated exact HTTPS origins admitted for Schemii through its trusted ingress peer; empty keeps loopback-only admission |
| `SCHEMER_PUBLIC_ORIGINS` | Optional comma-separated exact HTTPS origins admitted for Schemer through its trusted ingress peer; empty keeps loopback-only admission |
| `SCHEMII_POSTGRES_DB` | Included PostgreSQL database name |
| `SCHEMII_POSTGRES_USER` | Included PostgreSQL user |
| `SCHEMII_POSTGRES_PASSWORD` | Included PostgreSQL password |
| `SCHEMII_OPENCODE_TIMEOUT` | AI request timeout, default `300` seconds; accepted range `1`–`300` |
| `SCHEMII_METADATA_DSN` | Required native PostgreSQL metadata connection string; it must authenticate as `schemii_metadata_schemii` for Schemii or `schemii_metadata_schemer` for Schemer |
| `SCHEMII_METADATA_CONNECT_TIMEOUT` | Metadata connection timeout, default `5` seconds; accepted range `1`–`60` |
| `SCHEMII_METADATA_MAX_JSON_BYTES` | Metadata JSON payload ceiling, default and maximum `1048576` bytes |
| `SCHEMII_POSTGRES_GLOBAL_CAPACITY` | Process-wide PostgreSQL admission capacity; default `12` |
| `SCHEMII_POSTGRES_TARGET_CAPACITY` | Per exact-target admission capacity; default `4`, below global capacity |
| `SCHEMII_POSTGRES_CATALOG_CAPACITY` | Catalog execution class capacity; default `8` |
| `SCHEMII_POSTGRES_READ_CAPACITY` | Bounded-read execution class capacity; default `8` |
| `SCHEMII_POSTGRES_CONSOLE_CAPACITY` | Console execution class capacity; default `4` |
| `SCHEMII_POSTGRES_WRITE_CAPACITY` | Migration/write execution class capacity; default `1` |
| `SCHEMII_CONSOLE_TRANSACTION_MAXIMUM` | Active explicit Console transaction maximum; default `4`, maximum `64` |
| `SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS` | Explicit transaction idle connection-lifecycle expiry; default `300`, maximum `86400` |
| `SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS` | Explicit transaction absolute connection-lifecycle lifetime; default `1800`, maximum `604800` |
| `SCHEMII_MIGRATION_PLAN_TTL_SECONDS` | Durable migration-plan lifetime; default `900` seconds |
| `SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS` | Temporal-series manifest lifetime; default `300` seconds |

Native Schemii variables include `SCHEMII_HOST`, `SCHEMII_PORT`, `SCHEMII_CONFIG_DIR`, `SCHEMII_SCHEMA_DIR`, `SCHEMII_BEHIND_LOOPBACK_PROXY`, `SCHEMII_TRUSTED_LOCAL_PROXY`, and `SCHEMII_PUBLIC_ORIGINS`. Native Schemer variables include `SCHEMER_HOST`, `SCHEMER_PORT`, `SCHEMER_CONFIG_DIR`, `SCHEMER_DASHBOARD_DIR`, `SCHEMER_BEHIND_LOOPBACK_PROXY`, `SCHEMER_TRUSTED_LOCAL_PROXY`, `SCHEMER_PUBLIC_ORIGINS`, `SCHEMER_OPENCODE_URL`, `SCHEMER_OPENCODE_USERNAME`, `SCHEMER_OPENCODE_PASSWORD`, and `SCHEMER_OPENCODE_TIMEOUT`. Public origins require proxy mode and its trusted peer; the strict parser accepts only exact path-free HTTPS origins. Runtime metadata application names, application identities, login roles, database/`public` owner, `schemii_admin` owner, and protected-table RLS expectations are fixed by each application and cannot be weakened with native environment values. `SCHEMII_METADATA_APPLICATION_NAME` remains only an explicit migration/maintenance connection setting. `SCHEMER_DASHBOARD_DIR` defaults to `~/.local/share/schemer/dashboards`; the AI timeout defaults to 120 seconds and accepts `1`–`300`. `compose.schemer.ai.yaml` intentionally maps Schemer's OpenCode connection and timeout from shared `SCHEMII_OPENCODE_*` values. Both applications also read `SCHEMII_AI_MAINTENANCE_{INTERVAL,HEARTBEAT,LEASE,OPERATION_STALE,RESERVATION_STALE,DELIVERY_STALE,CLEANUP_RETENTION}_SECONDS`, `SCHEMII_AI_MAINTENANCE_RECOVERY_BATCH_SIZE`, and `SCHEMII_AI_MAINTENANCE_CLEANUP_BATCH_SIZE`; defaults are respectively `30`, `20`, `90`, `0`, `300`, `120`, `604800`, `100`, and `500`. Heartbeat must remain less than half the lease. The new capacity, TTL, and maintenance names are native process configuration; current checked-in Compose files do not forward host values for them automatically, so advanced Compose operators must add an explicit service-environment override for each enabled app. Admission capacities and response ceilings protect the process; durable Console settings and versioned AI bounds are separate user-owned restrictions; PostgreSQL role/database/session settings remain database policy.

Proxy mode also requires the corresponding exact lowercase `SCHEMII_TRUSTED_LOCAL_PROXY` or `SCHEMER_TRUSTED_LOCAL_PROXY` Docker DNS label. On every request the backend resolves that peer through Docker DNS and accepts exactly one resolved address; lookup failure, multiple addresses, a mismatched source, malformed configuration, duplicate or unknown forwarded headers, and mismatched public authorities fail closed. Native non-proxy loopback behavior remains unchanged and rejects forwarded headers.

Compose uses the official multi-architecture `nginx:1.29.1-alpine` image pinned by manifest-list digest. nginx supplies a mature HTTP/1.1 reverse-proxy implementation instead of repository-owned forwarding code: it preserves `Host`, `Origin`, response and download headers, disables response buffering for long-lived NDJSON, permits application-bounded bodies up to 20 MiB, and dynamically resolves the backend through Docker DNS. Each ingress runs read-only as UID 101 with all capabilities dropped, `no-new-privileges`, bounded tmpfs, and a full-path health check. It joins its internal app-ingress network plus a singleton non-internal bridge because Docker cannot publish a port from a container attached only to an internal network. Only these ingress services join the singleton bridges or publish `127.0.0.1`; backend containers publish no ports.

Direct Compose operation is advanced. It does not derive an instance, free port, or release identity. Always set a stable, unique `SCHEMII_INSTANCE`, choose collision-free `SCHEMII_HOST_PORT` and `SCHEMER_HOST_PORT` values for enabled applications, set `SCHEMII_CREDENTIAL_DIR` to the stable owner-only five-file credential directory, select the exact verified application/metadata/OpenCode image references, and include the complete file set for the intended mode. Schemer deliberately uses the same application image as Schemii. Prefer the launchers for routine Schemii installation, updates, and mode changes.

## Migration Safety

1. Select and verify the exact profile, database, and namespace.
2. Introspect first while preserving existing canvas layout.
3. Preview and review every SQL step, warning, lock, rewrite, and destructive operation.
4. Include destructive planning only when intended, then provide the separate apply confirmation.
5. Re-preview after any design, profile, namespace, or live-catalog change.
6. Back up important data and test risky changes against disposable or staging data first.

Migration apply, view mutation, and Mercury seed writes use the same database-local, namespace-scoped PostgreSQL advisory transaction lock. Schemii narrows lock waiting to five seconds only when PostgreSQL has no stricter nonzero `lock_timeout`; it installs no default statement timeout. Full-schema preview classifies every unresolved difference as blocking and returns `complete:false`, `applyCapable:false`, no durable plan ID, and a next action rather than offering a misleading safe subset. Apply requires an exact completeness proof over reviewed live and desired fingerprints, one transaction, stale profile/schema/layout/catalog guards, destructive review, and durable uncertain-commit reconciliation without replay. Preservation checks are scoped to affected tables and actual dependencies; partitioned tables and partitions remain introspectable but touched partition relationships are blocked for manual migration. Reconstruction proceeds only when the conservative touched-table inventory proves neutral state; Schemii does not claim unsupported reconstruction or one universal table/materialized-view manifest.

Apply-capable normal, view, and AI plans are UUID records in metadata PostgreSQL, not browser documents, process memory, or executable JSON files. Confirmation is persisted before target connection; target identity and `pg_current_xact_id()` are persisted before mutation; and the intended result is persisted before commit. A lost commit acknowledgement or interrupted `applying` execution is reconciled with `pg_xact_status` without replay. A committed transaction without a persisted intended result remains explicitly uncertain and requires manual inspection; it is never promoted to success automatically. PostgreSQL commit state remains `succeeded` when later saved-schema synchronization is pending, conflicted, or failed. Terminal private execution payloads have an explicit 30-day retention window and are then redacted by metadata cleanup. Deleted chats retain their complete operation authority, approval, attempt, outcome, usage, and result-delivery evidence until the configured chat-cleanup cutoff; one transaction then removes the chat-owned graph. Application-scoped authority transitions and shared immutable agent-policy revisions intentionally outlive chat cleanup, preserving audit and policy evidence without keeping result payloads or replayable operation state indefinitely.

## Developer Setup

End users do not need this section. Contributors need Python 3.10 or newer and Node.js in addition to Docker.

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Run the checks:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src
node --check src/schemii/web/app.js
node --check src/schemii/schemer_web/app.js
for test_file in tests/test_*.js; do node "$test_file" || exit 1; done
git diff --check
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests
python -m compileall -q src
node --check src/schemii/web/app.js
node --check src/schemii/schemer_web/app.js
Get-ChildItem tests/test_*.js | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
git diff --check
```

The opt-in live model contract test is documented in [`docs/AI_ASSISTANT.md`](docs/AI_ASSISTANT.md). Database integration tests must use disposable targets and leave no test objects or data behind.

## API

The browser uses same-origin local APIs for saved designs, dashboards, PostgreSQL, examples, AI, and shutdown. PostgreSQL, AI, example-restoration, and shutdown routes require a local origin plus the `X-Schemii-Token` returned by `/api/session`. The shared session client acquires this token lazily and retries one explicitly rejected session once. Schemii mounts profile, pageable catalog/lineage, schema-design, generic table-preview, read-SQL, all Console modes/settings/results/status, and migration/apply capabilities. Schemer mounts the same four human Console modes under independent Schemer write intent, plus profiles, catalog/lineage, verified relation preview, relation query/detail/temporal-series, and read-SQL; dashboard analytics and AI authority remain separately read-only and revision-bound, and Schemer does not expose schema introspection or migration/apply surfaces.

Console execution uses `POST /api/postgres/profiles/{profileId}/console/executions`, exact execution status receipts, cancellation, paged result resources, and explicit transaction resources. Managed read holds one repeatable-read read-only snapshot while its opaque result cursors remain open, then rolls back without rerunning SQL. Managed write commits the complete script atomically and spools returned rows before commit. Explicit mode keeps one capacity- and lifetime-bounded process-local transaction visible across requests until reviewed commit/rollback and closes retained results deterministically first. Autocommit/maintenance executes statements independently for PostgreSQL commands forbidden in transaction blocks and reports completed statement indexes plus `partial_committed` or uncertain outcomes. Durable settings store human write intent, default mode, statement limit, and row page size per application; execution binds their revision and the exact session/server/query/profile fingerprint/database/namespace. Durable pre-dispatch reservations reject execution-ID replay. Lost write responses are never automatically replayed; commit uncertainty requires reconciliation or manual target inspection. Result cursors are exact-owner, single-advance resources with a five-minute process-local lifetime; managed-read and explicit paging preserves the original snapshot, while committed/autocommit results use a bounded server spool. Browser JSON export drains the retained pages or spool within its TTL and operator caps; rows terminally omitted at a spool limit cannot be continued or exported, so continuation is not universal. PostgreSQL functions, sequences, and external systems may have nontransactional effects. Operator process ceilings and user settings remain distinct from PostgreSQL statement policy.

Schemer dashboard routes are `GET/POST /api/dashboards`, `GET/PUT/DELETE /api/dashboards/{id}`, revision-bound `POST /api/dashboards/legacy-sources/preview` and `POST /api/dashboards/legacy-sources/apply`, and revision-bound `POST /api/examples/mercury/reset`; dashboard deletion requires the current revision. `PUT /api/dashboards/{id}` accepts `{record,bindingAction}` and Mercury reset accepts `{expectedRevision,bindingAction}`, where `bindingAction` is `reject` or `remove`. Removing or replacing a slicer-bound widget/source therefore fails explicitly or removes the affected bindings in the same revision-guarded write. Legacy-source preview requires exact dashboard/revision/widget bindings and apply additionally requires the signed review digest plus `confirmed:true`; preview is read-only and apply re-inspects before one atomic compatible-only revision. Draft browser aggregate execution uses `dashboard-widgets/preview`; lower-level contextless execution remains on `relation/query` and `relation/detail` with complete verified caller snapshots. Persisted execution uses `saved-widgets/aggregate` and `saved-widgets/detail`, requiring dashboard ID/revision/widget ID while the server reconstructs the source, structured query, detail configuration, and visualization projection and composes applicable slicers. Temporal-series requests retain the exact saved line-widget check. Schema deletion similarly requires revision plus layout token. Profile deletion requires a fresh server impact preview and matching profile/dependency fingerprints; it reports but does not remove dependent schemas, dashboards, active chats, plans, or operations. Every API failure uses a structured `{error:{code,message,retryable?,details?}}` envelope. Schemer's separately confirmed AI analytic SQL executes through its exact chat/proposal operation, rejects stale target or dashboard bindings and `EXPLAIN`, and returns at most 100 rows, 50 columns, and 256 KiB of complete JSON values plus an opaque model-result reference. Both agent views cancel a running read through `DELETE /api/ai/sessions/{chatId}/proposals/{proposalId}/execution`; this is distinct from aborting the browser request. The general read-SQL route remains available to non-agent callers under its application-specific contract. Schema writes additionally use revision and layout-token checks.

Schemer retained pages use `GET /api/postgres/profiles/{profileId}/structured-results/{resultId}` with the opaque cursor returned in `resultResource` and send its binding only in `X-Schemer-Result-Binding`. JSON/CSV export adds `/export` with `format=json|csv`; `DELETE` with the same header explicitly cancels and releases the resource. Page and export access revalidate the exact PostgreSQL target/source and any dashboard revision or saved-widget authority carried by the original execution.

Legacy-source reviews bind the saved PostgreSQL profile-context fingerprint in addition to the exact dashboard, revision, widgets, relation fingerprints, and column snapshots. Confirmed apply revalidates that proof and changes only compatible source snapshots; titles, queries, presentations, detail reports, slicers, viewport, widget order, receipts, and incompatible widgets remain unchanged.

See `src/schemii/server.py`, `src/schemii/schemer_server.py`, `src/schemii/postgres_http.py`, and the focused server/HTTP contract tests for current routes. Do not expose these APIs beyond the verified local-ingress or host-Serve boundaries.

## Agent Instructions

An AI coding or terminal agent must read [`agent_guide.md`](agent_guide.md) and [`docs/AI_AGENT_SETUP.md`](docs/AI_AGENT_SETUP.md) before changing or operating Schemii. Saved-schema synchronization must follow [`.opencode/skills/preserve-schemii-layout/SKILL.md`](.opencode/skills/preserve-schemii-layout/SKILL.md).

## License

Schemii is released under the permissive [MIT License](LICENSE).
