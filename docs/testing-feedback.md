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

No additional selector or dependency is needed: these are the existing supported
commands, and the same Node runner owns local and hosted discovery.

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

## Broad pre-merge acceptance

Run the full Node and default Python commands, incremental Python static quality
against the intended change base, the explicitly configured real PostgreSQL
integration job, and the assembled browser matrix. Browser CI uses the canonical
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
that limitation explicitly.

The rollup validates downloaded timing records again and requires all seven
test lanes, one source/run/attempt cohort, all expected jobs and complete test
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
