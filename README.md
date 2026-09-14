# Schemii

The previous Schemii and Schemer implementation is preserved under [`archive/`](archive/) as a read-only reference from checkpoint `04a8fbb`.

New unified-backend and Schemii, Schemoo, and Schemer frontend architecture work belongs at the repository root. Archived code should remain unchanged unless an explicit archival correction is required.

## Scope

Schemii is a local, self-hosted PostgreSQL design and analytics workbench made up of three connected products:

- **Schemii** manages saved PostgreSQL connection profiles, editable schema designs, live catalog inspection, Console queries, and migration review.
- **Schemoo** defines durable semantic models over an explicit saved connection and schema, including relationships, derived fields, and required or optional model scopes.
- **Schemer** turns a Schemoo model into saved dashboards. Dashboard authors select the model scopes they expose; report users can activate those optional filters, explore tiles, and drill into retained result sets.

The repository is intentionally a local-development deployment: it has one local prototype principal and no application authentication or public ingress. Query result rows are transient, while product configuration and encrypted connection credentials are stored in the private metadata database. Model publication, ETL, and materialization are separate concerns rather than implicit dashboard behavior.

## Installation

Prerequisites are Git, Docker Engine with the Compose plugin, and OpenSSL. On Linux, the account that starts the stack needs permission to use Docker. Optional local certificate trust for Chromium browsers needs `certutil` from `libnss3-tools`. Tailscale is optional and only needed for the private tailnet preview route.

```bash
git clone git@github.com:LandMineDevelopment/schemii.git
cd schemii
./start.sh
```

Open <https://localhost:8001/> after the launcher reports healthy services. `./start.sh` is the supported lifecycle command: it builds current source, starts or refreshes the stack, creates the local TLS certificate and private metadata secrets, and performs health checks. Do not run Compose directly or commit/copy `.schemii/`; it contains local TLS material, database secrets, and the encryption key required to read saved connection passwords.

For a Tailscale-connected device, use the configured private preview at <https://omarchy.taile4f57f.ts.net/>. It remains tailnet-only and is not a public deployment boundary.

## Application structure

The application uses one composition root and an independent package for each product API. Schemii also owns its packaged, buildless frontend:

```text
src/schemii/
├── main.py
├── common/
│   ├── api/
│   ├── connections/
│   ├── metadata/
│   └── postgres/
├── schemii/
│   ├── frontend.py
│   ├── models.py
│   ├── routes.py
│   ├── web/
│   │   ├── index.html
│   │   └── assets/
│   └── workspaces/
├── schemoo/
│   ├── models.py
│   └── routes.py
└── schemer/
    ├── models.py
    └── routes.py
```

`main.py` constructs the FastAPI application, shared services, common routes, and three product routers. Each product package owns its product-specific API routes and Pydantic contracts. The same application serves the Schemii frontend at `/`, so browser requests use the active same-origin API without a separate frontend process or build step.

## Current API

The current API deliberately uses one local application user while product workflows are prototyped:

- `GET /api/v1/session` returns the valid local prototype principal.
- `/api/v1/connections` manages owner-scoped, durable PostgreSQL connection profiles.
- `/api/v1/schemii/workspaces` manages each user's durable local and PostgreSQL-backed designs plus presentation preferences.
- `POST /api/v1/schemii/workspaces/postgres` opens the user's existing design for one exact saved connection and namespace, or imports it once from a bounded PostgreSQL catalog snapshot when none exists.
- `PATCH /api/v1/schemii/workspaces/{id}` renames the owner's saved workspace with a revision check, without renaming its PostgreSQL database/schema or changing its saved design.
- `/api/v1/schemii/workspaces/{id}/catalog` returns a live PostgreSQL catalog snapshot.
- `/schemoo` opens the durable semantic-model editor; `/api/v1/schemoo/models` manages owner-private models over an explicit saved connection and schema.
- `/schemer` opens saved, model-bound analytics dashboards; `/api/v1/schemer/dashboards` manages dashboard and tile configuration.
- `POST /api/v1/schemer/dashboards/{id}/executions` compiles tiles into one retained session with independent forward-only cursors. Tile `/executions` starts an individual refresh or drill-through.
- `/api/v1/common/query-executions/{id}` provides shared read-result paging, cancellation, release, and export without requiring a Schemii workspace.
- Interactive OpenAPI documentation is available at `/docs`.

