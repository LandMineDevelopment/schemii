"""Offline provider-shaped controls for the bounded lifecycle adapter."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import pytest

from scripts.devops import github_lifecycle as lifecycle

START = datetime(2026, 11, 1, tzinfo=timezone.utc)
CUTOFF = START + timedelta(days=7)
ATTEMPT = UUID(int=3)
SECRET = "PRIVATE-DATA-SENTINEL"
REPOSITORY = "R_example"
HEAD = "a" * 40
SCOPE = lifecycle.Scope(
    "example/project", REPOSITORY, UUID(int=1), UUID(int=2), START, timedelta(days=2)
)
BEGIN = lifecycle.Checkpoint(SCOPE, START)


def clock():
    return CUTOFF + timedelta(days=3)


def stamp(day):
    return f"2026-11-{day:02}T00:00:00Z"


def pr(number, *, updated=5, state="OPEN", head=HEAD, draft=False):
    return dict(
        id=f"PR_{number}",
        number=number,
        createdAt=stamp(1),
        updatedAt=stamp(updated),
        closedAt=None if state == "OPEN" else stamp(updated),
        mergedAt=stamp(updated) if state == "MERGED" else None,
        state=state,
        isDraft=draft,
        headRefOid=head,
        title=SECRET,
        body=SECRET,
    )


def event(kind, identity, day, **extra):
    return dict(
        __typename=kind, id=identity, createdAt=stamp(day), actor=SECRET, **extra
    )


def review(number, state="APPROVED", *, submitted=3):
    return dict(
        id=f"REVIEW_{number}",
        state=state,
        submittedAt=stamp(submitted) if submitted else None,
        commit={"oid": HEAD},
        body=SECRET,
    )


class Fixture:
    def __init__(self, pulls=(), *, timeline=None, reviews=None, patch=None):
        self.pulls = {p["number"]: p for p in pulls}
        self.timeline = timeline or {}
        self.reviews = reviews or {}
        self.patch = patch
        self.calls = []
        self.repo_id = REPOSITORY
        self.conditional = False

    def __call__(self, request, tag):
        self.calls.append((request, tag))
        variables = request.graphql()["variables"]
        expected = {
            "owner": "example",
            "repo": "project",
            "size": request.size,
            "after": request.cursor,
        }
        if request.number is not None:
            expected["number"] = request.number
        assert variables == expected
        operation = request.operation
        rows = list(self.pulls.values())
        if operation != "listing":
            rows = getattr(self, operation).get(request.number, [])
        offset = int(request.cursor or 0)
        items = rows[offset : offset + request.size]
        end = offset + len(items)
        page = dict(
            totalCount=len(rows),
            nodes=items,
            pageInfo={"hasNextPage": end < len(rows), "endCursor": str(end)},
        )
        if operation == "timeline":
            # Unrelated timeline items can contribute to the connection's total.
            page.update(
                totalCount=len(rows) + 29, pageCount=len(items), updatedAt=stamp(6)
            )
        repo = {"id": self.repo_id}
        if operation == "listing":
            repo["pullRequests"] = page
        else:
            connection = {"timeline": "timelineItems", "reviews": "reviews"}[operation]
            repo["pullRequest"] = self.pulls[request.number] | {connection: page}
        body = {"data": {"repository": repo}}
        if self.patch is not None:
            response = self.patch(request, body, page)
            if response is not None:
                return response
        return lifecycle.Response(
            304 if self.conditional and tag else 200, body, {"ETag": '"page-v1"'}
        )


def collect(transport, **options):
    return lifecycle.collect(
        options.pop("checkpoint", BEGIN),
        options.pop("through", CUTOFF),
        options.pop("attempt_id", ATTEMPT),
        transport,
        clock=options.pop("clock", clock),
        **options,
    )


def facts(receipt, stream):
    assert SECRET not in json.dumps(receipt.report)
    return [fact for fact in receipt.report["facts"] if fact["stream"] == stream]


def test_more_than_100_pull_requests_and_independent_nested_pagination():
    pulls = [pr(i, updated=1, state="CLOSED") for i in range(1, 102)]
    pulls.extend([pr(102, state="MERGED"), pr(103)])
    checkpoint = lifecycle.Checkpoint(
        SCOPE, START + timedelta(days=4), UUID(int=4), "0" * 64
    )
    timeline = [
        event(
            "HeadRefForcePushedEvent", f"F_{i}", 2, beforeCommit=None, afterCommit=None
        )
        for i in range(101)
    ]
    reviews = [review(i) for i in range(101)]
    fixture = Fixture(pulls, timeline={102: timeline}, reviews={102: reviews})
    receipt = collect(fixture, checkpoint=checkpoint)
    assert receipt.report["status"] == "complete"
    assert receipt.report["scan_from"] == stamp(3).replace("Z", "+00:00")
    assert (
        receipt.report["coverage"]["listing"]
        == receipt.report["coverage"]["verification"]
        == "complete"
    )
    assert receipt.report["coverage"]["pull_requests"] == {
        "102": {"timeline": "complete", "reviews": "complete"},
        "103": {"timeline": "complete", "reviews": "complete"},
    }
    assert len(facts(receipt, "timeline")) == len(facts(receipt, "reviews")) == 101
    assert [(req.operation, req.number, req.cursor) for req, _ in fixture.calls] == [
        ("listing", None, None),
        ("listing", None, "100"),
        ("timeline", 102, None),
        ("timeline", 102, "100"),
        ("reviews", 102, None),
        ("reviews", 102, "100"),
        ("timeline", 103, None),
        ("reviews", 103, None),
        ("listing", None, None),
        ("listing", None, "100"),
    ]
    merged, opened = receipt.report["flow"]
    assert merged["open_to_merge_seconds"] == 4 * 86400
    assert merged["formal_github_review_count"] == 101
    assert opened["open_age_at_observation_upper_bound_seconds"] == 10 * 86400
    assert opened["formal_github_review_count"] == 0
    assert opened["initial_draft"] is None
    availability = receipt.report["availability"]
    assert {
        availability[k]
        for k in (
            "independent_t3_reviews",
            "native_t3_checks",
            "local_ci_checks",
            "hosted_github_checks",
            "deployment",
            "active_labor",
        )
    } == {"uncollected"}
    selections = " ".join(req.graphql()["query"] for req, _ in fixture.calls)
    assert "pullRequests(" in selections
    assert not any(
        field in selections
        for field in ("search(", "body", "title", "actor", "checkSuites")
    )


def test_events_deduplicate_by_occurrence_and_keep_return_to_old_head():
    force1 = event(
        "HeadRefForcePushedEvent",
        "F_1",
        3,
        beforeCommit={"oid": HEAD},
        afterCommit={"oid": "b" * 40},
    )
    force2 = event(
        "HeadRefForcePushedEvent",
        "F_2",
        5,
        beforeCommit={"oid": "b" * 40},
        afterCommit={"oid": HEAD},
    )
    ready1 = event("ReadyForReviewEvent", "READY_1", 2)
    ready2 = event("ReadyForReviewEvent", "READY_2", 5)
    draft = event("ConvertToDraftEvent", "DRAFT_1", 4)
    # Out-of-order observations, including an identical event spanning pages.
    fixture = Fixture(
        [pr(1)], timeline={1: [ready2, force2, draft, draft, force1, ready1]}
    )
    first = collect(fixture, page_size=3)
    second = collect(
        fixture,
        page_size=3,
        checkpoint=first.proposal,
        through=CUTOFF + timedelta(days=1),
        attempt_id=UUID(int=6),
    )
    first_events = facts(first, "timeline")
    assert first.report["status"] == second.report["status"] == "complete"
    assert len(first_events) == len({f["id"] for f in first_events}) == 5
    assert [
        f["value"]["after_sha"] for f in first_events if "after_sha" in f["value"]
    ] == [HEAD, "b" * 40]
    assert [f["id"] for f in first_events] == [
        f["id"] for f in facts(second, "timeline")
    ]
    assert [f["evidence_ref"] for f in first_events] != [
        f["evidence_ref"] for f in facts(second, "timeline")
    ]
    item = first.report["flow"][0]
    assert [
        (ep["provider_event_id"], ep["ended_at"]) for ep in item["ready_episodes"]
    ] == [("READY_1", "2026-11-04T00:00:00.000000+00:00"), ("READY_2", None)]
    assert not any(f["value"]["kind"] == "opened" for f in first_events)
    assert first.report["availability"]["unobserved_head_transitions"] == "unknown"
    assert first.report["availability"]["deleted_or_hidden_history"] == "unknown"


def test_reopened_and_tied_transitions_keep_ready_end_unknown():
    timeline = [
        event("ReadyForReviewEvent", "R_1", 2),
        event("ReopenedEvent", "REOPEN", 3),
        event("ReadyForReviewEvent", "R_2", 4),
        event("ConvertToDraftEvent", "DRAFT", 4),
        event("ClosedEvent", "CLOSE", 5),
    ]
    receipt = collect(Fixture([pr(1, state="CLOSED")], timeline={1: timeline}))
    item = receipt.report["flow"][0]
    assert item["state"] == "closed"
    assert item["open_to_merge_seconds"] is None
    assert [episode["ended_at"] for episode in item["ready_episodes"]] == [None, None]


def test_cutoff_selects_old_open_and_recent_terminal_with_nullable_merge_time():
    checkpoint = lifecycle.Checkpoint(
        SCOPE, START + timedelta(days=5), UUID(int=4), "0" * 64
    )
    pulls = [
        pr(1, updated=2, draft=True),
        pr(2, state="MERGED", updated=9) | {"mergedAt": None},
        pr(3, state="CLOSED", updated=2),
        pr(4, updated=9) | {"createdAt": stamp(9)},
    ]
    calls = iter(clock() + timedelta(seconds=i) for i in range(100))
    receipt = collect(Fixture(pulls), checkpoint=checkpoint, clock=lambda: next(calls))
    assert receipt.report["status"] == "complete"
    assert receipt.report["listing_evidence"]["listing"]["count"] == 4
    assert (
        receipt.report["listing_evidence"]["listing"]
        == receipt.report["listing_evidence"]["verification"]
    )
    opened, merged = receipt.report["flow"]
    assert [opened["number"], merged["number"]] == [1, 2]
    assert opened["draft_at_observation"]
    lower = opened["open_age_at_observation_lower_bound_seconds"]
    upper = opened["open_age_at_observation_upper_bound_seconds"]
    assert upper - lower == 1
    assert merged["state"] == "merged"
    assert merged["open_to_merge_seconds"] is None


def test_formal_reviews_keep_pending_and_edits_distinct_from_submission():
    fixture = Fixture(
        [pr(1)], reviews={1: [review(1, "PENDING", submitted=None), review(2)]}
    )
    first = collect(fixture)
    fixture.reviews[1] = [review(2, "DISMISSED"), review(3, submitted=None)]
    second = collect(
        fixture,
        checkpoint=first.proposal,
        through=CUTOFF + timedelta(days=1),
        attempt_id=UUID(int=6),
    )
    pending, approved = facts(first, "reviews")
    dismissed, uncertain = facts(second, "reviews")
    assert approved["id"] == dismissed["id"]
    assert approved["evidence_sha256"] != dismissed["evidence_sha256"]
    assert approved["value"]["occurred_at"] == dismissed["value"]["occurred_at"]
    assert pending["value"]["occurred_at"] is None
    assert uncertain["value"]["occurred_at"] is None
    assert first.report["flow"][0]["formal_github_review_count"] == 1
    assert second.report["flow"][0]["formal_github_review_count"] is None


def test_partial_graphql_response_retains_only_prior_valid_observations():
    first_event = event("ReadyForReviewEvent", "READY", 2)
    second_event = event("ClosedEvent", "CLOSED", 5)

    def patch(request, body, _page):
        if request.operation == "timeline" and request.cursor:
            return lifecycle.Response(
                200, body | {"errors": [{"message": SECRET, "path": [SECRET]}]}
            )
        return None

    fixture = Fixture([pr(1)], timeline={1: [first_event, second_event]}, patch=patch)
    partial = collect(fixture, page_size=1)
    assert partial.report["failure"]["reason"] == "graphql-errors"
    assert partial.report["failure"]["partial_data"] is True
    assert partial.report["failure"]["request"] == 3
    assert partial.report["coverage"]["pull_requests"]["1"] == {
        "timeline": "partial",
        "reviews": "pending",
    }
    assert [f["value"]["provider_id"] for f in facts(partial, "timeline")] == ["READY"]
    assert partial.report["flow"] is None
    assert lifecycle.advance_checkpoint(BEGIN, partial) == BEGIN
    assert partial.proposal is None
    assert len(fixture.calls) == 3


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (lifecycle.Response(403, SECRET), "permission-denied"),
        (
            lifecycle.Response(403, SECRET, {"X-RateLimit-Remaining": "0"}),
            "rate-limited",
        ),
        (lifecycle.Response(403, SECRET, {"Retry-After": "5"}), "rate-limited"),
        (lifecycle.Response(429, SECRET), "rate-limited"),
        (lifecycle.Response(503, SECRET), "http-error"),
        (lifecycle.Response(404, SECRET), "endpoint-hidden-or-missing"),
        (lifecycle.Response(304), "conditional-cache-miss"),
        (
            lifecycle.Response(
                200, {"errors": [{"type": "FORBIDDEN", "message": SECRET}]}
            ),
            "permission-denied",
        ),
        (
            lifecycle.Response(
                200, {"errors": [{"extensions": {"code": "RATE_LIMITED"}}]}
            ),
            "rate-limited",
        ),
        (TimeoutError(SECRET), "network-timeout"),
        (OSError(SECRET), "network-error"),
    ],
)
def test_failure_types_do_not_retry_or_leak(response, reason):
    calls = []

    def transport(request, tag):
        calls.append((request, tag))
        if isinstance(response, OSError):
            raise response
        return response

    receipt = collect(transport)
    assert receipt.report["status"] == "partial"
    assert receipt.report["failure"]["reason"] == reason
    assert receipt.report["failure"]["request"] == 1
    assert len(calls) == 1
    assert not facts(receipt, "snapshot")
    assert receipt.proposal is None


def test_cached_pages_have_original_provenance_and_do_not_share_report_mutations():
    fixture = Fixture([pr(1)])
    cache = lifecycle.PageCache()
    first = collect(fixture, cache=cache)
    first.report["facts"][0]["value"]["head_sha"] = SECRET
    fixture.conditional = True
    second = collect(fixture, cache=cache, attempt_id=UUID(int=6))
    assert second.report["status"] == "complete"
    original, verified = facts(second, "snapshot")
    for snapshot in original, verified:
        assert snapshot["http_status"] == 304
        assert snapshot["cached_from_attempt"] == str(ATTEMPT)
        assert snapshot["cached_from_request"] == 4
        assert snapshot["value"]["head_sha"] == HEAD
    with pytest.raises(ValueError, match="different transport"):
        collect(Fixture(), cache=cache)


def test_rate_exhaustion_after_good_page_stops_before_next_read():
    def patch(_request, body, _page):
        return lifecycle.Response(
            200, body, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123456"}
        )

    fixture = Fixture([pr(1)], patch=patch)
    receipt = collect(fixture)
    assert receipt.report["failure"]["reason"] == "rate-limited"
    assert receipt.report["failure"]["operation"] == "timeline"
    assert receipt.report["failure"]["rate_limit"]["reset_epoch_seconds"] == 123456
    assert len(fixture.calls) == 1
    assert len(facts(receipt, "snapshot")) == 1


def test_head_changes_between_detail_pages_keep_both_snapshots_and_prior_event():
    def patch(request, body, _page):
        if request.operation == "timeline" and request.cursor:
            body["data"]["repository"]["pullRequest"]["headRefOid"] = "b" * 40

    timeline = [event("ReadyForReviewEvent", f"R_{i}", 2) for i in range(2)]
    receipt = collect(
        Fixture([pr(1)], timeline={1: timeline}, patch=patch), page_size=1
    )
    assert receipt.report["failure"]["reason"] == "pr-changed-during-traversal"
    assert [fact["value"]["head_sha"] for fact in facts(receipt, "snapshot")] == [
        HEAD,
        "b" * 40,
    ]
    assert len(facts(receipt, "timeline")) == 1
    assert receipt.proposal is None


def test_pr_disappearing_from_second_listing_is_uncertainty():
    listing_reads = 0

    def patch(request, _body, page):
        nonlocal listing_reads
        if request.operation == "listing":
            listing_reads += 1
            if listing_reads == 2:
                page.update(totalCount=0, nodes=[])

    receipt = collect(Fixture([pr(1)], patch=patch))
    assert receipt.report["failure"]["reason"] == "repository-changed-during-traversal"
    assert receipt.report["flow"] is None
    assert len(facts(receipt, "snapshot")) == 1
    assert receipt.proposal is None


@pytest.mark.parametrize(
    "fault", ["cursor", "count", "timeline-time", "duplicate-edit"]
)
def test_nested_pagination_detects_source_inconsistency(fault):
    def patch(request, _body, page):
        if request.operation == "timeline" and request.cursor:
            if fault == "cursor":
                page["pageInfo"]["endCursor"] = request.cursor
            elif fault == "count":
                page["totalCount"] += 1
            elif fault == "timeline-time":
                page["updatedAt"] = stamp(7)
            else:
                page["nodes"] = [event("ReadyForReviewEvent", "R_0", 3)]

    timeline = [event("ReadyForReviewEvent", f"R_{i}", 2) for i in range(3)]
    receipt = collect(
        Fixture([pr(1)], timeline={1: timeline}, patch=patch), page_size=1
    )
    expected = {
        "cursor": "pagination-cursor-cycle",
        "count": "connection-count-changed",
        "timeline-time": "timeline-changed-during-traversal",
        "duplicate-edit": "provider-fact-changed",
    }
    assert receipt.report["failure"]["reason"] == expected[fault]
    assert receipt.proposal is None
    if fault == "duplicate-edit":
        original, changed = facts(receipt, "timeline")
        assert original["id"] == changed["id"]
        assert original["evidence_ref"] != changed["evidence_ref"]


def test_missing_listing_count_and_repository_identity_refuse_complete_denominator():
    def patch(_request, _body, page):
        page["totalCount"] += 1

    fixture = Fixture([pr(1)], patch=patch)
    receipt = collect(fixture)
    assert receipt.report["failure"]["reason"] == "connection-count-mismatch"
    fixture.repo_id = "R_other"
    receipt = collect(fixture, attempt_id=UUID(int=6))
    assert receipt.report["failure"]["reason"] == "repository-identity-changed"
    assert not facts(receipt, "snapshot")


def test_budget_failure_and_lost_ack_reconcile_without_replaying_read():
    fixture = Fixture([pr(1)])
    partial = collect(fixture, max_requests=1)
    assert partial.report["failure"]["reason"] == "request-budget-exhausted"
    complete = collect(fixture, attempt_id=UUID(int=9))
    assert partial.report["attempt_id"] != complete.report["attempt_id"]
    assert partial.report["status"] == "partial"
    assert complete.report["status"] == "complete"
    reloaded = replace(complete, report=json.loads(json.dumps(complete.report)))
    checkpoint = lifecycle.advance_checkpoint(BEGIN, reloaded)
    calls_after_first_write = len(fixture.calls)
    assert lifecycle.advance_checkpoint(checkpoint, reloaded) == checkpoint
    assert checkpoint.through == CUTOFF
    assert len(fixture.calls) == calls_after_first_write
    with pytest.raises(ValueError, match="checkpoint changed"):
        lifecycle.advance_checkpoint(checkpoint, partial)
    reloaded.report["status"] = "partial"
    with pytest.raises(ValueError, match="receipt evidence changed"):
        lifecycle.advance_checkpoint(checkpoint, reloaded)
