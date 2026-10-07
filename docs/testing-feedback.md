# Testing feedback and coverage

GitHub Actions is disabled for this repository. Development machines own testing
through `./ci.sh`, using the existing change planner and reviewed coverage profiles.
The former workflow is archived unchanged at
[testing/ci/hosted-workflow.yml](../testing/ci/hosted-workflow.yml), outside
GitHub's executable workflow directory. Its tests and provider-specific timing
and reuse tools preserve historical contracts; they do not schedule acceptance
or supply a current local pass. Do not wait for hosted checks or dispatch Actions.

```bash
./ci.sh --plan --base origin/main       # Describe all selected commands
./ci.sh --feedback --base origin/main   # Deterministic prefix; acceptance pending
./ci.sh --base origin/main              # Complete selected local acceptance
./ci.sh --full --base origin/main       # Deliberately select all layers
```

Use the constrained Python and quality bootstrap in
[testing/README.md](../testing/README.md). The complete deterministic commands
remain `npm test` and `python scripts/ci/python-tests.py`; focused Python
regressions use `python -m pytest -q PATH`. Default discovery includes `tests/`
and `testing/` once each and does not start the application, browser or opt-in
load scenarios. `npm test` uses the single `scripts/ci/node-tests.mjs` discovery
runner for frontend, harness, telemetry and the available load-unit family.
Setting `CI_TELEMETRY_FILE` adds the approved reporter without changing discovery.

## During development

Use the inventory below to choose the smallest check that exercises the changed
invariant. Run it after each meaningful edit, then schedule the selected local
acceptance plan for the complete source change. Repeated full browser matrices are not a
substitute for a focused regression or a faithful controlled failure.

1. For frontend state, start with the relevant `node --test` files, or use
   `npm test -- --test-name-pattern='<known invariant>'` for a known named check.
   Confirm the intended cases actually execute; a filter matching no tests
   supplies no behavioral evidence.
2. For Python ownership or API behavior, pass the relevant test modules to
   `python -m pytest -q`. Keep installed-graph, real-database or mounted checks
   whenever the invariant depends on those boundaries.
3. Run incremental Python quality against the intended branch base with
   `.venv-quality/bin/python scripts/check_python_quality.py BASE`, using the constrained quality-only
   environment. An unavailable comparison stops the check; resolve the intended
   base before continuing rather than treating an empty selection as validation.
4. When ownership is unknown or a change crosses boundaries, use the broader
   Node/Python commands and the required source acceptance layers. A narrow
   selection is feedback during development, not permission to omit acceptance.

Use `./ci.sh --plan --base origin/main` to print the actual
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

Use `./ci.sh --feedback` for the selected deterministic prefix. Python child
checks use the selected virtual-environment interpreter. Successful feedback
always has `acceptance: false`; the receipt lists any remaining database/browser
work as pending. `./ci.sh` runs the complete selected plan and stops at the first
failed command or missing prerequisite, after preserving the earlier outcomes.
Missing live prerequisites do not block cheap checks that precede that layer.
A failed, cancelled, incomplete or source-changing run is not acceptance.

Each run creates a private directory under `.schemii/local-ci/` with a source-bound
`receipt.json`, selected base/profile/mode, command status and elapsed time,
pending layers and sanitized per-case timing summaries where supported. The
source fingerprint includes dirty working-tree files; inspect it against the
current source before reuse. Keep receipts, logs, screenshots, traces and account
state private. No artifacts are uploaded automatically. Record prerequisite,
setup and execution boundaries honestly; a local elapsed time cannot establish
hosted queue savings. Preserve failed and blocked attempts.

## Selected local acceptance

The planner applies the complete committed/staged/unstaged change and selects the
reviewed profile. Unknown or unsafe changes select full acceptance. The exact
owned paths and closures live in
[scripts/ci/test_selection.py](../scripts/ci/test_selection.py); the command list
printed by `./ci.sh --plan` is authoritative for that checkout and base.