Schemoo stores current model rules, independently revisioned canvas layouts, and
Explore inputs in metadata—not query rows or copied credentials. Existing browser
prototype drafts are imported explicitly and left intact. See the
[Schemoo architecture and API guide](src/schemii/schemoo/README.md) for source-drift
handling, configuration limits, shared execution, and the remaining semantic-engine
limitations. Schemer uses the same model root, exposure, relationships, and scope rules.
Dashboards persist configuration only. Detail reports, aggregate reports, and drill-through grids automatically append
rows on scroll using the same page loader as Schemii; charts append fetched groups
on scroll. Short row batches fill the visible viewport automatically. Each cursor advances once, and going back uses
the cache. Changing slicers, refreshing, or leaving a dashboard releases its cursors.
Grouped dashboard cursors are protected from LRU eviction until closed or expired;
full protected capacity rejects new reads. A dashboard supports up to 20 tiles,
or the configured statement limit if lower. Database execution errors follow the
shared session transaction contract; cached rows remain available. Model publishing,
ETL and materialization remain separate future work.

This phase has no application authentication, so the packaged local deployment is explicitly `local-development` and its Compose ingress remains bound to loopback. The configured Tailscale Serve route exposes that loopback listener only to the tailnet and must be protected by tailnet ACLs. It is a preview route, not a public deployment boundary. The storage design does not depend on Tailscale; a future authenticated deployment can replace the local principal without changing connection or product route signatures. The server currently rejects an authenticated/public deployment mode instead of silently starting without its future identity adapter.

Connection profiles, workspaces, desired designs, import provenance, creation-time target identities, and presentation preferences survive application rebuilds and restarts in the deployment's private PostgreSQL volume. Passwords are accepted only by write models, represented as `SecretStr`, authenticated-encrypted before entering metadata PostgreSQL, omitted from profiles and errors, and decrypted only while opening the selected target. The persistent encryption key lives outside PostgreSQL under `.schemii/secrets`; losing that key makes stored passwords unrecoverable.

Each profile targets exactly one PostgreSQL host. TLS certificate and hostname verification (`verify-full`) is the default; weaker libpq SSL modes must be selected explicitly for environments that require them.

The local deployment's `internal-only` egress mode admits connection profiles only when their normalized host is listed in the operator-owned `SCHEMII_ALLOWED_TARGET_HOSTS` setting. The list contains private network identities, not user-entered patterns or inferred address ranges, and is checked when a profile is created, updated, and every time its credential is resolved for use. The metadata PostgreSQL identity is denied independently. Deployments must therefore control DNS for each allowed alias; an alternate alias or literal address is rejected unless the operator explicitly adds it.

A workspace is either a database-independent editable design or a new editable design imported from PostgreSQL. Targets cannot be attached, replaced, or detached after creation. A PostgreSQL import atomically records the catalog baseline, design revision, layout, provenance, and lossiness report, so existing local work cannot be overwritten. The saved connection's PostgreSQL grants—not a Schemii workspace mode—determine which database operations are permitted. Local designs can be exported as SQL for use outside Schemii. Database-backed workspaces inspect columns, constraints, relationships, indexes, triggers, functions, views, materialized views, enums, and domains from bounded PostgreSQL snapshots. Capabilities that remain planned are registered for review in the API map and return an explicit `501 planned_capability`.

## Schemii frontend

The frontend manages real connection profiles and workspaces, authors durable desired-schema objects, renders live catalog data, and saves presentation state through `/api/v1`. PostgreSQL catalogs can be copied into a newly created editable workspace from the workspace creation dialog. It does not fabricate rows or emulate unavailable server operations; unsupported actions open a capability-specific notice.

Shared frontend primitives used across product surfaces live in `src/schemii/common/web/assets`; Schemii-specific composition remains in `src/schemii/schemii/web/assets`. The shared layer owns reusable interaction and presentation contracts such as searchable selectors, sortable rows, query stories, buttons, icons, menus, tooltips, state panels, dialog chrome, and dock panes. Page modules own workflow-specific dialog lifecycles and product composition such as the schema canvas, catalog cards, API route stages, and response-contract rails.

Promote a frontend implementation into the shared UI layer when multiple real page consumers need the same contract or when one central implementation is required for accessibility or interaction correctness. Keep page-specific code local rather than adding speculative variants. Schemii and Schemoo share the HTTP client, DOM helpers, graph viewport, and result grid directly through `#common/*` imports. HTML import maps resolve that alias to `/assets/common/`; the Node package imports map resolves it to the same source files for tests. The common frontend installer owns that asset mount, independently of product frontends. Do not add product-owned copies or compatibility wrappers for these primitives.

