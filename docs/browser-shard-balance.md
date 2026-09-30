# Measured whole-file browser scheduling

Implementation for [#128](https://github.com/LandMineDevelopment/schemii/issues/128),
using [#125](https://github.com/LandMineDevelopment/schemii/issues/125) per-attempt
receipts delivered by [PR #148](https://github.com/LandMineDevelopment/schemii/pull/148).
The code retains **one serialized worker per independently launched stack**;
shared administrator preferences and account/fixture ownership have not been made
safe for parallel workers. No retry or failure policy is weakened.

## Ordinary CI evidence and limits

Weights come from [ordinary PR run 36651493606](https://github.com/LandMineDevelopment/schemii/actions/runs/36651493606),
attempt 1, checked-out source `f2cf555e8a62263d2caf97dcae9f27407f8c7508`.
All four browser lanes completed, with **232 desktop and 231 Android passes**,
six/seven existing skips and **zero retries/first-attempt failures**. The whole
workflow failed its separate Python/static lanes. Successful browser receipts
are scheduling evidence, not a successful full acceptance or a comparable
whole-workflow success sample. No run was dispatched to collect these weights.

These are separate metrics: the Actions browser step includes setup before the
reporter begins; reporter wall time includes its attempt lifecycle; file weights
sum nonoverlapping Before Hooks, test body and After Hooks. Queue/launcher time
is not inferred from that sum. Hosted hardware/topology variation was not measured.

| Profile | Observed browser step, shard 1 / 2 | Reporter wall, shard 1 / 2 | First-attempt file cost, shard 1 / 2 |
| --- | ---: | ---: | ---: |
| Desktop Chromium | 243s / 314s | 239.296s / 311.023s | 236.827s / 307.827s |
| Android Chromium | 288s / 439s | 284.372s / 435.257s | 280.764s / 432.205s |

Exact bytes of the validated downloaded public JSONL receipts had these SHA-256
digests. `source_id` is the SHA-256 of the repository-relative spec path, not a hash
of its contents. Matching those identities to the checked-out inventory produced
61 file weights, independently for both profiles. Only first attempts contributed;
retries remain a separate reliability/cost category.

```text
desktop shard 1  4da478789d77f19e4939fcda7e443f7b1601aa661cbb2de12ce73cec9e03c53f
desktop shard 2  476decd5a07a7b43a1219736603781c55afe8eda3a58013a3ae6b7dae50985b4
android shard 1  91b45a91b56dbf9eb340e934e1756292e848c0b114638692ee492ef861c54682
android shard 2  e863a720f779e3649526fe3cfc53e8bd39be3e4e7d3e066882d8641dfee10191
```

## Scheduling and invariant coverage

The launcher first asks the **real pinned Playwright runner** for discovery of
one intended project. It groups that inventory by whole file, sorts by descending
measured profile cost, then assigns each next file to the lower-cost shard, with
deterministic file/count tie breaks. New files receive the profile's median
positive observed cost and remain in the inventory. Known currently skipped
files retain their observed small cost and are not removed. Changing prerequisites
or test bodies requires new ordinary observations; a weight is an estimate.

The config applies the exact selected file manifest as an escaped regular
expression. The launcher forwards **no native `--shard`** and no additional worker
flag. A second sharding/filtering pass would lose cases. CLI options are bounded
to the two known profiles, two shards, `--plan` and `--list`.

| Invariant | Unique oracle and cost treatment | Intended scope |
| --- | --- | --- |
| Retained results plus raw transaction leave catalog capacity | Real assembled HTTPS/PostgreSQL access while both resource types stay live; retained result/session cleanup remains | `console-shared-capacity`: one desktop request case |
| Bulk commits, cancellation rollback and resume exclusion | Exact committed rows, elapsed floor, primary-key protection against replay, owned table/job cleanup | `bulk-jobs`: two desktop request cases |
| Temporary objects, savepoints, plans and commit/COPY semantics | Real same-backend SQL, explicit transaction state, exact CSV bytes and rollback outcomes | Three tagged `raw-console` request cases once; its four actual browser workflows remain on both profiles |
| Concurrent dashboard creation versus model deletion | Both legal race outcomes, no orphan and exact owned cleanup | One tagged request case once; rendered deletion protection remains on both profiles |
| Large COPY ingress | 24 MiB upload/download exact hash and session closure | Existing desktop-only transport case; Android discovery excludes the already skipped duplicate |
| Cursor disappears with its clicked control | Actual target visibility plus cursor class immediately after target removal, before unrelated completion clears it | Two controlled cases on both profiles |
| Real animation, reduced motion, resize and accessibility | Real-time playback/resize, reduced-motion replay and readable action list; original assertions unchanged | Both profiles |

Seven confirmed request-only cases are tagged `@request-only` where their
callbacks own them. Android excludes that tag; no UI/page case is tagged. The
observed Android costs removed from the mixed files are **6.708s** (`raw-console`)
and **2.615s** (dependency race). Whole request-only files disappear from Android
discovery. This preserves transport assertions and cleanup while removing their
second device execution. It does not turn failures into skips.

The actual discovery after these changes has **239 desktop cases across 61 files**
and **231 Android cases across 58 files**. Splitting the old two-product cursor
test into two independent cases adds one case per profile. The transport scope
removes seven Android cases and its preexisting skipped large-COPY duplicate.
The four custom shard assignments are disjoint and their union equals all **470
intended project/case identities**. A fresh temporary checkout containing a new
nested spec also proves exact inclusion on both profiles. Fixtures are removed
after these offline checks; no browser/application/session is started.

| Profile | Projected first-attempt file cost, shard 1 / 2 | Projected slow/fast ratio |
| --- | ---: | ---: |
| Desktop | 272.312s / 272.342s | 1.00011 |
| Android, request scope applied | 345.577s / 345.556s | 1.00006 |

Those are **offline projections replaying one observed cohort**, not measured
post-change runtime or speedup. They leave the previous guide-file cost unchanged
because controlled playback has not yet been timed on the assembled app. File
cost scheduling suggests 35.485s desktop and 86.628s Android improvement in the
slow shard's summed attempt time, before setup/variation or a new limiting job.
That is neither a critical-path wall guarantee nor aggregate runner savings.
Request-scope projection removes 21.823s of duplicated Android attempt work;
balancing alone redistributes work without saving aggregate test minutes.

## Controlled guide timing

The previous combined disappearing-target body took **20.534s desktop** and
**20.736s Android**, independently of 32/44ms setup and 100/270ms teardown.
The replacement installs `page.clock` **before navigation** in a Before Hook,
then pauses it after loading and before opening the guide. Network/navigation
therefore remains separate setup evidence. Each product gets its own case.

The controlled step advances every relevant callback with `clock.runFor`, not
`fastForward`, which can fire due timers only once. It asserts each visible target,
cursor movement and click phase, then the last target's actual hidden state and
the cursor's removal before advancing any completion/replay timer. The existing
Schemoo bookstore target label assertion remains. A broken disappearance handler
must fail here rather than pass because completion later hides the cursor.

The controlled assertion step has a **3,000ms wall timeout**; before-hook loading
is outside that bound. This is an implemented acceptance bound, **not a measured
pass**: assembled execution and a mutation/reproduction showing failure when
disappearance handling is broken remain required in the coordinator's deployment
window. Real-time and reduced-motion cases still use real timers. Clock behavior
follows the [official guide](https://playwright.dev/docs/clock) and
[Clock API](https://playwright.dev/docs/api/class-clock).

## CI execution and feedback phases

The workflow uses the bridge for browser execution and retains `./start.sh`, the
four matrix jobs, independent stacks, one worker, bootstrap policy, public timing
validation and the desktop-shard-1 backup verification lane.

Delivery on `40cb6d5` preserves the subsequently delivered report-only controls:
the complete classifier uses the prospective merge checkout, source changes
retain all seven required source jobs, and the always-evaluated final gate rejects
missing, cancelled or stale run/head/attempt receipts. Browser scheduling changes
only the browser command and the one installed-discovery step; classifier,
report-validator, gate, collector and canonical Node discovery code are unchanged.

```bash
node scripts/ci/run-browser-shard.mjs --project=desktop-chromium --shard=1/2
# CI substitutes matrix.project and matrix.shard. Do not append --shard again.
node scripts/ci/run-browser-shard.mjs --project=android-chromium --shard=2/2 --plan
node scripts/ci/run-browser-shard.mjs --project=desktop-chromium --shard=1/2 --list
```

The bridge sets `CI_TELEMETRY_PROJECT` and `CI_TELEMETRY_SHARD`. Since the custom
runner has no native `config.shard`, the timing reporter selects its lane with:

```js
config.shard?.current || Number(process.env.CI_TELEMETRY_SHARD || 0)
```

The existing metadata allowlist validates the shard value. A focused reporter
regression retains correct custom identities on every plan/attempt/end record,
failed first attempts, native-shard precedence and rejection of invalid labels.
The public timing reporter remains in `playwright.config.js`.

The four pure scheduling contracts live in `scripts/ci/browser-shards.test.mjs`,
already discovered by canonical `npm test`. They use only Node builtins and run
before npm installation or Python setup. They cover deterministic balance, new
file estimates, exact literal manifests and the absence of a second shard or
worker override. Neither the canonical runner nor its families need expansion.

The two real Playwright checks live in
`tests/browser-infrastructure/shards.test.mjs`, outside both Playwright's E2E
directory and early Node families. They run once in desktop shard 1, **after
`npm ci` and before `./start.sh`**. They prove the actual complete inventory and
new nested-spec discovery without starting a browser or application. The workflow
contract requires that phase/order, exact command and single-lane condition.

Focused offline verification runs the scheduling/reporter contracts, installed
inventory checks, browser workflow contract, real runner's `--list`, JS syntax
checks and `git diff --check`. The actual application was not rebuilt, started,
navigated or changed for this work.

At this integration checkpoint the four pure contracts, four reporter checks
and two installed-runner inventory checks passed together (10 checks, 4.683s).
The two workflow-contract variants passed in 1.80s. A disposable Git checkout
with **no `node_modules` or dependency installation** ran canonical `npm test`
with public timing enabled: all **423 attempts passed in 1.289s**, with no skips.
That local Node 25.2.1 observation proves the dependency boundary; it is not a
hosted Node 22 CI wall-time sample. The first scratch attempt lacked the Git
checkout metadata required by an unrelated native ownership test; adding a
normal disposable Git repository fixed that fixture, without changing product
code or reducing the selected tests. Scratch checkouts were removed in `finally`.

Current delivery verification retained the exact reviewed scheduling/guide source
and ran in a disposable Git archive of the integrated tree. Canonical Node
feedback passed **481 attempts with no failures/skips**, before any npm dependency
installation or Python environment, in **9.975s reporter wall time**. The current
suite includes the subsequently delivered load-fixture contracts; this is a
different inventory from the earlier 423-case observation, on a shared local
Node 25.2.1 host, and establishes no hosted speedup or slowdown.
After installing only the existing pinned Node dependencies in that archive,
the two real discovery checks passed in 5.474s and independently counted the same
239/231 profile cases and seven desktop-only request cases. All **204 focused
classifier/validator/timing/deployment contracts passed in 12.35s** without skips.
Six offline mutants failed their intended assertions: double sharding, dropped
unknown files, missing exact manifests, missing custom telemetry identity,
duplicated request-only device execution and the old workflow browser command.
Ruff lint/format, all changed JavaScript syntax, SHA-verified actionlint 1.7.12
and diff checks passed. The owned archive and its installed dependencies were
removed automatically when its verification context exited; selected receipts
remain. This does not satisfy the separate assembled guide/disappearance mutant.

## Remaining acceptance and natural monitoring

Keep #128 open with a non-closing reference until one representative full source
acceptance validates scheduling, controlled guide duration/failure detection and
account/preference/fixture cleanup. Then monitor the **next ten naturally
occurring comparable source runs** for a slow/fast browser-step ratio at most
1.15, preserving first-attempt failures and excluded incomplete/cancelled/changed
inventories. Later normal #148 runs had not been available at this evidence cut;
no repeat suite was launched to manufacture samples.

Report critical-path wall time and aggregate runner minutes separately, with
launcher/setup/backup cost and the file inventory. Explain a remaining indivisible
file limit if the ratio misses the target. Mechanical shard proof does not
establish [#73 manual UI acceptance](https://github.com/LandMineDevelopment/schemii/issues/73)
or native harness replacement/cleanup parity in #137. The delivered #127
report-only classification retains its separate full-source acceptance boundary.

## Earlier ordinary runs before per-attempt receipts


Read existing Actions run/job/step timestamps; no suite was dispatched to create
samples. These three ordinary PR runs completed successfully by September 30,
2026, 00:33 UTC. These are three source-changing PR runs, not ten comparable
source samples, a reliability denominator or a controlled before/after experiment.
PR head identities are `7a01ff5` (#145), `3e4fdf6` (#146) and `7c165c9` (#147).
Actions checks may execute a generated PR merge that incorporates a newer base;
future receipts identify the actual checked-out source through `github.sha`.

Sources: [#145 run 36647086085](https://github.com/LandMineDevelopment/schemii/actions/runs/36647086085),
[#146 run 36648539591](https://github.com/LandMineDevelopment/schemii/actions/runs/36648539591),
[#147 run 36649791190](https://github.com/LandMineDevelopment/schemii/actions/runs/36649791190).
The measured browser jobs use the same two projects, two native file shards and
one serialized worker per independently launched stack. The PR-head comparisons
show no changes to browser specs, Playwright configuration or package scripts;
the actual run step inventory confirms the browser commands. Hardware variation
between hosted runners is not measured here.

All table durations are seconds. `Other` is job elapsed minus launcher and
browser-step elapsed: dependency/browser installation, checkout, upload and
other overhead. Desktop shard 1 also owns isolated backup verification, so it
is not a pure browser scheduling lane.

| Run / profile | Shard 1 browser | Shard 2 browser | Slow / fast browser ratio | Shard 1 launcher / other / job | Shard 2 launcher / other / job |
| --- | ---: | ---: | ---: | --- | --- |
| #145 desktop | 294 | 343 | 1.167 | 59 / 40 / 393 | 58 / 33 / 434 |
| #145 Android | 299 | 317 | 1.060 | 60 / 34 / 393 | 47 / 29 / 393 |
| #146 desktop | 289 | 410 | 1.419 | 58 / 38 / 385 | 64 / 35 / 509 |
| #146 Android | 227 | 369 | 1.626 | 48 / 32 / 307 | 90 / 87 / 546 |
| #147 desktop | 290 | 419 | 1.445 | 58 / 37 / 385 | 57 / 31 / 507 |
| #147 Android | 259 | 451 | 1.741 | 67 / 37 / 363 | 59 / 38 / 548 |

Desktop shard 2 was the slower browser step in all three observations. Android
shard 2 varied from a small gap to a 192-second gap. In #146 its extra 42 seconds
of launcher time and 55 seconds of other overhead explain 97 seconds of job
imbalance separately from the 142-second browser-step gap. Combining those
categories would overstate what test rescheduling can fix.

| Run | Source jobs' critical path from creation | Summed seven job-minutes | Python job | First Node feedback from job start | Unit job start after run creation |
| --- | ---: | ---: | ---: | ---: | ---: |
| #145 | 463s | 35.867 | 461s | 6s | 2s |
| #146 | 549s | 38.150 | 472s | 7s | 3s |
| #147 | 551s | 39.367 | 481s | 7s | 2s |

Workflow `run_started_at` equalled `created_at` in these snapshots. Job starts
were 2–5 seconds after creation; these API timestamps mix runner dispatch and
scheduling and do not establish pure queue delay. Node's own step took 3 seconds
in each run. This confirms early feedback in the ordinary runs after #124.

In #145 Python remained the critical path even though desktop browser work was
uneven. #146/#147 were limited by Android shard 2. Balancing browser execution
alone cannot promise an equal wall-clock saving: Python, launcher variability,
backup verification and indivisible file/setup cost can become the limiting job.
No measured speedup or aggregate runner saving is claimed for a change not run.