| Profile | Required checks |
| --- | --- |
| native tooling and its two existing documentation companions | Node, static Python, all native Python controls and changed companion Markdown/link validation |
| harness or load tooling | Node, static Python, native + harness + load-planner Python closure |
| existing backend test leaves | Node, static Python, complete deterministic Python |
| existing frontend test leaves | complete Node |
| existing E2E test leaves | Node and complete desktop + Android browser acceptance |
| Schemer result-cache source, optionally with its existing direct Node regression | complete Node, whole frontend-serving Python file and frozen eight-file browser closure on both devices |
| Developer inspection source, optionally with its direct helper regression file | complete Node, static Python, eight whole Python files and two mounted browser files on both devices |
| full | Node, static Python, complete Python, real PostgreSQL and all browser acceptance |

Native documentation companions retain content/link validation before Node and
native controls. The narrowly reviewed product profiles retain their whole-file
Python and mounted browser closures; direct-test-only changes retain the ordinary
test-owner profile. New dependencies, shared policy, added/removed files and mixed
owners do not qualify for narrowed acceptance. `--full` deliberately selects every
layer, regardless of a narrower change profile.

Full acceptance includes Node, incremental static Python quality, complete Python
and compile checks, real PostgreSQL, selected browser shards on both desktop
Chromium and Android, and the launcher's isolated backup recovery smoke. Full
stress ramps and ten-minute browser lifecycle probes stay outside normal feedback
unless deliberately assigned. Test/CI/instruction-only changes do not require an
application rebuild merely to show the change; a deliberately selected mounted
browser layer still uses the canonical launcher.

Real PostgreSQL requires both `SCHEMII_TEST_METADATA_DSN` and
`SCHEMII_TEST_METADATA_PASSWORD` for an explicitly provisioned disposable external
database. Tests create and clean owned schemas. Do not connect them to user data
or the launcher's private metadata database, and do not invoke Docker directly.
Browser acceptance needs owned test credentials through private
`SCHEMII_E2E_CREDENTIALS_FILE` or explicit username/password variables, or explicit
`SCHEMII_E2E_BOOTSTRAP=1` consent on a fresh disposable stack. Missing prerequisites
are blocked, not skipped acceptance.

The coordinator schedules mounted acceptance after manual QA deployment leases
release. The local runner acquires the deployment/startup locks, uses `./start.sh`
as the only application lifecycle command and verifies both canonical
`https://localhost:8001` and Tailscale preview readiness before selected browser
execution. Do not narrow the selected device/shard inventory through ambient
filters or alternate origins. The browser suite does not replace authenticated
native-agent manual UI acceptance in #73/#137. Live-provider specs have separate
explicit prerequisites; their skips do not establish provider acceptance.

## Pre-merge acceptance and reuse

Inspect the complete change plan and existing local receipts before scheduling
missing acceptance once. Independent review inspects the actual source, scope,
commands, outcomes and evidence and reproduces material findings or missing
proof with focused checks. A new agent, handoff, worktree, review or merge is not
a reason to repeat a still-applicable check. Record what was run versus inspected.

A retained receipt may be reused only while its tested source, comparison base,
policy, selected inventory and required layers remain applicable to the current
PR head. Verify those boundaries explicitly; a matching filename or green process
exit is insufficient. Dirty-source evidence must be reconciled with the delivered
tree rather than relabeled as a current-head run. Failures, source changes and
missing/stale evidence justify fresh checks. Pending or blocked layers remain
visible and prevent a complete acceptance claim. GitHub schedules no required
PR/main checks for this repository. The historical hosted donor/gate protocol
below is not a substitute for this local evidence review.

Do not remove meaningful coverage, increase skips/timeouts or add retries to
conceal failures. Prefer a cheaper oracle only when it detects the same plausible
defect; distinguish source/package contracts from visible behavior guarantees.

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
| Mounted browser workflows | Save/reopen, keyboard focus, paging, permissions and mobile geometry; real UI plus exact persisted/result assertions | `npm run test:e2e -- --project=desktop-chromium tests/e2e/schemoo-model-editor-audit.spec.js` | Requires `./start.sh`, test-owned fixtures and released deployment leases; both devices and all selected shards remain required locally |

Check actual collection with `python -m pytest --collect-only -q`. The September 29
delivery baseline `77af33c` collected 1,628 default cases and 144 additional cases
under `testing/`; the original omitted provisioning inventory is 30 of those.
Counts grow as focused regressions land. Report collected, executed and skipped
counts together; PostgreSQL skips do not prove database behavior.

## Historical hosted workflow and measurements