Desired designs support durable schema authoring independently of a backing database. Database-backed designs can be reviewed and applied through server-authoritative migration plans with drift detection, explicit conflict resolution, durable execution recovery, and PostgreSQL-enforced permissions. General SQL execution and the workspace assistant use separate server-authoritative contracts. Example restoration and application shutdown are deliberately excluded from the rewrite API.

`common/metadata/factory.py` selects its storage boundary from the required `SCHEMII_STORAGE_MODE`. The launcher always selects durable PostgreSQL; in-memory storage must be explicitly selected and remains available for isolated unit tests. Missing or incomplete durable configuration fails startup rather than falling back to process memory. Repository operations require an owner ID so persistent users, sessions, and additional product ownership can be added without changing product route contracts.

Start the local application stack with the repository launcher:

```bash
./start.sh
```

Then open <https://localhost:8001/>. [`start.sh`](start.sh) is the single Docker Compose startup boundary and runs Docker directly without `sudo`. If Docker group membership was added after the current shell started, the launcher refreshes only its own process through `newgrp docker`; otherwise it uses the current session unchanged. The application containers never receive the Docker socket. The script builds the current source, waits for all services to become healthy, and reports the resulting service state.

The launcher creates a persistent self-signed server certificate for `localhost` and `127.0.0.1` under ignored local state at `.schemii/tls`, plus database role secrets and a persistent credential-encryption key under `.schemii/secrets`. All are excluded from Git and the Docker build context. Back up the metadata database and `.schemii/secrets/metadata_encryption_key` together. Do not print or share any file in `.schemii/secrets`. HTTPS protects transport but does not add application authentication.

Chromium-family browsers on Linux can trust only this exact certificate, without granting it certificate-authority privileges, through the user's NSS database:

```bash
certutil -D -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)" 2>/dev/null || true
certutil -A -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)" -t "P,," -i .schemii/tls/localhost.crt
```

Restart the browser or T3Code after changing trust. Remove the exception with `certutil -D -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)"`. Other clients can either trust `.schemii/tls/localhost.crt` through their own certificate store or retain their normal self-signed-certificate warning.

