# Single-server capacity measurement plan

Plan for [#135](https://github.com/LandMineDevelopment/schemii/issues/135) and
[#136](https://github.com/LandMineDevelopment/schemii/issues/136), based on the
[2026-09-29 audit](audits/2026-09-29-project-audit.md). These are proposed test
targets and executable recipes, not measured capacity. Native manual UI acceptance
and affected lifecycle fixes are the current priority. The first owned adapter is
documented in [testing/load](../testing/load/README.md).

## Gates before the campaign

1. Merge independent reviews and required checks. Run the launcher, then verify
   `https://localhost:8001`, `https://omarchy.taile4f57f.ts.net` and their API maps.
   Every live run holds the shared deployment lease; rebuilding waits for cleanup.
2. A two-account authenticated smoke must verify exact known rows/values,
   cross-owner denial, incremental first row/full drain, observer-recorded source
   release and idempotent owned cleanup. A native browser mechanical probe is not
   application acceptance and a cheap auth read is not report capacity.
3. Record source SHA/fingerprint, dirty status, hardware/kernel/CPU/RAM, competing
   processes, generator location, source/database/dataset topology, every effective
   app/ingress/metadata/source allocation and exactly **one** API process. Current
   historical app defaults (2 CPU, 2 GiB) and ordinary/retained/profile limits
   (20/19 globally, 4/3 per profile) must be confirmed from the assembled deployment.
   Do not add processes or tune pools as an unmeasured fix.
4. Capture failed-baseline and fixed regressions for idle expiry, wide driver
   results, metadata admission and cancellation/revocation/disconnect. The current
   adapter's 513 ID-only source is deliberately small; it cannot validate wide-row
   memory or the real 1,800s idle contract by itself.
5. Establish an independent observer with exact ownership and live resource gauges.
   Missing metrics, lost control/observation, data/isolation failure, accumulating
   backends/cursors or >=85% app memory stop the campaign. Protect user resources,
   the retained reader baseline and existing QA accounts.

## Stage recipes

The rate is actual HTTP calls/minute for single-call lanes. Cancel/composite
journeys disclose their arrival rate and count every actual call separately.

| Recipe | Offered stages | Duration | Purpose |
| --- | --- | --- | --- |
| smoke | 100 | 6s plus drain | Tiny opt-in protocol/ownership check |
| ramp | 100, 300, 600, 1,000, 3,000, 6,000, 12,000 | 2m warmup + >=5m hold each | First sustainable/capacity knee |
| confirm | Selected near-knee stage ×3 | 2m warmup + >=5m hold each | Repeatability, confidence and resource stability |
| spike | Baseline, 2× selected rate capped at 12,000, 0.1× recovery | 2m, 30s, 1m | Bounded overload and healthy recovery |
| soak | Selected sustainable rate | 2m warmup + 60m hold | Stable RSS, connections, sessions and jobs |

Extend low-volume holds when p99 has too few samples. Report counts rather than
claim precision from a tiny smoke; require at least 10,000 successful samples when
confirming p99 near the knee. Report three repetitions separately and compare
hardware, configuration, source, mix, account cardinality and generator delivery.
Stop at the first failed ramp stage; measure controlled overload separately.

Generate a plan and use the guarded workflow. For example, after the gates pass:

```bash
./test.sh load plan --recipe ramp --workload report-5 --active 2 \
  --output /private/load-report-ramp.json
./test.sh load prepare --plan /private/load-report-ramp.json \
  --accounts qa_report_author_001,qa_report_author_002 \
  --credentials-file .schemii/testing/credentials.json \
  --observer-file /private/load-observer.json
./test.sh load run --run LOAD_ID
./test.sh load status --run LOAD_ID
./test.sh load report --run LOAD_ID
./test.sh load cleanup --run LOAD_ID
```

Repeat plan/prepare with `--recipe confirm --rate KNEE`, `--recipe spike --rate
KNEE`, or `--recipe soak --rate SUSTAINABLE`. Select workloads explicitly rather
than turn an aggregate cheap-read success into query/dashboard user sizing.

## Workload and identity matrix

| Lane | Faithful correctness/recovery oracle | Initial implementation |
| --- | --- | --- |
| Authenticated cheap read | Exact authenticated owner identity | k6, one call/arrival |
| Catalog/compile | Expected source tables/valid saved plan | k6; fresh catalog and warm compile separated |
| Private 1/5/20-tile report | All 513 known order IDs per tile, exact completion/end | Incremental Node |
| CSV | Exact headers/row multiset, valid complete CSV | Incremental Node |
| Slow reader/disconnect/cancel | Incremental body, counted control calls, independent backend release | Foundation; real control acceptance pending |
| SQL SELECT/page/close | Ordered >page-size values, cursor progression, final close | Dedicated console fixture/client pending |
| Wide results and real idle expiry | Driver-sized byte/row oracle and observed 1,800s lifecycle | Pending; no ID-only substitute |
| Shared report/viewer grants | Private/raw result denial, authorized shared stream and revocation | Owned grant adapter pending |
| Login/cardinality churn | Separate login cost and session cleanup at 10/100/1,000 registered users | Owned account provisioner pending |
| Revisioned metadata writes | Optimistic revisions, conflicts, persistence and exact cleanup | Owned fixture lane pending |
| COPY/migration | Exact disposable writer schema and rollback/cleanup | Separate explicit writer campaign |

The first adapter selects existing retained accounts and does not expand their
permissions or profile limit. Test registered users (10/100/1,000) independently
from active authenticated identities (1/10/50/100+). Reuse sessions for steady
traffic; login churn has its own lane. Compare many readers on one saved-profile
revision with distinct profiles; distinguish owner-private and Schemii-managed
profiles and respect the per-owner 100-profile cap.

After named lanes pass, ratify a realistic mix with explicit weights and actual
calls per active user/minute. Exclude paid AI; a local stub cannot measure provider
capacity. Preserve snapshot and tile-fairness semantics; do not hide failures with
retries, skips or explicit cleanup inside a lane that is meant to prove auto expiry.

## Proposed budgets and decision rules

Cheap reads: p95 <=300ms, p99 <=1s, unexpected failures <0.1%. The initial harness
is stricter on the bounded deterministic corpus (zero unexpected failures).
Small interactive query/report: first row p95 <=2s; full journey p95 <=5s. Cancel
acknowledgement: p95 <=1s; independently observed source release <=5s. Exports,
long SQL and intentionally slow readers have separate documented budgets. Normal
sustainable stages have no capacity rejection, exact data and no accumulating
resources. 409/429/502/503 capacity rejection is not successful throughput; HTTP200
stream error is a failed journey. Generator drops/late arrivals or insufficient
CPU/network headroom invalidate capacity conclusions.

After each stage, arrivals stop, resources return to the captured baseline and a
fresh authenticated read plus SELECT must meet recovery budgets. Source/metadata
fault, lock, revocation, disconnect and TTL lanes must produce bounded rejection
and control recovery. Record app CPU/RSS/FD/threads, executor/event-loop pressure,
ordinary/retained/control/monitor admission, metadata/target connections/lock waits,
ingress sockets and generator resources. Metrics use bounded route/operation classes;
raw SQL, payloads, account/profile IDs and credentials stay out of public reports.

The maximum sustainable rate and first failing/rejecting stage are published **per
workload**, together with completed journeys, actual HTTP offered/sent/admitted/
completed/rejected/failed/incomplete/dropped totals and first-row/full-drain samples.
Confirm key knee stages from an independent Tailscale-connected generator when
possible and distinguish network effects from host capacity.

For the measured realistic mix only, estimate:

`active users = floor(0.70 × sustainable successful HTTP calls/min ÷ measured calls per active user/min)`

State the 30% throughput headroom plus independent retained-session/profile,
source, memory, burst and streaming constraints. Publish account-cardinality and
active-workload findings separately. If hardware/topology, generator delivery,
cleanup, larger source/catalog, external AI or distributed scaling were not tested,
label them unmeasured. Prioritize the smallest confirmed bottleneck fix; do not
publish a capacity estimate while a correctness/isolation/leak/OOM gate is unresolved.

## Observer source map and current gaps

Historical source inspection used `49ed0a3` and `audit/metadata-bounds` at
`13ec07c`; it did not collect runtime metrics. The current adapter consumes a private
observer file. Integrated PR160 supplies launcher-owned process/cgroup receipts and
an authenticated, administrator-only, no-store aggregate application snapshot.
The opt-in witness in [the load guide](../testing/load/README.md#opt-in-run-witness-and-active-recovery)
adds bounded run-owned source/job evidence. Neither turns unavailable fields into
zero. A refreshed timestamp must describe newly sampled values; no live capacity
qualification is established by these deterministic boundaries.

| Observer field | Concrete read-only source | Ownership / remaining boundary |
| --- | --- | --- |
| appRssBytes | Host `/proc/PID/status` `VmRSS` for a verified API PID; cgroup v2 `memory.current` separately describes the whole app cgroup | Record PID birth identity and scope; process RSS and cgroup memory usage are distinct |
| appMemoryLimitBytes | Resolve the verified API process's `/proc/PID/cgroup`, read that cgroup's `memory.max` and effective finite ancestors | `compose.test.yaml` is a default, not proof of a live limit; `max` must remain unmeasured |
| appCpuPercent | Delta of verified cgroup v2 `cpu.stat` `usage_usec` over monotonic time; read `cpu.max` for effective quota | Specify whether percent is one-core or quota-normalized; do not mix denominators |
| apiProcesses, threads, fds | Verified launcher/cgroup `/proc` identities, supported Uvicorn argv, endpoint namespace PID, Threads and readable FDs | Shells are distinguished from workers; unknown sidecars, unreadable argv and replacement cannot qualify one API process. Raw argv is not retained |
| retainedSessions, openCursors | Administrator `/api/v1/admin/runtime-observation` snapshots live Console and driver registries without waiting for busy locks | Deployment aggregate only; PostgreSQL `pg_cursors` is session-local and application handles cannot prove source release |
| ordinaryPermits, retainedPermits | The same authenticated snapshot reads reviewed gateway acquire/release state; monitoring/control remain separate | DB connections are not equivalent to permits while connecting or closing; aggregate is not run attribution |
| ownedBackends | Opt-in `testing/load/source_monitor.py`, explicit provisioned run-exclusive role, empty baseline, captured PID/backend-start/database/role/application | Exact same-role read-only authority; idle is live, stale/replaced/denied evidence fails closed. Borrowed/shared roles and ended `/activity` cannot qualify release |
| activeJobs | `testing/load/ownership.mjs` reads only receipt-owned IDs via owner execution and `/console/bulk-jobs/{id}` APIs | Immutable no-job adapters have a limited authenticated zero; missing/foreign/uncertain records stay unavailable, never a workspace-wide total |
| metadataActive, metadataRejected | Authenticated aggregate export of `MetadataConnectionFactory.admission_snapshot()` active/rejected | Actual process factory, not a newly constructed unrelated factory; wait durations remain unavailable |
| sourceConnections / lock waits | The source observer's bounded aggregate `pg_stat_activity` / `pg_locks`; existing owned execution `/activity` exposes database/wait/blocker state | `/activity` sets monitoringAvailable false for ended **or** visibility-restricted sessions and is not a source-release proof |
| ingressConnections | Verified ingress network namespace socket observation | `dev/ingress/nginx.conf` exposes timing logs, no connection-count or stub-status endpoint |
| eventLoopLagMs / executor pressure | A bounded instrument in the running API event loop / executor | No runtime sampler/export exists in the inspected source |

Existing request middleware emits safe route/status/handler timing, and ingress
access logs include `request_time` and `upstream_time`. Read them only through the
supported `./start.sh --logs schemii` / `./start.sh --logs ingress` controls. Their
bounded 200-line output is useful diagnosis, not a complete high-rate sample, and
handler timing is not a streaming body-drain measurement. Private `metadata.limit_events`
can explain rejection/eviction classes to an authorized observer; it is neither a
live permit gauge nor a product-readable metrics route.

The bounded foundation records generator PID/start, RSS, FDs, threads, namespace
network counters, Node CPU/scheduling measurements and external CPU ticks with
host `CLK_TCK` when available. Network capacity, competing traffic and CPU/memory
headroom remain unknown. Active
recovery now requires a fresh authenticated read, exact SELECT terminal, captured
backend and independently observed release. Live qualification, missing workload
and cardinality provisioners, a weighted mix and independent cleanup/review remain
pending. The hard report guard remains `capacityEligible: false`. Preserve normal
end-user grants, exclude SQL/row/credential/owner labels from public metrics, and
disclose observer budgets. Docker/socket access, inferred zero counters and
missing-source fallback cannot qualify capacity evidence.
