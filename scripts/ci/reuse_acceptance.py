"""Reuse reviewed full, cache or inspection PR receipts for an identical main tree.

Admission is best effort; the gate repeats it independently and fails closed.
No artifact is executed, and donor identities never become current-run receipts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

if __package__:
    from .classify_changes import changes, git, load_classification
    from .summary import summarize as lane_summary
    from .test_selection import (
        CACHE_PROFILE,
        INSPECTION_PROFILE,
        expected_jobs,
        expected_lanes,
        expected_scope,
        select_paths,
        source_job_names,
        valid_scope,
    )
    from .workflow_timing import (
        REPORT_JOB_NAMES,
        SKIPPED_BROWSER,
        STARTUP_FIELDS,
        browser_summary,
        current_identity,
        current_jobs,
        matches_identity,
        summarize,
        startup_jobs,
        test_evidence,
        timestamp,
    )
else:
    from classify_changes import changes, git, load_classification
    from summary import summarize as lane_summary
    from test_selection import (
        CACHE_PROFILE,
        INSPECTION_PROFILE,
        expected_jobs,
        expected_lanes,
        expected_scope,
        select_paths,
        source_job_names,
        valid_scope,
    )
    from workflow_timing import (
        REPORT_JOB_NAMES,
        SKIPPED_BROWSER,
        STARTUP_FIELDS,
        browser_summary,
        current_identity,
        current_jobs,
        matches_identity,
        summarize,
        startup_jobs,
        test_evidence,
        timestamp,
    )


WORKFLOW_PATH = ".github/workflows/ci.yml"
MODE = "reused-full-pr"
CACHE_MODE = "reused-schemer-result-cache-pr"
INSPECTION_MODE = "reused-developer-inspection-pr"
MODES = frozenset({MODE, CACHE_MODE, INSPECTION_MODE})
POLICY_FILES = frozenset(
    {
        WORKFLOW_PATH,
        "scripts/ci/test_selection.py",
        "scripts/ci/coverage-profiles.json",
        "scripts/ci/inspection-coverage.json",
        "scripts/ci/classify_changes.py",
        "scripts/ci/required_gate.py",
        "scripts/ci/reuse_acceptance.py",
        "scripts/ci/workflow_timing.py",
        "scripts/ci/startup_timing.py",
        "scripts/ci/summary.py",
        "scripts/ci/node-tests.mjs",
        "scripts/ci/node-reporter.mjs",
        "scripts/ci/timing.mjs",
        "scripts/ci/playwright-reporter.mjs",
        "scripts/ci/pytest_timing.py",
        "scripts/ci/browser-shards.mjs",
        "scripts/ci/run-browser-shard.mjs",
        "scripts/ci/python-tests.py",
    }
)
EXPENSIVE_NEEDS = {"test", "postgres-integration", "browser-smoke"}
ARCHIVE_LIMIT = 8 * 1024 * 1024
MEMBER_LIMIT = 20 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
MAX_AGE = 24 * 60 * 60


class Rejected(ValueError):
    """A fixed, public rejection category, never an API response."""


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def positive(value):
    return type(value) is int and value > 0


def donor_job_outcomes(profile):
    """Two independent donor topologies, each with its own required exclusions."""
    require(
        profile in {"full", CACHE_PROFILE, INSPECTION_PROFILE},
        "unsupported-donor-profile",
    )
    selected = expected_jobs(profile)
    return {
        **{
            name: "success" if name in selected else "skipped"
            for name in source_job_names(profile)
        },
        **{name: "success" for name in REPORT_JOB_NAMES},
        "Public workflow timing": "success",
        "CI validation": "success",
    }


DONOR_JOBS = set(donor_job_outcomes("full"))
CACHE_DONOR_JOBS = donor_job_outcomes(CACHE_PROFILE)
INSPECTION_DONOR_JOBS = donor_job_outcomes(INSPECTION_PROFILE)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    """Bounded read-only API access; signed downloads never receive the token."""

    def __init__(self, repository, token):
        require(
            bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)),
            "repository",
        )
        self.prefix = f"repos/{repository}/"
        self.token = token
        self.deadline = time.monotonic() + 90
        self.opener = build_opener(NoRedirect())

    def request(self, url, limit, *, authenticated=False):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "api-budget")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if authenticated:
            require(
                url.startswith("https://api.github.com/" + self.prefix), "api-route"
            )
            headers["Authorization"] = "Bearer " + self.token
        with self.opener.open(
            Request(url, headers=headers), timeout=min(5, remaining)
        ) as response:
            chunks, size = [], 0
            while True:
                require(time.monotonic() < self.deadline, "api-budget")
                # read1 performs at most one underlying read; check the total
                # deadline between reads even for a slow trickle of bytes.
                chunk = response.read1(min(64 * 1024, limit + 1 - size))
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                require(size <= limit, "download-budget")

    def get(self, path):
        return json.loads(
            self.request(
                "https://api.github.com/" + self.prefix + path,
                4 * 1024 * 1024,
                authenticated=True,
            )
        )

    def collection(self, path, key=None):
        """Two bounded pages, with complete count/closure rather than truncation."""
        result = []
        total = None
        for page in (1, 2):
            separator = "&" if "?" in path else "?"
            value = self.get(f"{path}{separator}per_page=100&page={page}")
            if key:
                require(
                    isinstance(value, dict) and type(value.get("total_count")) is int,
                    "api-pagination",
                )
                if total is None:
                    total = value["total_count"]
                require(
                    total == value["total_count"] and 0 <= total <= 200,
                    "api-pagination",
                )
                values = value.get(key)
            else:
                values = value
            require(
                isinstance(values, list)
                and len(values) <= 100
                and all(isinstance(item, dict) for item in values),
                "api-pagination",
            )
            result.extend(values)
            if total is not None and len(result) == total:
                return result
            if len(values) < 100:
                require(total is None, "api-pagination")
                return result
        # A full uncounted second page could have an undiscovered third page.
        raise Rejected("api-pagination")

    def archive(self, artifact_id):
        url = f"https://api.github.com/{self.prefix}actions/artifacts/{artifact_id}/zip"
        try:
            return self.request(url, ARCHIVE_LIMIT, authenticated=True)
        except HTTPError as error:
            require(error.code in {302, 303, 307}, "artifact-download")
            location = error.headers.get("Location", "")
            target = urlsplit(location)
            require(
                target.scheme == "https"
                and target.hostname
                and not target.username
                and not target.password
                and target.port in {None, 443},
                "artifact-redirect",
            )
            # The signed URL is used only for this unauthenticated request.
            return self.request(location, ARCHIVE_LIMIT)


def artifact_files(profile="full"):
    require(
        profile in {"full", CACHE_PROFILE, INSPECTION_PROFILE},
        "unsupported-donor-profile",
    )
    values = {
        "ci-classification-attempt-1": {"ci-classification.json"},
        "report-validation-attempt-1": {"report-validation.json"},
        "workflow-timing-attempt-1": {"workflow-summary.json"},
    }
    for lane, project, shard in sorted(expected_lanes(profile)):
        name = (
            f"{lane}-timing-attempt-1"
            if lane != "browser"
            else f"browser-timing-{project}-shard-{shard}-attempt-1"
        )
        values[name] = {f"{lane}.jsonl", f"{lane}-summary.json"}
        if lane == "browser":
            values[name].add("browser-dependencies.json")
    return values


ARTIFACT_FILES = artifact_files()
CACHE_ARTIFACT_FILES = artifact_files(CACHE_PROFILE)
INSPECTION_ARTIFACT_FILES = artifact_files(INSPECTION_PROFILE)


def donor_profile(artifacts):
    """Only three closed artifact contracts; caller/profile labels are not authority."""
    names = {value.get("name") for value in artifacts}
    if len(artifacts) == len(ARTIFACT_FILES) and names == set(ARTIFACT_FILES):
        return "full"
    if len(artifacts) == len(CACHE_ARTIFACT_FILES) and names == set(
        CACHE_ARTIFACT_FILES
    ):
        return CACHE_PROFILE
    require(
        len(artifacts) == len(INSPECTION_ARTIFACT_FILES)
        and names == set(INSPECTION_ARTIFACT_FILES),
        "donor-artifacts",
    )
    return INSPECTION_PROFILE


def target_owner_proof(target, root=None, *, profile=CACHE_PROFILE):
    """Recompute complete Git ownership and unchanged current policy, never a label."""
    require(profile in {CACHE_PROFILE, INSPECTION_PROFILE}, "unsupported-donor-profile")
    prefix = "cache" if profile == CACHE_PROFILE else "inspection"
    root = Path.cwd() if root is None else Path(root)
    before, head = target["before"], target["source_sha"]
    require(
        sha(before)
        and sha(head)
        and git(root, "rev-parse", "HEAD").decode().strip() == head
        and git(root, "rev-parse", "HEAD^{tree}").decode().strip() == target["tree"]
        and not git(root, "status", "--porcelain", "--untracked-files=no"),
        prefix + "-checkout-source",
    )
    git(root, "merge-base", "--is-ancestor", before, head)
    complete = changes(root, before, head)
    require(
        bool(complete)
        and select_paths(complete) == profile
        and all(status == "M" and len(paths) == 1 for status, paths in complete),
        prefix + "-target-owner",
    )

    def entry(commit, path):
        values = git(root, "ls-tree", "-z", commit, "--", path).split(b"\0")
        require(len(values) == 2 and values[1] == b"", prefix + "-tree-entry")
        identity, name = values[0].split(b"\t")
        mode, kind, blob = identity.decode().split()
        require(
            name.decode() == path
            and mode in {"100644", "100755"}
            and kind == "blob"
            and sha(blob),
            prefix + "-tree-entry",
        )
        return mode, blob

    changed = []
    for _, (path,) in sorted(complete):
        old_mode, old_blob = entry(before, path)
        new_mode, new_blob = entry(head, path)
        require(old_mode == new_mode, prefix + "-file-mode")
        changed.append(
            {
                "status": "M",
                "path": path,
                "mode": new_mode,
                "before_blob": old_blob,
                "after_blob": new_blob,
            }
        )
    policy_blobs = {}
    current_policy = Path(__file__).resolve().parents[2]
    for path in sorted(POLICY_FILES):
        old = entry(before, path)
        new = entry(head, path)
        data = (current_policy / path).read_bytes()
        current_blob = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
        require(old == new and new[1] == current_blob, prefix + "-policy-changed")
        policy_blobs[path] = new[1]
    return {
        "schema": 1,
        "profile": profile,
        "base": before,
        "head": head,
        "changes": changed,
        "policy_blobs": policy_blobs,
    }


def valid_receipt_mode(receipt):
    """Validate the closed mode/profile/owner pairing; live recheck remains required."""
    try:
        if not isinstance(receipt, dict) or receipt.get("mode") not in MODES:
            return False
        classification = receipt.get("classification")
        if not isinstance(classification, dict):
            return False
        if receipt["mode"] == MODE:
            return (
                classification.get("profile") == "full"
                and "target_owner_proof" not in receipt
            )
        profile = CACHE_PROFILE if receipt["mode"] == CACHE_MODE else INSPECTION_PROFILE
        proof, target = receipt.get("target_owner_proof"), receipt.get("target")
        if not isinstance(proof, dict) or not isinstance(target, dict):
            return False
        if (
            set(proof)
            != {"schema", "profile", "base", "head", "changes", "policy_blobs"}
            or type(proof["schema"]) is not int
            or proof["schema"] != 1
            or proof["profile"] != profile
            or proof["base"] != target["before"]
            or proof["head"] != target["source_sha"]
            or classification.get("valid") is not True
            or classification.get("lane") != "source"
            or classification.get("profile") != profile
            or classification.get("base") != classification.get("comparison_base")
            or classification.get("base") != target["before"]
        ):
            return False
        entries = proof["changes"]
        if (
            not isinstance(entries, list)
            or not entries
            or any(
                not isinstance(value, dict) or not isinstance(value.get("path"), str)
                for value in entries
            )
        ):
            return False
        paths = [value["path"] for value in entries]
        if (
            paths != sorted(set(paths))
            or select_paths([("M", [path]) for path in paths]) != profile
        ):
            return False
        if any(
            not isinstance(value, dict)
            or set(value) != {"status", "path", "mode", "before_blob", "after_blob"}
            or value["status"] != "M"
            or value["mode"] not in {"100644", "100755"}
            or not sha(value["before_blob"])
            or not sha(value["after_blob"])
            for value in entries
        ):
            return False
        evidence, donor = receipt.get("test_evidence"), receipt.get("donor")
        if (
            not isinstance(evidence, dict)
            or not isinstance(donor, dict)
            or evidence.get("profile") != profile
            or evidence.get("complete") is not True
            or classification.get("head") != donor.get("head_sha")
        ):
            return False
        lanes = evidence.get("lanes")
        if (
            not isinstance(lanes, list)
            or len(lanes) != len(expected_lanes(profile))
            or any(
                not isinstance(value, dict) or type(value.get("shard")) is not int
                for value in lanes
            )
        ):
            return False
        keys = [
            (value.get("lane"), value.get("project"), value["shard"]) for value in lanes
        ]
        if set(keys) != expected_lanes(profile) or any(
            value.get("complete") is not True
            or value.get("outcome") != "passed"
            or type(value.get("first_attempt_failures")) is not int
            or value["first_attempt_failures"] != 0
            or type(value.get("retry_recovered")) is not int
            or value["retry_recovered"] != 0
            or not matches_identity(
                value,
                {
                    field: donor[field]
                    for field in ("source_sha", "run_id", "run_attempt")
                },
            )
            or not valid_scope(profile, key, value.get("scope"))
            for value, key in zip(lanes, keys)
        ):
            return False
        blobs = proof["policy_blobs"]
        return (
            isinstance(blobs, dict)
            and set(blobs) == POLICY_FILES
            and all(sha(value) for value in blobs.values())
        )
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        return False


def unpack(data, digest, expected):
    require(
        len(data) <= ARCHIVE_LIMIT
        and isinstance(digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
        "artifact-digest",
    )
    require("sha256:" + hashlib.sha256(data).hexdigest() == digest, "artifact-digest")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        require(
            len(members) == len(expected)
            and {item.filename for item in members} == expected,
            "archive-members",
        )
        total = 0
        result = {}
        for item in members:
            mode = item.external_attr >> 16
            require(
                not item.is_dir()
                and not item.flag_bits & 1
                and not stat.S_ISLNK(mode)
                and (not stat.S_IFMT(mode) or stat.S_ISREG(mode)),
                "archive-member-type",
            )
            require(
                0 <= item.file_size <= MEMBER_LIMIT
                and item.file_size <= max(1, item.compress_size) * 100,
                "archive-size",
            )
            total += item.file_size
            require(total <= TOTAL_LIMIT, "archive-size")
            with archive.open(item) as member:
                value = member.read(MEMBER_LIMIT + 1)
            require(len(value) == item.file_size, "archive-size")
            result[item.filename] = value
        return result


def json_value(data):
    value = json.loads(data)
    require(isinstance(value, dict), "artifact-json")
    return value


def equal_json(first, second):
    # Python's ordinary equality considers True == 1; proof schemas do not.
    return json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(
        second, sort_keys=True, allow_nan=False
    )


def context(environ, event, classification, root):
    identity = current_identity(environ)
    require(
        environ.get("GITHUB_EVENT_NAME") == "push"
        and environ.get("GITHUB_REF") == "refs/heads/main",
        "event-not-main-push",
    )
    require(identity["run_attempt"] == 1, "current-rerun")
    require(
        event.get("forced") is False
        and event.get("deleted") is False
        and sha(event.get("before"))
        and event["before"] != "0" * 40
        and event.get("after") == identity["source_sha"] == identity["head_sha"],
        "push-binding",
    )
    require(
        classification.get("valid") is True
        and classification.get("lane") == "source"
        and classification.get("profile") == "full"
        and classification.get("head") == identity["source_sha"]
        and classification.get("base") == event["before"],
        "current-classification",
    )
    repository = environ["GITHUB_REPOSITORY"]
    require(
        event["repository"].get("full_name") == repository
        and positive(event["repository"].get("id")),
        "repository",
    )

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, timeout=10, text=True
        ).strip()

    require(
        git("rev-parse", "HEAD") == identity["source_sha"]
        and not git("status", "--porcelain", "--untracked-files=no"),
        "checkout-source",
    )
    return {
        **identity,
        "repository": repository,
        "repository_id": event["repository"]["id"],
        "before": event["before"],
        "tree": git("rev-parse", "HEAD^{tree}"),
    }


def provider_binding(client, target, donor_id=None, *, now=None):
    """Resolve the real CI workflow, unique merged PR, and attempt-one cohort."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    tip = client.get("git/ref/heads/main")
    require(tip["object"].get("sha") == target["source_sha"], "main-tip-changed")
    workflow = client.get("actions/workflows/ci.yml")
    require(
        positive(workflow.get("id"))
        and workflow.get("path") == WORKFLOW_PATH
        and workflow.get("state") == "active",
        "workflow-identity",
    )
    pulls = client.collection(f"commits/{target['source_sha']}/pulls")
    candidates = [
        value
        for value in pulls
        if value.get("merged_at")
        and value.get("merge_commit_sha") == target["source_sha"]
    ]
    require(
        len(candidates) == 1 and positive(candidates[0].get("number")),
        "merged-pr-ambiguous",
    )
    pr = client.get(f"pulls/{candidates[0]['number']}")
    require(
        pr.get("number") == candidates[0]["number"]
        and pr.get("merged") is True
        and pr.get("state") == "closed"
        and pr.get("merge_commit_sha") == target["source_sha"],
        "merged-pr-binding",
    )
    base, head = pr["base"], pr["head"]
    require(
        base["repo"].get("id") == head["repo"].get("id") == target["repository_id"]
        and base["repo"].get("full_name")
        == head["repo"].get("full_name")
        == target["repository"]
        and base.get("ref") == "main"
        and base.get("sha") == target["before"]
        and sha(head.get("sha")),
        "pr-repository-or-base",
    )
    runs = client.collection(
        f"actions/workflows/{workflow['id']}/runs?event=pull_request&head_sha={head['sha']}",
        "workflow_runs",
    )
    applicable = [
        run
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    require(
        len(applicable) == 1 and positive(applicable[0].get("id")),
        "donor-run-ambiguous",
    )
    run = client.get(f"actions/runs/{applicable[0]['id']}")
    require(donor_id is None or run.get("id") == donor_id, "donor-run-changed")
    require(
        run.get("id") == applicable[0]["id"]
        and run.get("workflow_id") == workflow["id"]
        and run.get("path") == WORKFLOW_PATH
        and run.get("event") == "pull_request"
        and run.get("head_sha") == head["sha"]
        and run.get("repository", {}).get("id")
        == run.get("head_repository", {}).get("id")
        == target["repository_id"]
        and run.get("run_attempt") == 1
        and run.get("status") == "completed"
        and run.get("conclusion") == "success",
        "donor-run-binding",
    )
    completed = timestamp(run.get("updated_at"))
    merged = timestamp(pr.get("merged_at"))
    require(
        completed is not None
        and merged is not None
        and 0 <= now - completed < MAX_AGE
        and completed <= merged <= now,
        "donor-age",
    )
    jobs = client.collection(f"actions/runs/{run['id']}/attempts/1/jobs", "jobs")
    artifacts = client.collection(f"actions/runs/{run['id']}/artifacts", "artifacts")
    profile = donor_profile(artifacts)
    expected_outcomes = donor_job_outcomes(profile)
    identity = {"run_id": run["id"], "run_attempt": 1, "head_sha": head["sha"]}
    require(
        len(jobs) == len(expected_outcomes)
        and {job.get("name") for job in jobs} == set(expected_outcomes)
        and current_jobs(jobs, identity)
        and all(
            job.get("status") == "completed"
            and job.get("conclusion") == expected_outcomes[job["name"]]
            for job in jobs
        ),
        "donor-jobs",
    )
    end = max(timestamp(job.get("completed_at")) or 0 for job in jobs)
    require(end > 0 and 0 <= now - end < MAX_AGE and end <= merged, "donor-age")
    require(
        len({value.get("id") for value in artifacts}) == len(artifacts),
        "donor-artifacts",
    )
    for value in artifacts:
        binding = value.get("workflow_run", {})
        require(
            positive(value.get("id"))
            and value.get("expired") is False
            and binding.get("id") == run["id"]
            and binding.get("head_sha") == head["sha"]
            and binding.get("repository_id")
            == binding.get("head_repository_id")
            == target["repository_id"]
            and isinstance(value.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"]),
            "artifact-binding",
        )
        expiry = timestamp(value.get("expires_at"))
        require(
            expiry is not None
            and expiry > now
            and type(value.get("size_in_bytes")) is int
            and 0 < value["size_in_bytes"] <= ARCHIVE_LIMIT,
            "artifact-expired-or-size",
        )
    return pr, workflow, run, jobs, artifacts


def verify(client, target, *, donor_id=None, now=None, retained=None, root=None):
    """Validate original bytes in automatically cleaned scratch, even at the gate."""
    pr, workflow, run, jobs, artifacts = provider_binding(
        client, target, donor_id, now=now
    )
    profile = donor_profile(artifacts)
    expected_files = artifact_files(profile)
    checkout = Path.cwd() if root is None else Path(root)
    with tempfile.TemporaryDirectory(prefix="schemii-acceptance-") as temporary:
        root = Path(temporary)
        total = 0
        expanded = 0
        for artifact in artifacts:
            data = client.archive(artifact["id"])
            total += len(data)
            require(total <= TOTAL_LIMIT, "download-budget")
            files = unpack(data, artifact["digest"], expected_files[artifact["name"]])
            expanded += sum(len(value) for value in files.values())
            require(expanded <= TOTAL_LIMIT, "archive-size")
            directory = root / artifact["name"]
            directory.mkdir()
            for name, value in files.items():
                (directory / name).write_bytes(value)
        classification = load_classification(
            root / "ci-classification-attempt-1/ci-classification.json"
        )
        require(
            classification["valid"] is True
            and classification["lane"] == "source"
            and classification["profile"] == profile
            and classification["base"] == target["before"]
            and classification["head"] == run["head_sha"],
            "donor-classification",
        )
        owner_proof = None
        if profile in {CACHE_PROFILE, INSPECTION_PROFILE}:
            require(
                classification["comparison_base"] == classification["base"]
                and classification["reason"] == "verified-owned-pr-change",
                "cache-donor-base"
                if profile == CACHE_PROFILE
                else "inspection-donor-base",
            )
            owner_proof = target_owner_proof(target, checkout, profile=profile)
        report = json_value(
            (root / "report-validation-attempt-1/report-validation.json").read_bytes()
        )
        require(
            set(report) == {"schema", "outcome", "files", "links", "execution_ms"}
            and type(report["schema"]) is int
            and report["schema"] == 1
            and report["outcome"] == "success"
            and all(
                type(report[key]) is int and report[key] >= 0
                for key in ("files", "links")
            )
            and type(report["execution_ms"]) in {int, float}
            and 0 <= report["execution_ms"] <= 10**15,
            "donor-reports",
        )
        evidence = test_evidence(root, profile=profile)
        require(evidence["complete"] is True, "donor-raw-receipts")
        evidence["lanes"].sort(
            key=lambda value: (value["lane"], value["project"], value["shard"])
        )
        source = evidence["lanes"][0]["source_sha"]
        donor_identity = {
            "source_sha": source,
            "head_sha": run["head_sha"],
            "run_id": run["id"],
            "run_attempt": 1,
        }
        require(
            sha(source)
            and all(
                value["run_id"] == run["id"] and value["run_attempt"] == 1
                for value in evidence["lanes"]
            ),
            "donor-cohort",
        )
        donor_commit = client.get(f"git/commits/{source}")
        current_commit = client.get(f"git/commits/{target['source_sha']}")
        require(
            donor_commit.get("sha") == source
            and current_commit.get("sha") == target["source_sha"]
            and [value.get("sha") for value in donor_commit["parents"]]
            == [target["before"], run["head_sha"]]
            and [value.get("sha") for value in current_commit["parents"]]
            in ([target["before"]], [target["before"], run["head_sha"]]),
            "synthetic-merge-binding",
        )
        require(
            sha(target["tree"])
            and donor_commit["tree"].get("sha")
            == current_commit["tree"].get("sha")
            == target["tree"],
            "different-tested-tree",
        )
        saved = json_value(
            (root / "workflow-timing-attempt-1/workflow-summary.json").read_bytes()
        )
        startup = startup_jobs(evidence, profile)
        # Older original receipts have no startup extension or phase fields. They
        # remain their original format; missing measurements are never fabricated.
        startup_format = bool(startup) or any(
            field in job for job in saved["jobs"] for field in STARTUP_FIELDS
        )
        measured = {
            **summarize(
                run,
                jobs,
                profile=profile,
                report_validation=True,
                startup=startup if startup_format else None,
            ),
            **donor_identity,
            "classification_valid": True,
            "test_evidence": evidence,
        }
        saved["test_evidence"]["lanes"].sort(
            key=lambda value: (value["lane"], value["project"], value["shard"])
        )
        require(
            measured["complete"] is True and equal_json(saved, measured),
            "donor-workflow-receipt",
        )
        public_files = {}
        for path in root.rglob("*.jsonl"):
            records = [
                json.loads(line) for line in path.read_text().splitlines() if line
            ]
            summary = lane_summary(records)
            # Recompute and validate derived summaries; never retain archive content
            # outside the already approved raw schema and these derived fields.
            saved_lane = json_value(
                path.with_name(path.stem + "-summary.json").read_bytes()
            )
            if "startup" in saved_lane:
                summary = browser_summary(records, saved_lane["startup"])
            require(equal_json(saved_lane, summary), "donor-lane-summary")
            lane, project, shard = summary["lane"], summary["project"], summary["shard"]
            key = lane, project, shard
            if expected_scope(profile, key) is not None:
                scoped = next(
                    value
                    for value in evidence["lanes"]
                    if (value["lane"], value["project"], value["shard"]) == key
                )["scope"]
                require(
                    valid_scope(profile, key, scoped),
                    "cache-donor-scope"
                    if profile == CACHE_PROFILE
                    else "inspection-donor-scope",
                )
                summary["scope"] = scoped
            expected_archive = (
                f"{lane}-timing-attempt-1"
                if lane != "browser"
                else f"browser-timing-{project}-shard-{shard}-attempt-1"
            )
            require(path.parent.name == expected_archive, "artifact-lane-binding")
            relative = str(path.relative_to(root))
            # Serialize the validated schema, retaining the cohort verbatim while
            # dropping whitespace or duplicate JSON keys from untrusted bytes.
            public_files[relative] = (
                "\n".join(json.dumps(value, allow_nan=False) for value in records)
                + "\n"
            ).encode()
            public_files[
                str(path.with_name(path.stem + "-summary.json").relative_to(root))
            ] = (json.dumps(summary, indent=2) + "\n").encode()
        # Recheck mutable provider identities after downloads, not only before them.
        again = provider_binding(client, target, run["id"], now=now)
        require(
            equal_json(again, (pr, workflow, run, jobs, artifacts)), "provider-changed"
        )
        if owner_proof is not None:
            require(
                equal_json(
                    owner_proof, target_owner_proof(target, checkout, profile=profile)
                ),
                "cache-owner-changed"
                if profile == CACHE_PROFILE
                else "inspection-owner-changed",
            )
        receipt = {
            "schema": 1,
            "mode": MODE
            if profile == "full"
            else CACHE_MODE
            if profile == CACHE_PROFILE
            else INSPECTION_MODE,
            "target": target,
            "donor": {
                **donor_identity,
                "pr": pr["number"],
                "tree": target["tree"],
                "base_sha": target["before"],
                "workflow_id": workflow["id"],
                "workflow_path": WORKFLOW_PATH,
                "repository_id": target["repository_id"],
            },
            "artifacts": sorted(
                (
                    {
                        "id": value["id"],
                        "name": value["name"],
                        "digest": value["digest"],
                    }
                    for value in artifacts
                ),
                key=lambda value: value["name"],
            ),
            "verified_jobs": sorted(donor_job_outcomes(profile)),
            "classification": classification,
            "test_evidence": evidence,
            "donor_cost": {
                key: measured[key]
                for key in ("critical_path_ms", "total_job_minutes", "jobs")
            },
        }
        if owner_proof is not None:
            receipt["target_owner_proof"] = owner_proof
        if retained is not None:
            retained.update(public_files)
        return receipt


def recheck(receipt, client, target, *, now=None, root=None):
    require(
        isinstance(receipt, dict)
        and set(receipt)
        == {
            "schema",
            "mode",
            "target",
            "donor",
            "artifacts",
            "verified_jobs",
            "classification",
            "test_evidence",
            "donor_cost",
        }
        | (
            {"target_owner_proof"}
            if receipt.get("mode") in {CACHE_MODE, INSPECTION_MODE}
            else set()
        )
        and type(receipt["schema"]) is int
        and receipt["schema"] == 1
        and valid_receipt_mode(receipt)
        and receipt["target"] == target
        and isinstance(receipt["donor"], dict)
        and positive(receipt["donor"].get("run_id")),
        "reuse-receipt",
    )
    verified = verify(
        client, target, donor_id=receipt["donor"]["run_id"], now=now, root=root
    )
    require(equal_json(verified, receipt), "reuse-proof-changed")
    return verified


def current_reuse_jobs(jobs, identity):
    """Exactly the fresh inputs and intentional skips, including the unexpanded matrix."""
    required = {
        **{name: "success" for name in REPORT_JOB_NAMES},
        "Incremental Python static quality": "success",
        "Node and deterministic Python behavior": "skipped",
        "Real PostgreSQL metadata behavior": "skipped",
    }
    names = [job.get("name") for job in jobs]
    browser = set(expected_jobs("full")) - set(required)
    skipped_browser = {SKIPPED_BROWSER} if SKIPPED_BROWSER in names else browser
    required.update({name: "skipped" for name in skipped_browser})
    inputs = [
        job
        for job in jobs
        if job.get("name") not in {"Public workflow timing", "CI validation"}
    ]
    return (
        len(names) == len(set(names))
        and len(inputs) == len(required)
        and {job.get("name") for job in inputs} == set(required)
        and current_jobs(jobs, identity)
        and all(
            job.get("status") == "completed"
            and job.get("conclusion") == required[job["name"]]
            for job in inputs
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mode, reason = "fresh", "admission-unavailable"
    try:
        classification = load_classification(args.classification)
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        target = context(os.environ, event, classification, Path.cwd())
        client = GitHub(target["repository"], os.environ["GITHUB_TOKEN"])
        retained = {}
        receipt = verify(client, target, retained=retained)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for relative, data in retained.items():
            destination = args.output.parent / "donor" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
        require(valid_receipt_mode(receipt), "reuse-mode-profile")
        mode, reason = receipt["mode"], "verified-identical-tested-tree"
    except Rejected as error:
        reason = str(error)
    except Exception:
        pass  # No provider response, credentials, or signed URL enters public output.
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write("acceptance=" + mode + "\n")
    print("Acceptance mode=" + mode + "; reason=" + reason)
    return 0  # Any uncertainty retains ordinary full acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
