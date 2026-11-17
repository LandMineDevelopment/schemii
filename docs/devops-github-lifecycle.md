# GitHub PR lifecycle projection

This offline adapter covers the collection and evidence boundary of
[#281](https://github.com/LandMineDevelopment/schemii/issues/281). It takes
GitHub GraphQL-shaped pages through an injected transport and projects them into
sanitized observations. The repository includes no authenticated transport,
collector command, schedule, database writer or Schemii view for this source.

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
flow and checkpoint proposal. Store a fresh report on replay; never rewrite the
first failure as successful.

To commit progress, first save the completed report and both checkpoints.
Use `advance_checkpoint(current, receipt)` to validate the report digest and
decide the new checkpoint. Atomically compare-and-set against the current prior
checkpoint in the caller's private store. On lost acknowledgement, reread that
store and reconcile the *same saved receipt*. The same committed proposal is
idempotent. Any other intervening checkpoint is a conflict. After partial
traversal, resume with the last committed checkpoint, the same cutoff and a new
attempt UUID. This repeats discovery from a fresh first page without trusting
a stale cursor. The adapter performs no persistence or acknowledgement itself.

## Integration gates

The accepted [storage contract](devops-metrics.md) and
[local-CI importer](devops-import.md) have no PR snapshot/event/review tables,
no GitHub lifecycle import-batch source kind, and no lifecycle coverage,
checkpoint or writer contract. Passing this projection into the local-CI
importer would misidentify the source. A future integration needs an explicit
lifecycle fact and health contract and a durable receipt/checkpoint boundary.
Independent T3 review and local/hosted check evidence need their own qualified
sources.

The remaining issue gates include independent current-head review, a
coordinator-authorized bounded read-only GitHub reconciliation (including
actual cursor, count and visibility semantics), and Schemii readback against
an approved disposable target after a compatible persistence interface exists.
GitHub Actions remains disabled. No browser, device, runtime or database
acceptance is established by the offline fixture suite.
