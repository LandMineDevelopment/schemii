# CI analysis in PostgreSQL and Schemii

Use the same versioned PostgreSQL views in Schemii, its assistant, or `psql`.
The initial pack for [#280](https://github.com/LandMineDevelopment/schemii/issues/280)
covers local CI evidence from the [storage](devops-metrics.md) and
[import](devops-import.md) contracts. The CLI prints queries and assistant
arguments without opening a connection. PostgreSQL evaluates the queries on its
current facts.

## Set up a reader

1. On an approved **disposable** analytics target, follow the storage guide to
   provision dedicated roles and use the guarded `storage.py plan`, `migrate`,
   and `status` workflow with the same database name and identity. Expect schema
   version 2 before using these views. Apply migration 002 through the storage
   command so its SQL and migration record commit together.
2. Supply synthetic facts or an approved disposable import. Read the import
   guide for preflight, verification and replay. Real intake and rollout require
   separately verified import and replay first, including the applicable producer
   recipe for historical inputs. Record the repository and installation UUIDs
   from the verified projection. A repository display name or commit by itself
   does not identify an installation.
3. In Schemii, open **PostgreSQL connections → New connection** and save the
   dedicated analytics target using only the provisioned reader login. For a
   network target, arrange reachability and certificate trust from the Schemii
   server and use **Verify full**. Never enter collector or migration credentials
   in an analyst connection. Keep credentials, session material and original
   diagnostic evidence in their private stores.
4. Open a PostgreSQL workspace for that saved connection and the `devops`
   namespace. In the live catalog, look for the `v1_ci_*` views and
   `v1_collection_health`. To confirm the target from SQL Console, run:

   ```sql
   SELECT current_database(), current_user;
   SELECT metric_id, name, definition
   FROM devops.v1_ci_analysis_definitions
   ORDER BY metric_id;
   SELECT repository_id, installation_id, max(observed_at) AS latest_observation
   FROM devops.v1_collection_health
   WHERE source_kind = 'local-ci-v1'
   GROUP BY repository_id, installation_id
   ORDER BY latest_observation DESC, repository_id, installation_id;
   ```

   These are reader grants across the dedicated database. Repository and
   installation filters select the analytical scope. They do not enforce tenant
   isolation. Provision separately controlled targets for audiences that must
   be isolated. The login inherits `devops_reader` only, with `CONNECT`, schema
   `USAGE` and `SELECT` on named views. The reader cannot query fact tables,
   current intermediates or migration records, or write facts or create objects.

## Start with one repository, installation and window

List the available queries for a person or program:

```bash
python scripts/devops/analysis.py catalog
python scripts/devops/analysis.py catalog --json
python scripts/devops/analysis.py sql --help
```

Run these queries in order on the same window. Substitute only the four scope
values below. The timestamps require offsets and are converted to UTC.

```bash
python scripts/devops/analysis.py sql health \
  --repository-id REPOSITORY_UUID --installation-id INSTALLATION_UUID \
  --from 2026-01-01T00:00:00Z --until 2026-02-01T00:00:00Z
```

| Recipe | Use the result to |
| --- | --- |
| `health` | Check local CI batches in the observation window, latest batch vs. latest complete import, runs in the window, and runs with missing start times. Source-window coverage stays unknown. |
| `batches` | Inspect the exact current batch revision, source kind, status and sanitized reason. Trace a command's pinned batch using its ID **and revision**. An older pinned revision may require the authorized collector. |
| `run-coverage` | Find incomplete producer verification, pending and reused slots, absent slot evidence and producer acceptance. |
| `verification` | Trace slots and case coverage gaps, first-attempt counts, observed recoveries, producer acceptance, command evidence and donor pins. |
| `slow-commands` and `run-elapsed` | Find costly scopes and inspect sample size, P50, P90, maximum, and excluded, open and unknown duration counts. |
| `case-hotspots` | Rank case keys by first failures, then cases with observed retries. Check eligible, excluded and unknown first attempts on the same row. |
| `attempt-contributors` | Inspect attempt zero and each retry for one `--command-id COMMAND_UUID`. The query returns the attempt revision, pinned command and import revision, observed time, evidence token and digest. |

Paste the emitted SQL into Console in **Read-only** mode and run it. Save it as
a named Console query if useful. Outside Schemii, pass the identical SQL to
`psql -X -v ON_ERROR_STOP=1` on the explicit reader target using an external
private passfile. When comparing full results, read every page of detailed
queries or use a complete Console export where available. Use the SQL totals
for counts and fractions. The size of a result page or assistant sample is
unrelated to the denominator. Record the window, repository, installation,
`definition_version` and `as_of` with a private decision note.

For an optional detail or simple count report, use the same reader connection
and `devops` namespace in Schemoo. Create a model from
`v1_ci_run_coverage` or `v1_ci_verification`, expose only the desired columns,
and filter by repository, installation and time window. A Schemer report based
on that model can list command slots or group the slot count by
`verification_state`. Compare the complete result against Console's
`verification` result aggregated at the same grain. Use the Console recipes
for weighted fractions and percentiles. Averages of row-level rates weight
small and large populations equally. Publish no model or report with private
evidence references. The saved pack described below supplies the initial
command-slot report and a separate run-grain Explore view.

## Structured assistant and API reads

- For a Schemii assistant attached to the reader workspace, enable **Run read
  queries** and **Analyze query results** under its existing policy. Keep the
  configured approval behavior, and leave write and migration actions disabled
  for this use. Use `schemii_list_relations` and `schemii_browse_rows` if enabled
  for discovery and bounded row inspection. For exact metrics, use the
  server-side aggregate recipes through `schemii_read_query`.
- Add `--format agent-json` to the same CLI command to produce the existing
  tool's typed `summary` and `queries` arguments. Each recipe uses exactly the
  same statement as Console. The CLI has no application login, database fallback,
  credential flags or copy of the facts. Within the assistant conversation,
  use the existing read-run and result tools for retained results. A rerun is
  a fresh observation. Check `hasMore` and any truncation or freshness notice
  before treating a detailed result as complete.
- For an authenticated HTTP client, use the existing workspace catalog
  `GET /api/v1/schemii/workspaces/{workspace_id}/relations` to discover views,
  and the workspace route `POST /api/v1/schemii/workspaces/{workspace_id}/console/executions`
  for the recipe SQL. Submit `mode: "managed_read"`, `statements: [SQL]`, a
  `consoleId` of the form `con_` followed by 32 lowercase hex characters,
  the workspace's `expectedWorkspaceRevision` and the current
  `expectedSettingsRevision` from `GET /api/v1/schemii/console/settings`.
  Read the returned execution status at `.../console/executions/{execution_id}`;
  after success, read `.../results/{result_id}` on that execution, passing its
  `nextCursor` as `cursor` when provided. Follow the ordinary session and
  workspace access policy. The deployed API map and, for authorized administrators,
  OpenAPI docs describe the API. No metrics-specific endpoint is required for this
  managed-read workflow. A dedicated analysis tool in the shared CLI or automatic
  assistant recipe discovery would require separately owned integration.

## Interpretation and provenance

Each SQL execution reads the latest visible fact revisions in PostgreSQL.
`as_of` is the query observation time. Window bounds are inclusive start and
exclusive end. Runs, commands and case groups use current **run start** so a
command with only a monotonic duration can still be measured. Batch queries use
batch observation time. A run missing a start time is reported by `health` and
excluded from time-windowed execution queries. The same window can produce
different results after corrections or later imports. For comparisons that must
share a snapshot, use one SQL statement or the same repeatable-read PostgreSQL
transaction, and retain the result and its evidence privately.

| Dataset | Grain and counting rule |
| --- | --- |
| `v1_ci_run_coverage` | One current run. Count visible slot dispositions. Independent required slot counts and missing-layer reasons are unavailable in this fact model. Zero visible slots means no slot rows are visible. |
| `v1_ci_command_elapsed` | One visible command slot. Rank recorded monotonic `duration_ms`. `wall_elapsed_ms` is a separate, optional clock. Reused, excluded, pending, source-changed and unmeasured rows contribute no duration sample. |
| `v1_ci_verification` | One slot with observed case counts and rates. An executed slot with known expected count exposes the count shortfall when observed cases do not exceed it. Unknown inventory, nonexecution, mismatches and observed cases missing attempt zero have explicit states or counts. Equal counts do not establish which planned cases were observed. |
| `v1_ci_case_first_attempts` | One observed case per executed command. Only observed attempt zero can be eligible. Pass, failure and timeout are eligible, skip/cancel are excluded, and a missing zero is unknown. A later observed pass after first failure or timeout contributes one recovery even with multiple retries. |
| `v1_test_attempts` and `v1_collection_health` | Foundation detail. Use qualified IDs and exact revision pins, timestamps, digests and opaque evidence tokens. The token is resolved against the authorized original evidence store. |

Duration percentiles use PostgreSQL's discrete percentile over measured rows
only. Report counts and outcome groups with the percentile. Scope groups use
available mode, profile, policy, base, inventory and cohort context. A null
cohort is an unknown comparison scope, so use its rankings to find records for
inspection. A singleton percentile describes one sample. Recorded source-changed
executions remain visible and are excluded from duration percentiles. Hotspots
separate reported source changes from other observations.

First failure and pass fractions divide by eligible first attempts. Retry recovery
divides by the same eligible population, while recovery given failure divides by
eligible first failures. A zero denominator produces NULL. The missing expected
case count and unknown first-attempt count describe different gaps and can both
be present. Reuse preserves the exact donor pin and contributes zero new
execution or attempt samples. Producer acceptance is preserved separately from
slot and case evidence. No reuse approval or coordinator acceptance is inferred.

To trace a hotspot, filter `v1_ci_case_first_attempts` in Console by the
qualified scope and case key shown by `case-hotspots`, using `IS NULL` for unknown
dimensions. The matching command IDs lead to `verification` and
`attempt-contributors`. To trace a slow-command group, use the same scope and
outcome in `v1_ci_command_elapsed`, keep `duration_state = 'measured'`, and sort
by `duration_ms`. Revisions in child fact rows pin the original parent
revisions. Separately named current revision columns describe the active
projection. A retracted current command or run hides its dependent rows.
A retracted batch disappears from batch health while exact historical pins remain
available to authorized history readers.

## Troubleshooting and cleanup

- **Empty or stale** — confirm UUIDs, window, schema version and the last
  observation in `health`. Zero returned rows describe the selected stored
  population. Check the approved input manifest for actual source coverage.
- **Partial or pending** — inspect visible slot states, missing cases, health and
  the original manifest. Preserve original failure evidence when considering
  retries or improvements. Never turn missing counts or timings into zero.
- **Denied** — check workspace ownership, current revisions and reader grants.
  For the assistant, check action and result permissions separately. Do not
  elevate the reader to bypass a denial.
- **Truncated or expired** — narrow the scope, page the retained result, export
  from Console, or aggregate in SQL. Record any fresh rerun as a new observation.
- **Upgrade or drift** — use the storage guide's `plan`/`status` checks.
  Reconcile migration history before another attempt. Preserve original diagnostics.
  Each migration adds a guarded schema version. Readers can discover query
  definition version independently of schema version.
- **Cleanup** — remove only task-owned disposable resources and saved analyst
  objects. Stop the test cluster before removing its private temporary directory.
  Keep failed executions and source-bound receipts in private evidence.

## Disposable verification

Set `SCHEMII_DEVOPS280_POSTGRES=1` for the focused test file. The fixture starts
its own socket-only PostgreSQL cluster with a generated throwaway password.
It uses the existing provisioner and tests migration from version 1 and reapply.
No running Schemii service is needed for these checks.

```bash
SCHEMII_DEVOPS280_POSTGRES=1 python scripts/ci/python-tests.py tests/devops/test_analysis.py
```

Verification covers selected-window totals, unknowns, timing distributions,
revision changes, tombstones, donor lineage, reader denials and identical
Console/assistant reads at the shared managed PostgreSQL cursor boundary. The
synthetic scenario yields two measured instances of the same failed command
scope, one invalid-source sample exclusion, and two eligible first failures for
the highest-ranked case, one observed recovery. The attempt trace retains two
failed observations before the passing retry.

Independent current-head acceptance and mounted Schemii walkthroughs remain
separate gates. Verify actual import/replay before natural analysis. Measure
query plans on a bounded real sample before deciding whether larger windows
need storage or query tuning.

## Saved CI Verification model and CI Health and Performance dashboard

The portable pack uses the existing Schemoo and Schemer saved-definition APIs.
It stores presentation and scope only. PostgreSQL retains the underlying facts
and business rules. The pack binds to one reader connection, one repository UUID,
one installation UUID and one half-open UTC run-start window. Both roots use
the same fixed filters. Scope IDs are inputs to the model rule and stay out of
report fields and ad hoc report filters. There are no joins between the roots.
Use the **CI runs** starting object in Schemoo for run counts, including runs
with no visible commands. The dashboard uses the **Command slots** starting
object and every tile counts or summarizes that grain.

| Field or group | Business type and use |
| --- | --- |
| `repository_id`, `installation_id` | UUID scope keys. Use both in fixed scope rules. A repository name, branch or SHA cannot substitute for either key. |
| `id`, `run_id`, `slot_key`, `case_key`, `attempt_number` | Internal fact identities and ordinals. A slot is one current command in one qualified run. A first attempt is ordinal zero for a qualified command/case. Run count uses one row per current run, never a distinct timestamp or a count of slot rows. |
| `started_at`, `run_started_at` | Typed timestamp for the UTC window. Missing run start places a fact outside the window; use `health` for those records. `finished_at`, `observed_at`, `as_of` keep their separate meanings in Console or authorized detail. |
| `mode`, `outcome`, `run_outcome`, `completeness`, `run_completeness` | Contracted producer codes. See the label map below. Preserve SQL NULL on fields where the producer did not assert a value. |
| `acceptance`, `producer_acceptance` | Nullable boolean. A producer assertion is independent of run status and observed slot evidence. |
| `command_key` | Registered command key such as `python-behavior`. Group by the stable code. Raw command lines are outside the fact contract. `profile`, `project`, `shard`, cohort, inventory and policy identifiers remain detail for cohort diagnosis. The portable dashboard provides no comparable-cohort claim. |
| `disposition`, `verification_state`, `case_coverage_state`, `slot_coverage_state`, `duration_state` | Controlled view codes. The dimensions and counts inherit their distinctions from migration 002. No report expression reclassifies a row. |
| `observed_slots`, `executed_slots`, `pending_slots`, `reused_slots`, `excluded_slots` | Integer coverage fields stored at the run-view grain. Sum only at that grain. Visible slots do not establish an independent required-obligation denominator. |
| `expected_cases`, `observed_cases`, `missing_case_observations` | Integer per-slot counts. NULL expected means unreported inventory. NULL observed on a non-executed slot means no new observation. A case count match alone does not prove membership. |
| `eligible_first_attempt_cases`, `excluded_first_attempt_cases`, `unknown_first_attempt_cases` | Observed first-attempt denominator and separate exclusions and missing ordinals. Summing these across mutually exclusive slots preserves the slot/case grain. Pending slots carry no eligible attempts. |
| `first_passes`, `first_failures`, `cases_with_retries`, `recovered_first_failures` | Integer observed numerators or counts. A recovery counts once for an eligible first failure with a later observed pass. Rates stay in the canonical SQL recipes and views. |
| `duration_ms`, `wall_elapsed_ms`, phase times | Recorded monotonic milliseconds are separate from wall-clock intervals. The dashboard reports count and maximum only for `duration_state = 'measured'`, by command and outcome. NULL phase measurements remain unknown. |
| Evidence refs and digests, source/index SHAs, revision pins, batch and donor IDs | Authorized trace detail. The fixed model allowlist excludes them from fields and report filters. Display names, descriptions and code labels are presentation metadata. Raw evidence text, credentials, paths, and sensitive diagnostics never enter the pack. |
| Import batch source, status, reason, timestamps and accepted/rejected totals | Closed source, status and reason codes with typed timestamps and counts at import-operation grain. Use `health` and `batches`. Imported row totals are never execution totals. |
| Attempt outcome and phase values | The attempt outcome is a closed code at command/case/ordinal grain. Attempts can be passed, failed, skipped, cancelled or timed out under the storage contract. Detailed attempt identities and nullable timing phases remain in the reader trace. |

The catalog can show names and types for columns the model leaves unexposed.
Exposure constrains model queries. PostgreSQL reader grants and application
authorization still govern access outside this model.

### Code and label map

The views return stable codes. Use these labels when reading the report or
describing an export. The report keeps the codes for filtering and grouping.

| Codes | Labels and business meaning |
| --- | --- |
| `selected`, `feedback`, `full` | Selected verification, feedback-only execution, full recipe requested. Mode is distinct from completeness. |
| `passed`, `feedback-passed`, `failed`, `source-changed` | Producer passed, feedback passed, producer failed, source changed during execution. |
| `running`, `pending`, `incomplete`, `cancelled` | In progress, pending, incomplete evidence, cancelled. |
| `unknown`, `complete`, `partial`, `pending`, `cancelled` (run completeness) | Unknown completeness, complete, partial, pending, cancelled. |
| `executed`, `reused`, `pending`, `excluded` (disposition) | Executed, reused from a pinned donor, pending, explicitly excluded. Reuse adds no new execution sample. |
| `fresh-passed`, `failed`, `invalid-source`, `unknown` (verification) | Original pass, execution failed, unstable source, no classified outcome. Other recorded disposition or incomplete/skip/cancel states remain separate. |
| `not-executed`, `unknown-inventory`, `count-mismatch`, `missing-observations`, `missing-first-attempts`, `empty-inventory`, `count-matches` | No new execution, expected inventory unknown, observed count exceeds expected, observations missing, attempt zero missing, explicit empty inventory, equal observed/expected counts. |
| `no-visible-slots`, `plan-size-unknown` | No slot rows visible, visible slots with independent plan size unknown. |
| `measured`, `open`, `unknown`, `invalid-source`, `excluded` (duration) | Measured, open, duration unavailable, invalid source, excluded from execution timing. A measured zero remains a sample. |

### Install and review with the existing APIs

Use an authorized analyst login with product authoring access and the dedicated
PostgreSQL reader connection. Before writing metadata, select the connection
and verify its ID, revision, database, `devops` namespace and required views
through `GET /api/v1/schemoo/catalog?connection_id=…&namespace=devops`.
Generate the model body with the installed project interpreter:

```bash
python -m scripts.devops.dashboard_pack model \
  --connection-id CONNECTION_ID --database DATABASE_NAME \
  --repository-id REPOSITORY_UUID --installation-id INSTALLATION_UUID \
  --from 2026-11-12T00:00:00Z --until 2026-11-18T00:00:00Z
```

1. Submit the JSON to `POST /api/v1/schemoo/models` and read the exact saved
   model using `GET /api/v1/schemoo/models/{id}`. Use that GET response as the
   input file to `python -m scripts.devops.dashboard_pack dashboard MODEL.json`.
2. Submit the dashboard body to `POST /api/v1/schemer/dashboards` and read
   `GET /api/v1/schemer/dashboards/{id}`. It pins the saved model rules revision.
   Execute `POST /api/v1/schemer/dashboards/{id}/executions/stream` with the
   dashboard's `expectedRevision`. Review tile errors and completion limits.
3. For run-grain Explore, use the **CI runs** starting object or
   `python -m scripts.devops.dashboard_pack run-explore` for the `explore`
   body. Plan or execute by saved model ID and `expectedRevision`. Compare
   this result with `run-coverage`, and the dashboard tiles with
   `verification` and the measured groups in `slow-commands`.

For repeat-safe application, `dashboard_pack.install_pack(client, scope, ...)`
accepts an authenticated client implementing ordinary `get` and `post`.
It checks the explicit live connection revision and uses a deterministic name
per connection and scope. It fetches saved definitions before reusing them,
refuses changed or ambiguous matches, and never overwrites a saved revision.
Use one writer per owner and scope. If a POST loses its acknowledgement,
reconcile by reading the same scope before another POST. The API provides no
atomic model/dashboard transaction or create-if-absent key. Generated templates
contain no session material and never execute SQL themselves.

### Limits and next dependencies

The pack reports observed rows on the selected source. A slot-level
dashboard has no authoritative run denominator, including runs that produced
no slots. Run-grain Explore and the `run-coverage` recipe supply that count.
Showing both grains on one dashboard requires per-tile root selection or an
additive purpose-built PostgreSQL rollup at an explicit grain. Joined run and
slot tiles would duplicate run measures.

Keep weighted rates and percentiles in SQL until the reporting platform can
consume a scoped, versioned aggregate view or saved exact query with named
numerators, denominators, group coverage and sample counts. An independent
plan denominator or pending-layer reasons require a typed source-bound
obligation dataset. Comparable history needs an admitted comparable cohort
and sample scope. T3 envelopes, effort and turns, issue elapsed and Git churn
require their own typed producer facts and joins. The pack derives none of
them from CI receipts.

The opt-in test uses the normal project test runner, the real application ASGI
routes and authentication, durable disposable PostgreSQL metadata, and a
separate private socket copy of the verified v2 data. Both HTTP requests and
result pages run in-process using the normal integration-test transport.
Mounted HTTPS, browser layout, visibility under the owner's existing account,
and durable live installation remain separate acceptance checks.
