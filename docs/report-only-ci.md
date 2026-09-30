# Report-only CI

[Issue #127](https://github.com/LandMineDevelopment/schemii/issues/127) gives
verified report changes a cheap feedback path. CI still triggers for every pull
request and main push. Its always-run classifier selects the report path only
when every changed file meets the policy below; unknowns use full source checks.

## Selected report paths

The allowlist is date-named Markdown directly inside `docs/audits/`, with the form
`YYYY-MM-DD-lowercase-report-name.md`, plus `docs/browser-shard-balance.md`.
Files must be ordinary, nonexecutable Git blobs. New reports and modifications
qualify. Renames, deletions, symlinks and executable-mode changes always
select source validation, including a source-to-report rename.

A modified root `README.md` qualifies only when its final `## Reports` section
contains blank lines and unique Markdown list links to selected reports, and
the entire preceding README is byte-for-byte unchanged. Adding that final
section preserves every existing README byte. Other README edits use source CI.
The selected reports are validated before the final gate can accept their index
links; a missing link target fails validation.

Agent contracts, skills, testing/evidence policy, fixtures, source, migrations,
dependencies, configuration, launchers and workflows retain full source CI.
The report validator receives only selected reports and positively proved README
index changes, including when they accompany source changes. Other Markdown
contracts keep their own format requirements; a front-matter skill does not need
a report title. Rename/deletion changes retain full CI without passing removed
paths to the report validator.
Adding another shortcut path requires changing the classifier in a full CI run.

## Complete comparisons

The classifier checks fetched commit objects rather than an event's truncated
file list. Pull requests compare their unique merge base with the event's actual
head SHA, retaining all commits on that branch and excluding unrelated advances
on the base branch. Pushes compare the entire event `before` → `after` range;
an earlier source change cannot hide behind a final report commit. A nonancestor
push, missing/all-zero SHA, ambiguous merge base or invalid diff fails
classification and selects the full source path. Empty comparisons use source CI.

The classifier runs from the triggering checkout, matching the other CI jobs:
the prospective GitHub merge revision for pull requests, and the triggering commit
for push/manual runs. This includes controls added on the base after a PR opened.
Its Git comparison still uses the event's actual base/head SHAs and unique merge
base, rather than treating the prospective merge SHA as the PR head. Full history
remains available; missing base/head objects continue to fail classification.

Markdown validation checks the prospective PR merge checkout, so a base-side
file removal cannot leave a new report's local link green. It uses the runner's
standard Python library, without installing Schemii, Node dependencies, a
Markdown package, browsers or application services. It checks nonempty UTF-8
Markdown titles, closed fences/links, references, local files/directories,
Markdown heading anchors and source line anchors. Code examples are not executed.
External links are checked for syntax, never fetched; their availability remains
an editorial review responsibility. This validator supports ordinary inline,
reference and HTML links, not every Markdown extension or rendered-layout rule.
Inline linked images and labels with escaped brackets validate their outer link
destinations as well as any image destination.

## Required status

`CI validation` is the always-evaluated aggregate suitable for required-check
policy. Existing seven source check names remain unchanged. Their intentional
skips are accepted only after successful positive report classification and
Markdown validation. Source changes require static quality, deterministic
Node/Python behavior, real PostgreSQL and all four named browser matrix legs.
The gate reads jobs from the exact run attempt, failing on API errors, missing or
duplicate legs, failure, cancellation, timeout, skip or unfinished status. It
also requires the timing job and a complete timing receipt. No top-level path
filter or secret-enabled `pull_request_target` is used.

The receipt binds the current source SHA, PR head SHA, run ID and attempt.
Source jobs and every test lane must belong to that same current attempt;
even a complete passing cohort from an earlier attempt is rejected. A PR's
head SHA is distinct from its checked-out prospective merge SHA. Both identities
are retained, so that distinction cannot either reject a valid PR or accept a
different head. Re-run all jobs to obtain a new complete attempt; partial reruns
cannot borrow previous-attempt receipts.

Workflow timing labels source jobs/tests explicitly inapplicable on report runs;
their passing-test denominator is zero, not a fabricated pass rate. Report
validation publishes its own execution milliseconds/status. Missing/cancelled
control jobs, unexpected source evidence and collector failures remain
incomplete. A fully observed failure remains different from passing acceptance.
Measured job minutes include classification and report validation; the rollup's
own job and the final gate run afterward and are outside that subtotal. Runner
dispatch/scheduling, setup and execution remain separate observations.

The **Run workflow** action on CI, or
`gh workflow run ci.yml --ref BRANCH`, explicitly selects full validation even
for a report branch. Main updates and ordinary source PRs retain all application
layers. Full stress, paid-provider and native manual acceptance keep their
separate prerequisite and ownership contracts.

## Verification and measurement

Focused regressions exercise real temporary Git histories for PR/main semantics,
earlier source commits, rename/delete/mixed/mode changes and README index proof.
The checkout regression creates a PR predating the classifier, advances its base
with the real controls and creates a two-parent prospective merge. It derives the
checkout choice from the actual workflow, proves old-head execution cannot find
the classifier and checks source/report classification from the merge. Missing
event base/head objects still produce a failing full-path receipt.
They inject classifier/link errors, cancelled or missing matrix jobs and
incomplete timing. Production classifier/validator commands are also executed
with Python's `-S` option to demonstrate that application packages are unnecessary:

```bash
.venv/bin/python -m pytest -q tests/test_ci_change_classification.py tests/test_ci_docs_validation.py tests/test_ci_timing.py tests/test_test_deployment.py
```

The cited pre-change report run took **10m47s** overall and **37m57s** summed job
time, including four launcher builds
([run 36619862101](https://github.com/LandMineDevelopment/schemii/actions/runs/36619862101)).
Local cheap-check timings do not establish hosted runner savings or queue time.
On this shared host, classifying the existing report-only commit `936d332` and
validating its one file/six links with actual `python3 -S` commands took
**101.455ms** combined: 51.391ms for classification and 50.064ms for the validator
process, including 1.586ms of validation. Before review corrections, the 188
focused cases above passed in 10.08s on Python 3.14.7. The 131 classifier/validator
cases, including real skill updates and nested/escaped link counterexamples,
passed in 3.01s after those corrections; no application or browser was started.
These single local observations are not percentiles or hosted performance claims.
The workflow passed `actionlint` 1.7.12 (official release asset SHA-256 checked),
plus a parsed graph check preserving the seven source names and matrix inventory.
New Python files passed Ruff lint/format; modified timing code passed the legacy
undefined-name boundary. After integrating the canonical Node runner and argument
repairs, the full pinned local quality script passed Ruff and then failed
Mypy 2.3.1 on Python 3.14.7 with 19
transitive errors in seven application files unchanged from timing delivery
`5cbeaea`. Those unrelated files were preserved. Hosted Python 3.12 static
quality and independent review remain required; this local failure is retained.
Record the first ordinary report-only run after delivery: report feedback from
job start (target under one minute), dispatch delay, whole workflow wall time
and summed all-job minutes including rollup/gate. Compare the measured total
with that historical baseline and retain the changed topology/runner limitation.
Use naturally occurring runs; do not launch application suites merely to create
benchmark samples.
