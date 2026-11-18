# GitHub PR lifecycle projection

This offline adapter covers the collection and evidence boundary of
[#281](https://github.com/LandMineDevelopment/schemii/issues/281). It takes
GitHub GraphQL-shaped pages through an injected transport and projects them into
sanitized observations. A guarded storage API accepts approved saved receipts
and exposes typed PostgreSQL readback.

Run the provider-shaped controls from the project's constrained Python environment.

```sh
python -m pytest -q tests/devops/test_github_lifecycle.py
```

The fixtures exercise repository, timeline and review pagination beyond 100
items, incomplete responses, changed heads, duplicate and reordered events,
conditional reads, rate limits and lost checkpoint acknowledgement. Their data
is synthetic and must stay out of production metrics.

## Collection boundary

The caller supplies a `Scope` with the approved repository key, expected GitHub
repository node ID, registry UUID, installation UUID, window start and overlap.
Start with `Checkpoint(scope, scope.window_start)`. For each actual traversal,
call `collect(checkpoint, through, attempt_id, transport, clock=...)` with a new
UUID and a fixed cutoff later than the checkpoint and at or before the start of
observation. The scan starts at the checkpoint minus overlap, clamped to the
window start. The result contains the sanitized report, prior checkpoint and a
checkpoint proposal when traversal completed.

The transport callable receives `(request, if_none_match)`. Use
`request.graphql()` for the query and variables and return a `Response` with the
HTTP status, decoded body and relevant headers. Any future authenticated
transport must pin the GitHub endpoint and visibility context, set finite
connect/read and response-byte limits, map timeouts and network failures to
`TimeoutError` and `OSError`, and return the first actual outcome. The adapter
serializes page requests, limits page size to 100 and request count to a
caller-selected budget, and stops on exhaustion or failure. It never retries
or sleeps. Numeric retry/reset metadata is available to the caller when the
provider emits it. Unclassified 403 responses are reported as permission
failures. A 404 or a hidden node carries missing-or-hidden uncertainty. A
GraphQL errors envelope refuses that page, including when it also contains data.

Repository discovery walks the full visible `repository.pullRequests`
connection across all states. A PR created by the cutoff is selected for detail
when it is currently open or was updated since the overlapped lower bound.
This retains older open PRs and allows an older PR's recent merge or changes to
enter the window. Observations can include provider times later than the
cutoff. Use actual event timestamps to form a time cohort. The cutoff marks
scan progress. An earlier point-in-time state cannot be reconstructed from it.

Each selected PR gets independent timeline and formal review pagination. Detail
pages assert the PR snapshot observed in the first listing. A second full
listing compares the sanitized snapshots and records a matching count and
digest on success. The adapter also checks cursor progress, listing and review
counts, and the filtered timeline's page counts and update marker. Completion
means that the specified connections were traversed under these checks, as
visible to the transport. GitHub does not supply an atomic cross-page snapshot.
A transient change between reads, a hidden or deleted fact, or an edit outside
the overlap can escape detection. Reconciliation retains prior observations
and leaves absences unresolved. Search results provide no denominator here.
For the underlying field and connection semantics see
[GitHub's Pull Requests GraphQL reference](https://docs.github.com/en/graphql/reference/pulls)
and [Repositories GraphQL reference](https://docs.github.com/en/graphql/reference/repos).

A caller may reuse a process-local `PageCache` with the same transport instance
and unchanged visibility context. A conditional 304 must have an exact cached
page for that repository, operation, PR, cursor and page size. Each reused
observation points back to the original attempt and request ordinal. A 304
without that page is a partial attempt. Cache pages contain only the parsed
projection, pagination metadata and ETags. Request cursors and ETags remain
private and stay out of the report. Conditional support depends on the
transport and endpoint. The synthetic 304 controls do not establish that
GitHub's GraphQL endpoint emits usable ETags for these queries.

## What the report means

- **Snapshots** contain the provider PR ID, number, current draft flag, head
  SHA, PR state and the provider's creation, update, close and merge times.
  Snapshot observation bounds bracket the page request. The normalized
  timestamps on the PR have separate meaning. Both listing passes retain
  selected snapshots and identify their provenance.
- **Timeline facts** anchor ready, converted to draft, closed, reopened,
  merged and force-pushed events by GitHub node ID. A force push can have
  unknown before or after SHA. Ordinary intermediate head changes remain
  unknown. A return to an old SHA keeps its own event identity.
- **Formal review facts** keep the review ID, observed state, optional
  submission time and optional reviewed SHA. A later dismissal changes the
  observed state while retaining the submission time the provider reports.
  Pending reviews do not enter the submitted count. When a nonpending review
  lacks a submission time the count is unknown. An exhaustively observed
  connection can report zero formal submissions with independent local review
  still uncollected.
- **Flow** is available for complete attempts. Each selected PR's current
  state distinguishes merged, closed unmerged and open. Open-to-merge uses
  the provider's creation and merge times. Open age uses the listing
  observation bounds and includes open drafts. Ready episodes pair observed
  ready events with later observed end transitions where their order is
  unambiguous. An unknown beginning, ambiguous transitions or an unobserved
  end stays unknown. A pair does not prove continuous readiness between
  events. Merge, formal review submission, independent T3 reviews, native T3
  checks, local CI checks, hosted GitHub checks, deployment and active labor
  each have separate meanings. Only the GitHub lifecycle and formal-review
  surfaces are collected here.

Keep all collection reports in approved private storage, keyed by attempt UUID.
The projection uses the foundation's repository identity, installation UUID,
safe source keys, evidence SHA-256, opaque evidence refs and observation-time
conventions. Evidence hashes cover allowlisted facts. Bodies, titles, comments,
patches, actors, raw errors and response headers stay outside the projection.
Repeated events have the same logical ID across attempts. Evidence references
identify individual observations, including conflicting versions. A report
retains already observed facts when a later page fails, while withholding the
flow and checkpoint proposal. Reuse the saved report on replay. Preserve the
original failure.

## Save and publish a receipt

Save an immutable private JSON envelope for the traversal, including failures.
The caller supplies the approved scope and expected canonical envelope SHA-256
from the evidence owner. The envelope uses UTF-8 and contains these fields.

| Field | Value |
| --- | --- |
| `format_version` | Integer `1` |
| `scope` | `repository_id`, `installation_id`, `repository_key`, `provider_repository_id`, `window_start`, and integer `overlap_us` |
| `before` | `through`, `attempt_id`, and `evidence_sha256`. An initial checkpoint uses the window start and two nulls. |
| `proposal` | The same checkpoint fields for a complete attempt, or null for a partial attempt |
| `report` | The entire sanitized adapter report |

Use canonical UUID strings and the adapter's UTC ISO 8601 timestamps. Compute
each digest from JSON with sorted keys, compact separators, ASCII escapes
and no nonfinite numbers. A complete proposal points to the report's own
attempt ID, cutoff and canonical SHA-256. The outer digest also binds the
overlap and exact prior attempt, including for partials. Digests verify saved
content against approval. The evidence owner supplies source authenticity.

Call `publish(raw, expected_sha256, scope, **target)` from
`scripts.devops.github_lifecycle_storage`. `raw` is the saved envelope in bytes
(up to 20 MiB). `target` requires `guard_profile`, `collector_profile`,
`database_name` and `database_id`. Follow the existing
[private profile and target contract](devops-import.md). The profiles and
database UUID must agree and the collector must pass the effective-role guard.
Migration 003 must be applied through the storage migration operator. The
repository UUID must already identify the approved GitHub repository key in
the registry. The first saved attempt binds that installation's provider
repository ID, window and overlap. A changed visibility context or scope
requires a separately approved installation UUID.

Publication validates the envelope before connecting. A transaction adds the
attempt, per-PR coverage and observations together. Complete attempts advance
the checkpoint from the exact committed prior tuple. After a partial, retain
that original receipt and restart listing at page one with a fresh attempt
UUID. The current checkpoint and approved overlap supply the next scan floor.

If a commit acknowledgement is lost, call
`readback(raw, expected_sha256, scope, **target)` with the same saved envelope.
It uses the same guard and returns the current checkpoint and the exact
attempt ID and seal. `replay` confirms a saved partial or the complete receipt
at the current checkpoint. `historically-committed` confirms an earlier
complete receipt and reports a checkpoint conflict with the actual current
tuple. `absent` reports whether the saved prior still permits publication.
A valid delayed partial can cite the true initial tuple or a committed prior
complete attempt. A reused attempt ID with a different seal is refused.
Preserve uncertain outcomes and original saved envelopes when reconciling.

## Read typed history

Filter the `devops.v1_github_pr_lifecycle_attempts`,
`devops.v1_github_pr_lifecycle_coverage`, and
`devops.v1_github_pr_lifecycle_observations` views by both repository and
installation UUIDs. Join on both UUIDs and the attempt UUID. For a PR join,
also match its provider PR ID. Only the current complete row has
`is_current_checkpoint`; no such row means the series is at its initial
window start. For example, the current tuple for two bound UUID parameters is:

```sql
SELECT through, attempt_id, report_sha256
FROM devops.v1_github_pr_lifecycle_attempts
WHERE repository_id = $1 AND installation_id = $2 AND is_current_checkpoint;
```

Coverage represents the initially selected PRs. Timeline and formal-review
states are independent. A missing coverage row carries no assertion. Distinguish
observations by attempt and evidence ref, logical events by logical UUID, and
value versions by logical UUID plus evidence SHA-256, all within the qualified
series. A 304 retains its revalidation bounds and the original attempt/request
pointer as lineage, even when the donor row is unavailable. To report zero
formal submissions, require complete PR review coverage and check that no
observed nonpending review lacks a submission timestamp. Independent review
and other uncollected evidence remain unknown. Derived flow and readiness
episodes stay in the private adapter receipt. The local CI batch ledger and
freshness remain independent of lifecycle receipts.

## Integration gates

Run the sealed synthetic control with an explicitly disposable, socket-only
PostgreSQL cluster using the constrained project Python environment.

```sh
SCHEMII_DEVOPS281_POSTGRES=1 python scripts/ci/python-tests.py tests/devops/test_github_lifecycle_storage.py
```

Migration 003 changes the pending-migration gate for any executable that uses
the current target guard. Coordinate code and schema activation. Issue #281
still requires independent current-head review, a coordinator-authorized
bounded read-only GitHub reconciliation of actual cursor, count and visibility
semantics, and matching Schemii readback against an approved disposable target.