Runtime configuration is grouped at the top of `start.sh` and may also be supplied through `SCHEMII_TEST_APP_PORT`, `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, `SCHEMII_TEST_POSTGRES_PASSWORD`, `SCHEMII_STARTUP_TIMEOUT`, `SCHEMII_TLS_DIRECTORY`, `SCHEMII_TLS_CERTIFICATE_DAYS`, and `SCHEMII_SECRET_DIRECTORY`. `SCHEMII_ALLOWED_TARGET_HOSTS` is deployment-owned and must contain only PostgreSQL aliases reachable on the intended private target network. Database identity and password overrides are initialization inputs and must continue to match retained local state. The Compose ingress remains loopback-only because this prototype intentionally has no application authentication.

Non-secret administrator policy is loaded once at startup from the absolute path in `SCHEMII_CONFIG_FILE`; the local stack mounts [`dev/schemii.toml`](dev/schemii.toml). The file owns process connection admission, bounded catalog materialization, operation timeouts, Console statement/session/memory policy, query-history and saved-query retention, migration review/lease timing, and per-user metadata resource limits. Invalid, unknown, or internally conflicting settings fail startup instead of being silently ignored. These are Schemii safety ceilings; PostgreSQL permissions and stricter database-side limits remain authoritative.

Query history stores only normalized SQL and its run timestamp. Result rows are never written to the metadata database or a Schemii filesystem spool; interactive pages stay transient and full CSV downloads stream from PostgreSQL. When a configured limit rejects an action or evicts a transient result session, Schemii returns an actionable error where a request is still active and writes a bounded `metadata.limit_events` record containing only the limit identity, configured and observed values, outcome, request identity, user/workspace context, and timestamp. SQL, credentials, parameters, and row data are deliberately absent from that journal.

## Workspace assistant

The local Compose stack includes one shared, private Pi inference sidecar,
authenticated with a server-held bearer secret. It has no published host port,
database access, shell tools or persistent chat storage. The browser talks only
to Schemii's same-origin API. Pi returns structured proposals; Schemii owns tool
validation, permissions, execution and user approval.

AI settings support ChatGPT Codex device sign-in, OpenAI API keys, and verified
free OpenCode Zen models. The server periodically refreshes free-model availability;
it never silently substitutes a model. Conversations can switch models between
turns. Credentials are encrypted and owner-scoped, with configurable inactivity
expiration. See [AI runtime documentation](ai/prototype/README.md) for retention,
provider privacy, deployment limitations and sidecar tests. The current supported
deployment remains a single API process with the local development principal.

Each turn receives the current owner-scoped workspace, desired design, bounded chat
history, and—only when granted—a freshly inspected live catalog. Design proposals
cover the complete desired-design model and execute through the same revision and
integrity checks as direct edits. Structured browsing, history/reset, migration
planning/execution, and SQL use the same application services as the UI.
Migration review, apply, external-conflict choices, and uncertain-outcome
reconciliation have independent permissions. Conflict resolution updates metadata and requires
a fresh plan. Reconciliation checks transaction evidence without rerunning SQL;
its receipt can still report uncertainty or a newer-design synchronization conflict.
Each individual action supports Disabled, Ask per batch, or Automatic, including
create/update/delete for each design object type and separate undo/redo/reset.
The server derives design permissions from actual before/after effects, so a
whole-table replacement cannot bypass permissions on its columns or indexes.
All effects in a batch must be enabled; any Ask effect requires one review.
Existing capability JSON is normalized on read to equivalent action modes;
new settings save the complete bounded action map in the same metadata field.
Omitted actions in an explicit map are disabled. Catalog and result visibility
remain separate binary context-access switches. Preparing a
Console draft remains distinct from executing write SQL. Authorized write batches
use one transaction; uncertain commits are not retried. Migration submission is
not proof of completion: the assistant must inspect the execution receipt.
Related design calls save atomically in one revision. An exact mixed-service batch
can share one approval, but is sequential, stops on failure, and reports partial
completion; it is not a distributed transaction.

Assistant metadata stores conversations, SQL proposals, revision bindings, action
receipts, counts, and transient result identifiers. It never stores query result
rows. Rows shown in the assistant remain in the Console's bounded process memory.
The assistant can bundle labeled reads in one tool call, receive their results
together, and continue analyzing without another user message. Read approval
defaults on; approving or dismissing actions resumes the same question. Automatic
reads do not authorize schema edits or writes. “Analyze query results” separately
controls access to row values.

Earlier runs are referenced by chat-scoped IDs and timestamps. Retained results
can be compared; released results require rerunning saved SQL under the current
read permission and approval policy. Reruns explicitly warn that data may have
changed: they cannot reconstruct historical snapshots. Each query in a batch
uses its own read-only transaction. Result samples share a row/byte budget; use
SQL aggregation for complete comparisons. Row-backed answers remain transient.
Approval continuations store IDs only, never provider transcripts or rows, and
expire with their conversation history. Batch size, tool rounds, active-workflow
time, and context budgets are controlled in [`dev/schemii.toml`](dev/schemii.toml).
At the tool-round limit, one final tools-disabled response summarizes the collected
evidence and identifies incomplete checks. It cannot execute more actions and still
obeys the workflow timeout, permission checks, and context/response memory limits.

Anonymous or free upstream models should be used only for non-confidential prototype
data because their providers may retain prompts. A production deployment should
select and authenticate an approved provider before sending schema or row context.

## Development checks

The test suite is split by the boundary it verifies. Run the fast Python behavior
suite and buildless frontend module suite during development:

```bash
.venv/bin/python -m pytest -q
npm test
```

Python tests use explicitly selected in-memory repositories unless a test is in
`tests/integration`. They should assert public behavior, transaction outcomes,
and durable state transitions rather than source layout or fixed route counts.
Frontend module tests cover state and rendering contracts without requiring a
running server.

Real PostgreSQL integration tests require a disposable, externally reachable
database and are enabled explicitly. The CI workflow provisions that database;
the local launcher intentionally keeps its databases private.

```bash
SCHEMII_TEST_METADATA_DSN='host=127.0.0.1 port=5432 dbname=schemii_test user=postgres' \
SCHEMII_TEST_METADATA_PASSWORD='replace-with-test-password' \
  .venv/bin/python -m pytest -q tests/integration
