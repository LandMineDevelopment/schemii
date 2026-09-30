# Browser shard balance from ordinary CI runs

This is measured scheduling evidence for [#128](https://github.com/LandMineDevelopment/schemii/issues/128),
which depends on the per-attempt receipts in [#125](https://github.com/LandMineDevelopment/schemii/issues/125).
[#127](https://github.com/LandMineDevelopment/schemii/issues/127) separately owns
docs-only classification and its required-check gate. No browser assignment,
worker count, skip or retry policy changes are made by this report.

## Observed runs

Read existing Actions run/job/step timestamps; no suite was dispatched to create
samples. All three ordinary PR runs completed successfully by September 30,
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

## Smallest supported scheduling proposal

Retain one worker per stack and separate desktop/Android weights. Increasing
workers against the shared admin preference/session fixtures would add an
ownership problem before it establishes a speed benefit. Per-attempt telemetry
must provide file-level first-attempt setup, execution and teardown costs; the
current step totals cannot identify which file to move safely. Existing retries
also mean a green job alone does not establish first-attempt success.

Once naturally occurring complete receipts are available, aggregate their
`source_id` file identities per project and retain first-attempt cost separately
from retry cost. Build a deterministic, duration-weighted whole-file assignment
from those observed costs. Keep a conservative deterministic fallback for newly
discovered files and prove that assignments are disjoint and their union equals
the intended discovery inventory. Do not apply native `--shard` a second time
after filtering files. Account for fixture setup/cleanup and the backup lane,
rather than balancing only test-body durations.

Keep request-only transport/race scope distinct from real desktop/mobile
interaction and geometry before reducing duplicate device execution. Guide
clock work is another independent test-behavior change; aggregate shard gaps do
not establish which assertions or waits can be removed.

Validate a scheduling implementation in one ordinary source acceptance, then
monitor the next ten naturally occurring comparable runs for the #128 ratio
target of at most 1.15. Preserve cancellations, incomplete shards and changed
inventories as explicit exclusions, and triage first-attempt failures and fixture
leaks independently of duration. The three observations here support collecting
weights and preserving serialized ownership; they do not support a specific
file movement or demonstrate that the target has been met.

For #127, none of these PRs is a docs-only classifier sample. Its allowlist,
base/head and rename/deletion semantics, local-link checks and always-evaluated
required gate still need implementation and dedicated verification. This report
cannot be used to claim its one-minute docs target or runner savings.
