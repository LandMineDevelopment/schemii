# Testing feedback and coverage

The supported deterministic commands are `npm test` and `python -m pytest -q`.
Use the constrained Python bootstrap in [testing/README.md](../testing/README.md).
Default Python discovery includes `tests/` and `testing/`; it does not launch the
application or opt-in load/browser work. CI runs the Node checks before Python
setup. Dispatch delay and setup time still contribute to hosted feedback; the
30-second early-feedback target requires an ordinary hosted run, not a local claim.

Local and CI Node execution share `npm test` and the single discovery runner
`scripts/ci/node-tests.mjs`. It selects deterministic `.test.js`/`.test.mjs` files
from frontend, harness, telemetry and the load-unit family when that family is
present. Browser specs and executable load scenarios are excluded. Setting
`CI_TELEMETRY_FILE` adds the approved reporters before the selected file arguments
without changing discovery; there is no second CI test-glob list. The deployment
contract check executes the actual CI command in a temporary four-family checkout
and checks unique receipts, with planted opt-in files that fail if selected.

## During development

Use the inventory below to choose the smallest check that exercises the changed
invariant. Run it after each meaningful edit, then let the ordinary PR acceptance
path cover the complete source change. Repeated full browser matrices are not a
substitute for a focused regression or a faithful controlled failure.

1. For frontend state, start with the relevant `node --test` files, or use
   `npm test -- --test-name-pattern='<known invariant>'` for a known named check.
   Confirm the intended cases actually execute; a filter matching no tests
   supplies no behavioral evidence.
2. For Python ownership or API behavior, pass the relevant test modules to
   `python -m pytest -q`. Keep installed-graph, real-database or mounted checks
   whenever the invariant depends on those boundaries.
3. Run incremental Python quality against the intended branch base with
   `python scripts/check_python_quality.py BASE`, using the constrained quality
   environment. An unavailable comparison stops the check; resolve the intended
   base before continuing rather than treating an empty selection as validation.
4. When ownership is unknown or a change crosses boundaries, use the broader
   Node/Python commands and the required source CI layers. A narrow selection
   is feedback during development, not permission to omit acceptance.

Use `python scripts/test-changes.py --base origin/main` to print the actual
Git comparison, committed/staged/unstaged changed paths, untracked status and all
required commands. Existing owned regular-file modifications with stable Git and
working-tree modes retain their profile before commit. The union of each diff
boundary matters: an unstaged reversal cannot hide a staged shared change.
Unknown, untracked, new, renamed, deleted, conflicted, symlinked, mode-changing or
mixed-owner work selects full checks.
Index flags that can conceal tracked edits also prevent narrow selection; the
plan lists those paths as unverified rather than claiming they are unchanged.
Git configurations that disable executable-mode or symlink discovery also force
full checks, since they can conceal changes outside the visible diff.

Use `--feedback` to execute the selected deterministic checks during development.
The plan explicitly lists any required PostgreSQL/browser layers as pending;
successful feedback is not full acceptance. `--run` executes the complete plan
and stops on the first failed check or missing prerequisite at that layer's
boundary, so missing live credentials do not block earlier cheap checks.
Real-database checks require `SCHEMII_TEST_METADATA_DSN`. Browser checks require
explicitly owned account credentials or bootstrap consent. The application
command remains `./start.sh`; deployment leases still apply.

## Affected PR acceptance

A strict schema-2 descriptor selects one reviewed profile from the complete
PR merge-base-to-head diff. Only modifications to frozen existing regular files
with unchanged modes qualify; new siblings, shared helpers, mixed families,
renames, deletions, symlinks, dependencies, startup, CI policy and every product
source change select full acceptance. Source/test/tooling pushes to main and
manual dispatch run full acceptance. The existing positively verified audit
report shortcut remains available on both PRs and main.

| PR profile | Required checks |
| --- | --- |
| native tooling | Node, static Python, all native Python controls |
| harness or load tooling | Node, static Python, native + harness + load-planner Python closure |
| existing backend test leaves | Node, static Python, complete deterministic Python |
| existing frontend test leaves | complete Node |
| existing E2E test leaves | Node and complete desktop + Android browser acceptance |
| full | Node, static Python, complete Python, real PostgreSQL and all browser acceptance |