The following sections record the archived GitHub Actions contract and measured
results before hosted execution was disabled. Hosted jobs, matrices, public
artifact policies, automatic dispatch and provider donor verification below are
historical reference and regression fixtures, not current execution or merge
requirements. Keep their original identities and measured limitations; do not
claim those timings describe the local runner or turn old hosted receipts into
local acceptance. Current local policy is defined above.

### Archived hosted profile and gate contract

A strict schema-2 descriptor selects one reviewed profile from the complete
PR merge-base-to-head diff. Only modifications to frozen existing regular files
with unchanged modes qualify; new siblings, shared helpers, mixed families,
renames, deletions, symlinks, dependencies, startup, CI policy and unmapped
product source changes select full acceptance. Source/test/tooling pushes to main run full
acceptance unless the exact-tree post-merge proof below succeeds. Manual dispatch
always runs full acceptance. The existing positively verified audit
report shortcut remains available on both PRs and main.

| PR profile | Required checks |
| --- | --- |
| native tooling and its two existing documentation companions | Node, static Python, all native Python controls and changed companion Markdown/link validation |
| harness or load tooling | Node, static Python, native + harness + load-planner Python closure |
| existing backend test leaves | Node, static Python, complete deterministic Python |
| existing frontend test leaves | complete Node |
| existing E2E test leaves | Node and complete desktop + Android browser acceptance |
| Schemer result-cache source, optionally with its existing direct Node regression | complete Node, whole frontend-serving Python file and frozen eight-file browser closure on both devices |
| Developer inspection source, optionally with its direct helper regression file | complete Node, static Python, eight whole Python files and two mounted browser files on both devices |
| full | Node, static Python, complete Python, real PostgreSQL and all browser acceptance |

The exact path lists and closures live in
[scripts/ci/test_selection.py](../scripts/ci/test_selection.py). This first mapping
accelerates tooling/test-only PRs and two independently reviewed product owners.
Native ownership additionally includes exactly the existing
[stock T3 skill](../.agents/skills/stock-t3-agents/SKILL.md) and
[native agent runbook](../testing/agents/README.md), alone or together with owned
native code/configuration. These instructions govern the same connection,
assignment and cleanup behavior covered by all of `testing/agents`; complete Node
feedback and static Python checks remain required. The strict report-validation
job checks every changed companion's code fences and local links. Only this exact
skill may use its closed, bounded `stock-t3-agents` name/description metadata as its
document title; unrelated Markdown keeps the normal title requirement.
Local `--feedback` and `--run` put the same companion content validator before
Node/static/native execution, including proven dirty edits and full plans with a
changed companion. Its local input accepts only these two paths; local worktree
descriptors remain ineligible for hosted acceptance.
Unknown siblings, other documentation, policy files, mixed owners and unsafe Git
states retain full acceptance. Manual dispatch and main retain their existing full
or positively verified reuse rules. This avoids unrelated installed-application,
PostgreSQL and browser work for native instructions; actual selected-CI elapsed
savings had not been measured for every profile.

The exact product owner is `src/schemii/schemer/web/result-cache.js`, alone or with
an existing modification to `tests/frontend/schemer-result-cache.test.js`. The
source must be present; direct-test-only changes keep their ordinary frontend
profile. Additional source/test/shared paths and unsafe changes remain full.

The product profile retains complete Node feedback, all of `tests/test_frontend.py`,
and the eight whole browser files declared in
[coverage-profiles.json](../scripts/ci/coverage-profiles.json), on desktop and
Android with the same six isolated stacks and one worker per stack. Node protects
cache budgets, cancellation, subscriber ordering and decoded results. Mounted
browser cases retain real independently granted reports, streamed drills/exports,
persistence/recovery and Schemoo-to-Schemer inputs. The optional live-AI case
retains its existing prerequisite limit; its skip is not provider acceptance.

