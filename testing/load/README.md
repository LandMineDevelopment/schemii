# Owned single-server load measurements

This opt-in foundation addresses [#135](https://github.com/LandMineDevelopment/schemii/issues/135).
It does not establish application acceptance or server/user capacity. The measured
campaign in [#136](https://github.com/LandMineDevelopment/schemii/issues/136) remains
queued behind native UI acceptance and lifecycle fixes. See the
[measurement plan](../../docs/single-server-capacity.md).

Use the supported entry point `./test.sh load`. The ordinary `./test.sh` UI
prepare/run/claim workflow remains separate. A load preparation reserves the exact
retained accounts using the same cross-worktree account guard and holds the same
deployment lease as manual QA. It calls only `./start.sh` to assemble the application
and verifies both canonical HTTPS and Tailscale HTTPS. No launcher runs during unit
tests or `load plan`. Do not edit source or rebuild while either harness owns a lease.

## Tools and recipes

Node uses the existing host runtime; no npm or production dependency is added.
Fixture preparation also uses the existing Python/sqlglot environment to read
output labels from the application's compiled SQL plan without executing SQL.
Ordinary exact-rate traffic uses host-side **k6 2.3.0**, pinned to upstream release
checksums. The optional installer supports Linux amd64/arm64, never uses elevated
privileges, and refuses to replace an existing destination:

```bash
./scripts/install_k6.sh
./test.sh load plan --recipe smoke --workload cheap-read --rate 100 --active 2 \
  --output /private/load-plan.json
./test.sh load prepare --plan /private/load-plan.json \
  --accounts qa_report_author_001,qa_report_author_002 \
  --credentials-file .schemii/testing/credentials.json \
  --k6-bin .schemii/tools/k6/2.3.0/k6 --allow-unobserved
./test.sh load run --run LOAD_ID
./test.sh load status --run LOAD_ID
./test.sh load report --run LOAD_ID
./test.sh load cleanup --run LOAD_ID
./test.sh load report --run LOAD_ID
```

`run` schedules work in the lease-holding controller and returns immediately.
Read status/report to see terminal results; a successful dispatch is not a passing
run. Reports survive cleanup. Failed preparation also needs cleanup. Cleanup closes
owned sessions, cancels recorded owned executions, deletes only receipt-owned
models/dashboards, verifies owner listings, and releases accounts/lease. It preserves
the 120 retained accounts, source profiles, schemas, baseline rows, starter models,
starter dashboards, history receipts, evidence and caches. There is no retained
database reset or grant broadening.

For exact row tests, replace `cheap-read` in the plan with `report-1`, `report-5`,
`report-20`, `csv`, `slow-reader`, `disconnect` or `cancel`. These use the incremental
Node client. `catalog` and `compile` are separate k6 lanes. All source/report lanes
currently require explicitly named retained **report_author** accounts. Preparation
adds one uniquely named model and three dashboards per account, then checks that
the other account cannot read the private model. Every write has a persisted intent
and response receipt; interrupted creates reconcile only their exact absent-before
run names. Source data remain read-only. NDJSON and CSV expect all 513 baseline
order IDs, independent of row order, with no duplicates or extra/missing values.
NDJSON must include every expected tile's completion plus the final `end` event;
HTTP 200 with an error event fails. Disclosed preview caps require explicit matching
oracle expectations. Exact columns are derived from the real saved-model plan
(the default label is `Orders.id`), rather than assuming a raw table column name.
CSV is parsed incrementally, including split UTF-8, quoted
newlines, escaped quotes and a required final record terminator.

Registered account count comes from the private fixture registry; active identities
are the selected authenticated accounts. This first adapter neither provisions
1,000 users nor silently raises profile caps. Its saved profiles are distinct;
shared-profile/viewer grant fixtures are still required before shared-user sizing.

## HTTP totals and limits

Ordinary k6 scenarios schedule exactly one HTTP request per arrival. Their stage
rate is actual HTTP calls per minute; redirects and implicit authentication are
disabled. Authentication, provisioning, isolation probes and cleanup occur outside
the measured stage. They are not included in offered stage rates. Stream/CSV stages
also schedule one call per arrival. **Cancel** rates describe journey arrivals:
each started journey can add a separately counted cancel call. Reports expose
scheduled/offered/started/completed/failed/dropped arrivals and actual
sent/admitted/completed/rejected/failed/incomplete HTTP totals. Admitted HTTP means
responses that were not classified as capacity rejection; it does not prove source
permit admission. Capacity-related 409/429/502/503 count as rejections; failed body
protocols remain failed or incomplete regardless of HTTP status. Intentional
disconnect/cancel lanes retain those incomplete/failed stream totals rather than
present them as successful throughput. Unexpected failures stop arrivals.

Default limits are 256 concurrent generator operations, 30s request deadlines,
32 MiB drained body, 1 MiB event/CSV-record, 12,000 arrivals/min and 3,600s per stage.
Late/full Node generators drop arrivals rather than hide demand in a queue. k6
reports `dropped_iterations`, actual `http_reqs` and custom per-outcome counts;
accounting mismatch, drops, correctness failures and thresholds invalidate the
stage. First-row and full-drain measurements come from parsing body chunks. A
generator on the host competes with the stack; reports disclose that placement.
Unexpected authentication/server/transport failures have aborting k6 thresholds;
their failed/incomplete counts remain in the summary. Failed ownership writes join
the exact spawned generator/controller process group before reservations release.

Run recipes are explicit: `smoke` (6s), `ramp` (each of 100/300/600/1,000/3,000/6,000/
12,000 calls/min, 2m warmup and 5m measured hold), `confirm` (three near-knee repeats),
`spike` (bounded 2× burst), and `soak` (2m warmup plus 60m measured hold). Recipes
are immutable within the versioned envelope and stop at the first failed stage.
Full stress is never a default PR check.

## Independent observation and recovery

`--allow-unobserved` is allowed only for smoke protocol work and leaves recovery
unverified and capacity ineligible. Other recipes require `--observer-file` pointing
to a private mode-0600 JSON file, refreshed independently at least every 5s:

```json
{
  "at": "2026-09-29T20:00:00Z",
  "appRssBytes": 100000000,
  "appMemoryLimitBytes": 2147483648,
  "appCpuPercent": 10,
  "retainedSessions": 0,
  "openCursors": 0,
  "ownedBackends": 0,
  "activeJobs": 0,
  "ordinaryPermits": 0,
  "retainedPermits": 0
}
```

An observer must count the exact load run's owned backends/jobs and state its
topology/ownership method. Unknown counters must be omitted rather than written
as zero. Optional numeric gauges are metadataActive, metadataRejected,
sourceConnections, ingressConnections, threads, fds and eventLoopLagMs. Only these
explicitly allowed numeric fields enter reports. Raw SQL, user/profile IDs, source
rows, credentials, cookies and labels derived from them never enter public reports.
Observations older than 15s or RSS at/above 85% of the app limit stop the generator.
Ten-second post-arrival drain must return all six resource gauges to their captured
baseline. A completed stream or explicit close is not proof of source release.
The observer implementation, real process count/effective budgets, control-plane
recovery probe, cancellation deadlines and independently reviewed cleanup remain
live acceptance requirements. Reports always keep capacityEligible false in this
foundation until those independent campaign gates are implemented and verified.

Private state is under `artifacts/load/LOAD_ID`: controller handle/config, sessions,
create intents and opaque object IDs. Share only the allowlisted `report.json` after
review, never the directory. The controller removes its exact private Unix socket
directory on normal cleanup/SIGINT/SIGTERM. After forced controller termination,
`cleanup` verifies recorded controller/generator PID and birth identity have exited
before reacquiring a shared deployment lease and reconciling fixtures. It refuses
a live or unresolved generator; it never kills a peer process or steals accounts.
Forced termination and orphan cleanup require live lifecycle evidence before this
adapter can be called proven. The independent native browser transport's ten-minute
expiry/20 MiB cleanup contract is separate from durable load evidence.

## Fast checks

```bash
node --test testing/load/*.test.mjs
bash -n scripts/install_k6.sh
node --check testing/load/cli.mjs
node --check testing/load/controller.mjs
node --check testing/load/exact-rate.k6.js
```

The deterministic tests cover stream terminal/errors/truncation, wrong/duplicate
rows, CSV chunk boundaries and partial files, cross-owner disclosure, uncertain
fixture writes, ownership drift, retained resource preservation, HTTP accounting,
generator underdelivery, leak detection, envelope guards and report sanitization.
They do not start the application or generate load.
