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

1. Download the [Schemii source ZIP](https://github.com/LandMineDevelopment/schemii/archive/refs/heads/main.zip).
2. Extract it.
3. Open a terminal in the extracted `schemii-main` directory.

Keep the extracted directory in the same location. Its path identifies this Schemii installation and its Docker volumes.

### With Git

```bash
git clone https://github.com/LandMineDevelopment/schemii.git
cd schemii
```

## Start Schemii

Linux or macOS:

```bash
bash ./start.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The launcher checks Docker, builds the application, starts the complete private Compose stack, waits for health checks, and prints the local URL. It opens the browser automatically unless opening is disabled.

The first start downloads several container images and build dependencies. It requires internet access and may take several minutes. Later starts are normally much faster.

No account is required to start Schemii. Anonymous AI models may be available, but AI use can require provider authentication when anonymous models are unavailable. No model request is made until the user sends a chat message.

Schemii and Schemer require the dedicated metadata PostgreSQL schema to be current before either serves requests. Each app's `/api/readiness` reports metadata, optional OpenCode, observed target-database degradation, and PostgreSQL execution admission counters separately. Metadata is the only required readiness component; optional target and OpenCode degradation remains visible without taking static UI or saved-resource access out of service. Compose health checks use this endpoint. AI chat UUIDs, policies, grants, proposals, operation attempts, and bounded one-use query-result references are stored there; OpenCode session IDs are opaque server-only external references and titles are display-only. Legacy Schemii JSON chat authority and Schemer JSON/title authority are moved or marked idempotently under `retired-json-authority` and are never imported as executable records.

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

Only the Schemii UI is published to host loopback. Both PostgreSQL services and OpenCode are private in bridge modes. Linux host-network modes publish metadata PostgreSQL only to an instance-specific host-loopback port so the host-network Schemii process can reach it; it is never bound to the LAN.

### Other Modes

| Goal | Linux or macOS | Windows PowerShell |
| --- | --- | --- |
| Complete default stack | `bash ./start.sh` | `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| Local design only | `bash ./start.sh ui` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ui` |
| Tutorial PostgreSQL without AI | `bash ./start.sh docker-db` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode docker-db` |
| AI without included PostgreSQL | `bash ./start.sh ai` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ai` |
| Linux host PostgreSQL without AI | `bash ./start.sh local-db` | Not supported; use `ui` and `host.docker.internal` |
| Linux host PostgreSQL with AI | `bash ./start.sh ai-local-db` | Not supported; use `ai` and `host.docker.internal` |

Set `SCHEMII_NO_OPEN=1` on Linux/macOS or use `-NoOpen` on PowerShell to suppress browser opening.

## First Steps

The first default start creates two saved examples:

- **Mercury Books: PostgreSQL tutorial** is linked to the live `bookstore` namespace. Its nine tables include 80 books, 150 customers, 500 orders, and more than 1,200 linked order items for realistic exploration, alongside generated and identity columns, composite keys, checks, JSONB, B-tree and GIN indexes, functions, and a trigger. Tutorial v4 adds the canonical `book_catalog`, `order_summary`, `low_stock_books`, and `customer_order_totals` views plus the `monthly_sales` materialized view and its qualifying unique `sales_month` index. Reconciliation creates missing v4 objects and preserves recognized reserved objects whose definitions were modified; it skips dependent index restoration when a modified `monthly_sales` definition is incompatible. Upgrade caveat: a legacy-v3 `order_summary` with the old reserved comment but a modified definition is treated as a reserved-object collision and stops reconciliation rather than being overwritten.
- **Event Studio: Local design example** is a seven-table design that demonstrates local modeling, relationships, checks, indexes, composite keys, and SQL/JSON export without a database connection.

Use the folder button to switch designs, the disk button to save, and the PostgreSQL tool to inspect data or preview migration SQL. Click the Tables canvas and use the arrow keys to pan by one grid cell; hold Shift with an arrow to move four cells at a time. The four-page introduction can be reopened from the **?** menu.

### Schemii Views

The shared Tables / Views / SQL Console selector remains available across those workspaces. The left tool rail keeps Undo, Redo, fit/zoom, PostgreSQL sync, functions, and the AI assistant available while replacing table-only tools with Views or SQL actions. Views provides Browse, Create, Refresh, and permission-gated Delete controls. The shared Console in both applications provides managed read, managed all-or-nothing write transaction, explicit multi-request transaction, and autocommit/maintenance modes. Schemer's write-capable human Console is intentional and uses its own durable application-scoped write intent; it does not inherit Schemii intent or AI authority, and the selected PostgreSQL role remains authoritative for permissions. Human write intent, default mode, statement limit, and row page size are durable application-scoped settings with optimistic revisions, not expiring pseudo-permissions and not AI authority. Views opens a live view browser for the PostgreSQL target saved in the active schema record and is available only when that record has an exact `sourceProfileId`, `database`, and `namespace`, a current schema revision, and a current layout token. It loads ordinary and materialized views with ordered columns, bounded definitions, owner and advisory privileges, stable fingerprints, pageable direct lineage across namespaces, and materialized population/concurrent-refresh eligibility. One focusable graphical canvas renders verified physical sources, query-local CTE and derived-table stages where applicable, exactly one real outer-SELECT `query_block`, the final view, and verified consumers. The root query-block card owns that SELECT's verified inputs and aliases, complete join conditions and partial reasons, filters, and selected projection summary. A physical source never bypasses an available root query block, CTEs and derived tables are not temporary tables, and syntactic dependency order is not PostgreSQL runtime execution order. High-contrast destination arrowheads and segment-local labels make direction explicit. Every source, stage, query block, final view, and consumer has a titled drag surface. Deterministic fallback columns and balanced lanes are never saved automatically; persisted Views positions and viewport remain authoritative and isolated from Tables layout. Source, verified join, query-block, and output focus modes emphasize only endpoint-verified paths while retaining unrelated context, and Clear focus or an empty-canvas click removes emphasis without moving or saving the canvas. Foreign tables are first-class read/catalog sources and may appear in lineage, although the dedicated Views mutation lifecycle remains limited to ordinary and materialized views.

An imported table's inspector also provides a table-prefilled, permanently read-only Console. Its single Run action executes highlighted SQL or, when there is no selection, the statement under the cursor. It uses the same revision-bound exact-target managed-read execution, cancellation, retained-result paging, export, and persistent PostgreSQL diagnostics as the standalone SQL workspace. Results land in the existing Data view, which temporarily becomes the Console results drawer; Clear releases those results and restores the live table preview. Its gold accent indicates that it remains scoped to the Tables workspace, and it cannot enable writes.

Creating, editing, or deleting a view always uses Schemii's dedicated preview and opaque-plan apply APIs; it is not sent through the Console or the full-schema migration route. Ordinary views support `CREATE VIEW`, PostgreSQL-validated `CREATE OR REPLACE VIEW`, and reviewed deletion. Materialized views support creation, reviewed transactional recreation, and deletion. Recreation warns that stored rows are discarded and repopulated when the view was previously populated; deletion warns that the relation and all rows stored in it are permanently removed. Source-table rows are never deleted by these operations. Kind conversion remains unsupported, no generated step uses `CASCADE`, and direct dependents block destructive operations. There is no materialized-view refresh endpoint or refresh control yet. Apply runs in one transaction with the saved role, namespace advisory locking, narrowed lock waiting, inherited PostgreSQL statement policy, profile/relation stale checks, and target locking before authoritative reinspection. PostgreSQL validates ordinary replacement output compatibility during apply; Schemii does not claim to reconstruct or pre-detect every unsupported object or output rename, reorder, removal, or type change.

After PostgreSQL commits, Schemii narrowly appends a deterministic saved item for creation, updates the exact stable saved item for replacement, or removes that semantic item for deletion. It preserves custom fields where applicable, all unrelated schema content, and the complete parsed layout, including any layout object associated with a deleted view. If exact identity/revision reconciliation or storage fails after commit, the response reports the PostgreSQL commit separately from a `schemaSync` conflict/storage error; the browser refreshes and never retries the DDL plan.

Deleting an example remains respected across restarts. Use **? > Restore examples** to reinstall missing examples. Existing saved designs and layouts are not replaced. In included-database mode, restoration can refresh the reserved tutorial connection password from current `.env` settings, so re-preview any open migration afterward.

### Schemer Dashboard Workspace

Schemer is not currently included in `start.sh` or `start.ps1`. Enable it with the complete advanced Compose file set shown below; direct Compose uses fixed loopback ports and does not perform the launchers' free-port selection.

Schemer is an analytics workspace served separately from Schemii while reusing the same Python `PostgresService`, capability-scoped PostgreSQL HTTP router, authenticated browser session client, PostgreSQL browser client, profile form/repository contracts, profile store, visual tokens, SVG icon registry, icon-button factory, delegated tooltip behavior, status controls, loading controls, and menu behavior. Common actions such as Close are instantiated from the same shared component rather than copied between apps. The bundled Mercury dashboard contains six functioning widgets backed by the live `bookstore.order_summary` view; it does not embed preview values or rows. **Restore Mercury demo** rebuilds those definitions from the verified included profile while preserving existing widget layouts, viewport, and unrelated custom widgets. Dashboard widgets render as uniform responsive tiles. Clicking a tile expands it from its dashboard position into an app-wide detail view using the widget's own header. Activating a KPI, chart mark, or table row opens its live detail report with the matching filters. `View SQL` shows a readable, copyable equivalent with bound values populated while server execution remains parameterized. Edit mode supports persisted, animated drag-and-drop swaps when the dragged widget center overlaps another widget, keyboard-accessible earlier/later movement, widget creation, duplication, and deletion without freeform positioning or resizing.

Schemii and Schemer use the same responsive introduction shell, server-start/opt-out policy, page controller, reduced-motion playback rules, and animated cursor implementation. Each application shows its introduction once after a new local server process starts, not again on an ordinary browser refresh. Selecting **Do not show on future server starts** disables that application's automatic introduction for the current browser origin; **Show introduction** in the application menu remains available and allows the option to be cleared. Schemer's four read-only animated pages demonstrate creating a dashboard, entering Edit mode and adding a widget, assigning a verified source and applying a basic visualization, then opening the widget and a chart mark's detail report. The demonstrations use synthetic local markup and never create dashboards, save widgets, execute PostgreSQL queries, or alter user-owned layout.

Dashboard management includes create, open/switch, rename, duplicate, archive/unarchive, active/archived filtering, and delete workflows. Dashboard and widget edits autosave with revision checks; navigation stops when pending persistence fails instead of discarding local work. A dashboard is limited to 100 widgets. New manual widgets may begin as unconfigured placeholders and gain live data only after a verified source and structured query are applied. Confirmed AI dashboard/widget mutation proposals execute through Schemer's durable operation authority and dashboard-owned idempotent receipts while preserving unrelated layout and configuration. The visible date-range control is disabled because dashboard slicers and date-range filtering remain deferred.

The Data sources dialog manages shared PostgreSQL connection profiles only. In dashboard Edit mode, each tile has an **Edit** action that opens that widget's configuration editor, where its name can be edited independently of source or query setup. Names and source assignments save automatically; query drafts remain local until **Apply query & run** succeeds. Connected keyboard-navigable tabs provide separate Source, Visualization, Filters, Sort & Limit, and Detail Report views inside one scrolling content pane. The Source view browses tables, partitioned tables, views, materialized views, and foreign tables for that widget alone. Namespace and relation catalogs use exact-target, filter-, sort-, page-size-, profile-, and catalog-fingerprint-bound opaque keyset cursors; system namespaces require explicit `scope=all` opt-in and carry system classifications. The server verifies `current_database()` before returning identities and rejects changed profiles, databases, catalogs, and cursor contexts. Selecting a relation loads its normalized kind, ordered semantic columns, PostgreSQL display types, nullability, full deterministic relation fingerprint, and catalog-derived type/operator/aggregate capabilities. New version-2 source snapshots persist those capabilities; legacy version-1 snapshots remain display-compatible but must be explicitly reselected before structured query editing or execution. The Sort & Limit view accepts multiple result fields and preserves their top-to-bottom priority in generated `ORDER BY`. Narrow layouts retain dashboard switching and creation controls, and reduced-motion preferences suppress workspace transitions.

In a widget editor, a verified relation can be assigned only to that widget. Version-1 dashboards remain compatible with empty widget configurations, while sourced widgets persist exactly one `source` object containing profile, database, namespace, relation, kind, and fingerprint. A configured aggregate stores a version-2 `query` with ordered `dimensions`, one or more ordered `measures`, parameterized filter groups, `sort`, and a row `limit`. Conditions inside a filter group are combined with `AND`; groups are combined with `OR`. Available operators follow the PostgreSQL column type: ordered comparisons and `between` for numbers and dates, text matching for character columns, equality for booleans, and null checks where broader comparison is unsupported. Date and timestamp values use typeable, Schemer-themed calendar controls that close on outside click or Escape, while `between` provides explicit From and To bounds. Existing version-1 flat filters load as one AND group. Measures support row count, column count, count distinct, sum, average, minimum, and maximum with stable IDs and lineage. The validator rejects source arrays, joins, caller SQL, incomplete identities, unsupported kinds, malformed fingerprints, stale or unknown fields, invalid operator/type or aggregate/type combinations, and unknown configuration fields. Assignments can be cleared, and sourced tiles display their exact database, namespace, and relation.

Applying a query creates a first-class Aggregate Report widget. Its versioned `table` presentation lists every dimension and measure target without changing generated SQL. Dimensions always render before measures, while each group can be reordered independently. Presentation settings persist display labels, widths from 64 to 1024 pixels, hidden state, left pinning, and a bounded page size of 10, 25, 50, or 100 rows. Hidden columns remain in both the query and presentation record, so showing them again restores their configuration. Pagination operates only over the server-bounded aggregate response; truncation remains visible when the query limit excludes additional groups. Aggregate rows and measure cells retain source-column, filter-group, dimension-value, and measure lineage hooks for the Phase 7 drill-through drawer. Pivots, subtotals, and grand totals remain intentionally deferred.

Aggregate Report editors place table, KPI, grouped bar, line, and donut controls on the Visualization tab. The selector includes a decorative, explicitly data-free sample of the selected mode. Each mode exposes bespoke role blocks: table uses all configured dimensions and measures, KPI uses no dimensions and one or more measures, bar and line use one dimension and one or more measures, and donut uses one dimension and one measure. Compatible roles carry forward when the mode changes; narrower modes truncate only their active selection while retained query fields remain available to wider modes. Empty or invalid required blocks receive a stronger highlight. Execution projects the authoritative query to the active mode's roles, so KPI can run ungrouped without deleting chart dimensions and donut can use one measure without deleting the others. An optional version-1 `visualization` presentation stores these per-mode selections. Existing reports without this object continue in table mode. Measure formatting shows currency and decimal-place inputs only when the selected number format uses them. Chart marks preserve query lineage and each chart includes a keyboard-accessible data table. Expanded line charts backed by a PostgreSQL date or timestamp use a proportional UTC timeline rather than equal row spacing. A server manifest selects a fixed resolution that keeps the complete filtered domain within the widget's saved result limit; the browser then lazy-loads aligned half-open time windows as they enter the horizontal viewport and caches every loaded window until refresh, source/query change, revision change, or dashboard navigation. Scrolling back reuses browser-cached points without querying PostgreSQL, every cached point remains selectable, and unloaded windows remain visual gaps rather than invented connections. Selecting a bucket drills through its complete half-open UTC range instead of incorrectly matching only the bucket-start value. Visualization and query edits remain local drafts until **Apply query & run** verifies the projected query and saves the dashboard. Dashboard tiles display only the resulting visualization, keeping configuration controls inside the widget editor.

Each Aggregate Report also stores a version-1 detail-report presentation tied to the same verified source relation. It configures ordered source columns, labels, widths, visibility, number formats, default sort, row identifier, and a page size capped at 100. Activating a KPI value, chart mark, aggregate row, or measure cell opens a shared vertical workspace with the widget filters, clicked dimension values, optional measure lineage, matching-row count, live query time, bounded server pagination, sorting, and search actions on every column header. A search action smoothly widens its column and reveals the input beside the field name. Only one input expands at a time; opening another shrinks the previous column and preserves its active value as a compact header badge. Multiple column searches combine with `AND`, debounce while typing, reset pagination, cast the selected PostgreSQL values to text for matching, and keep every search value parameterized. Either pane header swaps which pane is expanded while leaving the other available as a compact header. The server re-verifies the exact database, relation kind, fingerprint, and live columns, then executes parameterized count and page statements in one read-only repeatable-read transaction. The configured row identifier provides a stable tie-breaker for sorted pages. Detail SQL and bound parameters remain separately inspectable from the detail header. Dashboard slicers, exports, record editing, joins, and cross-widget drill-through remain deferred.

Every sourced widget and active detail report includes a **Data Lineage** action. One reusable dialog shows the redacted profile label and ID, exact database/namespace/relation identity, relation kind, fingerprint, ordered PostgreSQL columns, verification state, widget filters, clicked dimensions, selected measure, column searches and sort, query duration, row counts, truncation, and refresh time. Views and materialized views show their bounded PostgreSQL query definition when available; tables show their authoritative ordered catalog columns and explicitly state that PostgreSQL does not expose one complete creation statement. Aggregation SQL, detail-page SQL, detail-count SQL, and each parameter list remain separate. Copy controls operate only on the explicitly selected statement or parameter JSON and never interpolate values or include connection credentials. Dashboard slicers are shown as not applied while Phase 6 remains deferred. Read-only `EXPLAIN` remains a separately approved future extension.

Schemer can use the same private OpenCode service and provider subscriptions as Schemii while retaining its own `/workspace-schemer` chat history, instructions, skills, and proposal tools. Provider API keys and OAuth/subscription credentials remain in the existing `schemii-opencode-data` volume, so connecting through either app makes that provider available to both without credential re-entry. Both apps use the same left-side assistant drawer, activity timeline, reasoning/tool cards, provider settings, and history controls. A confirmed proposal card displays a visible running state and live elapsed timer, then retains the final success, failure, or cancellation duration. Running AI query cards expose **Stop**; the server durably binds the cancellation to the exact proposal, signals the active PostgreSQL connection, and waits for read-only rollback. History and proposals are server-bound to the exact application, chat, resource, disclosure level, saved revision, and data target. Confirmed actions create persistent, idempotent operation records so retries and reconnects observe one execution. Metadata and dashboard disclosure modes remain row-free. Data mode requires an exact profile, database, and namespace to enable the inert `schemer_read_query` proposal tool; every query requires browser confirmation and only a server-owned, bounded, one-use result reference is returned to the model. Schemer may also propose opening an exact listed dashboard and creating or changing dashboards/widgets through its dashboard-owned executor. Analytic SQL may join relations when necessary, while persisted widget configuration remains single-relation and caller-SQL-free.

Schemer verifies every persisted widget source against live PostgreSQL when a dashboard opens and when the catalog is refreshed. Refresh uses the lightweight exact-database relation listing rather than Schemii's full namespace introspection, then verifies each saved relation kind, fingerprint, and column snapshot. Mismatches return `relation_changed` and block the widget rather than silently adopting new metadata. Missing or unreachable sources are also blocked. The strict singular source shape has no join or cross-relation column-reference fields, and the dashboard validator rejects attempts to add them.

Relation columns include advisory role suggestions derived from PostgreSQL type categories. Numeric values are suggested as measures, temporal values as dates, text/enums/booleans as dimensions, and UUID or conservatively named `id`/`*_id` values as identifiers. Suggestions are displayed as labels only: they are not persisted, do not select a role, and are excluded from relation fingerprints.

The relation detail pane can request a 20-row source preview. The dedicated preview API requires the complete verified source identity, rechecks kind and fingerprint in the same read-only transaction used for selection, inherits PostgreSQL statement policy, quotes every identifier, selects only verified columns from one relation, parameterizes offset and limit, and caps requests at 50 rows. It never accepts joins or caller SQL; preview order is explicitly reported as unstable.

New source assignments persist a version-2 semantic column snapshot containing name, PostgreSQL display type, nullability, ordinal, and fingerprinted catalog-derived capabilities. Live verification compares that snapshot with PostgreSQL and reports missing relations plus named missing, added, and changed columns. Legacy version-1 snapshots remain readable but require explicit source reselection to acquire current capabilities before structured queries run. Changed sources stay blocked until the user reselects the live relation; Schemer never rewrites a saved fingerprint or snapshot automatically.

Aggregate execution uses a dedicated endpoint that accepts one complete verified relation snapshot and a validated single-relation structured query model, including unsaved drafts that must run successfully before persistence. Schemer browser requests include dashboard ID/revision context so stale-dashboard execution is rejected; the endpoint also supports the base source/query request without dashboard context, so that guard is not a universal API boundary. Relation identity, kind, fingerprint, columns, profile database, and query fields are revalidated by the service, but standard aggregate and detail requests do not claim server reconstruction from a saved widget ID. The server quotes all identifiers, binds every filter and limit value, acquires an access-share lock before taking the catalog snapshot, and executes in a repeatable-read, read-only transaction with a statement timeout and a 500-row maximum. Generated SQL is formatted across clauses and grouped predicates so `View SQL` mirrors the AND/OR structure. Responses include a bounded generic result table, truncation state, exact generated SQL, bound parameters, and dimension/measure/filter-group lineage. The temporal-series companion route has the stronger saved-widget contract: it accepts only strict manifest or aligned-window requests for one verified date/timestamp dimension and up to eight measures, requires the exact saved line widget ID, and verifies its source plus server-reconstructed visualization projection while holding the dashboard revision guard. It interprets PostgreSQL `date` and timestamp-without-time-zone values as UTC, preserves timestamp-with-time-zone instants, applies saved filter groups to both manifest and windows, forces chronological bucket order, and rejects expired HMAC-signed manifests, stale refresh generations, misaligned ranges, unknown fields, or windows denser than one row per server-derived bucket. Each request remains independently read-only and repeatable-read; cached windows are refresh-coherent but are not claimed to share one long-lived PostgreSQL snapshot. Schemer does not accept joins or caller-authored SQL through these endpoints.

In the preceding analytics contract, "with a statement timeout" means the selected PostgreSQL role/database/session setting; Schemer does not install an application default.

Set a stable direct-Compose instance and collision-free ports before running either command. In a POSIX shell:

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
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.schemer.yaml up --build -d
```

To run both applications with one shared OpenCode service, set `SCHEMII_CREDENTIAL_DIR` to the absolute owner-only five-file credential directory described below and include both AI overrides:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.ai.yaml -f compose.schemer.yaml -f compose.schemer.ai.yaml up --build -d
```

With the example overrides above, open Schemii at `http://127.0.0.1:18080/` and Schemer at `http://127.0.0.1:18081/`; without overrides, direct Compose defaults to ports 8080 and 8081. The applications share the dedicated metadata service but connect as distinct runtime roles; `metadata-migrate` must finish successfully before either application starts. Saved PostgreSQL profiles are shared through the same instance-scoped `schemii-config` volume; passwords remain server-side and are never returned to either browser. Versioned dashboard records are stored separately in the owner-only `schemer-dashboards` volume and survive container replacement or restart. Deleting that volume permanently deletes the saved dashboards. Direct native launches use `SCHEMER_DASHBOARD_DIR`, which defaults to `~/.local/share/schemer/dashboards`.

Schemer saves edits automatically using revision checks. If another browser tab saves the same dashboard first, the stale tab is blocked rather than overwriting the newer record and must reload the current dashboard.

## Everyday Use

Rerun the same launcher command to start or update an installation. The launcher reuses its saved designs, profiles, database, AI credentials, and chats.

The launcher prints an **Instance** name and URL. Separate installation directories receive separate instance names, ports, containers, images, and volumes. Do not move or rename an installation directory unless you intentionally want a new derived instance or have set a stable `SCHEMII_INSTANCE` environment variable.

When upgrading an older installation that has legacy volumes but no remaining container, the launcher stops instead of opening an empty-looking instance. Follow its displayed command to reuse the legacy `schemii` data, or choose a unique `SCHEMII_INSTANCE` for a separate installation.

Use **? > Shut down Schemii** to save pending design changes and stop the UI process. PostgreSQL and OpenCode may remain running so the next UI start is fast. To stop every container, use Docker Desktop's Containers view, or stop containers with the printed instance label:

```bash
docker stop $(docker ps -q --filter "label=com.docker.compose.project=<instance>")
```

PowerShell:

```powershell
docker ps -q --filter "label=com.docker.compose.project=<instance>" | ForEach-Object { docker stop $_ }
```

Starting Schemii again restores those containers without deleting data.

### Update A Git Checkout

```bash
git pull --ff-only
bash ./start.sh
```

PowerShell:

```powershell
git pull --ff-only
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

For a ZIP installation, extract the new files over the same installation directory and rerun the launcher. Back up important data first.

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

List the exact volumes for the launcher-printed instance:

```bash
docker volume ls --filter "label=com.docker.compose.project=<instance>"
```

Back up the config, schemas, Schemer dashboards, both PostgreSQL databases, and non-cache OpenCode volumes before upgrades or migration work. Use `pg_dump` for important PostgreSQL data. Metadata can contain sensitive authority history and transient query-result payloads, so protect and retain its backups separately from user target backups.

The launchers generate five cryptographically random credentials per instance: one metadata bootstrap initialization secret, three metadata PostgreSQL login-role passwords, and one internal OpenCode password. Every credential file has one optional LF newline after a single value of 16-256 characters from `[A-Za-z0-9_-]`; all launchers, container entrypoints, and server-side readers enforce that same format, while the metadata rotation function validates the three database passwords it accepts. They persist outside the repository at `$XDG_DATA_HOME/schemii/credentials/<instance>` (defaulting to `~/.local/share/schemii/credentials/<instance>`) on Linux/macOS and `%LOCALAPPDATA%\Schemii\credentials\<instance>` on Windows. Directories are restricted to the owner and files to the owner on POSIX. On Windows, the launcher removes inheritance and applies and verifies owner/current-user-only ACLs recursively on reused and new credential content, transaction staging, and backups; an ACL application or verification error stops the operation. Container entrypoints briefly retain `CHOWN`, `DAC_OVERRIDE`, `SETGID`, `SETPCAP`, and `SETUID` to copy owner-only mounted files, drop to the application UID, and clear the capability bounding set before the application starts. Do not commit, print, email, or include this directory in an unencrypted general-purpose backup.

Database backup on Linux/macOS, using the printed instance:

```bash
postgres_id=$(docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres")
docker exec "$postgres_id" pg_dump -U schemii -d schemii > schemii-postgres.sql
```

PowerShell:

```powershell
$postgresId = docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres"
docker exec $postgresId pg_dump -U schemii -d schemii > schemii-postgres.sql
```

If `.env` changes the user or database, substitute those values. Back up metadata with `pg_dump` through the local socket as the container's PostgreSQL operating-system user; do not expose its port merely to perform a backup:

```bash
metadata_id=$(docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=metadata-postgres")
docker exec -u postgres "$metadata_id" pg_dump -d schemii_metadata --format=custom > schemii-metadata.dump
```

Restore only into a stopped, reviewed instance after backing up its current state. Start only `metadata-postgres`, inspect the archive with `pg_restore --list schemii-metadata.dump`, and confirm its owners are the expected `schemii_metadata_owner` and bootstrap-owned administration objects. Then run `docker exec -i -u postgres "$metadata_id" pg_restore --clean --if-exists --exit-on-error -d schemii_metadata < schemii-metadata.dump`; do not use `--no-owner`, because metadata restore must preserve archived ownership. Rerun the normal launcher, verify `/api/readiness`, and query `pg_class`, `pg_proc`, `pg_namespace`, and `aclexplode` to confirm expected owners and ACLs, including the bootstrap-owned `SECURITY DEFINER` rotation function, no public execute privilege, and only migration-role execute access. A restore replaces metadata authority history; it does not restore designs, dashboards, target PostgreSQL, or OpenCode data.

To archive a stopped named volume, repeat this command for `<instance>_schemii-config`, `<instance>_schemii-schemas`, `<instance>_schemer-dashboards` when present, `<instance>_schemii-opencode-data`, `<instance>_schemii-opencode-config`, and `<instance>_schemii-opencode-state`:

```bash
docker run --rm -v <volume-name>:/source:ro -v "$PWD":/backup alpine:3.20 tar -czf /backup/<volume-name>.tgz -C /source .
```

On PowerShell, replace `"$PWD"` with an absolute directory accepted by Docker Desktop. Keep backups outside the installation directory before replacing source files.

Never run `docker compose down --volumes` or remove project volumes unless permanent deletion is intended. Doing so can delete saved designs, Schemer dashboards and widget layouts, profiles and passwords, migration history, PostgreSQL data, provider credentials, chats, and AI state.

Back up, restore, or rotate the instance credential set with `bash ./start.sh credentials-backup <protected-directory>`, `bash ./start.sh credentials-restore <protected-directory>`, and `bash ./start.sh credentials-rotate`. PowerShell equivalents use `-Mode credentials-backup -Path <directory>`, `-Mode credentials-restore -Path <directory>`, and `-Mode credentials-rotate`. Backup output contains plaintext credentials, and restore requires its `instance` marker to exactly match the selected instance. One instance-scoped cross-process lock serializes initialization, interrupted-transaction cleanup and recovery, backup, restore, and rotation; it is released before normal long-running Compose startup. PowerShell uses an exclusive OS file handle, which the OS releases after a crash. POSIX uses an atomic owner-PID lock directory, removes locks whose owner process has exited, and times out rather than waiting forever. Rotation and restore stage old and new sets in an owner-only transaction directory, wait for PostgreSQL readiness before the first password update, update PostgreSQL through the migration login and narrow `SECURITY DEFINER` function, replace active file contents without changing the file identities mounted by existing containers, restart consumers, wait again, and verify the resulting migration login. A failure triggers rollback of PostgreSQL, files, and restarts; if the process is interrupted, the next launcher run resumes deterministic rollback while retaining both sets for manual recovery if automatic recovery cannot authenticate. Back up metadata and credentials together before rotation.

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

The script discovers both Schemii and Schemer-only projects from exact repository Compose labels, or orphaned instances from multiple correctly named and labeled persistent resources. It lists detected instances and requires typing `UNINSTALL`. Before removal it rechecks every resource's project and logical-resource labels. It removes `schemer-dashboards`, only project-scoped images proven to have been used solely by verified instance containers, and only a credential directory whose `instance` marker exactly matches the detected project. Shared global tags such as `schemer:local` are retained. Docker must be installed and running so the script can verify resource removal before deleting the repository. For deliberate unattended use, pass `--yes` on Linux/macOS or `-Yes` on PowerShell.

## PostgreSQL Connections

Open the PostgreSQL tool, create a connection, and use **Save & test** before selecting a namespace or introspecting.

| PostgreSQL location | Launch mode | Profile host |
| --- | --- | --- |
| Included tutorial container | Default or `docker-db` | `postgres` |
| Linux host bound to loopback | `local-db` or `ai-local-db` | `127.0.0.1` |
| Windows/macOS host through Docker Desktop | `ui` or `ai` | `host.docker.internal` |
| Existing container on the same private Docker network | Custom Compose override | Service name or network alias |
| Remote or managed PostgreSQL | Any bridge mode | Server DNS name or IP address |

Inside normal Docker bridge mode, `127.0.0.1` refers to the Schemii container, not the host. Base Compose does not add a Linux `host.docker.internal` mapping; use a Linux host-network mode for a loopback-bound Linux PostgreSQL server.

For remote databases, prefer `sslmode=verify-full` with trusted certificates and use a narrowly privileged role. Inspection needs catalog and target-schema access. Migration apply additionally needs only the DDL privileges required by the reviewed plan.

### Included Database Settings

The included profile is created automatically. Its evaluation defaults are:

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

First startup needs internet access to download images and packages. Registry, proxy, DNS, or firewall failures can prevent image downloads.

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
| `SCHEMER_HOST_PORT` | Fixed Schemer loopback port for advanced Compose; default `8081` |
| `SCHEMER_IMAGE` | Schemer image name for advanced Compose; default `schemer:local` |
| `SCHEMII_NO_OPEN` | Set to `1` to suppress browser opening on Linux/macOS |
| `SCHEMII_POSTGRES_DB` | Included PostgreSQL database name |
| `SCHEMII_POSTGRES_USER` | Included PostgreSQL user |
| `SCHEMII_POSTGRES_PASSWORD` | Included PostgreSQL password |
| `SCHEMII_OPENCODE_TIMEOUT` | AI request timeout, default `300` seconds; accepted range `1`–`300` |
| `SCHEMII_METADATA_DSN` | Required native PostgreSQL metadata connection string; Compose supplies the scoped runtime role |
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

Native Schemii variables include `SCHEMII_HOST`, `SCHEMII_PORT`, `SCHEMII_CONFIG_DIR`, `SCHEMII_SCHEMA_DIR`, `SCHEMII_BEHIND_LOOPBACK_PROXY`, and `SCHEMII_METADATA_APPLICATION_NAME`. Native Schemer variables include `SCHEMER_HOST`, `SCHEMER_PORT`, `SCHEMER_CONFIG_DIR`, `SCHEMER_DASHBOARD_DIR`, `SCHEMER_BEHIND_LOOPBACK_PROXY`, `SCHEMER_OPENCODE_URL`, `SCHEMER_OPENCODE_USERNAME`, `SCHEMER_OPENCODE_PASSWORD`, and `SCHEMER_OPENCODE_TIMEOUT`. `SCHEMER_DASHBOARD_DIR` defaults to `~/.local/share/schemer/dashboards`; the AI timeout defaults to 120 seconds and accepts `1`–`300`. `compose.schemer.ai.yaml` intentionally maps Schemer's OpenCode connection and timeout from shared `SCHEMII_OPENCODE_*` values. Both applications also read `SCHEMII_AI_MAINTENANCE_{INTERVAL,HEARTBEAT,LEASE,OPERATION_STALE,RESERVATION_STALE,DELIVERY_STALE,CLEANUP_RETENTION}_SECONDS`, `SCHEMII_AI_MAINTENANCE_RECOVERY_BATCH_SIZE`, and `SCHEMII_AI_MAINTENANCE_CLEANUP_BATCH_SIZE`; defaults are respectively `30`, `20`, `90`, `0`, `300`, `120`, `604800`, `100`, and `500`. Heartbeat must remain less than half the lease. The new capacity, TTL, and maintenance names are native process configuration; current checked-in Compose files do not forward host values for them automatically, so advanced Compose operators must add an explicit service-environment override for each enabled app. Admission capacities and response ceilings protect the process; durable Console settings and versioned AI bounds are separate user-owned restrictions; PostgreSQL role/database/session settings remain database policy.

Direct Compose operation is advanced. It does not derive an instance or free port. Always set a stable, unique `SCHEMII_INSTANCE`, choose collision-free `SCHEMII_HOST_PORT` and `SCHEMER_HOST_PORT` values for enabled applications, set `SCHEMII_CREDENTIAL_DIR` to the stable owner-only five-file credential directory, and include the complete file set for the intended mode. Set distinct image names with `SCHEMII_IMAGE` and `SCHEMER_IMAGE` when projects should not share build tags. Prefer the launchers for routine Schemii installation, updates, and mode changes.

## Migration Safety

1. Select and verify the exact profile, database, and namespace.
2. Introspect first while preserving existing canvas layout.
3. Preview and review every SQL step, warning, lock, rewrite, and destructive operation.
4. Include destructive planning only when intended, then provide the separate apply confirmation.
5. Re-preview after any design, profile, namespace, or live-catalog change.
6. Back up important data and test risky changes against disposable or staging data first.

Migration apply, view mutation, and Mercury seed writes use the same database-local, namespace-scoped PostgreSQL advisory transaction lock. Schemii narrows lock waiting to five seconds only when PostgreSQL has no stricter nonzero `lock_timeout`; it installs no default statement timeout. Full-schema preview classifies every unresolved difference as blocking and returns `complete:false`, `applyCapable:false`, no durable plan ID, and a next action rather than offering a misleading safe subset. Apply requires an exact completeness proof over reviewed live and desired fingerprints, one transaction, stale profile/schema/layout/catalog guards, destructive review, and durable uncertain-commit reconciliation without replay. Preservation checks are scoped to affected tables and actual dependencies; partitioned tables and partitions remain introspectable but touched partition relationships are blocked for manual migration. Reconstruction proceeds only when the conservative touched-table inventory proves neutral state; Schemii does not claim unsupported reconstruction or one universal table/materialized-view manifest.

Apply-capable normal, view, and AI plans are UUID records in metadata PostgreSQL, not browser documents, process memory, or executable JSON files. Confirmation is persisted before target connection; target identity and `pg_current_xact_id()` are persisted before mutation; and the intended result is persisted before commit. A lost commit acknowledgement or interrupted `applying` execution is reconciled with `pg_xact_status` without replay. A committed transaction without a persisted intended result remains explicitly uncertain and requires manual inspection; it is never promoted to success automatically. PostgreSQL commit state remains `succeeded` when later saved-schema synchronization is pending, conflicted, or failed. Terminal private execution payloads have an explicit 30-day retention window and are then redacted by metadata cleanup.

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

Schemer dashboard routes are `GET/POST /api/dashboards`, `GET/PUT/DELETE /api/dashboards/{id}`, and revision-bound `POST /api/examples/mercury/reset`; dashboard deletion requires the current revision. Draft aggregate/detail execution remains on `relation/query` and `relation/detail` with complete verified caller snapshots. Persisted execution uses `saved-widgets/aggregate` and `saved-widgets/detail`, requiring dashboard ID/revision/widget ID while the server reconstructs the source, structured query, detail configuration, and visualization projection. Temporal-series requests retain the exact saved line-widget check. Schema deletion similarly requires revision plus layout token. Profile deletion requires a fresh server impact preview and matching profile/dependency fingerprints; it reports but does not remove dependent schemas, dashboards, active chats, plans, or operations. Every API failure uses a structured `{error:{code,message,retryable?,details?}}` envelope. Schemer's separately confirmed AI analytic SQL executes through its exact chat/proposal operation, rejects stale target or dashboard bindings and `EXPLAIN`, and returns at most 100 rows, 50 columns, and 256 KiB of complete JSON values plus an opaque model-result reference. Both agent views cancel a running read through `DELETE /api/ai/sessions/{chatId}/proposals/{proposalId}/execution`; this is distinct from aborting the browser request. The general read-SQL route remains available to non-agent callers under its application-specific contract. Schema writes additionally use revision and layout-token checks.

See `src/schemii/server.py`, `src/schemii/schemer_server.py`, `src/schemii/postgres_http.py`, and the focused server/HTTP contract tests for current routes. Do not expose these APIs beyond the loopback-only application boundary.

## Agent Instructions

An AI coding or terminal agent must read [`agent_guide.md`](agent_guide.md) and [`docs/AI_AGENT_SETUP.md`](docs/AI_AGENT_SETUP.md) before changing or operating Schemii. Saved-schema synchronization must follow [`.opencode/skills/preserve-schemii-layout/SKILL.md`](.opencode/skills/preserve-schemii-layout/SKILL.md).

## License

Schemii is released under the permissive [MIT License](LICENSE).