Actual unfiltered discovery must equal the independently reviewed frozen case
inventory before scoped browser execution. Raw-derived Python/browser plans and
source identities must equal that same inventory at the gate; a self-consistent
smaller plan cannot pass. Whole files are assigned disjointly across the three
shards per device. Editing their source or the policy selects full acceptance and
requires deliberate inventory review. Unknown profiles, missing cases and extra
filters are rejected rather than silently narrowing coverage.
The `developer-inspection` profile requires a modification to at least one of
`source_inspection.py`, `common/api/inspection.py`, or
`common/postgres/inspection.py`, optionally with the existing
`tests/test_source_inspection.py`. Direct-helper-only changes keep complete backend
checks. All eight Python files run whole and unfiltered: source, route, database,
system and developer inspection, frontend serving, application structure and runtime
hardening. [inspection-coverage.json](../scripts/ci/inspection-coverage.json) requires
87 original Python cases as a minimum. Every newly discovered helper case must also
pass on attempt zero, with its exact helper source identity; no other frozen Python
file can supply extra cases. The other seven Python files retain their reviewed
source hashes and exact inventories. Ambient pytest filters are rejected, and the
runner preserves the existing four installed-inspection files in one process.

Mounted `shared-ui-audit.spec.js` and `ai-diagnostic-permissions.spec.js` run whole
on desktop and Android, one exclusively owned stack per device, with all fourteen
cases required and no skips. They protect actual generated documents, startup and
map consumers. Standalone PostgreSQL execution and launcher backup recovery are
excluded because these owners derive static inspection metadata; the selected
mounted graph still uses the standard configured stack and fixture preparation.
Full and cache topology and coverage stay unchanged. The retained ordinary full
observation suggests roughly 1.8–2 minutes of source-critical savings, but no ordinary selected run had yet established actual elapsed savings.

Complete CI Python uses `python scripts/ci/python-tests.py`: the inspection fixture
consumers stay together in one isolated process. Default runs with at least four
available affinity CPUs run seven independent CI/native control files in a third
process; normal pytest discovery in the remaining process ignores precisely those
two owned groups. Smaller or unknown CPU capacity retains two processes, and
explicit tooling arguments retain their existing selection behavior. All groups
merge one strict canonical receipt. The PR186 phase model suggests roughly one
minute of Python-step savings; the predicted reduction was not measured on ordinary current-head CI, and browser work may still dominate the workflow. Full and E2E
acceptance use six isolated hosted application
stacks per device; the frozen cache profile keeps three. Full/E2E stacks use the
owned two-process execution described below. Selected cache and inspection
profiles retain their existing single-process whole-file execution.
The gate derives exact expected jobs and receipt lanes from the profile, requires
selected layers to succeed, and requires excluded workflow needs to be skipped.
Missing/duplicate/stale/failed/recovered selected receipts cannot pass.
Full and E2E profiles require all twelve browser legs; cache requires six.
Unknown step time remains `null`.

For scale only, natural main run 36763414216 attempt 1 took 474 seconds to its
source critical path: unit wall 258 seconds and browser walls 354–457 seconds.
Omitting its PostgreSQL and four browser jobs would remove 29.167 job-minutes;
retaining its unchanged unit job would imply a 277-second source path. This is
an estimate from old job boundaries, not measured selective-CI speed. Actual elapsed savings were not established by that estimate.

### Ordinary hosted cost reference

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

#### Two browser processes per full stack

The first desktop lane overlaps collection-only infrastructure discovery with
canonical launcher startup under one owned coordinator. Both must pass and stop
before account preparation or browser cases begin. Discovery starts no browser,
global fixture setup or application requests. Other lanes continue calling
`./start.sh` directly. The Actions startup step measures this readiness barrier;
the launcher's original receipt retains the actual build/replacement/readiness
phases, so overlapping collection time is not added twice.

Full and E2E CI prepare two independently authenticated accounts before any test
body runs. Each account owns its connection profiles, imported catalogs,
workspaces, storage state and private working directory. Preparation completes
both catalog imports before the serial phase, including shared-target DDL tests.
The accounts still use the same seeded PostgreSQL targets: account isolation
does not make shared databases or server admission limits independent.

The coordinator runs `scripts/ci/run-browser-shard.mjs --parallel=2` against the
prepared `SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE`. Within each stack, reviewed files
that exercise shared DDL, global capacity, administrator/shared profiles or live
provider configuration run first in one process. The remaining positively
reviewed files run in two overlapping processes, each with one Playwright worker.
Unknown files stay serial until their ownership is reviewed. Cases remain within
their original whole file; retries, skips and acceptance coverage are unchanged.
The six-stack routing balances the serial cost plus the slower overlapping group,
rather than dividing a serial plan and leaving its shared phase as the bottleneck.

