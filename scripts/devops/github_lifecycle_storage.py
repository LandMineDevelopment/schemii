"""Guarded publication and reconciliation of approved, saved lifecycle receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import math
import re
from typing import Any, Literal
from uuid import UUID, uuid5

from scripts.devops import github_lifecycle as lifecycle, importer, storage

ATTEMPTS = "github_pr_lifecycle_attempts"
COVERAGE = "github_pr_lifecycle_coverage"
OBSERVATIONS = "github_pr_lifecycle_observations"
HASH = re.compile(r"[a-f0-9]{64}")
UTC_STAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?\+00:00"
)
MAX_INT = 2**31 - 1
MAX_REQUESTS = 10_000
MAX_FACTS = MAX_REQUESTS * 100
PR_STATES = ("open", "closed", "merged")
STATES = ("pending", "partial", "complete")
PASSES = ("listing", "listing-verification")
DETAILS = ("timeline", "reviews")
EVENTS = {kind for _, kind, _ in lifecycle.TYPES.values()}
REVIEWS = {state.lower() for state in lifecycle.REVIEWS}
FAILURES = frozenset(
    """
    conditional-cache-miss connection-count-changed connection-count-mismatch
    endpoint-hidden-or-missing graphql-errors http-error invalid-pagination
    invalid-provider-data missing-provider-field network-error network-timeout
    observation-clock-regressed pagination-cursor-cycle permission-denied
    pr-changed-during-traversal pr-hidden-or-missing pr-identity-changed
    provider-fact-changed rate-limited repository-changed-during-traversal
    repository-hidden-or-missing repository-identity-changed request-budget-exhausted
    timeline-changed-during-traversal unsupported-timeline-event