The exact path lists and closures live in
[scripts/ci/test_selection.py](../scripts/ci/test_selection.py). This first mapping
accelerates tooling and test-only PRs; product changes retain full acceptance.
Complete CI Python uses `python scripts/ci/python-tests.py`: two isolated processes
keep the inspection fixture consumers together and all other discovery in the
other process, merging one strict canonical receipt. Explicit tooling arguments
use the same runner. Browser acceptance uses three isolated hosted application
stacks per device, retaining serialized whole-file execution and every case.
The gate derives exact expected jobs and receipt lanes from the profile, requires
selected layers to succeed, and requires excluded workflow needs to be skipped.
Missing/duplicate/stale/failed/recovered selected receipts cannot pass; full and
browser profiles require all six browser legs. Unknown step time remains `null`.

For scale only, natural main run 36763414216 attempt 1 took 474 seconds to its
source critical path: unit wall 258 seconds and browser walls 354–457 seconds.
Omitting its PostgreSQL and four browser jobs would remove 29.167 job-minutes;
retaining its unchanged unit job would imply a 277-second source path. This is
an estimate from old job boundaries, not measured selective-CI speed. Fresh
ordinary full and selected PR runs must establish actual elapsed savings.

## Ordinary hosted cost reference

The successful [PR #167 run, attempt 1](https://github.com/LandMineDevelopment/schemii/actions/runs/36666053425)
checked out source `86edfe85bbe99cf984a9976e931243e33d3ad7fe`. This is one
ordinary Ubuntu/Python 3.12/Node 22 observation, not a percentile or a controlled
before/after benchmark.

| Layer | Observed inventory and time | Interpretation |
| --- | --- | --- |
| Node | 478 passed; 9.577s reporter wall | First Node feedback arrived 14s after its job started, about 26s after workflow creation |
| Default Python | 2,031 collected; 1,945 passed, 86 skipped; 264.705s reporter wall | Setup 52.740s, execution 203.789s, teardown 0.347s; skips retain their prerequisite limits |
| Inspection subset | All 39 passed; 129.994s of disjoint setup/execution/teardown | Total phases are 49.1% of Python wall; its 114.399s execution is 56.1% of Python execution; shared setup is charged to its initializing case |
| Slowest browser shard | Android shard 2: 550s job wall | Launcher startup 61s, test step 438s, setup/other 51s |
| Complete workflow | 582s from workflow creation to final job completion; 562s acceptance critical path; 37.367 total job-minutes | Concurrent job costs must not be added to estimate developer wait |

Reporter wall, job wall and workflow elapsed describe different boundaries.
Phase sums exclude collection, startup and reporting. Dispatch/start delays
include scheduling and dependencies; the available timestamps do not isolate
pure runner queue time. Compare future ordinary runs at their recorded source
and inventory, retaining failed attempts rather than selecting only green ones.

## Focused inventory

Costs below are audit observations at the referenced baseline, not current
percentiles. Focused commands are from the repository root and do not replace
the broader acceptance path.

| Ownership / invariant | Unique coverage / cheapest faithful oracle | Focused command | Observed cost and broader layer |
| --- | --- | --- | --- |
| Auth and exact connection ownership | Denial, revoked sessions and cross-owner fencing; in-memory API outcomes | `python -m pytest -q tests/test_account_auth.py tests/test_query_cancellation.py` | Part of the remaining ~68s Python suite; real PostgreSQL verifies persistence and role enforcement |
| Migration execution | Stale revision/target, one execution, uncertain commit and no DDL replay; fake transport exercises lifecycle races | `python -m pytest -q tests/test_migration_execution.py` | Preserve real PostgreSQL rollback/catalog/restart and mounted UI review/apply paths |
| Console and cancellation | Session owner/turn/revocation fences, cancellation and raw transaction state | `python -m pytest -q tests/test_raw_console.py tests/test_query_cancellation.py` | Unit reaping does not establish lifespan scheduling or real lock/admission release; retain real transport cases |
| Semantic compiler | Safe AST, grain/filter/repetition and generated SQL contracts; pure compiler inputs | `python -m pytest -q tests/test_schemoo_filters.py tests/test_schemoo_repetition.py tests/test_schemoo_column_comparisons.py` | Real PostgreSQL result oracles cover type/NULL/calendar semantics that AST checks cannot |
| Changed frontend state | State ordering, late responses, aborts, cache budgets and fixture ledgers; Node component outcomes | `node --test tests/frontend/request-coordinator.test.js tests/frontend/schemer-result-cache.test.js` | Whole Node suite: 395 passes, ~0.63–0.68s local / ~2s hosted at audit; browser adds physical geometry and persistence |
| QA provisioning and cleanup | Owned fixture ledger, redaction, preserved peers and interrupted cleanup; temporary directories/mocked API | `python -m pytest -q testing/test_report_author_fixtures.py testing/harness` | 30 audit cases, ~0.08–0.14s; native guardrail cases are additionally collected, without duplicate unittest invocation |
| Native stock-agent guardrails | Assignment, runtime capacity/config, isolated connection ownership and cleanup algorithms | `python -m pytest -q testing/agents` | Deterministic subprocess/temp-data regressions; mechanical isolation probes and authenticated manual acceptance remain separate |
| Inspection graph | Graph completeness, no I/O/secrets, identity and derive-once; immutable document or small synthetic graph where faithful | `python -m pytest -q tests/test_developer_inspection.py tests/test_system_inspection.py tests/test_route_inspection.py tests/test_database_inspection.py` | 36 cases accounted for at least 149.46s / 67.2% of prior full run; fixture work tracked independently in #126 |
| Mounted browser workflows | Save/reopen, keyboard focus, paging, permissions and mobile geometry; real UI plus exact persisted/result assertions | `npm run test:e2e -- --project=desktop-chromium tests/e2e/schemoo-model-editor-audit.spec.js` | Requires `./start.sh`, test-owned fixtures and released deployment leases; all projects/shards remain in CI |

Check actual collection with `python -m pytest --collect-only -q`. The September 29
delivery baseline `77af33c` collected 1,628 default cases and 144 additional cases
under `testing/`; the original omitted provisioning inventory is 30 of those.
Counts grow as focused regressions land. Report collected, executed and skipped
counts together; PostgreSQL skips do not prove database behavior.

## Pre-merge acceptance and reuse

Use the complete change plan and the current PR's selected profile. Inspect
existing checks and their actual source-bound receipts before running acceptance;
the coordinator schedules missing work once. A new agent, review, handoff, worktree
or merge does not require another local full suite or a manually dispatched CI run.
Independent review examines actual evidence and reproduces material findings or
gaps with focused controls. Record commands, scope, tested source, outcomes and
evidence locations; distinguish inspected evidence from checks you actually ran.
Relevant source changes, failures and missing/stale evidence justify fresh checks.
Passing required hosted checks must belong to the current PR head; preserve failed
attempts and let GitHub schedule required PR/main acceptance automatically.

For a full profile, acceptance comprises Node and complete Python, incremental
Python quality against the intended base, explicitly configured real PostgreSQL
and all six browser legs. A narrower reviewed profile omits only its documented
unrelated layers. Browser CI uses the canonical
HTTPS stack through `./start.sh`; it does not replace manual native-agent UI
acceptance in #73/#137. Backup recovery remains an isolated-database check.
Live provider/report specs require their own authenticated fixture prerequisites;
track their skips and ownership rather than treating a green default run as proof.
Full capacity ramps/soaks in #135/#136 stay outside the normal PR feedback path.

Do not remove meaningful coverage, increase skips/timeouts or add retries to
conceal failures. Prefer a cheaper oracle only when it detects the same plausible
defect; distinguish source/package contracts from visible behavior guarantees.

## Public CI timing and evidence policy

`npm test` uses one deterministic discovery path for frontend, harness, telemetry
and available load-unit tests. Each required family, and any present optional
load family, must contain tests; an absent load family remains supported while
that work lands. Browser acceptance and stress execution files stay outside this
command. `npm test -- <Node options>` forwards the original argument array before
the discovered files, including name selection, reporters and destinations.
Instrumentation adds its sanitized reporter alongside the requested reporter,
preserving native unknown-option and reporter/destination mismatch errors.
A focused name-filter receipt describes only that selected inventory; ordinary
unfiltered CI remains the full deterministic lane. Controlled four-family checks
prove excluded body failures are not executed with a matching name filter and
still fail the unfiltered command, both with and without instrumentation.
SIGINT/SIGTERM checks verify the runner stops its owned waiting test process and
retains passing receipts as incomplete cancellation evidence.

GitHub Actions artifacts for this public repository are public evidence. Each
Node, Python, PostgreSQL and browser lane emits JSONL with a fixed schema: source
SHA, numeric run/attempt, fixed lane/project/shard labels, hashed test and source
identities, source line, attempt, outcome and phase durations. Titles, parameter
labels, arbitrary skip reasons, errors, stdout/stderr, request/response content,
fixture objects and environment values are never serialized. Hash identities can
be matched to the checked-out test inventory without publishing dynamic titles.
Skip metadata says `declared-or-runtime`; prerequisites remain in the test source.

Before upload, `scripts/ci/summary.py` validates every record's exact fields and
types, duplicate/missing attempts and plan completeness. Upload steps point only
at the validated JSONL and derived summary, with seven-day retention. A failed
validation blocks that artifact. The previous whole Playwright-results/HTML
upload is removed: traces, screenshots, raw logs, storage-state, cookies,
Authorization headers, provider keys and fixture secrets are not approved public
artifacts. Existing trace-disabled sensitive tests retain their policy. Sanitized
failed-attempt receipts remain available when a retry makes the job green.

Python reports setup, test-body and teardown durations independently. Playwright
counts only top-level Before/After Hook steps; nested durations overlap and are
not added twice. Node exposes test durations and whole-run elapsed time, without
a distinct per-fixture setup API. Node tests execute concurrently, so summed test
durations can exceed elapsed time; do not subtract their sum to infer overhead.
The Actions rollup uses job/step timestamps for launcher startup, test steps and
remaining setup/other time, workflow dispatch delay, start delay after workflow
start, critical path and total job-minutes. GitHub timestamps do not isolate pure
runner queue time from scheduling/dependencies; `after_workflow_start_ms` retains
that limitation explicitly. Missing, malformed or reversed timestamps on a
named test or launcher step produce `null` for that phase and its setup residual;
partial phase totals must not appear as zero execution or extra setup. Genuine
zero durations remain zero. Explicitly skipped conditional steps contribute
zero even without timestamps; pending/in-progress steps remain unknown. Jobs
without a named phase keep zero for that phase. Unknown job wall time makes total job-minutes unknown; inconsistent
phase totals exceeding job wall also leave the setup residual unknown. Known
job wall, outcomes and independently measured phases remain available. Missing
phase measurements do not replace the complete job/test outcome and identity
requirements of the acceptance gate.

The rollup validates downloaded timing records again and requires the exact selected
test lanes (nine for full acceptance), one source/run/attempt cohort, all expected jobs and complete test
lifecycle records. Missing shards/footers, cancelled or unstarted cases and API
collection failure mark it incomplete. A failed-but-fully-observed attempt remains
distinct from an incomplete run. First-attempt rate excludes skips/cancelled/
unstarted cases; retry recovery retains the original failed attempt in its
denominator. Expected-failure tests retain their actual failed status.

Terminal ownership is authoritative even after all test receipts passed. A lane
is complete only with a nonempty discovered inventory, an ending `passed` or
`failed` status, every planned case observed and no cancelled/unstarted cases.
Terminal cancellation, timeout, `collection-error` and `error` remain incomplete;
their observed passing attempts are retained without establishing acceptance.
Pytest collection reports distinguish import/collection failures from human
interruption because both otherwise use exit code 2. Collection failures remain
`collection-error` when explicitly continuing to execute valid collected cases.
Only fixed error categories are published; exception text and fixture values are
excluded. Hash identities require JSON strings of the exact length and format,
so integer lookalikes cannot pass artifact validation.
Node failed file-bootstrap wrappers similarly set terminal `error` outside the
individual test denominator; undiscovered cases in an unloadable file cannot be
treated as complete even when another file's tests all pass. Ordinary observed
assertion failures keep their failed-attempt records and terminal `failed` status.
Playwright global setup/teardown or runner errors set terminal `error` through
its lifecycle error hook, retaining passed attempts without claiming completion.

Reliability sampling uses the next ten naturally occurring comparable source
runs, including first-attempt failures. Record canceled/incomplete runs and
changed test inventories as exclusions; successful complete-run duration is a
separate cohort. No artificial reruns populate this sample. Any retry recovery
is a triage signal with the test identity, first-attempt status and timing; keep
the existing retry policy while identifying and fixing the actual cause.

Local verification on Python 3.14.7 / Node 25.2.1 used secret sentinels in failure
messages, titles, skip reasons, fixture values, attachments and API payloads.
The real Playwright runner synthetic retry/pass/fail/skip test starts no browser
or application; it is additionally runnable after `npm ci`. The reporter-free
398-case Node run took 664.7ms; the instrumented run took 1,028.6ms and wrote
228,822 bytes (~224 KiB). These single observations on a shared host suggest
~364ms overhead, not a percentile or controlled benchmark. The 12-case fixture
subset emitted 7,319 bytes and completed in 0.05s with Python timing enabled.
Hosted Python 3.12 / Node 22 behavior and ordinary-run overhead remain CI checks.