Actual discovery proves the original case inventory. Child receipts retain
original source/case identities despite their private working directories, and
the joined receipt must contain every original case exactly once. Missing,
duplicate, foreign, cancelled or malformed child evidence fails acceptance.
Public browser artifacts retain the same three validated members; credentials,
storage state, screenshots and private ownership ledgers are not uploaded.

CI's always-run cleanup owns secondary fixtures/account deletion and both
disposable process roots after children have stopped. It preserves the primary
account, existing work, live peers and dependency caches. A failed or uncertain
write retains its ownership intent for targeted recovery; cleanup failure fails
the job. Process cancellation owns only its captured child groups, bounds
termination and waits for reaping, including descendants that retain pipes after
their leader exits. The small ownership ledger remains useful private evidence.

For deliberately scheduled local acceptance, prepare a fresh private directory
with `node tests/e2e/helpers/parallel-account.js prepare ABSOLUTE_DIRECTORY
--receipt=ABSOLUTE_PREPARE_RECEIPT` and explicit bootstrap consent/owned
credentials. Export its `accounts.json`
path, then run the coordinator's assigned project/shard with `--parallel=2`.
After every child has stopped, use `node tests/e2e/helpers/parallel-account.js
cleanup ABSOLUTE_DIRECTORY --receipt=ABSOLUTE_CLEANUP_RECEIPT`. Both commands
require a fresh absolute receipt path outside the disposable directory and write
a fixed status receipt. An existing primary must already have the canonical
bookstore fixture as its first database workspace; preparation rejects conflicting
existing work rather than reordering or deleting it. This workflow requires the
canonical launcher and deployment lease and does not replace native manual QA.
Developers continue using focused checks; a new agent or merge does not authorize
another full acceptance run.
For changes to CI dispatch or shared runners, run complete cheap Node feedback
once before pushing: its workflow contracts cover adjacent paths that a narrow
name filter can miss. Reuse that pass until relevant source changes; it does not
authorize repeating Python, database or browser acceptance locally.

#### Identical-tree post-merge acceptance

For an ordinary push to main, CI can reuse a same-repository merged PR's full
acceptance from the preceding 24 hours. The tested prospective merge tree must
equal the current checked-out main tree, including workflow, policy, tests and
dependencies. The donor must be the actual CI workflow's unique successful
attempt-one PR run for that final head, with all 19 jobs passed and all fifteen raw
test lanes complete with zero first-attempt failures or retry recoveries.

Three additional closed modes support the selected Schemer cache, developer-inspection
and native-tooling PRs. Main retains full classification, but both admission and the
independent gate must re-prove that the entire push belongs to that exact existing owner,
with the donor comparison/base equal to main's previous commit and the same
reviewed policy bytes. The original cache donor must have exactly eight complete
raw lanes (Node, frontend Python and six browser legs), eleven original artifacts,
and thirteen provider jobs: eleven successful with static/PostgreSQL intentionally
skipped. Its Python/browser case/source/outcome inventory must match the current
frozen scope, including only documented prerequisite skips. The same repository,
unique final-head, actual workflow, tree/parent, attempt-one and 24-hour checks
apply. Arbitrary selected profiles, mixed or batched main changes and changed
bases/policies fall back to full acceptance. This avoids accelerating a PR only
to repeat full application testing immediately after its accepted merge.

Inspection reuse additionally proves the complete main push modifies only its
three source leaves and optional direct helper, with source present, unchanged
regular modes, the exact donor base and the same current policy bytes. Its donor
has nine provider jobs (eight successful and PostgreSQL intentionally skipped),
seven artifacts and four complete original raw lanes. The frozen graph/mounted
inventory and complete dynamic helper plan are independently revalidated at
admission and the gate. Main retains full classification and fresh report/static
checks while avoiding duplicate acceptance for that identical tested tree.