""".split()
)
AVAILABILITY = {
    "initial_draft_state": "unknown",
    "unobserved_head_transitions": "unknown",
    "deleted_or_hidden_history": "unknown",
    "independent_t3_reviews": "uncollected",
    "native_t3_checks": "uncollected",
    "local_ci_checks": "uncollected",
    "hosted_github_checks": "uncollected",
    "deployment": "uncollected",
    "active_labor": "uncollected",
}
OBSERVATION_FIELDS = {"request", "observed_from", "observed_at", "http_status"}
CACHE_FIELDS = {"cached_from_attempt", "cached_from_request"}
FLOW_SECONDS = {
    "open_to_merge_seconds",
    "open_age_at_observation_lower_bound_seconds",
    "open_age_at_observation_upper_bound_seconds",
}


def require(condition: Any, reason: str = "invalid-lifecycle-evidence") -> None:
    if not condition:
        storage.refuse(
            reason,
            "Preserve the saved envelope and reconcile its approved scope, seal and checkpoint.",
        )


def fields(
    value: Any, required: str, optional: set[str] | None = None
) -> dict[str, Any]:
    keys = set(required.split())
    require(
        isinstance(value, dict) and keys <= value.keys() <= keys | (optional or set())
    )
    return value


def token(value: Any, pattern: re.Pattern[str] = lifecycle.TOKEN) -> str:
    require(isinstance(value, str) and pattern.fullmatch(value))
    return value


def integer(value: Any, low: int, high: int = MAX_INT) -> int:
    require(type(value) is int and low <= value <= high)
    return value


def identifier(value: Any) -> UUID:
    require(isinstance(value, str) and len(value) == 36)
    result = UUID(value)
    require(str(result) == value)
    return result


def instant(value: Any) -> datetime:
    return datetime.fromisoformat(token(value, UTC_STAMP))


def optional_instant(value: Any) -> datetime | None:
    return None if value is None else instant(value)


def optional_sha(value: Any) -> str | None:
    return None if value is None else token(value, lifecycle.OID)


def scope_values(scope: lifecycle.Scope) -> dict[str, Any]:
    return {
        "repository_id": str(scope.repository_id),
        "installation_id": str(scope.installation_id),
        "repository_key": scope.repository_key,
        "provider_repository_id": scope.provider_repository_id,
        "window_start": scope.window_start.isoformat(),
        "overlap_us": scope.overlap // timedelta(microseconds=1),
    }


def checkpoint(scope: lifecycle.Scope, data: Any) -> lifecycle.Checkpoint:
    point = fields(data, "through attempt_id evidence_sha256")
    return lifecycle.Checkpoint(
        scope,
        instant(point["through"]),
        None if point["attempt_id"] is None else identifier(point["attempt_id"]),
        None
        if point["evidence_sha256"] is None
        else token(point["evidence_sha256"], HASH),
    )


@dataclass(frozen=True)
class _Staged:
    before: lifecycle.Checkpoint
    proposal: lifecycle.Checkpoint | None
    attempt: dict[str, Any]
    coverage: list[dict[str, Any]]
    observations: list[dict[str, Any]]


def cache_fields(data: dict[str, Any]) -> dict[str, Any]:
    present = CACHE_FIELDS.intersection(data)
    require(not present or data.get("http_status") == 304 and present == CACHE_FIELDS)
    if not present:
        return {}
    return {
        "cached_from_attempt": identifier(data["cached_from_attempt"]),
        "cached_from_request": integer(data["cached_from_request"], 1, MAX_REQUESTS),
    }


def rate_fields(data: Any) -> dict[str, Any]:
    rate = fields(data, "", {"remaining", "retry_after_seconds", "reset_epoch_seconds"})
    require(not rate or len(rate) == 3)
    return {
        f"rate_{key}": None if value is None else integer(value, 0, 9_999_999_999)
        for key, value in rate.items()
    }


def failure_fields(data: Any, attempt: dict[str, Any], rate: Any) -> dict[str, Any]:
    failure = fields(
        data,
        "reason operation number rate_limit",
        OBSERVATION_FIELDS
        | CACHE_FIELDS
        | {
            "timeline_updated_at",
            "partial_data",
            "clock_regressed",
        },
    )
    reason, operation, number = (
        failure["reason"],
        failure["operation"],
        failure["number"],
    )
    require(
        isinstance(reason, str) and reason in FAILURES and operation in PASSES + DETAILS
    )
    if operation in DETAILS:
        integer(number, 1)
    else:
        require(number is None)
    require(rate_fields(failure["rate_limit"]) == rate_fields(rate))
    context = cache_fields(failure)
    if "request" in failure:
        context["request"] = integer(failure["request"], 1, attempt["requests"])
        require(context["request"] == attempt["requests"])
        lower = instant(failure["observed_from"])
        upper = optional_instant(failure["observed_at"])
        require(
            attempt["started_at"] <= lower <= (upper or lower) <= attempt["finished_at"]
        )
        context.update(observed_from=lower, observed_at=upper)
    else:
        require(
            not (
                failure.keys()
                & (OBSERVATION_FIELDS | CACHE_FIELDS | {"timeline_updated_at"})
            )
        )
    if "http_status" in failure:
        context["http_status"] = integer(failure["http_status"], 100, 599)
    if "timeline_updated_at" in failure:
        require(operation == "timeline")
        context["timeline_updated_at"] = instant(failure["timeline_updated_at"])
    for key in ("partial_data", "clock_regressed"):
        if key in failure:
            require(type(failure[key]) is bool)
            context[key] = failure[key]
    if "partial_data" in context:
        require(reason in ("rate-limited", "permission-denied", "graphql-errors"))
        require(context.get("http_status") == 200)
    if "clock_regressed" in context:
        require(reason in ("network-error", "network-timeout"))
        require(context.get("request") is not None and "http_status" not in context)
        require(context["clock_regressed"] == (context["observed_at"] is None))
    return {
        f"failure_{key}": value
        for key, value in dict(
            context, reason=reason, operation=operation, number=number
        ).items()
    }


def fact_value(stream: str, data: Any, pr_id: str, number: int) -> dict[str, Any]:
    if stream == "snapshot":
        value = fields(
            data,
            "provider_id number created_at updated_at closed_at merged_at state draft head_sha",
        )
        require(value["provider_id"] == pr_id and integer(value["number"], 1) == number)
        created, updated = instant(value["created_at"]), instant(value["updated_at"])
        closed, merged = (
            optional_instant(value["closed_at"]),
            optional_instant(value["merged_at"]),
        )
        state = value["state"]
        require(state in PR_STATES and type(value["draft"]) is bool)
        require(created <= updated and (closed is None or created <= closed))
        require(merged is None or state == "merged" and created <= merged)
        return dict(
            pr_state=state,
            draft=value["draft"],
            head_sha=token(value["head_sha"], lifecycle.OID),
            created_at=created,
            updated_at=updated,
            closed_at=closed,
            merged_at=merged,
        )
    if stream == "timeline":
        kind = data.get("kind") if isinstance(data, dict) else None
        require(isinstance(kind, str) and kind in EVENTS)
        force = kind == "head-force-pushed"
        value = fields(
            data,
            "provider_id kind occurred_at" + (" before_sha after_sha" if force else ""),
        )
        return dict(
            event_kind=kind,
            occurred_at=instant(value["occurred_at"]),
            before_sha=optional_sha(value.get("before_sha")),
            after_sha=optional_sha(value.get("after_sha")),
        )
    value = fields(data, "provider_id kind review_state occurred_at reviewed_sha")
    state = value["review_state"]
    require(
        value["kind"] == "formal-review" and isinstance(state, str) and state in REVIEWS
    )
    submitted = optional_instant(value["occurred_at"])
    require(state != "pending" or submitted is None)
    return dict(
        review_state=state,
        submitted_at=submitted,
        reviewed_sha=optional_sha(value["reviewed_sha"]),
    )


def observation(data: Any, attempt: dict[str, Any]) -> dict[str, Any]:
    fact = fields(
        data,
        "id source_key evidence_ref evidence_sha256 pull_request_id number stream provenance value",
        OBSERVATION_FIELDS | CACHE_FIELDS | {"timeline_updated_at"},
    )
    require(OBSERVATION_FIELDS <= fact.keys())
    stream, provenance = fact["stream"], fact["provenance"]
    require(provenance in PASSES + DETAILS and stream in ("snapshot", provenance))
    require(stream == "snapshot" or provenance in DETAILS)
    request = integer(fact["request"], 1, attempt["requests"])
    lower, upper = instant(fact["observed_from"]), instant(fact["observed_at"])
    require(attempt["started_at"] <= lower <= upper <= attempt["finished_at"])
    status = integer(fact["http_status"], 200, 304)
    cache = cache_fields(fact)
    require(status == 200 or status == 304 and cache)
    if cache.get("cached_from_attempt") == attempt["attempt_id"]:
        require(cache["cached_from_request"] < request)
    require(("timeline_updated_at" in fact) == (stream == "timeline"))
    marker = instant(fact["timeline_updated_at"]) if stream == "timeline" else None
    pr_id, number = token(fact["pull_request_id"]), integer(fact["number"], 1)
    values = fact_value(stream, fact["value"], pr_id, number)
    provider_id = token(fact["value"]["provider_id"])
    attempt_id = str(attempt["attempt_id"])
    identity = [attempt["provider_repository_id"], pr_id, stream, provider_id]
    if stream == "snapshot":
        identity.extend((attempt_id, request))
    key = f"github-{stream}-{importer.digest(identity)}"
    evidence = importer.digest(fact["value"])
    ref = f"github-{importer.digest([attempt_id, key, request, evidence])}"
    logical_id = uuid5(attempt["installation_id"], key)
    require(
        (fact["source_key"], fact["id"], fact["evidence_sha256"], fact["evidence_ref"])
        == (key, str(logical_id), evidence, ref)
    )
    return dict(
        _qualifiers(attempt),
        evidence_ref=ref,
        logical_id=logical_id,
        source_key=key,
        evidence_sha256=evidence,
        stream=stream,
        provenance=provenance,
        pull_request_id=pr_id,
        number=number,
        provider_id=provider_id,
        request=request,
        observed_from=lower,
        observed_at=upper,
        http_status=status,
        timeline_updated_at=marker,
        **cache,
        **values,
    )


def _qualifiers(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: attempt[key] for key in ("repository_id", "installation_id", "attempt_id")
    }


def listing_fields(report: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
    evidence = fields(report["listing_evidence"], "", {"listing", "verification"})
    listing = attempt["listing_state"]
    verification = attempt["verification_state"]
    require(listing in STATES[1:] and verification in STATES)
    require(verification == "pending" or listing == "complete")
    require(("listing" in evidence) == (listing == "complete"))
    require(verification != "pending" or "verification" not in evidence)
    require((attempt["status"] == "complete") == (verification == "complete"))
    if verification == "complete":
        require(
            "verification" in evidence
            and evidence["listing"] == evidence["verification"]
        )
    elif "verification" in evidence:
        require(
            attempt["failure_reason"] == "repository-changed-during-traversal"
            and evidence["listing"] != evidence["verification"]
        )
    values: dict[str, Any] = {}
    for operation, state in evidence.items():
        check = fields(state, "count evidence_sha256")
        values[f"{operation}_count"] = integer(check["count"], 0)
        values[f"{operation}_evidence_sha256"] = token(check["evidence_sha256"], HASH)
    return values


def _snapshot_identity(row: dict[str, Any]) -> tuple[int, str, str]:
    return row["number"], row["pull_request_id"], row["evidence_sha256"]


def coverage_rows(
    data: Any,
    observations: list[dict[str, Any]],
    attempt: dict[str, Any],
) -> list[dict[str, Any]]:
    require(isinstance(data, dict))
    initial = [row for row in observations if row["provenance"] == "listing"]
    verifier = [
        row for row in observations if row["provenance"] == "listing-verification"
    ]
    if attempt["listing_state"] == "partial":
        require(
            not data and all(row["provenance"] == "listing" for row in observations)
        )
        require(attempt["failure_operation"] == "listing")
        return []
    first = {row["number"]: row for row in initial}
    require(
        len(first) == len(initial) == len({row["pull_request_id"] for row in initial})
    )
    require(
        set(data) == {str(number) for number in first}
        and len(first) <= attempt["listing_count"]
    )
    if attempt["verification_state"] == "complete":
        require(
            sorted(map(_snapshot_identity, initial))
            == sorted(map(_snapshot_identity, verifier))
        )
    if "verification_count" in attempt:
        require(
            len(verifier) <= attempt["verification_count"]
            and len({row["number"] for row in verifier}) == len(verifier)
            and len({row["pull_request_id"] for row in verifier}) == len(verifier)
        )
    if attempt["verification_state"] == "pending":
        require(not verifier)
    if attempt["status"] == "partial":
        require(
            attempt["failure_operation"] in DETAILS
            or attempt["verification_state"] == "partial"
        )
        require(
            (attempt["failure_operation"] == "listing-verification")
            == (attempt["verification_state"] == "partial")
        )
    rows = []
    operations: list[tuple[int, str, str]] = []
    for number, snapshot in sorted(first.items()):
        info = fields(data[str(number)], "timeline reviews")
        timeline, reviews = info["timeline"], info["reviews"]
        require(timeline in STATES and reviews in STATES)
        require(reviews == "pending" or timeline == "complete")
        operations.extend(
            ((number, "timeline", timeline), (number, "reviews", reviews))
        )
        rows.append(
            dict(
                _qualifiers(attempt),
                pull_request_id=snapshot["pull_request_id"],
                number=number,
                timeline_state=timeline,
                review_state=reviews,
            )
        )
    if attempt["verification_state"] != "pending":
        require(all(state == "complete" for _, _, state in operations))
    else:
        failed = (attempt["failure_number"], attempt["failure_operation"], "partial")
        require(failed in operations)
        offset = operations.index(failed)
        require(all(state == "complete" for _, _, state in operations[:offset]))
        require(all(state == "pending" for _, _, state in operations[offset + 1 :]))
    states = {(number, op): state for number, op, state in operations}
    for row in observations:
        operation, number = row["provenance"], row["number"]
        if operation in DETAILS:
            require(states.get((number, operation)) in ("partial", "complete"))
            if row["stream"] == "snapshot":
                require(
                    attempt.get("failure_reason") == "pr-changed-during-traversal"
                    and (number, operation)
                    == (attempt["failure_number"], attempt["failure_operation"])
                    and row["request"] == attempt.get("failure_request")
                    and _snapshot_identity(row) != _snapshot_identity(first[number])
                )
            else:
                require(row["pull_request_id"] == first[number]["pull_request_id"])
    return rows


def validate_flow(data: Any, coverage: list[dict[str, Any]], complete: bool) -> None:
    if not complete:
        require(data is None)
        return
    require(isinstance(data, list) and len(data) == len(coverage))
    for entry, row in zip(data, coverage):
        item = fields(
            entry,
            """number pull_request_id state draft_at_observation initial_draft
                      ready_episodes formal_github_review_count """
            + " ".join(FLOW_SECONDS),
        )
        require(
            integer(item["number"], 1) == row["number"]
            and item["pull_request_id"] == row["pull_request_id"]
        )
        require(
            item["state"] in PR_STATES
            and type(item["draft_at_observation"]) is bool
            and item["initial_draft"] is None
        )
        for key in FLOW_SECONDS:
            seconds = item[key]
            require(
                seconds is None
                or type(seconds) in (int, float)
                and 0 <= seconds < 315_537_897_600
                and math.isfinite(seconds)
            )
        if item["formal_github_review_count"] is not None:
            integer(item["formal_github_review_count"], 0, MAX_FACTS)
        episodes = item["ready_episodes"]
        require(isinstance(episodes, list) and len(episodes) <= MAX_FACTS)
        for episode in episodes:
            episode = fields(episode, "provider_event_id started_at ended_at")
            token(episode["provider_event_id"])
            start, end = (
                instant(episode["started_at"]),
                optional_instant(episode["ended_at"]),
            )
            require(end is None or start < end)


def _stage(raw: bytes, expected_sha256: str, scope: lifecycle.Scope) -> _Staged:
    """Validate and take an owned typed snapshot before opening any connection."""
    require(type(raw) is bytes and 0 < len(raw) <= importer.MAX_FILE)
    try:
        raw.decode("utf-8")
        payload = fields(
            importer.json_value(raw),
            "format_version scope before proposal report",
        )
        require(integer(payload["format_version"], 1, 1) == 1)
        external_scope = scope_values(scope)
        sealed_scope = fields(payload["scope"], " ".join(external_scope))
        integer(sealed_scope["overlap_us"], 1, 2_592_000_000_000)
        require(sealed_scope == external_scope, "lifecycle-scope-mismatch")
        seal = importer.digest(payload)
        require(seal == token(expected_sha256, HASH), "lifecycle-seal-mismatch")
        before = checkpoint(scope, payload["before"])
        proposal = (
            None
            if payload["proposal"] is None
            else checkpoint(scope, payload["proposal"])
        )
        report = fields(
            payload["report"],
            """
            source_kind attempt_id repository installation_id window_start prior_through
            prior_evidence_sha256 scan_from through started_at finished_at requests last_rate_limit
            status failure coverage listing_evidence facts flow availability
        """,
        )
        require(report["source_kind"] == lifecycle.SOURCE, "wrong-lifecycle-source")
        require(
            report["repository"]
            == {
                "id": external_scope["repository_id"],
                "provider": "github",
                "repository_key": scope.repository_key,
                "provider_id": scope.provider_repository_id,
            }
            and report["installation_id"] == external_scope["installation_id"]
            and instant(report["window_start"]) == scope.window_start
            and instant(report["prior_through"]) == before.through
            and report["prior_evidence_sha256"] == before.evidence_sha256,
            "lifecycle-scope-mismatch",
        )
        attempt_id = identifier(report["attempt_id"])
        floor, through = instant(report["scan_from"]), instant(report["through"])
        started, finished = (
            instant(report["started_at"]),
            instant(report["finished_at"]),
        )
        require(
            attempt_id != before.attempt_id
            and before.through < through <= started <= finished
        )
        require(
            floor
            == before.through - min(scope.overlap, before.through - scope.window_start)
        )
        report_sha = importer.digest(report)
        status = report["status"]
        require(status in ("complete", "partial"))
        complete = status == "complete"
        if complete:
            require(
                report["failure"] is None
                and proposal
                == lifecycle.Checkpoint(scope, through, attempt_id, report_sha)
            )
            lifecycle.advance_checkpoint(
                before, lifecycle.Collection(report, before, proposal)
            )
        else:
            require(proposal is None)
        require(report["availability"] == AVAILABILITY)
        coverage = fields(report["coverage"], "listing verification pull_requests")
        attempt = dict(
            repository_id=scope.repository_id,
            installation_id=scope.installation_id,
            attempt_id=attempt_id,
            source_kind=lifecycle.SOURCE,
            format_version=1,
            provider_repository_id=scope.provider_repository_id,
            window_start=scope.window_start,
            overlap_us=sealed_scope["overlap_us"],
            prior_through=before.through,
            prior_attempt_id=before.attempt_id,
            prior_evidence_sha256=before.evidence_sha256,
            scan_from=floor,
            through=through,
            started_at=started,
            finished_at=finished,
            requests=integer(report["requests"], 0, MAX_REQUESTS),
            status=status,
            listing_state=coverage["listing"],
            verification_state=coverage["verification"],
            report_sha256=report_sha,
            envelope_sha256=seal,
            **rate_fields(report["last_rate_limit"]),
        )
        require(not complete or attempt["requests"] >= 2)
        if not complete:
            attempt.update(
                failure_fields(report["failure"], attempt, report["last_rate_limit"])
            )
        attempt.update(listing_fields(report, attempt))
        facts = report["facts"]
        require(
            isinstance(facts, list)
            and len(facts) <= min(MAX_FACTS, attempt["requests"] * 100)
        )
        observations = [observation(fact, attempt) for fact in facts]
        _validate_observation_order(observations, attempt)
        covered = coverage_rows(coverage["pull_requests"], observations, attempt)
        validate_flow(report["flow"], covered, complete)
        return _Staged(before, proposal, attempt, covered, observations)
    except (ValueError, KeyError, OverflowError, RecursionError):
        storage.refuse(
            "malformed-lifecycle-evidence", "Use the saved strict v1 JSON envelope."
        )


def _validate_observation_order(
    rows: list[dict[str, Any]], attempt: dict[str, Any]
) -> None:
    refs: set[str] = set()
    previous_request = 0
    previous_page: tuple | None = None
    previous_end = attempt["started_at"]
    previous_stage = (0, 0)
    detail_versions: dict[UUID, str] = {}
    for row in rows:
        request, operation = row["request"], row["provenance"]
        stage = (0, 0)
        if operation in DETAILS:
            stage = (row["number"], DETAILS.index(operation))
        elif operation == "listing-verification":
            stage = (MAX_INT + 1, 0)
        require(stage >= previous_stage)
        previous_stage = stage
        page = (
            operation,
            row["number"] if operation in DETAILS else None,
            row["observed_from"],
            row["observed_at"],
            row["http_status"],
            row.get("cached_from_attempt"),
            row.get("cached_from_request"),
            row["timeline_updated_at"],
        )
        require(row["evidence_ref"] not in refs)
        refs.add(row["evidence_ref"])
        if request == attempt.get("failure_request"):
            require("failure_partial_data" not in attempt)
            require(operation == attempt["failure_operation"])
            require(
                all(
                    row[key] == attempt.get(f"failure_{key}")
                    for key in ("http_status", "observed_from", "observed_at")
                )
            )
        if request == previous_request:
            require(page == previous_page)
        else:
            require(request > previous_request and row["observed_from"] >= previous_end)
            previous_request, previous_page, previous_end = (
                request,
                page,
                row["observed_at"],
            )
        if operation in PASSES:
            require(
                row["created_at"] <= attempt["through"]
                and (
                    row["pr_state"] == "open"
                    or row["updated_at"] >= attempt["scan_from"]
                )
            )
        elif row["stream"] != "snapshot":
            logical = row["logical_id"]
            if logical in detail_versions:
                require(
                    attempt.get("failure_reason") == "provider-fact-changed"
                    and row["evidence_sha256"] != detail_versions[logical]
                    and request == attempt.get("failure_request")
                    and operation == attempt.get("failure_operation")
                    and row["number"] == attempt.get("failure_number")
                )
            detail_versions[logical] = row["evidence_sha256"]


@dataclass(frozen=True)
class Publication:
    disposition: Literal["published", "replay", "historically-committed", "absent"]
    checkpoint: lifecycle.Checkpoint
    attempt_id: UUID
    envelope_sha256: str
    checkpoint_conflict: bool = False


def _commit(conn, staged: _Staged, *, write: bool) -> Publication:
    scope = staged.before.scope
    qualified = (scope.repository_id, scope.installation_id)
    key = (*qualified, staged.attempt["attempt_id"])
    # Lock the qualified series independently of other advisory-lock namespaces.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 281))",
            (f"github-pr-lifecycle-v1:{qualified[0]}:{qualified[1]}",),
        )
        registry = conn.execute(
            "SELECT provider, repository_key FROM devops.repositories WHERE id = %s",
            (qualified[0],),
        ).fetchone()
        require(
            registry == {"provider": "github", "repository_key": scope.repository_key},
            "lifecycle-registry-mismatch",
        )
        bound = conn.execute(
            """SELECT provider_repository_id, window_start, overlap_us
               FROM devops.github_pr_lifecycle_attempts
               WHERE repository_id = %s AND installation_id = %s LIMIT 1""",
            qualified,
        ).fetchone()
        require(
            bound is None
            or bound
            == {
                column: staged.attempt[column]
                for column in (
                    "provider_repository_id",
                    "window_start",
                    "overlap_us",
                )
            },
            "lifecycle-series-mismatch",
        )
        existing = conn.execute(
            """SELECT envelope_sha256 FROM devops.github_pr_lifecycle_attempts
               WHERE repository_id = %s AND installation_id = %s AND attempt_id = %s""",
            key,
        ).fetchone()
        seal = staged.attempt["envelope_sha256"]
        require(
            existing is None or existing["envelope_sha256"] == seal,
            "lifecycle-attempt-conflict",
        )
        cursor = conn.execute(
            """SELECT through, attempt_id, report_sha256 FROM devops.github_pr_lifecycle_attempts
               WHERE repository_id = %s AND installation_id = %s AND status = 'complete'
               ORDER BY through DESC LIMIT 1""",
            qualified,
        ).fetchone()
        current = (
            lifecycle.Checkpoint(scope, scope.window_start)
            if cursor is None
            else lifecycle.Checkpoint(
                scope,
                cursor["through"],
                cursor["attempt_id"],
                cursor["report_sha256"],
            )
        )
        if existing is not None:
            historical = staged.proposal is not None and staged.proposal != current
            return Publication(
                "historically-committed" if historical else "replay",
                current,
                key[2],
                seal,
                historical,
            )
        before = staged.before
        conflict = staged.proposal is not None and current != before
        if (
            staged.proposal is None
            and before.attempt_id is not None
            and current != before
        ):
            prior = conn.execute(
                """SELECT 1 FROM devops.github_pr_lifecycle_attempts
                   WHERE repository_id = %s AND installation_id = %s AND attempt_id = %s
                       AND through = %s AND report_sha256 = %s AND status = 'complete'""",
                (
                    *qualified,
                    before.attempt_id,
                    before.through,
                    before.evidence_sha256,
                ),
            ).fetchone()
            conflict = prior is None
        if not write:
            return Publication("absent", current, key[2], seal, conflict)
        require(not conflict, "lifecycle-checkpoint-conflict")
        importer.insert(conn, ATTEMPTS, staged.attempt)
        for row in staged.coverage:
            importer.insert(conn, COVERAGE, row)
        for row in staged.observations:
            importer.insert(conn, OBSERVATIONS, row)
        return Publication("published", staged.proposal or current, key[2], seal)


def publish(
    raw: bytes,
    expected_sha256: str,
    scope: lifecycle.Scope,
    *,
    guard_profile: Path,
    collector_profile: Path,
    database_name: str,
    database_id: UUID,
) -> Publication:
    """Publish one approved envelope. Use readback after an uncertain commit."""
    staged = _stage(raw, expected_sha256, scope)
    with importer.target(
        guard_profile, collector_profile, database_name, str(database_id)
    ) as conn:
        return _commit(conn, staged, write=True)


def readback(
    raw: bytes,
    expected_sha256: str,
    scope: lifecycle.Scope,
    *,
    guard_profile: Path,
    collector_profile: Path,
    database_name: str,
    database_id: UUID,
) -> Publication:
    """Reconcile the exact saved attempt and current checkpoint without inserting."""
    staged = _stage(raw, expected_sha256, scope)
    with importer.target(
        guard_profile, collector_profile, database_name, str(database_id)
    ) as conn:
        return _commit(conn, staged, write=False)
