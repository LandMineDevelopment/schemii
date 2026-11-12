# Owned single-server load measurements

This opt-in foundation addresses [#135](https://github.com/LandMineDevelopment/schemii/issues/135).
It does not establish application acceptance or server/user capacity. The measured
campaign in [#136](https://github.com/LandMineDevelopment/schemii/issues/136) remains
queued behind native UI acceptance and lifecycle fixes. See the
[measurement plan](../../docs/single-server-capacity.md).

Use the supported entry point `./test.sh load`. The ordinary `./test.sh` UI
prepare/run/claim workflow remains separate. Retained-account preparation reserves
the exact accounts and deployment lease used by manual QA, calls only `./start.sh`
and verifies both HTTPS origins. Disposable preparation attaches to an already
held launcher fixture through its constrained consumer contract below; it never
starts a second stack. No launcher runs during unit tests or `load plan`. Do not
edit source or rebuild while either harness owns a lease.

## Private fixture execution receipts

Disposable report and CSV journeys declare a run-owned operation nonce after
their private intent is durable, before sending the authenticated request. CSV
uses a saved dashboard tile. The fixture wrapper correlates the nonce through
ASGI tasks and `asyncio.to_thread`, then records the actual base Console reserve
return synchronously. It returns the identical receipt and preserves application
errors and cleanup order, including a reserve that finishes after cancellation.
The nonce grants no application access. Production routes and permissions are
unchanged; report receipts are never read through the forbidden common GET.

The attached consumer uses `start.sh --metadata-pressure-executions` with its
existing fixed attachment file and bounded allowlisted stdin record. Only the
holder may write the fixed read-only declaration input and export the exact
owned container's fixed private `execution-observation.json`. Existing source,
lease, run, attachment and process-incarnation guards apply. No arbitrary paths,
SQL, rows, credentials or whole execution objects are exported. Existing metadata
observation retains its separate 16 KiB bound.

The channel holds at most 128 unacknowledged operations in 128 KiB. Acknowledgment
follows durable recording of the exact captured IDs and status in the private
journey journal. Late unacknowledged captures also enter a bounded private cleanup
journal. Nonce history is bounded at 4,096, and the existing holder IPC request
budget remains 1,024; exhaustion stops observation rather than silently evicting
work. Missing, unmatched, unreadable, pruned, restarted or lost observation stays
unknown and blocks qualification. Legacy retained-mode CSV capture remains
explicitly unqualified.

Receipt status comes only from exact-owner `get_owned` reads. Request completion
is recorded separately: `succeeded` does not prove a native cursor or permit was
released, nor that CSV/NDJSON values were correct. Independent captured source
identity/release evidence and full protocol oracles remain mandatory. A CSV too
fast for the independent source witness leaves a qualification gap; receipt
capture does not manufacture that witness.

This instrumentation adds synchronous capture-file writes, private launcher IPC,
and up to 16 ordinary metadata reads per 100 ms sampler sweep. Its private scalar
counters report metadata reads, capture writes and elapsed sampling/capture time.
These are observer costs, separate from offered application HTTP calls. They do
not measure the complete instrumentation CPU, scheduler, IPC or database impact;
`overheadMeasured` and `stockCapacityEligible` remain false. No capacity claim is
supported until the coordinator qualifies the real assembled fixture and its
independent witnesses.

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
in the retained mode require explicitly named **report_author** accounts. Preparation
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

Registered count in retained mode comes from its private registry; active identities
are the explicitly selected accounts. Disposable mode provisions its own population
and profiles. Neither mode changes the per-owner profile limit or resets retained data.

## Disposable populations and composite journeys

The new adapters require an explicitly held **representative-load-v1** fixture from
the owning launcher's `./start.sh --hold-load-fixture SECONDS` workflow. The older
`pressure-v1` fixture is rejected. The fixed representative profile declares one
API process, app 2 CPU/2 GiB, metadata 1 CPU/1 GiB, source 2 CPU/2 GiB, ingress
0.5 CPU/128 MiB, normal/readiness metadata admission 16/2, ordinary source 20/4
and retained source 19/3. The holder records effective allocations and database
settings; the consumer compares these with the immutable profile before attaching.
Retained 19/3 is configuration evidence, not a live permit measurement.
Instrumentation is fixture-only and its overhead remains unmeasured. Both stock
capacity eligibility and campaign capacity eligibility stay false.

Only the coordinator may create/hold a live fixture after its independent review.
The held authority, credentials and source witness remain private. Preparation
accepts no borrowed credential file or retained-account list in disposable mode:

```bash
./test.sh load plan --recipe smoke --workload mix --fixture-mode disposable \
  --registered 10 --active 2 --profile-mode managed-shared \
  --output /private/load-plan.json
./test.sh load prepare --plan /private/load-plan.json \
  --fixture-authority /private/owned-fixture/authority.json \
  --observer-file /private/load-observation.json
```

Preparation returns a run directory. Before `run`, the coordinator starts the
independent collector using a verified launcher **host PID/cgroup runtime receipt**,
the fixture administrator credentials, and that run's generated
`ownership-private.json`. The generated private manifest's `observer` object supplies
the exact paths/account: authority's `osRuntimeReceiptFile`, the run's converted
`observer-credentials-private.json`, ownership config and declared output. Use those
with `node testing/load/observer.mjs --runtime-receipt FILE --credentials-file FILE
--account NAME --ownership-config FILE --output FILE`. The holder's `startup-effective-configuration` receipt
is a separate contract and cannot substitute for the host observation receipt.
The fixture OS export and init/API process consumer are authored; live host
observation/attach source certification is still required before qualified smoke;
`--allow-unobserved` can only support explicitly unqualified smoke protocol work.

The controller runs in one dedicated process group and uses incremental Node only.
It attaches through the owning launcher, sends bounded heartbeats, and keeps helper
processes in the same group. The holder must verify committed source fingerprints
before attachment. Expiry stops/joins the exact group before fixture disposal and
retained-stack restoration. No workload may continue against the restored stack.
Offline load cleanup requires matching run/nonce, saved consumer PID/start/argv
identity, a joined attachment, and the holder's completed cleanup/restoration
journal; it sends no HTTP requests. Unknown group or mutation identity blocks cleanup.

`--registered 10|100|1000` and `--active 1|10|50|100` are independent dimensions
(other bounded values through 1,000 are accepted). Only active users receive steady
authenticated sessions. Inactive registrations are not fabricated as active traffic.
The provisioner creates nonadministrator accounts with unique secret-bound intents,
checks cross-owner denial, and preserves all retained peers. Profile modes are
`private-single`, `private-multiple` (use `--profiles N`), `managed-shared`, and
`managed-distinct`. Private profiles are capped at 100 **per owner**; distinct
managed profiles use one explicit managed owner and are capped at 100 including
its existing profiles. Variant journeys actually use the selected profile. No limit
is raised to support the population.

The source helper uses the held fixture's separately declared source administrator,
v2 endpoint/server/fixture proof and same disposable database. It creates one owned
schema per active identity: 513 ordered IDs with an exact owner nonce, plus optional
129 wide rows containing four 8,192-character cells. Ordinary users receive schema
USAGE and table SELECT only. Before every mutation it records an intent; nonce,
schema owner, physical OID, exact relation set and actual server identity govern
resume and cleanup. Foreign objects, uncertain commits, unknown dependencies or
unprovable outcomes are retained and blocked. It never uses `CASCADE`, creates a
database/role, borrows an administrator, or writes ordinary source traffic. Estimated
dataset payload is preflighted against 1 GiB; an oversized wide population is refused.

| Adapter | Executed contract |
| --- | --- |
| `sql-page` | One exact owned SELECT, successful execution, ordered full values across cursors, explicit result close; synchronous retained-backend capture before paging |
| `wide-rows` | Same lifecycle with full wide-cell values and nonce checked; no browser-sized stand-in |
| `idle-expiry` | `--recipe lifecycle`, one partial page per active identity, real 1,800-second wait, expired continuation and independent source release; tests cannot shorten elapsed time |
| `shared-report` | Explicit managed source and saved-dashboard viewer grants, complete shared stream, private model denial, no raw execution receipt access |
| `login-churn` | Separate login/read/logout journey, isolated cookie and durable session intent |
| `metadata-write` | Owned workspace label PATCH with expected revision, exact increment and persisted readback; no source mutation |
| `mix` | Deterministic weighted journey arrivals; each actual HTTP operation and each lane reconciled separately |

Pure viewers keep only Schemer capabilities. A separate explicitly granted fixture
administrator recovery identity is disclosed outside registered/active workload
counts; its fresh read/SELECT/close calls are reported as recovery instrumentation.
It receives explicit product/source access and workspace binding. Administrator
status alone never supplies product access. Source backends remain independently
witnessed. Report execution terminal evidence uses the fully drained authenticated
stream (or an exact terminal control acknowledgment), bound to its saved dashboard
or model/revision. Raw execution reads are forbidden for report-scoped executions,
including owner-private reports. A cancellation acknowledgment alone never proves
source release; disconnected or still-pending operations remain unknown until the
supported evidence exists.
CSV responses currently expose no internal execution ID. Their durable operation
intent stays unknown until complete CSV validation and a later complete source
cohort release; the missing per-execution ID/acquisition boundary is disclosed and
does not qualify CSV capacity or exact execution-ID coverage.

The default mix weights are 40% cheap read, 10% catalog, 10% compile, 20% SQL paging,
10% five-tile report, 5% shared report and 5% metadata label writes. A private
`--mix-file` may specify unique allowed lanes with positive integer weights totaling
100. COPY/migrations, login churn and real idle expiry remain separate named lanes;
they cannot be smuggled into an ordinary mix. COPY/migration writer provisioning is
not implemented by this read-only-source fixture and remains a separate campaign.

Population, resource and event journals stay private. The plan declares finite
record/byte/snapshot budgets before preparation. Appends are fsynced in bounded
checksummed chunks; a failed/exhausted append poisons the writer before its mutation
can run. Crash tails are retained, and unknown creates are never replayed merely
because a name is absent. Cleanup keeps every created execution ID. Observation
rotates fairly across at most 80 pending executions and 16 additional owners per
batch, with explicit coverage/staleness/backlog. A terminal ID retires from active
observation only after a fresh complete same-authority cohort witness proves source
release; cleanup history is never pruned. Unobserved work is unknown, never zero.

The holder allows 60–3,600 seconds. Preparation time and recovery consume that
budget; a dispatch without enough remaining time is refused. The complete
120-second warmup plus 3,600-second soak cannot fit and remains pending a separately
reviewed longer campaign lifecycle. No tiny smoke or full campaign has been run
for these adapters. The opt-in `SCHEMII_LOAD_ADAPTER_LIVE=1` test consumes an already
prepared two-identity run (`SCHEMII_LOAD_PREPARED_RUN`); it starts no stack, checks
exact terminal/recovery and always requests owned cleanup. Real idle execution
still takes at least 1,800 seconds.

## HTTP totals and limits

Ordinary k6 scenarios schedule exactly one HTTP request per arrival. Their stage
rate is actual HTTP calls per minute; redirects and implicit authentication are
disabled. Authentication, provisioning, isolation probes and cleanup occur outside
the measured stage. They are not included in offered stage rates. Stream/CSV stages
also schedule one call per arrival. **Cancel** rates describe journey arrivals:
each started journey can add a separately counted cancel call. SQL, login, metadata
and mixed rates also describe journey arrivals. Reports expose
scheduled/offered/started/completed/failed/dropped arrivals and actual
sent/admitted/completed/rejected/failed/incomplete HTTP totals. Admitted HTTP means
responses that were not classified as capacity rejection; it does not prove source
permit admission. Capacity-related 409/429/502/503 count as rejections; failed body
protocols remain failed or incomplete regardless of HTTP status. Intentional
disconnect/cancel lanes retain those incomplete/failed stream totals rather than
present them as successful throughput. Unexpected failures stop arrivals.

Default limits are 256 concurrent generator operations, 30s request deadlines,
32 MiB drained body, 1 MiB event/CSV-record, 12,000 arrivals/min and 3,600s per stage.
Disposable Node stages also stop before exceeding 12,000 actual HTTP calls in a
rolling minute; composite rates never silently become unbounded call rates.
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

The optional launcher receipt and read-only collector provide real OS and
application aggregate samples. The opt-in run witness below adds independent
source attribution and receipt-owned work inspection. Generator headroom and live
qualification remain incomplete, so capacity remains ineligible. When preparing
a fresh deployment, set an absolute **new** task-owned receipt path before the
supported launcher runs. `SCHEMII_RUNTIME_RECEIPT` adds no container access to the
application or collector: only `./start.sh` reads selected container identities
after its health checks, under the existing deployment lease. It refuses an
existing receipt rather than adopting or overwriting another task's evidence.
Normal starts and `--prepare-testing` validate and export this receipt; other
launcher actions ignore the optional output, so a retained receipt does not block
backup, log or fixture verification commands or require Node for them.

```bash
install -d -m 700 /private/load-campaign
export SCHEMII_RUNTIME_RECEIPT=/private/load-campaign/runtime.json
./test.sh load prepare --plan /private/load-plan.json \
  --accounts qa_report_author_001,qa_report_author_002 \
  --credentials-file .schemii/testing/credentials.json \
  --observer-file /private/load-campaign/observation.json \
  --k6-bin .schemii/tools/k6/2.3.0/k6
# After preparation is ready, run this collector in a separate owned terminal.
node testing/load/observer.mjs \
  --runtime-receipt /private/load-campaign/runtime.json \
  --output /private/load-campaign/observation.json \
  --credentials-file /private/admin-credentials.json --account ADMIN_USERNAME \
  --seconds 7200
# While the collector remains live, dispatch and inspect the owned load run.
./test.sh load run --run LOAD_ID
./test.sh load status --run LOAD_ID
./test.sh load report --run LOAD_ID
./test.sh load cleanup --run LOAD_ID
# Stop the collector with Ctrl-C in its own terminal after recovery/cleanup evidence.
```

The foundation must validate the fresh observer at dispatch, after preparation's
rebuild; an observer of the previous deployment cannot serve the new run. A reused
deployment needs its original validated receipt, not a manufactured new identity.
Keep admin credentials separate from retained load accounts, and do not broaden a
load account's grants. Without the optional admin pair the collector still samples
OS resources, but internal gauges remain unavailable. Authentication, fixture
work, observer requests (one every five seconds), cleanup and readiness probes are
outside the measured arrival totals and add disclosed background traffic.

The collector checks boot identity, root PID/birth, cgroup device/inode and live
process membership on each sample. It records every configured stack service's
cgroup memory charge, the strictest ancestor memory/CPU quota, cumulative CPU and
throttling usec, process identities/RSS/threads and readable FD counts; vanished
members and unavailable FD readings are disclosed. RSS
is the sum of process RSS and can count shared pages more than once.
CPU percentage uses monotonic elapsed time with 100% representing one CPU core;
it is not normalized to the container's quota. Sampling duration also uses the
monotonic clock. The existing
RSS guard is supplemented by a conservative 85% cgroup-charge guard; a rejected
sample stops arrivals through the existing observer-unavailable gate. Shared
ancestor budgets compete with peer cgroups and are not dedicated capacity.

`GET /api/v1/admin/runtime-observation` requires an authenticated administrator
and reports no SQL, profile/account IDs, result tokens or source rows. Its gauges
come from each owning registry/condition, not one cross-service atomic snapshot.
The collector matches the endpoint's namespace PID to the launcher's app cgroup.
It reports one API process only when that is the cgroup's single process;
additional processes leave API worker count unverified. The three source
admission lanes report reserved permits separately from established native
connection handles. Established handles are application ownership counts, not
an independent `pg_stat_activity` census. Managed read/manual transaction/raw
session handles contribute to retained sessions. Native cursor counts include
primary and replay/export cursors; result-page tokens remain separate. A busy
cursor or Console registry is unavailable immediately instead of waiting behind
source I/O or native cleanup. Raw counts declare `registryStatus: available` only
after acquiring their lock without waiting; contention returns `registryStatus: busy`
without registered/opening counts. The endpoint preserves that qualification in
the raw source's counts. The collector omits the entire combined `retainedSessions`
gauge while raw counts are busy or unqualified, while independently known cursor
and admission gauges remain usable. Available empty registries report real zero.
Metadata admission reuses the reviewed factory snapshot. Memory-only or
unsupported adapters remain explicitly unavailable.

Private `observation.json.jsonl` retains numeric samples and qualifications, and
`observation.json.owner.json` records the collector's PID/birth and stop receipt.
Output is bounded to 20 MiB and three hours; exceeding the budget or losing the
recorded application stops the collector and invalidates its current reading.
SIGINT/SIGTERM close the observer's HTTP client and log out its own admin session.
Its exact private authentication receipt is removed only after successful logout.
Failed logout retains that receipt and marks cleanup pending. After forced exit,
reconcile only this observer's recorded cookie with:

```bash
node testing/load/observer.mjs --recover /private/load-campaign/observation.json
```

Recovery refuses a live PID/birth, validates the recorded account identity before
logout, and converges when the session is already expired. Durable receipts/logs
remain for audit; temporary atomic-write files are removed. Forced exit before a
login cookie has been received and durably recorded cannot recover an unknown
session; normal authentication expiry still applies to that window. Recovery is
an explicit ownership action, not an automatically running cleanup guardian.
No guardian, browser, container management socket or persistent collector daemon
is introduced. Controlled SIGTERM/SIGKILL tests prove the collector's process and
receipt behavior with synthetic authentication; live application authentication
cleanup remains a campaign acceptance check.

The public report allowlist strips qualification fields and process
identities. Retain/review the selected private collector receipt and log alongside
the public report before interpreting its gauges. Unknown owned-backend/job
counts without the explicit witness, ingress socket counts, database monitoring
visibility, effective CPU-set allocation, event-loop lag and generator headroom are **not** written as
zero. They remain separate campaign requirements; OS sampling and internal
handles cannot by themselves prove source release or safe capacity.

Collector orphan-session recovery is separate from campaign recovery. Campaign
recovery requires qualified `ownedBackends` and `activeJobs`, a fresh authenticated
read and exact SELECT terminal, independently witnessed source acquisition/release,
and no active receipt-owned work. Without the opt-in witness and recovery fixture,
recovery remains unverified. Authentication cleanup alone leaves
[#135](https://github.com/LandMineDevelopment/schemii/issues/135)/
[#136](https://github.com/LandMineDevelopment/schemii/issues/136) incomplete.

GitHub Actions is disabled. Use `./ci.sh --plan --base origin/main` for the selected
local inventory and preserve its source-bound receipts, including failed attempts
and pending live prerequisites. The coordinator schedules complete selected local
acceptance and any bounded live observation/authentication-cleanup qualification;
focused observer regressions do not establish deployment or campaign acceptance.

`--allow-unobserved` is allowed only for smoke protocol work and leaves recovery
unverified and capacity ineligible. Other recipes require `--observer-file` pointing
to the collector's private mode-0600 output, refreshed independently at least every
5s. Use the collector rather than writing aggregate counters by hand. This sanitized
report extract omits private qualifications and is **not** valid collector input:

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
Live collector verification, fully qualified API process count, control-plane
recovery probe, cancellation deadlines and independently reviewed cleanup remain
live acceptance requirements. Reports always keep capacityEligible false in this
foundation until those independent campaign gates are implemented and verified.

## Opt-in run witness and active recovery

The observation helper itself adds no grants, application routes or source writes.
The explicit disposable provisioner above supplies owned fixtures; for other
observation-only use the coordinator must provision a disposable,
**run-exclusive source role** and its exact database and retain the provisioner's
private authority receipt. Borrowed author roles are unsuitable. The monitor
connects as the exact source role to observe its own `pg_stat_activity`; no
administrator or author fallback is permitted. Its separate application name
excludes its own connection. Pre-existing metadata backends are outside this scope.

All configuration/receipt JSON must be current-user-owned regular files, mode
0600, in owned mode-0700 directories. The reader walks ancestors through no-follow
directory descriptors, rejects unsafe ownership/writable ancestors and symlinks,
opens leaves nonblocking, and checks identity before/after a bounded read. FIFOs,
devices, replacements and parent aliases cannot supply input or create output.
The provisioning receipt contains `version: 2`, `status: "ready"` and matching `runId`,
`kind`, `exclusiveToRun`, `database`, `role`, `applicationName`, `endpoint` and
`server` fields. It is
evidence from the provisioner, not a declaration to fabricate for borrowed roles.
The source config contains the private connection secret:

```json
{
  "version": 2, "runId": "load_0123456789abcdef",
  "authority": {
    "kind": "exclusive_source_role", "exclusiveToRun": true,
    "database": "disposable_database", "role": "disposable_run_role",
    "applicationName": "schemii",
    "provisioningReceipt": "/private/load/provisioning-private.json",
    "endpoint": {"host": "127.0.0.1", "port": 5432, "sslmode": "require"},
    "server": {
      "address": "127.0.0.1", "port": 5432,
      "postmasterStart": "2026-11-11T09:00:00.000000+00:00",
      "databaseOid": 100,
      "fixtureId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  },
  "connection": {
    "host": "127.0.0.1", "port": 5432,
    "dbname": "disposable_database", "user": "disposable_run_role",
    "password": "<from private provisioner>", "sslmode": "require"
  }
}
```

Before source traffic, initialize a **new** ledger and require an empty baseline:

```bash
.venv/bin/python testing/load/source_monitor.py \
  --config /private/load/source-config-private.json \
  --output /private/load/source-private.json
```

The provisioner sets the disposable database COMMENT to exactly
`schemii-load:<fixtureId>` using a unique 64-hex fixture ID. It records the actual
server address/port, PostgreSQL postmaster-start timestamp (UTC, six fractional
digits), and database OID. Fixed catalog reads under the source role verify all
these fields on every sample before reading activity. No control-system function,
extra table, grant or administrator fallback is needed. The declared endpoint
must exactly match host/port/TLS mode in the connection. Endpoint changes, fixture
replacement, restart, missing marker or unknown actual identity require a new
provisioned run; neither baseline nor previous captures may be silently rebound.

Fixed read-only, timeout-bounded activity queries read PID, backend start, database,
role, application and state. They exclude query text and source rows. Each captured
backend retains its exact PID plus UTC microsecond start and full target identity.
Discovery uses the exact run labels, then **every captured PID** is rechecked
without database/role/application filters. Changed labels or unknown visibility
invalidate evidence. A fully visible newer backend-start distinguishes a reused
PID from the old incarnation; the new run-owned incarnation remains live if found.
Idle backends remain live. Scope change, denied visibility and monitor failure
invalidate evidence; a failed owned ledger cannot be retried into success.
Foreign evidence is preserved. A bounded file lock serializes observer/recovery
samples. More than 10,000 live/captured identities stops observation. Sampling
proves release of captured sessions, not sessions entirely between samples.
The v2 ledger carries `serverIdentity`, matching `authority.server`, and complete
`capturedChecks` with `present`, `absent`, or `pid_reused` per PID/start identity
(the last includes `observedBackendStart`). `releaseVerified` requires nonempty
captures, complete rechecks and no live captured identity. Version-1 source
evidence is deliberately rejected; it cannot establish endpoint/disappearance proof.
The host monitor uses one separate read-only source connection at a time per
ledger; it does not consume or appear in the application's admission counters.

After supported preparation, place `ownership-private.json` in the exact run
directory. `runId` must equal the generated `fixtures-private.json` `tag`:

```json
{
  "version": 1, "runId": "load_0123456789abcdef", "workload": "report-1",
  "fixtureReceipt": "/private/exact-run/fixtures-private.json",
  "sourceConfig": "/private/load/source-config-private.json",
  "sourceOutput": "/private/load/source-private.json",
  "recovery": {
    "username": "<recorded owner>",
    "workspaceId": "<run-owned workspace ID>",
    "consoleId": "<run-owned Console ID>",
    "workspaceRevision": 1, "settingsRevision": 1
  }
}
```

Pass `--ownership-config /private/exact-run/ownership-private.json` to the normal
collector with its runtime receipt and administrator options. The source authority
must match the recovery workspace's fixed target. The coordinator must separately
provision that owned workspace/Console/profile; this foundation does not widen or
attach retained fixtures. The engine automatically consumes that same run-local
configuration, and rejects mismatched run/fixture/workload evidence.

Owned work inspection authenticates each recorded owner and reads only journaled
execution/job IDs through supported owner APIs. Missing, foreign, expired or
uncertain IDs are unavailable even if cleanup marked them closed. Execution status
never proves backend release. Current immutable adapters create no bulk jobs, so
their authenticated no-job baseline is a real, limited zero. Future bulk adapters
must journal `{username, id, workspaceId}` in `jobs` and use
`observeOwnedWork(..., "owned-bulk-job")`; an empty journal cannot qualify them.
Each sample allows at most 256 owner/ID reads and a four-second cumulative work
budget (an in-flight read retains its three-second HTTP deadline). Exceeding either
is unavailable, not partial coverage. Larger campaigns need a separately qualified
bounded observation strategy. Observer reads remain outside offered stage totals.

Recovery records a private creation intent before a fresh authenticated read and
managed-read SELECT. It journals the execution before paging, checks one exact
value plus backend and actual server/fixture identity and terminal state, witnesses that retained backend,
closes the exact result, and requires independent disappearance. Failed creation
retains its intent; unknown execution IDs are never guessed. Known failed probes
cancel only their recorded execution. Probe calls are outside stage arrival rates.
Cleanup must inspect pending recovery intents before releasing disposable fixtures;
unknown ownership remains unresolved. The existing campaign cleanup does not
automatically reconcile these new intents.

API count requires supported Uvicorn argv, verified cgroup membership and endpoint
namespace PID. Shells are distinguished from API workers; unknown sidecars,
multiple workers, unreadable argv and replacement cannot qualify one API process.
Argv/environment are not retained. Generator samples record PID/start, RSS, FDs,
threads and namespace network bytes/drops. Node adds exact CPU microseconds and
arrival scheduling lag. External-generator CPU ticks use host `getconf CLK_TCK`
when available; missing tick frequency leaves CPU duration unknown.
Namespace traffic is aggregate and CPU affinity is not a measured quota. Network
capacity, competing traffic and CPU/memory headroom remain unknown, so
`headroomVerified` stays false. Unsupported event-loop, executor, ingress,
metadata and source-wait durations stay unknown.

Fast deterministic controls start no launcher or database:

```bash
node --test testing/load/observer.test.mjs testing/load/runtime.test.mjs \
  testing/load/ownership.test.mjs testing/load/campaign.test.mjs
.venv/bin/python -m pytest -q tests/test_load_source_monitor.py
```

Live oracles are opt-in and have **not** run for this foundation. The coordinator
may set `SCHEMII_LOAD_SOURCE_LIVE=1` plus `SCHEMII_LOAD_SOURCE_CONFIG` for the Python
source acquire/release oracle, or `SCHEMII_LOAD_OWNERSHIP_LIVE=1` plus
`SCHEMII_LOAD_OWNERSHIP_CONFIG` and a new private
`SCHEMII_LOAD_OWNERSHIP_JOURNAL_DIR` for the Node HTTPS SELECT/release oracle.
Neither starts/provisions a runtime. Per-lane two-account HTTPS smoke, generator
qualification, interrupted fixtures, missing workload/cardinality/mix adapters and
independent review remain pending. `capacityEligible` stays false.

Private state is under `artifacts/load/LOAD_ID`: controller handle/config, sessions,
create intents and opaque object IDs. Share only the allowlisted `report.json` after
review, never the directory. Both the incremental Node transport and actual k6
generator send the validated canonical or preview Origin required by the application
authentication boundary, including compile POSTs; cookies stay private. The k6
generator rejects an unvalidated origin before offering work or disclosing a cookie.
Preparation declares the observer file path but does not consume a pre-rebuild
snapshot. Start the independent collector after the ready launcher runtime receipt
exists; `run` requires a fresh valid observation below the memory stop threshold
before changing state or offering any work. Missing, stale or placeholder evidence
leaves the run ready with no arrivals.

The controller removes its exact private Unix socket directory on normal
cleanup/SIGINT/SIGTERM. A durable launch receipt records the newly created runtime
directory's device/inode before spawn, and the controller waits for its exact
PID/birth identity handoff before deployment or fixture writes. If an ownership
write fails after spawn, the launcher joins its owned child/group, disposes that
exact directory, records stopped recovery, then releases its reservations.
`cleanup` can recover this verified stopped launch without `controller-owner.json`;
a missing owner on a pending or unknown launch cannot authorize signaling,
directory deletion or account release. Existing paths are never adopted and
unexpected runtime files or replacement directories are preserved.

After forced controller termination,
`cleanup` verifies recorded controller/generator PID and birth identity have exited
before reacquiring a shared deployment lease and reconciling fixtures. It refuses
a live or unresolved generator; it never kills a peer process or steals accounts.
Forced termination and orphan cleanup require live lifecycle evidence before this
adapter can be called proven. The independent native browser transport's ten-minute
expiry/20 MiB cleanup contract is separate from durable load evidence.

## Fast checks

```bash
node --test testing/load/*.test.mjs
python -m pytest -q tests/test_load_planner.py
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
The Node cases need no Python project dependencies and remain in the early CI
phase. The faithful application planner/header and production Python parser CLI
checks run in `tests/test_load_planner.py` in the deterministic Python phase after
dependency installation; fixture inputs come from the same `fixture-spec.json`
used by real preparation.
