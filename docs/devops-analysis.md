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
evidence references. The initial disposable check covers Console and the shared
reader, while the saved-model and report walkthrough remains an integration gate.

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

Independent current-head acceptance, authenticated Schemii/API and report
walkthroughs, and a decision demonstrated with authorized natural evidence are
still due. Schedule actual import/replay verification before natural analysis.
Measure query plans on a bounded real sample before deciding whether larger
windows need storage or query tuning.