```

Assembled browser tests exercise both desktop Chromium and an Android-sized
viewport against the canonical application stack:

```bash
./start.sh
npm run test:e2e
```

Playwright keeps screenshots and traces only for failures under `artifacts/`.
The browser suite owns and removes uniquely named test workspaces, so repeated
runs do not depend on prior application state.

## Seeded Docker test deployment

[`compose.test.yaml`](compose.test.yaml) runs the packaged Schemii application with two private PostgreSQL 17 services: a durable metadata control plane and an isolated demo target. The application authenticates to metadata with a dedicated non-superuser runtime role. The demo target uses a separate non-superuser role, while one-shot bootstrap jobs alone hold database initialization authority. `start.sh` is the supported startup command:

```bash
./start.sh
```

Open <https://localhost:8001/> and create a connection with:

| Field | Value |
|---|---|
| Name | `Seeded test database` |
| Host | `postgres` |
| Port | `5432` |
| Database | `schemii_test` |
| Username | `schemii` |
| Password | `schemii-local-test` |
| SSL mode | `disable` |

Use namespace `bookstore` for the populated Mercury Books tutorial data. It includes nine tables, 500 orders, relationships, checks, JSONB, generated columns, indexes, a trigger, functions, four views, and a populated materialized view. Use `catalog_lab` to test partitioned tables, enum and domain-backed columns, a composite foreign key, a partial index, an exclusion constraint, a procedure, and populated and unpopulated materialized views.

List the saved demo test scenarios with:

```bash
./start.sh --list-demo-scenarios
```

Reset the isolated target database and only its launcher-owned connection,
workspace, design, baseline, layout, and history metadata with:

```bash
./start.sh --reset-demo baseline
```

The saved scenarios are `baseline`, `column-migrations`, `column-order`, `compatible-drift`,
`conflicting-drift`, `live-browser`, `sql-console`, `type-conversions`, and `undo-redo`. `live-browser` creates a
database-backed workspace with populated tables, joined views, and a materialized
view for testing search, row previews, and source-derived lineage.
`sql-console` opens a guided set of starter query tabs for cursor/selection
execution, Run all, pinned and paged results, saved-query history, and explicit
commit/rollback testing. `column-migrations` demonstrates a new
required column on an empty table alongside a safe widening on a populated
table. The reset output prints the new workspace URL. Each run records the
current Git commit (with `+dirty` when applicable) and a digest of its base and
alteration files in `schemii_fixture.fixture_state` inside the demo database.
`column-order` shows the independent saved app order for an empty and a
populated table, plus the optional reviewed PostgreSQL reconstruction choices.
This makes a reproduced state traceable to both code and fixture content.

`type-conversions` saves four narrowing changes. Open **Review migration** and
explicitly choose strict conversion for each column. Three pass; `note` refuses
to truncate `Reset baseline` to eight characters. A custom `left(note, 8)`
expression demonstrates an intentional transformation requiring destructive
confirmation. The integer default and unique key remain intact. Reset with
`./start.sh --reset-demo type-conversions` to repeat the exercise.

Type conversions use PostgreSQL to validate values at review time and recheck
strict preservation while holding table locks during execution. Custom expressions
currently support one source column, built-in scalar types, casts, arithmetic,
CASE, COALESCE/NULLIF, and common text/numeric functions. User routines, subqueries,
aggregates and array conversions remain explicitly unsupported. Validation retains
SQL and safe outcomes in the existing expiring plan; it never retains row values.

The former command remains a baseline alias:

```bash
./start.sh --reset-migration-demo
```

Neither PostgreSQL service has a published host port; both are reachable only on the private database network. Saved connection admission rejects the normalized metadata server identity, including its configured network aliases and every database on that server, so a target profile cannot point Schemii back at its control plane. A minimal, non-root Nginx sidecar publishes the loopback HTTPS port while the application remains on internal application and database networks. The application, ingress, and one-shot jobs run as non-root users; no service mounts or accesses the Docker socket; and both HTTP containers use read-only filesystems. The seeded target password is intentionally limited to this local test deployment and must not be used in production.

`./start.sh` is the only supported lifecycle boundary. It builds the candidate image before replacing the running HTTP services, then waits on dependency-aware readiness. `./start.sh --reset-demo` resets only the reserved demo target and its associated metadata; it does not erase general saved workspaces or the metadata volume. Full-volume deletion is intentionally not exposed as a routine launcher operation because it destroys saved designs, connection profiles, encrypted credentials, and history.

The seed records a fixture version and refuses to use a retained target volume with an older catalog shape. PostgreSQL initialization variables apply only when their data volume is first created. Keep the original database, username, and password values for retained state; the launcher rejects a conflicting explicit password instead of desynchronizing a mounted secret from the database role.

Set `SCHEMII_TEST_APP_PORT`, `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, or `SCHEMII_TEST_POSTGRES_PASSWORD` before running `start.sh` to override the development defaults.
