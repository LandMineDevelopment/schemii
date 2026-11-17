"""Read-only GitHub PR lifecycle projection over an injected page transport."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import groupby
import json
import re
from typing import Callable
from uuid import UUID, uuid5

SOURCE = "github-pr-lifecycle-v1"
SNAPSHOT = "id number createdAt updatedAt closedAt mergedAt state isDraft headRefOid"
PAGE = "totalCount pageInfo { hasNextPage endCursor }"
TYPES = {
    "ReadyForReviewEvent": ("READY_FOR_REVIEW_EVENT", "ready-for-review", ""),
    "ConvertToDraftEvent": ("CONVERT_TO_DRAFT_EVENT", "convert-to-draft", ""),
    "ClosedEvent": ("CLOSED_EVENT", "closed", ""),
    "ReopenedEvent": ("REOPENED_EVENT", "reopened", ""),
    "MergedEvent": ("MERGED_EVENT", "merged", ""),
    "HeadRefForcePushedEvent": (
        "HEAD_REF_FORCE_PUSHED_EVENT",
        "head-force-pushed",
        "beforeCommit { oid } afterCommit { oid }",
    ),
}
REVIEWS = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}
TOKEN = re.compile(r"[A-Za-z0-9_+=/:-]{1,160}")
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

# Request only provider identities, clocks and typed lifecycle fields.
OPERATIONS = {
    "listing": f"""
        query PRListing($owner: String!, $repo: String!, $size: Int!, $after: String) {{
          repository(owner: $owner, name: $repo) {{
            id pullRequests(first: $size, after: $after, orderBy: {{field: CREATED_AT, direction: ASC}}) {{
              {PAGE} nodes {{ {SNAPSHOT} }}
            }}
          }}
        }}
    """,
}
timeline_types = " ".join(
    f"... on {name} {{ id createdAt {extra} }}" for name, (_, _, extra) in TYPES.items()
)
for operation, selection in (
    (
        "timeline",
        f"timelineItems(first: $size, after: $after, itemTypes: [{', '.join(t[0] for t in TYPES.values())}])"
        f" {{ {PAGE} pageCount updatedAt nodes {{ __typename {timeline_types} }} }}",
    ),
    (
        "reviews",
        f"reviews(first: $size, after: $after) {{ {PAGE} nodes {{ id state submittedAt commit {{ oid }} }} }}",
    ),
):
    OPERATIONS[operation] = f"""
        query PRDetails($owner: String!, $repo: String!, $number: Int!, $size: Int!, $after: String) {{
          repository(owner: $owner, name: $repo) {{
            id pullRequest(number: $number) {{ {SNAPSHOT} {selection} }}
          }}
        }}
    """


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("expected timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Scope:
    repository_key: str
    provider_repository_id: str
    repository_id: UUID
    installation_id: UUID
    window_start: datetime
    overlap: timedelta

    def __post_init__(self):
        if (
            not isinstance(self.repository_key, str)
            or len(self.repository_key) > 160
            or not REPO.fullmatch(self.repository_key)
            or not isinstance(self.provider_repository_id, str)
            or not TOKEN.fullmatch(self.provider_repository_id)
            or not isinstance(self.repository_id, UUID)
            or not isinstance(self.installation_id, UUID)
            or not isinstance(self.overlap, timedelta)
            or not timedelta(0) < self.overlap <= timedelta(days=30)
        ):
            raise ValueError("invalid lifecycle scope")
        object.__setattr__(self, "window_start", _utc(self.window_start))


@dataclass(frozen=True)
class Checkpoint:
    scope: Scope
    through: datetime
    attempt_id: UUID | None = None
    evidence_sha256: str | None = None

    def __post_init__(self):
        bound = _utc(self.through)
        object.__setattr__(self, "through", bound)
        if bound < self.scope.window_start:
            raise ValueError("checkpoint predates window")
        if self.attempt_id is None:
            if self.evidence_sha256 is not None or bound != self.scope.window_start:
                raise ValueError("invalid initial checkpoint")
        elif (
            not isinstance(self.attempt_id, UUID)
            or not isinstance(self.evidence_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.evidence_sha256)
        ):
            raise ValueError("invalid checkpoint evidence")


@dataclass(frozen=True)
class Request:
    repository_key: str
    operation: str
    number: int | None
    cursor: str | None = field(repr=False)
    size: int

    def graphql(self) -> dict:
        owner, repo = self.repository_key.split("/")
        variables = {
            "owner": owner,
            "repo": repo,
            "size": self.size,
            "after": self.cursor,
        }
        if self.number is not None:
            variables["number"] = self.number
        return {"query": OPERATIONS[self.operation], "variables": variables}


@dataclass(frozen=True)
class Response:
    status: int
    body: object = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class _Page:
    rows: list[dict]
    total: int
    after: str | None
    snapshot: dict | None
    timeline_updated_at: str | None


@dataclass(frozen=True)
class _CachedPage:
    tag: str
    page: _Page
    attempt_id: UUID
    request_number: int


class PageCache:
    """Process-local conditional cache owned by one transport/visibility context."""

    def __init__(self):
        self._transport: Transport | None = None
        self._entries: OrderedDict[tuple[str, Request], _CachedPage] = OrderedDict()

    def _bind(self, transport: Transport) -> None:
        if self._transport is None:
            self._transport = transport
        elif self._transport is not transport:
            raise ValueError("cache belongs to a different transport")

    def _store(self, key: tuple[str, Request], entry: _CachedPage | None) -> None:
        self._entries.pop(key, None)
        if entry is not None:
            self._entries[key] = entry
            if len(self._entries) > 1024:
                self._entries.popitem(last=False)


Transport = Callable[[Request, str | None], Response]


@dataclass(frozen=True)
class Collection:
    report: dict
    before: Checkpoint
    proposal: Checkpoint | None


def advance_checkpoint(current: Checkpoint, receipt: Collection) -> Checkpoint:
    """Reconcile a saved receipt before retrying its checkpoint compare-and-set."""
    if receipt.proposal is not None:
        if _digest(receipt.report) != receipt.proposal.evidence_sha256:
            raise ValueError("receipt evidence changed")
        if current == receipt.proposal:
            return current  # Acknowledgement was lost after the write.
    if current != receipt.before:
        raise ValueError("checkpoint changed; reconcile the saved attempts")
    return receipt.proposal or current


class _Incomplete(Exception):
    def __init__(self, reason: str, **details: object):
        super().__init__(reason)
        self.details = {"reason": reason, **details}


def _require(condition: object, reason: str = "invalid-provider-data") -> None:
    if not condition:
        raise _Incomplete(reason)


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise _Incomplete("invalid-provider-data")
    return value


def _text(value: object, pattern: re.Pattern) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _Incomplete("invalid-provider-data")
    return value


def _stamp(value: object) -> str:
    value = _text(
        value,
        re.compile(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z"
        ),
    )
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").isoformat(
            timespec="microseconds"
        )
    except ValueError:
        raise _Incomplete("invalid-provider-data") from None


def _oid(commit: object) -> str | None:
    return None if commit is None else _text(_object(commit).get("oid"), OID)


def _snapshot(row: dict) -> dict:
    state = row["state"]
    _require(state in ("OPEN", "CLOSED", "MERGED"))
    number = row["number"]
    _require(type(number) is int and 0 < number <= 2**31 - 1)
    _require(type(row["isDraft"]) is bool)
    created, updated = _stamp(row["createdAt"]), _stamp(row["updatedAt"])
    closed = _stamp(row["closedAt"]) if row["closedAt"] is not None else None
    merged = _stamp(row["mergedAt"]) if row["mergedAt"] is not None else None
    _require(created <= updated and (closed is None or created <= closed))
    _require(merged is None or (state == "MERGED" and created <= merged))
    return {
        "provider_id": _text(row["id"], TOKEN),
        "number": number,
        "created_at": created,
        "updated_at": updated,
        "closed_at": closed,
        "merged_at": merged,
        "state": state.lower(),
        "draft": row["isDraft"],
        "head_sha": _text(row["headRefOid"], OID),
    }


def _timeline(row: dict) -> dict:
    typename = row["__typename"]
    _require(
        isinstance(typename, str) and typename in TYPES, "unsupported-timeline-event"
    )
    event: dict = {
        "provider_id": _text(row["id"], TOKEN),
        "kind": TYPES[typename][1],
        "occurred_at": _stamp(row["createdAt"]),
    }
    if typename == "HeadRefForcePushedEvent":
        event.update(
            before_sha=_oid(row["beforeCommit"]), after_sha=_oid(row["afterCommit"])
        )
    return event


def _review(row: dict) -> dict:
    state = row["state"]
    _require(isinstance(state, str) and state in REVIEWS)
    submitted = row["submittedAt"]
    _require(state != "PENDING" or submitted is None)
    return {
        "provider_id": _text(row["id"], TOKEN),
        "kind": "formal-review",
        "review_state": state.lower(),
        "occurred_at": _stamp(submitted) if submitted is not None else None,
        "reviewed_sha": _oid(row["commit"]),
    }


def _parse_page(body: object, request: Request, repository_id: str) -> _Page:
    response = _object(body)
    errors = response.get("errors")
    if "errors" in response:
        if not isinstance(errors, list) or not 0 < len(errors) <= 100:
            raise _Incomplete("invalid-provider-data")
        codes = set()
        for item in errors:
            item = _object(item)
            extensions = _object(item.get("extensions") or {})
            code = item.get("type") or extensions.get("type") or extensions.get("code")
            if isinstance(code, str) and len(code) <= 64:
                codes.add(code)
        if codes & {"RATE_LIMITED", "RATE_LIMIT_EXCEEDED"}:
            reason = "rate-limited"
        elif codes & {"FORBIDDEN", "UNAUTHORIZED", "INSUFFICIENT_SCOPES"}:
            reason = "permission-denied"
        else:
            reason = "graphql-errors"
        raise _Incomplete(reason, partial_data=response.get("data") is not None)
    try:
        repository = _object(response["data"])["repository"]
        _require(repository is not None, "repository-hidden-or-missing")
        repository = _object(repository)
        _require(repository["id"] == repository_id, "repository-identity-changed")
        snapshot = None
        if request.operation == "listing":
            connection = repository["pullRequests"]
            parse = _snapshot
        else:
            parent = repository["pullRequest"]
            _require(parent is not None, "pr-hidden-or-missing")
            snapshot = _snapshot(_object(parent))
            _require(snapshot["number"] == request.number, "pr-identity-changed")
            if request.operation == "timeline":
                connection, parse = parent["timelineItems"], _timeline
            else:
                connection, parse = parent["reviews"], _review
        connection = _object(connection)
        info = _object(connection["pageInfo"])
        total, rows = connection["totalCount"], connection["nodes"]
        _require(type(total) is int and 0 <= total <= 2**31 - 1)
        _require(isinstance(rows, list) and len(rows) <= request.size)
        _require(type(info["hasNextPage"]) is bool)
        timeline_updated_at = None
        if request.operation == "timeline":
            count = connection["pageCount"]
            _require(
                type(count) is int and count == len(rows), "connection-count-mismatch"
            )
            timeline_updated_at = _stamp(connection["updatedAt"])
        after = None
        if info["hasNextPage"]:
            after = info["endCursor"]
            _require(
                isinstance(after, str) and 0 < len(after) <= 512 and rows,
                "invalid-pagination",
            )
        return _Page(
            [parse(_object(row)) for row in rows],
            total,
            after,
            snapshot,
            timeline_updated_at,
        )
    except KeyError:
        raise _Incomplete("missing-provider-field") from None


def _header_int(value: object) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        return int(value)
    return None


def _http_failure(status: int, rate: dict) -> str:
    if status == 429 or (
        status == 403
        and (rate["remaining"] == 0 or rate["retry_after_seconds"] is not None)
    ):
        return "rate-limited"
    if status in (401, 403):
        return "permission-denied"
    if status == 404:
        return "endpoint-hidden-or-missing"
    return "http-error"


class _Scan:
    def __init__(
        self, checkpoint, through, attempt_id, transport, clock, size, budget, cache
    ):
        self.scope = checkpoint.scope
        self.through = through
        self.floor = checkpoint.through - min(
            self.scope.overlap, checkpoint.through - self.scope.window_start
        )
        self.attempt_id = attempt_id
        self.transport, self.clock = transport, clock
        self.size, self.budget, self.cache = size, budget, cache
        self.requests = 0
        self.last_clock = _utc(clock())
        self.started_at = self.last_clock
        self.request_context: dict = {}
        self.observation: dict = {}
        self.rate: dict = {}
        self.facts: list[dict] = []
        self.coverage: dict = {
            "listing": "pending",
            "verification": "pending",
            "pull_requests": {},
        }

    def _read(self, request: Request, *, verification: bool) -> _Page:
        self.observation = {}
        self.request_context = {
            "operation": "listing-verification" if verification else request.operation,
            "number": request.number,
        }
        _require(self.rate.get("remaining") != 0, "rate-limited")
        _require(self.requests < self.budget, "request-budget-exhausted")
        key = (self.scope.provider_repository_id, request)
        cached = self.cache._entries.get(key)
        lower = _utc(self.clock())
        _require(lower >= self.last_clock, "observation-clock-regressed")
        self.requests += 1
        network_failure = None
        try:
            reply = self.transport(request, cached.tag if cached else None)
        except TimeoutError:
            network_failure = "network-timeout"
        except OSError:
            network_failure = "network-error"
        upper = _utc(self.clock())
        self.observation = {
            "request": self.requests,
            "observed_from": lower.isoformat(),
            "observed_at": upper.isoformat() if upper >= lower else None,
        }
        self.last_clock = max(lower, upper)
        if network_failure:
            raise _Incomplete(network_failure, clock_regressed=upper < lower)
        _require(upper >= lower, "observation-clock-regressed")
        _require(
            isinstance(reply, Response)
            and type(reply.status) is int
            and 100 <= reply.status <= 599
        )
        self.observation["http_status"] = reply.status
        headers = {key.lower(): value for key, value in reply.headers.items()}
        self.rate = {
            "remaining": _header_int(headers.get("x-ratelimit-remaining")),
            "retry_after_seconds": _header_int(headers.get("retry-after")),
            "reset_epoch_seconds": _header_int(headers.get("x-ratelimit-reset")),
        }
        if reply.status == 304:
            _require(cached is not None, "conditional-cache-miss")
            self.observation.update(
                cached_from_attempt=str(cached.attempt_id),
                cached_from_request=cached.request_number,
            )
            self.cache._entries.move_to_end(key)
            return cached.page
        if reply.status != 200:
            raise _Incomplete(_http_failure(reply.status, self.rate))
        page = _parse_page(reply.body, request, self.scope.provider_repository_id)
        tag = headers.get("etag")
        if not isinstance(tag, str) or not re.fullmatch(
            r'(?:W/)?"[!-#%&\x27-~]{1,200}"', tag
        ):
            tag = None
        self.cache._store(
            key, _CachedPage(tag, page, self.attempt_id, self.requests) if tag else None
        )
        return page

    def _fact(self, stream: str, row: dict, pr: dict) -> None:
        identity = [
            self.scope.provider_repository_id,
            pr["provider_id"],
            stream,
            row["provider_id"],
        ]
        if stream == "snapshot":
            identity.extend([str(self.attempt_id), self.requests])
        key = f"github-{stream}-{_digest(identity)}"
        evidence_sha = _digest(row)
        self.facts.append(
            {
                "id": str(uuid5(self.scope.installation_id, key)),
                "source_key": key,
                "evidence_ref": f"github-{_digest([str(self.attempt_id), key, self.requests, evidence_sha])}",
                "evidence_sha256": evidence_sha,
                "pull_request_id": pr["provider_id"],
                "number": pr["number"],
                "stream": stream,
                "provenance": self.request_context["operation"],
                **self.observation,
                "value": row.copy(),
            }
        )

    def _selected(self, pr: dict) -> bool:
        return datetime.fromisoformat(pr["created_at"]) <= self.through and (
            pr["state"] == "open"
            or datetime.fromisoformat(pr["updated_at"]) >= self.floor
        )

    def pages(
        self, operation: str, parent: dict | None = None, *, verification: bool = False
    ) -> dict:
        cursor = None
        cursors: set[str] = set()
        seen: dict[str, dict] = {}
        numbers: dict[int, str] = {}
        total = None
        while True:
            page = self._read(
                Request(
                    self.scope.repository_key,
                    operation,
                    parent["number"] if parent else None,
                    cursor,
                    self.size,
                ),
                verification=verification,
            )
            if page.snapshot is not None and page.snapshot != parent:
                self._fact("snapshot", page.snapshot, page.snapshot)
                raise _Incomplete("pr-changed-during-traversal")
            if total is None:
                total = page.total
                timeline_updated_at = page.timeline_updated_at
            _require(total == page.total, "connection-count-changed")
            _require(
                timeline_updated_at == page.timeline_updated_at,
                "timeline-changed-during-traversal",
            )
            if page.timeline_updated_at is not None:
                self.observation["timeline_updated_at"] = page.timeline_updated_at
            for row in page.rows:
                identity = row["provider_id"]
                previous = seen.get(identity)
                if row == previous:
                    continue
                if parent is not None:
                    self._fact(operation, row, parent)
                else:
                    if self._selected(row):
                        self._fact("snapshot", row, row)
                    _require(
                        numbers.setdefault(row["number"], identity) == identity,
                        "pr-identity-changed",
                    )
                _require(previous is None, "provider-fact-changed")
                seen[identity] = row
            _require(len(seen) <= total, "connection-count-mismatch")
            if page.after is None:
                # GitHub documents totalCount for the whole timeline connection.
                # The filtered timeline is exhausted by its own pageCount/cursor.
                _require(
                    operation == "timeline" or len(seen) == total,
                    "connection-count-mismatch",
                )
                return seen
            cursor = page.after
            _require(cursor not in cursors, "pagination-cursor-cycle")
            cursors.add(cursor)


def _flow(pr: dict, timeline: dict, reviews: dict, observation: dict) -> dict:
    transitions = {
        "ready-for-review",
        "convert-to-draft",
        "closed",
        "merged",
        "reopened",
    }
    ordered = sorted(
        (row for row in timeline.values() if row["kind"] in transitions),
        key=lambda event: event["occurred_at"],
    )
    episodes = []
    ready = None
    for stamp, group in groupby(ordered, key=lambda event: event["occurred_at"]):
        events = list(group)
        kinds = {event["kind"] for event in events}
        if ready is not None:
            if kinds <= {"convert-to-draft", "closed", "merged"}:
                ready["ended_at"] = stamp
            ready = None
        for event in events:
            if event["kind"] == "ready-for-review":
                episode = {
                    "provider_event_id": event["provider_id"],
                    "started_at": stamp,
                    "ended_at": None,
                }
                episodes.append(episode)
                if len(events) == 1:
                    ready = episode

    created = datetime.fromisoformat(pr["created_at"])
    merged = datetime.fromisoformat(pr["merged_at"]) if pr["merged_at"] else None
    lower = datetime.fromisoformat(observation["observed_from"])
    upper = datetime.fromisoformat(observation["observed_at"])
    submitted = [row for row in reviews.values() if row["review_state"] != "pending"]
    return {
        "number": pr["number"],
        "pull_request_id": pr["provider_id"],
        "state": pr["state"],
        "draft_at_observation": pr["draft"],
        "open_to_merge_seconds": (merged - created).total_seconds() if merged else None,
        "open_age_at_observation_lower_bound_seconds": (
            (lower - created).total_seconds()
            if pr["state"] == "open" and lower >= created
            else None
        ),
        "open_age_at_observation_upper_bound_seconds": (
            (upper - created).total_seconds()
            if pr["state"] == "open" and upper >= created
            else None
        ),
        "initial_draft": None,
        "ready_episodes": episodes,
        "formal_github_review_count": (
            len(submitted)
            if all(row["occurred_at"] is not None for row in submitted)
            else None
        ),
    }


def collect(
    checkpoint: Checkpoint,
    through: datetime,
    attempt_id: UUID,
    transport: Transport,
    *,
    clock: Callable[[], datetime],
    page_size: int = 100,
    max_requests: int = 1000,
    cache: PageCache | None = None,
) -> Collection:
    """Traverse serially once. Only a complete report proposes a checkpoint."""
    through = _utc(through)
    if (
        through <= checkpoint.through
        or not isinstance(attempt_id, UUID)
        or attempt_id == checkpoint.attempt_id
        or type(page_size) is not int
        or not 1 <= page_size <= 100
        or type(max_requests) is not int
        or not 1 <= max_requests <= 10_000
    ):
        raise ValueError("invalid lifecycle attempt bounds")
    cache = cache if cache is not None else PageCache()
    cache._bind(transport)
    scan = _Scan(
        checkpoint,
        through,
        attempt_id,
        transport,
        clock,
        page_size,
        max_requests,
        cache,
    )
    if through > scan.started_at:
        raise ValueError("cutoff is later than the start of observation")

    coverage = scan.coverage
    listing_evidence = {}
    flow = []
    failure = None
    try:
        coverage["listing"] = "partial"
        first = scan.pages("listing")
        coverage["listing"] = "complete"
        listing_evidence["listing"] = {
            "count": len(first),
            "evidence_sha256": _digest(first),
        }
        observations = {f["pull_request_id"]: f for f in scan.facts}
        candidates = sorted(
            (pr for pr in first.values() if scan._selected(pr)),
            key=lambda pr: pr["number"],
        )
        coverage["pull_requests"] = {
            pr["number"]: {"timeline": "pending", "reviews": "pending"}
            for pr in candidates
        }
        for pr in candidates:
            sources = coverage["pull_requests"][pr["number"]]
            sources["timeline"] = "partial"
            timeline = scan.pages("timeline", pr)
            sources["timeline"] = "complete"
            sources["reviews"] = "partial"
            reviews = scan.pages("reviews", pr)
            sources["reviews"] = "complete"
            flow.append(_flow(pr, timeline, reviews, observations[pr["provider_id"]]))

        coverage["verification"] = "partial"
        last = scan.pages("listing", verification=True)
        listing_evidence["verification"] = {
            "count": len(last),
            "evidence_sha256": _digest(last),
        }
        _require(first == last, "repository-changed-during-traversal")
        coverage["verification"] = "complete"
    except _Incomplete as exc:
        failure = {
            **exc.details,
            **scan.request_context,
            **scan.observation,
            "rate_limit": scan.rate,
        }

    scope = checkpoint.scope
    report = {
        "source_kind": SOURCE,
        "attempt_id": str(attempt_id),
        "repository": {
            "id": str(scope.repository_id),
            "provider": "github",
            "repository_key": scope.repository_key,
            "provider_id": scope.provider_repository_id,
        },
        "installation_id": str(scope.installation_id),
        "window_start": scope.window_start.isoformat(),
        "prior_through": checkpoint.through.isoformat(),
        "prior_evidence_sha256": checkpoint.evidence_sha256,
        "scan_from": scan.floor.isoformat(),
        "through": through.isoformat(),
        "started_at": scan.started_at.isoformat(),
        "finished_at": scan.last_clock.isoformat(),
        "requests": scan.requests,
        "last_rate_limit": scan.rate,
        "status": "complete" if failure is None else "partial",
        "failure": failure,
        "coverage": coverage,
        "listing_evidence": listing_evidence,
        "facts": scan.facts,
        "flow": flow if failure is None else None,
        "availability": {
            "initial_draft_state": "unknown",
            "unobserved_head_transitions": "unknown",
            "deleted_or_hidden_history": "unknown",
            "independent_t3_reviews": "uncollected",
            "native_t3_checks": "uncollected",
            "local_ci_checks": "uncollected",
            "hosted_github_checks": "uncollected",
            "deployment": "uncollected",
            "active_labor": "uncollected",
        },
    }
    # String keys keep the evidence digest stable across a JSON storage round trip.
    coverage["pull_requests"] = {
        str(pr): info for pr, info in coverage["pull_requests"].items()
    }
    proposal = (
        None if failure else Checkpoint(scope, through, attempt_id, _digest(report))
    )
    return Collection(report, checkpoint, proposal)