Native reuse requires the complete main push to modify only the existing native
code, controls, configuration and two documentation companions in its frozen owner
set, with unchanged regular modes, exact donor base and unchanged current policy.
The original native donor retains complete Node and whole-directory
`testing/agents` Python discovery: two complete original raw lanes and exactly five
artifacts. Static/unit, classification, report validation, timing and the original
gate must succeed; PostgreSQL and the browser matrix must be intentionally skipped.
The browser exclusion may be the exact unexpanded provider placeholder or all
twelve existing skipped legs, with no mixture, duplicate or extra job.
Other tooling profiles share these archive names, so names only identify a
candidate: the authenticated original classification and freshly recomputed complete
native ownership must both agree. Harness, load and backend-test donors remain
ineligible. Native's complete dynamic pytest plan uses the same immutable reviewed
workflow, actual discovery and authenticated original-receipt trust as full donors;
no smaller case list or frozen minimum is manufactured. Both admission and the gate
repeat the raw receipt, provider, tree, base, owner and policy checks.

Admission downloads bounded, digest-checked original artifacts and validates
their schemas, identities and completeness. The gate independently repeats
provider and original-receipt verification. Current classification, report and
static checks still run; only duplicate Node/Python, PostgreSQL and browser
execution is skipped. Skipped matrix placeholders are accepted only with the
exact known provider name, intentional exclusion and current-cohort skipped
status. They cannot stand in for selected browser acceptance.

Direct pushes, forks, changed trees/bases, ambiguous or rerun donors, stale or
missing evidence and admission API failures select normal full acceptance.
A corrupt or no-longer-verifiable proof at the gate fails closed. A reused main
run cannot become a donor. Donor SHA/run/test timings remain separate from
current main execution; no old case is relabeled as a new pass. Useful selected
evidence is retained, and disposable validation directories clean up at their
own context boundary.

The implementation is [reuse_acceptance.py](../scripts/ci/reuse_acceptance.py).
The ordinary identical-tree [PR177](https://github.com/LandMineDevelopment/schemii/actions/runs/37072329849)
and [main177](https://github.com/LandMineDevelopment/schemii/actions/runs/37073332970)
observations measured 382 versus 69 seconds, saving 313 seconds (5m13s).
Total executed runner-minutes were 34.117 versus 1.150. This is one natural
full-PR/main comparison across different events, not a percentile guarantee.
Scoped product-profile hosted speed was not measured for every product owner.
Do not create sampling PRs or dispatch hosted runs to manufacture it. Environment-dependent external services and
hosted runner images are not made immutable by tree equality; the bounded age
and unchanged same-repository execution contract are the reuse policy.

Do not remove meaningful coverage, increase skips/timeouts or add retries to
conceal failures. Prefer a cheaper oracle only when it detects the same plausible
defect; distinguish source/package contracts from visible behavior guarantees.

### Public CI timing and evidence policy

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

Canonical browser startup additionally opts into the launcher's bounded receipt
with `SCHEMII_START_TIMING_FILE`. The fixed source/run/attempt/project/shard
identity must match the browser lane and current hosted checkout before upload.
`workflow_timing.py --startup` validates the receipt and attaches its closed
consolidated `startup` field to the existing `browser-summary.json`; the browser
artifact keeps exactly its existing three members. No raw launcher output,
commands, logs, paths or environment values are public timing inputs. Measurement
starts after runtime access and possible stale-group recovery; `preflight` remains
`unmeasured`. Preparation, build, replacement, up/readiness and post-start retain
their separate observations. Workflow jobs expose `startup_build_ms`,
`startup_replacement_ms` and `startup_up_readiness_ms` alongside the broader
Actions `startup_ms`; these clocks need not agree and are not added twice.
Missing measurements remain `null`, while observed zero durations stay zero.

The rollup and both reuse checks independently revalidate the exact optional
summary extension and its original identities. Historical applicable receipts
without it keep their original format; no measurements are invented or relabeled.
Selected reuse also proves the imported startup validator's policy bytes.
A canonical launch failure may publish only the separately validated fixed
`startup-diagnostic` artifact. Its partial phases and terminal remain unknown
when unobserved. That artifact is excluded from the ordinary timing download and
cannot enter the exact successful-donor artifact set. Browser failures and retry
recoveries retain the existing strict acceptance and publication rules.

The rollup validates downloaded timing records again and requires the exact selected
test lanes (fifteen for full acceptance), one source/run/attempt cohort, all expected jobs and complete test
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
Those observations do not establish compatibility or speed on other runtimes.
