"""An identical tree can reuse original acceptance, never its provider conclusion alone."""

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest

from scripts.ci import reuse_acceptance as reuse
from scripts.ci import required_gate as gate
from scripts.ci.required_gate import CONTROL_NEEDS, evaluate
from scripts.ci.summary import summarize as lane_summary
from scripts.ci.test_selection import (
    CACHE_PROFILE,
    CACHE_SOURCE,
    CACHE_TEST,
    INSPECTION_PROFILE,
    INSPECTION_SOURCES,
    INSPECTION_TEST,
    SOURCE_NEEDS,
    expected_lanes,
    expected_jobs,
    required_needs,
    source_job_names,
    expected_scope,
)
from scripts.ci.workflow_timing import summarize, test_evidence as collect_evidence


NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc).timestamp()
TARGET = {
    "source_sha": "a" * 40,
    "head_sha": "a" * 40,
    "run_id": 22,
    "run_attempt": 1,
    "repository": "owner/repo",
    "repository_id": 100,
    "before": "b" * 40,
    "tree": "c" * 40,
}
DONOR = {"source_sha": "d" * 40, "head_sha": "e" * 40, "run_id": 11, "run_attempt": 1}


def raw(key):
    lane, project, shard = key
    meta = {
        "schema": 1,
        **{key: DONOR[key] for key in ("source_sha", "run_id", "run_attempt")},
        "lane": lane,
        "project": project,
        "shard": shard,
    }
    return [
        {**meta, "kind": "start", "planned": 1},
        {**meta, "kind": "plan", "test_id": "1" * 64},
        {
            **meta,
            "kind": "attempt",
            "test_id": "1" * 64,
            "source_id": "2" * 64,
            "source_line": 1,
            "attempt": 0,
            "outcome": "passed",
            "skip": "none",
            "setup_ms": 1,
            "execution_ms": 2,
            "teardown_ms": 1,
        },
        {**meta, "kind": "end", "outcome": "passed", "wall_ms": 5},
    ]


def job(name, identity=DONOR, outcome="success"):
    return {
        "name": name,
        "status": "completed",
        "conclusion": outcome,
        "started_at": "2026-10-02T10:00:02Z",
        "completed_at": "2026-10-02T10:00:10Z",
        **{key: identity[key] for key in ("head_sha", "run_id", "run_attempt")},
        "steps": [],
    }


def classification(head, base):
    return {
        "schema": 2,
        "valid": True,
        "lane": "source",
        "profile": "full",
        "reason": "source-or-unknown-change",
        "base": base,
        "head": head,
        "comparison_base": base,
        "markdown": [],
    }


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            output.writestr(name, data)
    return stream.getvalue()


def encoded(value):
    return json.dumps(value).encode()


class Provider:
    def __init__(self, root):
        self.run = {
            "id": DONOR["run_id"],
            "workflow_id": 10,
            "path": reuse.WORKFLOW_PATH,
            "event": "pull_request",
            "head_sha": DONOR["head_sha"],
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "repository": {"id": 100},
            "head_repository": {"id": 100},
            "created_at": "2026-10-02T10:00:00Z",
            "run_started_at": "2026-10-02T10:00:01Z",
            "updated_at": "2026-10-02T10:00:12Z",
            "pull_requests": [],
        }
        repository = {"id": 100, "full_name": TARGET["repository"]}
        self.pr = {
            "number": 7,
            "merged": True,
            "state": "closed",
            "merged_at": "2026-10-02T11:00:00Z",
            "merge_commit_sha": TARGET["source_sha"],
            "base": {"ref": "main", "sha": TARGET["before"], "repo": repository},
            "head": {"sha": DONOR["head_sha"], "repo": repository},
        }
        self.workflow = {"id": 10, "path": reuse.WORKFLOW_PATH, "state": "active"}
        self.jobs = [job(name) for name in sorted(reuse.DONOR_JOBS)]
        self.tip = {"object": {"sha": TARGET["source_sha"]}}
        self.commits = {
            DONOR["source_sha"]: {
                "sha": DONOR["source_sha"],
                "tree": {"sha": TARGET["tree"]},
                "parents": [{"sha": TARGET["before"]}, {"sha": DONOR["head_sha"]}],
            },
            TARGET["source_sha"]: {
                "sha": TARGET["source_sha"],
                "tree": {"sha": TARGET["tree"]},
                "parents": [{"sha": TARGET["before"]}],
            },
        }
        self.files = {}
        self.files["ci-classification-attempt-1"] = {
            "ci-classification.json": encoded(
                classification(DONOR["head_sha"], TARGET["before"])
            )
        }
        self.files["report-validation-attempt-1"] = {
            "report-validation.json": encoded(
                {
                    "schema": 1,
                    "outcome": "success",
                    "files": 0,
                    "links": 0,
                    "execution_ms": 1,
                }
            )
        }
        for key in sorted(expected_lanes("full")):
            lane, project, shard = key
            name = (
                f"{lane}-timing-attempt-1"
                if lane != "browser"
                else f"browser-timing-{project}-shard-{shard}-attempt-1"
            )
            records = raw(key)
            self.files[name] = {
                f"{lane}.jsonl": b"\n".join(encoded(value) for value in records),
                f"{lane}-summary.json": encoded(lane_summary(records)),
            }
            if lane == "browser":
                self.files[name]["browser-dependencies.json"] = b"{}"
            (root / (name + ".jsonl")).write_bytes(self.files[name][f"{lane}.jsonl"])
        measured = {
            **summarize(self.run, self.jobs, profile="full", report_validation=True),
            **DONOR,
            "classification_valid": True,
            "test_evidence": collect_evidence(root, profile="full"),
        }
        self.files["workflow-timing-attempt-1"] = {
            "workflow-summary.json": encoded(measured)
        }
        self.artifacts = []
        self.bytes = {}
        for index, name in enumerate(sorted(self.files), 1):
            self.set_archive(index, name)

    def set_archive(self, artifact_id, name):
        data = archive(self.files[name])
        self.bytes[artifact_id] = data
        value = {
            "id": artifact_id,
            "name": name,
            "expired": False,
            "expires_at": "2026-10-03T10:00:00Z",
            "size_in_bytes": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "workflow_run": {
                "id": self.run["id"],
                "head_sha": self.run["head_sha"],
                "repository_id": 100,
                "head_repository_id": 100,
            },
        }
        self.artifacts = [
            item for item in self.artifacts if item["id"] != artifact_id
        ] + [value]

    def get(self, path):
        if path == "git/ref/heads/main":
            return self.tip
        if path == "actions/workflows/ci.yml":
            return self.workflow
        if path == "pulls/7":
            return self.pr
        if path == "actions/runs/11":
            return self.run
        if path.startswith("git/commits/"):
            return self.commits[path.rsplit("/", 1)[1]]
        raise AssertionError(path)

    def collection(self, path, key=None):
        if path.endswith("/pulls"):
            return [self.pr]
        if key == "workflow_runs":
            return [self.run]
        if key == "jobs":
            return self.jobs
        if key == "artifacts":
            return self.artifacts
        raise AssertionError(path)

    def archive(self, artifact_id):
        return self.bytes[artifact_id]


@pytest.fixture
def provider(tmp_path):
    return Provider(tmp_path)


def test_equal_squash_tree_and_empty_run_pr_array_reuses_original_full_receipts(
    provider,
):
    retained = {}
    receipt = reuse.verify(provider, TARGET, now=NOW, retained=retained)
    assert receipt["mode"] == reuse.MODE
    assert (
        receipt["target"]["source_sha"]
        != receipt["donor"]["source_sha"]
        != receipt["donor"]["head_sha"]
    )
    assert receipt["verified_jobs"] == sorted(reuse.DONOR_JOBS)
    assert (
        len(receipt["artifacts"]) == 18 and len(receipt["test_evidence"]["lanes"]) == 15
    )
    assert len(retained) == 30 and all("dependencies" not in name for name in retained)
    assert reuse.recheck(receipt, provider, TARGET, now=NOW) == receipt
    for name, value in retained.items():
        if name.endswith("jsonl"):
            assert all(
                json.loads(line)["run_id"] == DONOR["run_id"]
                for line in value.splitlines()
            )


@pytest.mark.parametrize(
    "damage",
    [
        "workflow-id",
        "workflow-path",
        "workflow-event",
        "repository",
        "fork",
        "head",
        "base",
        "unmerged",
        "merge-sha",
        "run-attempt",
        "expired",
        "aged",
        "tip",
        "missing-job",
        "duplicate-job",
        "failed-gate",
        "job-cohort",
        "unknown-job",
        "missing-artifact",
        "duplicate-artifact",
        "artifact-cohort",
        "missing-digest",
        "raw-missing-end",
        "raw-recovered",
        "raw-terminal-error",
        "raw-source",
        "raw-extra-field",
        "selected",
        "saved-summary",
        "synthetic-parent",
        "synthetic-tree",
        "target-tree",
    ],
)
def test_provider_and_raw_receipt_mutations_cannot_admit(provider, damage):
    if damage == "workflow-id":
        provider.run["workflow_id"] = 20
    elif damage == "workflow-path":
        provider.workflow["path"] = ".github/workflows/unrelated.yml"
    elif damage == "workflow-event":
        provider.run["event"] = "push"
    elif damage in {"repository", "fork"}:
        provider.run["repository" if damage == "repository" else "head_repository"][
            "id"
        ] = 200
    elif damage == "head":
        provider.run["head_sha"] = "f" * 40
    elif damage == "base":
        provider.pr["base"]["sha"] = "f" * 40
    elif damage == "unmerged":
        provider.pr["merged"] = False
    elif damage == "merge-sha":
        provider.pr["merge_commit_sha"] = "f" * 40
    elif damage == "run-attempt":
        provider.run["run_attempt"] = 2
    elif damage == "expired":
        provider.artifacts[0]["expired"] = True
    elif damage == "aged":
        provider.run["updated_at"] = "2026-10-01T10:00:00Z"
    elif damage == "tip":
        provider.tip["object"]["sha"] = "f" * 40
    elif damage == "missing-job":
        provider.jobs.pop()
    elif damage == "duplicate-job":
        provider.jobs.append(provider.jobs[0])
    elif damage == "failed-gate":
        next(value for value in provider.jobs if value["name"] == "CI validation")[
            "conclusion"
        ] = "failure"
    elif damage == "job-cohort":
        provider.jobs[0]["run_id"] = 20
    elif damage == "unknown-job":
        provider.jobs[0]["name"] = "Unexpected browser leg"
    elif damage == "missing-artifact":
        provider.artifacts.pop()
    elif damage == "duplicate-artifact":
        provider.artifacts.append(provider.artifacts[0])
    elif damage == "artifact-cohort":
        provider.artifacts[0]["workflow_run"]["head_sha"] = "f" * 40
    elif damage == "missing-digest":
        provider.artifacts[0]["digest"] = None
    elif damage.startswith("raw-") or damage in {"selected", "saved-summary"}:
        name = "node-timing-attempt-1"
        records = [
            json.loads(line) for line in provider.files[name]["node.jsonl"].splitlines()
        ]
        if damage == "raw-missing-end":
            records.pop()
        elif damage == "raw-recovered":
            records[2]["outcome"] = "failed"
            records.insert(3, {**records[2], "attempt": 1, "outcome": "passed"})
        elif damage == "raw-terminal-error":
            records[-1]["outcome"] = "error"
        elif damage == "raw-source":
            records[0]["source_sha"] = "f" * 40
        elif damage == "raw-extra-field":
            records[2]["error"] = "unapproved secret"
        elif damage == "selected":
            name = "ci-classification-attempt-1"
            value = json.loads(provider.files[name]["ci-classification.json"])
            value.update(profile="native", reason="verified-owned-pr-change")
            provider.files[name]["ci-classification.json"] = encoded(value)
        else:
            provider.files[name]["node-summary.json"] = b"{}"
        if damage.startswith("raw-"):
            provider.files[name]["node.jsonl"] = b"\n".join(
                encoded(value) for value in records
            )
        artifact_id = next(
            value["id"] for value in provider.artifacts if value["name"] == name
        )
        provider.set_archive(artifact_id, name)
    elif damage == "synthetic-parent":
        provider.commits[DONOR["source_sha"]]["parents"][1]["sha"] = "f" * 40
    elif damage == "synthetic-tree":
        # Head-tree equality cannot substitute for the tested prospective merge.
        provider.commits[DONOR["source_sha"]]["tree"]["sha"] = "f" * 40
    elif damage == "target-tree":
        provider.commits[TARGET["source_sha"]]["tree"]["sha"] = "f" * 40
    with pytest.raises((ValueError, KeyError)):
        reuse.verify(provider, TARGET, now=NOW)


@pytest.mark.parametrize(
    "damage",
    [
        "unknown-field",
        "target",
        "donor",
        "receipt-summary",
        "provider-rerun",
        "api-error",
    ],
)
def test_gate_redownloads_and_rejects_corrupt_or_changed_proof(provider, damage):
    receipt = copy.deepcopy(reuse.verify(provider, TARGET, now=NOW))
    if damage == "unknown-field":
        receipt["unapproved"] = "secret"
    elif damage == "target":
        receipt["target"]["run_id"] = 99
    elif damage == "donor":
        receipt["donor"]["source_sha"] = "f" * 40
    elif damage == "receipt-summary":
        receipt["test_evidence"]["first_attempt_failures"] = 10
    elif damage == "provider-rerun":
        provider.run["run_attempt"] = 2
    else:
        provider.get = lambda path: (_ for _ in ()).throw(
            OSError("private provider body")
        )
    with pytest.raises((ValueError, OSError)):
        reuse.recheck(receipt, provider, TARGET, now=NOW)


@pytest.mark.parametrize("error", [False, True])
def test_download_extraction_scratch_is_automatically_cleaned(
    provider, monkeypatch, tmp_path, error
):
    monkeypatch.setattr(reuse.tempfile, "tempdir", str(tmp_path))
    observed = []
    original = provider.archive

    def download(artifact_id):
        observed.extend(tmp_path.glob("schemii-acceptance-*"))
        if error:
            raise OSError("download interrupted")
        return original(artifact_id)

    provider.archive = download
    if error:
        with pytest.raises(OSError):
            reuse.verify(provider, TARGET, now=NOW)
    else:
        reuse.verify(provider, TARGET, now=NOW)
    assert observed and all(not path.exists() for path in observed)


@pytest.mark.parametrize(
    "damage", ["digest", "traversal", "extra", "duplicate", "symlink", "bomb", "size"]
)
def test_archive_content_is_bounded_and_digest_verified(damage):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        name = "../node.jsonl" if damage == "traversal" else "node.jsonl"
        if damage == "symlink":
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            output.writestr(info, b"target")
        else:
            output.writestr(name, b"x" * 100000 if damage == "bomb" else b"{}")
        if damage in {"extra", "duplicate"}:
            output.writestr("secret.txt" if damage == "extra" else name, b"secret")
    data = stream.getvalue()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if damage == "digest":
        digest = "sha256:" + "0" * 64
    elif damage == "size":
        data += b"x" * reuse.ARCHIVE_LIMIT
    with pytest.raises(ValueError):
        reuse.unpack(data, digest, {"node.jsonl"})


def current_inputs(identity=TARGET):
    return [
        job(
            name,
            identity,
            "skipped"
            if name
            in {
                "Node and deterministic Python behavior",
                "Real PostgreSQL metadata behavior",
                reuse.SKIPPED_BROWSER,
            }
            else "success",
        )
        for name in [
            *reuse.REPORT_JOB_NAMES,
            "Incremental Python static quality",
            "Node and deterministic Python behavior",
            "Real PostgreSQL metadata behavior",
            reuse.SKIPPED_BROWSER,
        ]
    ]


def test_gate_and_timing_keep_current_skips_and_donor_cost_separate(provider):
    receipt = reuse.verify(provider, TARGET, now=NOW)
    jobs = current_inputs()
    timing = {
        **summarize(
            provider.run, jobs, profile="full", report_validation=True, reused=True
        ),
        **{
            key: TARGET[key]
            for key in ("source_sha", "head_sha", "run_id", "run_attempt")
        },
        "acceptance_mode": reuse.MODE,
        "reuse": receipt,
    }
    assert timing["complete"]
    assert (
        len(timing["jobs"]) == 3
        and timing["total_job_minutes"] < receipt["donor_cost"]["total_job_minutes"]
    )
    assert "test_evidence" not in timing and receipt["donor"]["run_id"] == 11
    needs = {
        name: {"result": "skipped" if name in reuse.EXPENSIVE_NEEDS else "success"}
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }
    needs["classify"]["outputs"] = {"acceptance": reuse.MODE}
    identity = {
        key: TARGET[key] for key in ("source_sha", "head_sha", "run_id", "run_attempt")
    }
    args = (
        classification(TARGET["head_sha"], TARGET["before"]),
        needs,
        jobs,
        timing,
        identity,
    )
    assert evaluate(*args, reuse=receipt)[0]
    assert not evaluate(*args)[0]
    for name in reuse.EXPENSIVE_NEEDS:
        damaged = copy.deepcopy(needs)
        damaged[name]["result"] = "success"
        assert not evaluate(args[0], damaged, jobs, timing, identity, reuse=receipt)[0]
    for damage in [
        jobs[:-1],
        jobs + [jobs[0]],
        jobs + [job("unknown", TARGET)],
        [dict(value, conclusion="success") for value in jobs],
    ]:
        assert not evaluate(args[0], needs, damage, timing, identity, reuse=receipt)[0]


def test_two_page_api_requires_complete_counts_and_rejects_unbounded_lists(monkeypatch):
    client = reuse.GitHub("owner/repo", "private-token")
    pages = [
        {"total_count": 101, "jobs": [{}] * 100},
        {"total_count": 101, "jobs": [{}]},
    ]
    monkeypatch.setattr(client, "get", lambda path: pages.pop(0))
    assert len(client.collection("jobs", "jobs")) == 101
    monkeypatch.setattr(
        client, "get", lambda path: {"total_count": 201, "jobs": [{}] * 100}
    )
    with pytest.raises(ValueError):
        client.collection("jobs", "jobs")
    monkeypatch.setattr(client, "get", lambda path: [{}] * 100)
    with pytest.raises(ValueError):
        client.collection("pulls")


def test_archive_redirect_is_credential_free_and_bounded(monkeypatch):
    from urllib.error import HTTPError

    client = reuse.GitHub("owner/repo", "private-token")
    calls = []

    def request(url, limit, *, authenticated=False):
        calls.append((url, limit, authenticated))
        if authenticated:
            raise HTTPError(
                url,
                302,
                "redirect",
                {"Location": "https://storage.example.invalid/private-signed-query"},
                None,
            )
        return b"archive"

    monkeypatch.setattr(client, "request", request)
    assert client.archive(1) == b"archive"
    assert [call[2] for call in calls] == [True, False]
    assert all(call[1] == reuse.ARCHIVE_LIMIT for call in calls)


def test_workflow_keeps_controls_fresh_and_uses_recheck_with_no_extra_donor_artifact():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    ).read_text()
    # Parse at job indentation, without needing a third-party YAML parser.
    sections = dict(
        re.findall(
            r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:|\Z)",
            workflow.split("jobs:\n", 1)[1],
            re.MULTILINE | re.DOTALL,
        )
    )
    assert "acceptance != 'reused-full-pr'" not in sections["static-quality"]
    for name in reuse.EXPENSIVE_NEEDS:
        assert "acceptance != 'reused-full-pr'" in sections[name]
    assert "timeout-minutes: 2" in sections["classify"]
    assert (
        "if: steps.reuse.outputs.acceptance == 'reused-full-pr'" in sections["classify"]
    )
    assert "reuse_acceptance.py" in sections["classify"]
    assert "--reuse artifacts/acceptance-reuse/reuse.json" in sections["required-gate"]
    assert "pull-requests: read" in sections["required-gate"]
    assert len(reuse.DONOR_JOBS) == 19 and len(reuse.ARTIFACT_FILES) == 18


def test_main_cli_falls_back_without_provider_errors_or_credentials(tmp_path):
    manifest = tmp_path / "classification.json"
    manifest.write_bytes(
        encoded(classification(TARGET["source_sha"], TARGET["before"]))
    )
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/ci/reuse_acceptance.py",
            "--classification",
            str(manifest),
            "--output",
            str(tmp_path / "reuse.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output)},
    )
    assert result.returncode == 0 and output.read_text() == "acceptance=fresh\n"
    assert result.stdout == "Acceptance mode=fresh; reason=admission-unavailable\n"
    assert not (tmp_path / "reuse.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "manual",
        "pull-request",
        "ref",
        "forced",
        "deleted",
        "before",
        "after",
        "rerun",
        "selected",
        "reports",
        "dirty",
        "checkout",
        "repo",
    ],
)
def test_context_requires_an_ordinary_current_main_push(tmp_path, damage):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.name", "Owned fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "file").write_text("before")
    git("add", ".")
    git("commit", "--quiet", "-m", "before")
    before = git("rev-parse", "HEAD")
    (tmp_path / "file").write_text("after")
    git("commit", "--quiet", "-am", "after")
    after = git("rev-parse", "HEAD")
    env = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": after,
        "GITHUB_RUN_ID": "22",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    event = {
        "before": before,
        "after": after,
        "forced": False,
        "deleted": False,
        "repository": {"id": 100, "full_name": "owner/repo"},
    }
    manifest = classification(after, before)
    assert reuse.context(env, event, manifest, tmp_path)["tree"] == git(
        "rev-parse", "HEAD^{tree}"
    )
    if damage in {"manual", "pull-request"}:
        env["GITHUB_EVENT_NAME"] = (
            "workflow_dispatch" if damage == "manual" else "pull_request"
        )
    elif damage == "ref":
        env["GITHUB_REF"] = "refs/heads/other"
    elif damage in {"forced", "deleted"}:
        event[damage] = True
    elif damage in {"before", "after"}:
        event[damage] = "0" * 40
    elif damage == "rerun":
        env["GITHUB_RUN_ATTEMPT"] = "2"
    elif damage in {"selected", "reports"}:
        manifest["profile"] = "native" if damage == "selected" else "reports"
    elif damage == "dirty":
        (tmp_path / "file").write_text("changed policy")
    elif damage == "checkout":
        env["GITHUB_SHA"] = before
        event["after"] = before
        manifest["head"] = before
    else:
        event["repository"]["id"] = "100"
    with pytest.raises(ValueError):
        reuse.context(env, event, manifest, tmp_path)


@pytest.mark.parametrize(
    "error",
    [
        reuse.Rejected("donor-raw-receipts"),
        OSError("private token signed-url provider-response"),
    ],
)
def test_admission_api_and_validation_uncertainty_returns_full_fallback(
    provider, tmp_path, monkeypatch, capsys, error
):
    manifest = tmp_path / "classification.json"
    manifest.write_bytes(encoded(classification(TARGET["head_sha"], TARGET["before"])))
    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    destination = tmp_path / "retained/reuse.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reuse_acceptance.py",
            "--classification",
            str(manifest),
            "--output",
            str(destination),
        ],
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(reuse, "context", lambda *args: TARGET)
    monkeypatch.setattr(reuse, "GitHub", lambda *args: provider)
    monkeypatch.setattr(
        reuse, "verify", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )
    assert reuse.main() == 0
    assert output.read_text() == "acceptance=fresh\n"
    assert not destination.exists()
    assert "private" not in capsys.readouterr().out


def test_archive_lane_swap_and_ambiguous_merge_or_runs_are_rejected(provider):
    original_collection = provider.collection
    for key in ("workflow_runs", None):
        provider.collection = lambda path, selected=None: (
            original_collection(path, selected) * 2
            if selected == key
            else original_collection(path, selected)
        )
        with pytest.raises(ValueError):
            reuse.verify(provider, TARGET, now=NOW)
    provider.collection = original_collection
    names = [
        f"browser-timing-desktop-chromium-shard-{shard}-attempt-1" for shard in (1, 2)
    ]
    provider.files[names[0]], provider.files[names[1]] = (
        provider.files[names[1]],
        provider.files[names[0]],
    )
    for name in names:
        artifact_id = next(
            value["id"] for value in provider.artifacts if value["name"] == name
        )
        provider.set_archive(artifact_id, name)
    with pytest.raises(ValueError, match="artifact-lane-binding"):
        reuse.verify(provider, TARGET, now=NOW)


def test_network_reads_enforce_total_deadline_and_never_forward_auth_on_redirect(
    monkeypatch,
):
    from urllib.error import HTTPError

    client = reuse.GitHub("owner/repo", "private-token")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read1(self, count):
            if getattr(self, "read", False):
                return b""
            self.read = True
            return b"archive"

    class Opener:
        def open(self, request, *, timeout):
            calls.append((request, timeout))
            if request.get_header("Authorization"):
                raise HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {
                        "Location": "https://storage.example.invalid/private-signed-query"
                    },
                    None,
                )
            return Response()

    client.opener = Opener()
    assert client.archive(1) == b"archive"
    assert calls[0][0].get_header("Authorization") == "Bearer private-token"
    assert calls[1][0].get_header("Authorization") is None
    assert all(timeout <= 5 for _, timeout in calls)
    client.deadline = 0
    with pytest.raises(ValueError, match="api-budget"):
        client.request("https://storage.example.invalid/private-signed-query", 10)


@pytest.mark.parametrize(
    "profile", ["reports", "native", "frontend-tests", "backend-tests"]
)
def test_current_literal_skipped_matrix_is_valid_only_for_an_excluded_browser_lane(
    tmp_path, provider, profile
):
    lane = "reports" if profile == "reports" else "source"
    selected = expected_jobs(profile)
    jobs = [
        job(name, outcome="success" if name in selected else "skipped")
        for name in source_job_names(profile)
        if not name.startswith("Assembled browser smoke (")
    ]
    jobs += [job(name) for name in reuse.REPORT_JOB_NAMES]
    jobs.append(job(reuse.SKIPPED_BROWSER, outcome="skipped"))
    inputs = tmp_path / "selected"
    inputs.mkdir()
    for key in sorted(expected_lanes(profile)):
        (inputs / ("-".join(map(str, key)) + ".jsonl")).write_bytes(
            b"\n".join(encoded(value) for value in raw(key))
        )
    manifest = {
        **classification(DONOR["head_sha"], TARGET["before"]),
        "lane": lane,
        "profile": profile,
        "reason": "verified-report-only"
        if lane == "reports"
        else "verified-owned-pr-change",
    }
    needs = {
        name: {
            "result": "success"
            if name in CONTROL_NEEDS or name in required_needs(profile)
            else "skipped"
        }
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }

    def timing_for(values):
        return {
            **summarize(
                provider.run, values, lane=lane, profile=profile, report_validation=True
            ),
            **DONOR,
            "test_evidence": collect_evidence(
                inputs, lane=lane, profile=profile, identity=DONOR
            ),
        }

    assert evaluate(manifest, needs, jobs, timing_for(jobs), DONOR)[0]
    for damage in ["legacy", "suffix", "failed", "stale", "duplicate"]:
        altered = copy.deepcopy(jobs)
        placeholder = altered[-1]
        if damage == "legacy":
            placeholder["name"] = (
                "Assembled browser smoke (${{ matrix.project }}, shard ${{ matrix.shard }}/3)"
            )
        elif damage == "suffix":
            placeholder["name"] += " arbitrary"
        elif damage == "failed":
            placeholder["conclusion"] = "failure"
        elif damage == "stale":
            placeholder["run_id"] = 999
        else:
            altered.append(copy.deepcopy(placeholder))
        assert not evaluate(manifest, needs, altered, timing_for(altered), DONOR)[0]


def test_full_acceptance_never_accepts_an_unexpanded_skipped_browser_placeholder(
    provider,
):
    jobs = [
        value
        for value in provider.jobs
        if not value["name"].startswith("Assembled browser smoke (")
    ]
    jobs.append(job(reuse.SKIPPED_BROWSER, outcome="skipped"))
    assert not summarize(provider.run, jobs, profile="full", report_validation=True)[
        "complete"
    ]


def test_gate_cli_revalidates_live_donor_after_admission(
    provider, tmp_path, monkeypatch, capsys
):
    receipt = reuse.verify(provider, TARGET, now=NOW)
    identity = {
        key: TARGET[key] for key in ("source_sha", "head_sha", "run_id", "run_attempt")
    }
    jobs = current_inputs()
    needs = {
        name: {"result": "skipped" if name in reuse.EXPENSIVE_NEEDS else "success"}
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }
    needs["classify"]["outputs"] = {"acceptance": reuse.MODE}
    timing = {
        **summarize(
            provider.run, jobs, profile="full", report_validation=True, reused=True
        ),
        **identity,
        "acceptance_mode": reuse.MODE,
        "reuse": receipt,
    }
    manifest_path, timing_path, reuse_path = [
        tmp_path / name
        for name in ("classification.json", "workflow-summary.json", "reuse.json")
    ]
    manifest_path.write_bytes(
        encoded(classification(TARGET["head_sha"], TARGET["before"]))
    )
    timing_path.write_bytes(encoded(timing))
    reuse_path.write_bytes(encoded(receipt))
    event = tmp_path / "event.json"
    event.write_text("{}")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "required_gate.py",
            "--classification",
            str(manifest_path),
            "--timing",
            str(timing_path),
            "--reuse",
            str(reuse_path),
        ],
    )
    for name, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_TOKEN": "private-token",
        "CI_NEEDS": json.dumps(needs),
        "GITHUB_REPOSITORY": TARGET["repository"],
        "GITHUB_RUN_ID": "22",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(gate, "current_identity", lambda env: identity)
    monkeypatch.setattr(
        gate, "api", lambda path: {"total_count": len(jobs), "jobs": jobs}
    )
    monkeypatch.setattr(reuse, "context", lambda *args: TARGET)
    monkeypatch.setattr(reuse, "GitHub", lambda *args: provider)
    original = reuse.recheck
    monkeypatch.setattr(
        reuse,
        "recheck",
        lambda receipt, client, target: original(receipt, client, target, now=NOW),
    )
    assert gate.main() == 0
    provider.run["run_attempt"] = 2
    assert gate.main() == 1
    assert "private" not in capsys.readouterr().out


def cache_repository(root, *, paired=False, stale_policy=False, profile=CACHE_PROFILE):
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.name", "Owned cache fixture")
    git("config", "user.email", "fixture@example.invalid")
    current = Path(__file__).resolve().parents[1]
    for relative in reuse.POLICY_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((current / relative).read_bytes())
    source_files = (
        (CACHE_SOURCE, CACHE_TEST)
        if profile == CACHE_PROFILE
        else (*sorted(INSPECTION_SOURCES), INSPECTION_TEST)
    )
    for relative in source_files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original cache fixture\n")
    if stale_policy:
        (root / "scripts/ci/coverage-profiles.json").write_text("stale policy\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "current reviewed policy")
    before = git("rev-parse", "HEAD")
    for relative in (
        (CACHE_SOURCE,) if profile == CACHE_PROFILE else sorted(INSPECTION_SOURCES)
    ):
        (root / relative).write_text("changed source fixture\n")
    if paired:
        (
            root / (CACHE_TEST if profile == CACHE_PROFILE else INSPECTION_TEST)
        ).write_text("changed direct test fixture\n")
    git("commit", "--quiet", "-am", "cache change")
    head = git("rev-parse", "HEAD")
    target = {
        **TARGET,
        "before": before,
        "source_sha": head,
        "head_sha": head,
        "tree": git("rev-parse", "HEAD^{tree}"),
    }
    return target


def selected_raw(key, *, allowed_skip=False, profile=CACHE_PROFILE, helper_extra=False):
    scope = expected_scope(profile, key)
    if scope is None:
        return raw(key)
    meta = {
        key: value
        for key, value in raw(key)[0].items()
        if key not in {"kind", "planned"}
    }
    if helper_extra and profile == INSPECTION_PROFILE and key == ("python", "none", 0):
        scope[hashlib.sha256(b"new unfiltered helper case").hexdigest()] = {
            "source_id": hashlib.sha256(INSPECTION_TEST.encode()).hexdigest(),
            "allow_skip": False,
        }
    tests = sorted(scope)
    return [
        {**meta, "kind": "start", "planned": len(tests)},
        *[{**meta, "kind": "plan", "test_id": test} for test in tests],
        *[
            {
                **meta,
                "kind": "attempt",
                "test_id": test,
                "source_id": scope[test]["source_id"],
                "source_line": 1,
                "attempt": 0,
                "outcome": "skipped"
                if allowed_skip and scope[test]["allow_skip"]
                else "passed",
                "skip": "declared-or-runtime"
                if allowed_skip and scope[test]["allow_skip"]
                else "none",
                "setup_ms": 1,
                "execution_ms": 2,
                "teardown_ms": 1,
            }
            for test in tests
        ],
        {**meta, "kind": "end", "outcome": "passed", "wall_ms": 5},
    ]


def cache_provider(
    root,
    checkout,
    target,
    *,
    allowed_skip=False,
    profile=CACHE_PROFILE,
    helper_extra=False,
):
    legacy = root / "legacy"
    legacy.mkdir()
    provider = Provider(legacy)
    provider.pr["base"]["sha"] = target["before"]
    provider.pr["merge_commit_sha"] = target["source_sha"]
    provider.tip["object"]["sha"] = target["source_sha"]
    parents = subprocess.check_output(
        ["git", "rev-list", "--parents", "-1", target["source_sha"]],
        cwd=checkout,
        text=True,
    ).split()[1:]
    final_head = parents[1] if len(parents) == 2 else DONOR["head_sha"]
    provider.pr["head"]["sha"] = provider.run["head_sha"] = final_head
    provider.commits = {
        DONOR["source_sha"]: {
            "sha": DONOR["source_sha"],
            "tree": {"sha": target["tree"]},
            "parents": [{"sha": target["before"]}, {"sha": final_head}],
        },
        target["source_sha"]: {
            "sha": target["source_sha"],
            "tree": {"sha": target["tree"]},
            "parents": [{"sha": value} for value in parents],
        },
    }
    provider.jobs = [
        job(name, {**DONOR, "head_sha": final_head}, outcome)
        for name, outcome in reuse.donor_job_outcomes(profile).items()
    ]
    manifest = {
        **classification(final_head, target["before"]),
        "profile": profile,
        "reason": "verified-owned-pr-change",
    }
    provider.files = {
        "ci-classification-attempt-1": {"ci-classification.json": encoded(manifest)},
        "report-validation-attempt-1": provider.files["report-validation-attempt-1"],
    }
    selected = root / "selected-raw"
    selected.mkdir()
    for key in sorted(expected_lanes(profile)):
        lane, project, shard = key
        name = (
            f"{lane}-timing-attempt-1"
            if lane != "browser"
            else f"browser-timing-{project}-shard-{shard}-attempt-1"
        )
        records = selected_raw(
            key, allowed_skip=allowed_skip, profile=profile, helper_extra=helper_extra
        )
        data = b"\n".join(encoded(value) for value in records)
        provider.files[name] = {
            f"{lane}.jsonl": data,
            f"{lane}-summary.json": encoded(lane_summary(records)),
        }
        if lane == "browser":
            provider.files[name]["browser-dependencies.json"] = b"{}"
        (selected / (name + ".jsonl")).write_bytes(data)
    measured = {
        **summarize(
            provider.run, provider.jobs, profile=profile, report_validation=True
        ),
        **{**DONOR, "head_sha": final_head},
        "classification_valid": True,
        "test_evidence": collect_evidence(selected, profile=profile),
    }
    provider.files["workflow-timing-attempt-1"] = {
        "workflow-summary.json": encoded(measured)
    }
    provider.artifacts, provider.bytes = [], {}
    for index, name in enumerate(sorted(provider.files), 1):
        provider.set_archive(index, name)
    return provider


@pytest.fixture
def selected_provider(tmp_path):
    checkout = tmp_path / "checkout"
    target = cache_repository(checkout)
    return cache_provider(tmp_path, checkout, target), target, checkout


@pytest.mark.parametrize("paired", [False, True])
@pytest.mark.parametrize("allowed_skip", [False, True])
def test_selected_cache_donor_keeps_original_scopes_and_gate_skips(
    tmp_path, paired, allowed_skip
):
    checkout = tmp_path / "checkout"
    target = cache_repository(checkout, paired=paired)
    provider = cache_provider(tmp_path, checkout, target, allowed_skip=allowed_skip)
    retained = {}
    receipt = reuse.verify(provider, target, root=checkout, now=NOW, retained=retained)
    assert reuse.MODE == "reused-full-pr" and reuse.valid_receipt_mode(receipt)
    assert (
        receipt["mode"] == reuse.CACHE_MODE
        and receipt["classification"]["profile"] == CACHE_PROFILE
    )
    assert (
        len(receipt["artifacts"]) == 11 and len(receipt["test_evidence"]["lanes"]) == 8
    )
    proof = receipt["target_owner_proof"]
    assert [value["path"] for value in proof["changes"]] == (
        [CACHE_SOURCE] if not paired else sorted([CACHE_SOURCE, CACHE_TEST])
    )
    assert set(proof["policy_blobs"]) == reuse.POLICY_FILES
    scoped = [value for value in receipt["test_evidence"]["lanes"] if "scope" in value]
    assert len(scoped) == 7 and all(
        value["scope"]["profile"] == CACHE_PROFILE for value in scoped
    )
    assert (
        len(retained) == 16
        and sum(
            "scope" in json.loads(value)
            for name, value in retained.items()
            if name.endswith("-summary.json")
        )
        == 7
    )
    assert (
        receipt["donor"]["source_sha"] == DONOR["source_sha"]
        and receipt["donor"]["run_id"] == DONOR["run_id"]
    )
    assert reuse.recheck(receipt, provider, target, root=checkout, now=NOW) == receipt
    jobs = current_inputs(target)
    identity = {
        key: target[key] for key in ("source_sha", "head_sha", "run_id", "run_attempt")
    }
    timing = {
        **summarize(
            provider.run, jobs, profile="full", report_validation=True, reused=True
        ),
        **identity,
        "acceptance_mode": reuse.CACHE_MODE,
        "reuse": receipt,
    }
    needs = {
        name: {"result": "skipped" if name in reuse.EXPENSIVE_NEEDS else "success"}
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }
    needs["classify"]["outputs"] = {"acceptance": reuse.CACHE_MODE}
    assert "test_evidence" not in timing
    assert evaluate(
        classification(target["head_sha"], target["before"]),
        needs,
        jobs,
        timing,
        identity,
        reuse=receipt,
    )[0]
    damaged = copy.deepcopy(receipt)
    damaged["target_owner_proof"]["changes"][0]["path"] = "unrelated.js"
    assert not reuse.valid_receipt_mode(damaged)
    assert not evaluate(
        classification(target["head_sha"], target["before"]),
        needs,
        jobs,
        {**timing, "reuse": damaged},
        identity,
        reuse=damaged,
    )[0]


@pytest.mark.parametrize(
    "damage",
    [
        "mixed",
        "test-only",
        "new",
        "delete",
        "rename",
        "mode",
        "symlink",
        "policy",
        "stale-policy",
        "dirty",
        "batch",
        "checkout",
    ],
)
def test_cache_target_proof_rejects_complete_diff_or_policy_uncertainty(
    tmp_path, damage
):
    checkout = tmp_path / "checkout"
    target = cache_repository(checkout, stale_policy=damage == "stale-policy")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=checkout, text=True).strip()

    if damage == "mixed":
        (checkout / "unrelated.md").write_text("unrelated report")
    elif damage == "test-only":
        (checkout / CACHE_SOURCE).write_text("original cache fixture\n")
        (checkout / CACHE_TEST).write_text("direct test only")
    elif damage == "new":
        (checkout / "new.js").write_text("new input")
    elif damage == "delete":
        (checkout / CACHE_SOURCE).unlink()
    elif damage == "rename":
        (checkout / CACHE_SOURCE).rename(checkout / "renamed.js")
    elif damage == "mode":
        (checkout / CACHE_SOURCE).chmod(0o755)
    elif damage == "symlink":
        (checkout / CACHE_SOURCE).unlink()
        (checkout / CACHE_SOURCE).symlink_to("result-cache-target.js")
    elif damage == "policy":
        (checkout / reuse.WORKFLOW_PATH).write_text("changed execution contract")
    elif damage in {"dirty", "batch", "checkout"}:
        (checkout / CACHE_SOURCE).write_text("later cache change")
    if damage not in {"dirty", "stale-policy"}:
        git("add", ".")
        git("commit", "--quiet", "-m", "target mutation")
        if damage != "checkout":
            head = git("rev-parse", "HEAD")
            target.update(
                source_sha=head, head_sha=head, tree=git("rev-parse", "HEAD^{tree}")
            )
    provider = cache_provider(tmp_path, checkout, target)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        reuse.verify(provider, target, root=checkout, now=NOW)


@pytest.mark.parametrize(
    "damage",
    [
        "comparison-base",
        "other-profile",
        "static-executed",
        "postgres-executed",
        "browser-skipped",
        "extra-postgres",
        "missing-browser",
        "case-replaced",
        "source-replaced",
        "case-missing",
        "extra-case",
        "forbidden-skip",
        "retry",
        "fake-scope",
    ],
)
def test_cache_donor_rejects_incomplete_or_differently_scoped_evidence(
    selected_provider, damage
):
    provider, target, checkout = selected_provider
    if damage in {"comparison-base", "other-profile"}:
        name = "ci-classification-attempt-1"
        value = json.loads(provider.files[name]["ci-classification.json"])
        if damage == "comparison-base":
            value["comparison_base"] = "f" * 40
        else:
            value.update(profile="e2e-tests", reason="verified-owned-pr-change")
        provider.files[name]["ci-classification.json"] = encoded(value)
    elif damage in {"static-executed", "postgres-executed", "browser-skipped"}:
        chosen = (
            "Incremental Python static quality"
            if damage == "static-executed"
            else "Real PostgreSQL metadata behavior"
            if damage == "postgres-executed"
            else "Assembled browser smoke (desktop-chromium, shard 1/3)"
        )
        next(value for value in provider.jobs if value["name"] == chosen)[
            "conclusion"
        ] = "skipped" if damage == "browser-skipped" else "success"
        name = None
    elif damage == "extra-postgres":
        name = "postgres-timing-attempt-1"
        provider.files[name] = {"postgres.jsonl": b"{}", "postgres-summary.json": b"{}"}
        provider.set_archive(100, name)
        name = None
    elif damage == "missing-browser":
        provider.artifacts = [
            value
            for value in provider.artifacts
            if value["name"] != "browser-timing-desktop-chromium-shard-1-attempt-1"
        ]
        name = None
    elif damage == "fake-scope":
        name = "workflow-timing-attempt-1"
        value = json.loads(provider.files[name]["workflow-summary.json"])
        next(value for value in value["test_evidence"]["lanes"] if "scope" in value)[
            "scope"
        ]["planned"][0] = "f" * 64
        provider.files[name]["workflow-summary.json"] = encoded(value)
    else:
        name = "python-timing-attempt-1"
        records = [
            json.loads(line)
            for line in provider.files[name]["python.jsonl"].splitlines()
        ]
        attempt = next(value for value in records if value["kind"] == "attempt")
        if damage == "case-replaced":
            original = attempt["test_id"]
            for value in records:
                if value.get("test_id") == original:
                    value["test_id"] = "f" * 64
        elif damage == "source-replaced":
            attempt["source_id"] = "f" * 64
        elif damage == "case-missing":
            records = [
                value for value in records if value.get("test_id") != attempt["test_id"]
            ]
            records[0]["planned"] -= 1
        elif damage == "extra-case":
            records.insert(
                1,
                {
                    **next(value for value in records if value["kind"] == "plan"),
                    "test_id": "f" * 64,
                },
            )
            records.insert(-1, {**attempt, "test_id": "f" * 64})
            records[0]["planned"] += 1
        elif damage == "forbidden-skip":
            attempt.update(outcome="skipped", skip="declared-or-runtime")
        else:
            attempt["outcome"] = "failed"
            records.insert(
                records.index(attempt) + 1,
                {**attempt, "attempt": 1, "outcome": "passed"},
            )
        provider.files[name]["python.jsonl"] = b"\n".join(
            encoded(value) for value in records
        )
        provider.files[name]["python-summary.json"] = encoded(lane_summary(records))
    if name:
        artifact_id = next(
            value["id"] for value in provider.artifacts if value["name"] == name
        )
        provider.set_archive(artifact_id, name)
    with pytest.raises((ValueError, KeyError)):
        reuse.verify(provider, target, root=checkout, now=NOW)


@pytest.mark.parametrize(
    "damage",
    ["owner-proof", "mode", "mode-profile", "current-policy", "base", "run", "raw"],
)
def test_cache_gate_recomputes_target_and_provider_after_admission(
    selected_provider, damage
):
    provider, target, checkout = selected_provider
    receipt = reuse.verify(provider, target, root=checkout, now=NOW)
    if damage == "owner-proof":
        receipt["target_owner_proof"]["policy_blobs"][reuse.WORKFLOW_PATH] = "f" * 40
    elif damage == "mode":
        receipt["mode"] = "reused-arbitrary-pr"
    elif damage == "mode-profile":
        receipt["mode"] = reuse.MODE
    elif damage == "current-policy":
        (checkout / "scripts/ci/coverage-profiles.json").write_text(
            "policy changed after admission"
        )
    elif damage == "base":
        provider.pr["base"]["sha"] = "f" * 40
    elif damage == "run":
        provider.run["run_attempt"] = 2
    else:
        artifact = next(
            value
            for value in provider.artifacts
            if value["name"] == "node-timing-attempt-1"
        )
        provider.bytes[artifact["id"]] = b"corrupt replacement"
    with pytest.raises(ValueError):
        reuse.recheck(receipt, provider, target, root=checkout, now=NOW)


def test_full_receipt_shape_and_closed_mode_pairing_remain_compatible(provider):
    receipt = reuse.verify(provider, TARGET, now=NOW)
    assert reuse.valid_receipt_mode(receipt)
    assert "target_owner_proof" not in receipt
    assert reuse.MODES == frozenset(
        {reuse.MODE, reuse.CACHE_MODE, reuse.INSPECTION_MODE}
    )
    assert set(receipt) == {
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
    for mode in (reuse.CACHE_MODE, reuse.INSPECTION_MODE, "arbitrary", True, None, 1):
        assert not reuse.valid_receipt_mode({**receipt, "mode": mode})


def test_selected_cache_conventional_merge_uses_the_tested_tree_and_final_head(
    tmp_path,
):
    checkout = tmp_path / "checkout"
    target = cache_repository(checkout)
    final_head = target["source_sha"]
    merged = subprocess.check_output(
        [
            "git",
            "commit-tree",
            target["tree"],
            "-p",
            target["before"],
            "-p",
            final_head,
            "-m",
            "owned merge fixture",
        ],
        cwd=checkout,
        text=True,
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", merged], cwd=checkout, check=True
    )
    target.update(source_sha=merged, head_sha=merged)
    provider = cache_provider(tmp_path, checkout, target)
    receipt = reuse.verify(provider, target, root=checkout, now=NOW)
    assert receipt["mode"] == reuse.CACHE_MODE
    assert receipt["donor"]["head_sha"] == final_head != merged
    assert receipt["donor"]["source_sha"] == DONOR["source_sha"] != merged
    assert reuse.recheck(receipt, provider, target, root=checkout, now=NOW) == receipt


@pytest.mark.parametrize("error", [False, True])
def test_selected_validation_scratch_cleans_itself_on_success_and_owner_rejection(
    selected_provider, monkeypatch, tmp_path, error
):
    provider, target, checkout = selected_provider
    monkeypatch.setattr(reuse.tempfile, "tempdir", str(tmp_path))
    observed = []
    original = provider.archive

    def download(artifact_id):
        observed.extend(tmp_path.glob("schemii-acceptance-*"))
        return original(artifact_id)

    provider.archive = download
    if error:
        (checkout / CACHE_SOURCE).write_text("tracked dirty cache after admission")
        with pytest.raises(ValueError, match="cache-checkout-source"):
            reuse.verify(provider, target, root=checkout, now=NOW)
    else:
        reuse.verify(provider, target, root=checkout, now=NOW)
    assert observed and all(not path.exists() for path in observed)


def test_selected_admission_outputs_the_explicit_cache_mode(
    selected_provider, tmp_path, monkeypatch, capsys
):
    provider, target, checkout = selected_provider
    manifest = tmp_path / "classification.json"
    manifest.write_bytes(
        encoded(classification(target["source_sha"], target["before"]))
    )
    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    receipt_path = tmp_path / "admission/reuse.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reuse_acceptance.py",
            "--classification",
            str(manifest),
            "--output",
            str(receipt_path),
        ],
    )
    for name, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_TOKEN": "private-token",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(reuse, "context", lambda *args: target)
    monkeypatch.setattr(reuse, "GitHub", lambda *args: provider)
    original = reuse.verify
    monkeypatch.setattr(
        reuse,
        "verify",
        lambda client, target, *, retained: original(
            client, target, root=checkout, now=NOW, retained=retained
        ),
    )
    assert reuse.main() == 0
    assert output.read_text() == "acceptance=" + reuse.CACHE_MODE + "\n"
    receipt = json.loads(receipt_path.read_text())
    assert (
        reuse.valid_receipt_mode(receipt)
        and len(list((receipt_path.parent / "donor").rglob("*.jsonl"))) == 8
    )
    assert "private" not in capsys.readouterr().out


def test_selected_scope_corruption_cannot_pass_mode_helper_or_gate(selected_provider):
    provider, target, checkout = selected_provider
    receipt = reuse.verify(provider, target, root=checkout, now=NOW)
    lane = next(
        value
        for value in receipt["test_evidence"]["lanes"]
        if value["lane"] == "python"
    )
    lane["scope"]["observed"][0]["source_id"] = "f" * 64
    assert not reuse.valid_receipt_mode(receipt)
    with pytest.raises(ValueError, match="reuse-receipt"):
        reuse.recheck(receipt, provider, target, root=checkout, now=NOW)


def test_closed_donors_have_independent_exact_topology_contracts():
    full, cache = (
        reuse.donor_job_outcomes("full"),
        reuse.donor_job_outcomes(CACHE_PROFILE),
    )
    assert len(full) == 19 and set(full.values()) == {"success"}
    assert len(cache) == 13 and list(cache.values()).count("success") == 11
    assert {name for name, outcome in cache.items() if outcome == "skipped"} == {
        "Incremental Python static quality",
        "Real PostgreSQL metadata behavior",
    }
    assert len(reuse.ARTIFACT_FILES) == 18 and len(expected_lanes("full")) == 15
    assert (
        len(reuse.CACHE_ARTIFACT_FILES) == 11
        and len(expected_lanes(CACHE_PROFILE)) == 8
    )
    assert all(
        "/6)" in name for name in full if name.startswith("Assembled browser smoke (")
    )
    assert all(
        "/3)" in name for name in cache if name.startswith("Assembled browser smoke (")
    )
    assert reuse.DONOR_JOBS == set(full) and reuse.CACHE_DONOR_JOBS == cache
    for profile in ("e2e-tests", "native", "reports", "unknown"):
        with pytest.raises(ValueError, match="unsupported-donor-profile"):
            reuse.donor_job_outcomes(profile)
        with pytest.raises(ValueError, match="unsupported-donor-profile"):
            reuse.artifact_files(profile)


@pytest.mark.parametrize(
    "damage",
    [
        "old-full-three",
        "cache-jobs",
        "wrong-denominator",
        "missing-six",
        "extra-cache-leg",
        "classification-cache",
        "raw-collapsed-six",
    ],
)
def test_full_donor_rejects_old_or_cross_profile_topology(provider, damage):
    if damage == "old-full-three":
        provider.artifacts = [
            value
            for value in provider.artifacts
            if "browser-timing-" not in value["name"]
            or any(f"-shard-{shard}-" in value["name"] for shard in (1, 2, 3))
        ]
        provider.jobs = [
            value
            for value in provider.jobs
            if not value["name"].startswith("Assembled browser smoke (")
            or any(f"shard {shard}/" in value["name"] for shard in (1, 2, 3))
        ]
        for value in provider.jobs:
            value["name"] = value["name"].replace("/6)", "/3)")
    elif damage == "cache-jobs":
        provider.jobs = [
            job(name, outcome=outcome)
            for name, outcome in reuse.CACHE_DONOR_JOBS.items()
        ]
    elif damage in {"wrong-denominator", "extra-cache-leg"}:
        chosen = next(
            value
            for value in provider.jobs
            if value["name"].startswith("Assembled browser smoke (")
        )
        other = {**chosen, "name": chosen["name"].replace("/6)", "/3)")}
        if damage == "wrong-denominator":
            chosen["name"] = other["name"]
        else:
            provider.jobs.append(other)
    elif damage == "missing-six":
        provider.artifacts = [
            value
            for value in provider.artifacts
            if value["name"] != "browser-timing-desktop-chromium-shard-6-attempt-1"
        ]
    elif damage == "classification-cache":
        name = "ci-classification-attempt-1"
        value = json.loads(provider.files[name]["ci-classification.json"])
        value.update(profile=CACHE_PROFILE, reason="verified-owned-pr-change")
        provider.files[name]["ci-classification.json"] = encoded(value)
        provider.set_archive(
            next(value["id"] for value in provider.artifacts if value["name"] == name),
            name,
        )
    else:
        name = "browser-timing-desktop-chromium-shard-6-attempt-1"
        records = [
            json.loads(line)
            for line in provider.files[name]["browser.jsonl"].splitlines()
        ]
        for value in records:
            value["shard"] = 3
        provider.files[name]["browser.jsonl"] = b"\n".join(
            encoded(value) for value in records
        )
        provider.files[name]["browser-summary.json"] = encoded(lane_summary(records))
        provider.set_archive(
            next(value["id"] for value in provider.artifacts if value["name"] == name),
            name,
        )
    with pytest.raises(ValueError):
        reuse.verify(provider, TARGET, now=NOW)


@pytest.mark.parametrize(
    "damage",
    [
        "full-jobs",
        "wrong-denominator",
        "extra-full-leg",
        "raw-four",
        "extra-four-artifact",
        "classification-full",
    ],
)
def test_cache_donor_rejects_full_topology_even_for_overlapping_raw_indices(
    selected_provider, damage
):
    provider, target, checkout = selected_provider
    if damage == "full-jobs":
        provider.jobs = [job(name) for name in reuse.DONOR_JOBS]
    elif damage in {"wrong-denominator", "extra-full-leg"}:
        chosen = next(
            value
            for value in provider.jobs
            if value["name"].startswith("Assembled browser smoke (")
        )
        other = {**chosen, "name": chosen["name"].replace("/3)", "/6)")}
        if damage == "wrong-denominator":
            chosen["name"] = other["name"]
        else:
            provider.jobs.append(other)
    elif damage == "raw-four":
        name = "browser-timing-desktop-chromium-shard-3-attempt-1"
        records = [
            json.loads(line)
            for line in provider.files[name]["browser.jsonl"].splitlines()
        ]
        for value in records:
            value["shard"] = 4
        provider.files[name]["browser.jsonl"] = b"\n".join(
            encoded(value) for value in records
        )
        provider.files[name]["browser-summary.json"] = encoded(lane_summary(records))
        provider.set_archive(
            next(value["id"] for value in provider.artifacts if value["name"] == name),
            name,
        )
    elif damage == "extra-four-artifact":
        name = "browser-timing-desktop-chromium-shard-4-attempt-1"
        provider.files[name] = provider.files[
            "browser-timing-desktop-chromium-shard-3-attempt-1"
        ]
        provider.set_archive(100, name)
    else:
        name = "ci-classification-attempt-1"
        value = json.loads(provider.files[name]["ci-classification.json"])
        value.update(profile="full", reason="source-or-unknown-change")
        provider.files[name]["ci-classification.json"] = encoded(value)
        provider.set_archive(
            next(value["id"] for value in provider.artifacts if value["name"] == name),
            name,
        )
    with pytest.raises(ValueError):
        reuse.verify(provider, target, root=checkout, now=NOW)


def test_cache_donor_requires_current_full_six_main_skip_shape(selected_provider):
    provider, target, checkout = selected_provider
    receipt = reuse.verify(provider, target, root=checkout, now=NOW)
    jobs = [job(name, target) for name in reuse.REPORT_JOB_NAMES]
    jobs += [
        job(
            name,
            target,
            "success" if name == "Incremental Python static quality" else "skipped",
        )
        for name in source_job_names("full")
    ]
    assert len(jobs) == 17 and reuse.current_reuse_jobs(jobs, target)
    identity = {
        key: target[key] for key in ("source_sha", "head_sha", "run_id", "run_attempt")
    }
    needs = {
        name: {"result": "skipped" if name in reuse.EXPENSIVE_NEEDS else "success"}
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }
    needs["classify"]["outputs"] = {"acceptance": reuse.CACHE_MODE}

    def gate_for(values):
        timing = {
            **summarize(
                provider.run,
                values,
                profile="full",
                report_validation=True,
                reused=True,
            ),
            **identity,
            "acceptance_mode": reuse.CACHE_MODE,
            "reuse": receipt,
        }
        assert "test_evidence" not in timing
        return evaluate(
            classification(target["head_sha"], target["before"]),
            needs,
            values,
            timing,
            identity,
            reuse=receipt,
        )[0]

    assert gate_for(jobs)
    for damage in (
        "cache-three",
        "partial",
        "wrong-total",
        "mixed-placeholder",
        "duplicate",
        "successful-six",
    ):
        damaged = copy.deepcopy(jobs)
        browser = next(
            value
            for value in damaged
            if value["name"].startswith("Assembled browser smoke (")
        )
        if damage == "cache-three":
            damaged = [
                value
                for value in damaged
                if not value["name"].startswith("Assembled browser smoke (")
                or any(f"shard {shard}/" in value["name"] for shard in (1, 2, 3))
            ]
            for value in damaged:
                value["name"] = value["name"].replace("/6)", "/3)")
        elif damage == "partial":
            damaged.remove(browser)
        elif damage == "wrong-total":
            browser["name"] = browser["name"].replace("/6)", "/3)")
        elif damage == "mixed-placeholder":
            damaged.append(job(reuse.SKIPPED_BROWSER, target, "skipped"))
        elif damage == "duplicate":
            damaged.append(copy.deepcopy(browser))
        else:
            browser["conclusion"] = "success"
        assert not reuse.current_reuse_jobs(damaged, target)
        assert not gate_for(damaged)
    placeholder = [
        value
        for value in jobs
        if not value["name"].startswith("Assembled browser smoke (")
    ]
    placeholder.append(job(reuse.SKIPPED_BROWSER, target, "skipped"))
    assert reuse.current_reuse_jobs(placeholder, target) and gate_for(placeholder)


@pytest.fixture
def inspection_provider(tmp_path):
    checkout = tmp_path / "checkout"
    target = cache_repository(checkout, paired=True, profile=INSPECTION_PROFILE)
    return (
        cache_provider(
            tmp_path, checkout, target, profile=INSPECTION_PROFILE, helper_extra=True
        ),
        target,
        checkout,
    )


def test_closed_inspection_donor_preserves_four_original_lanes_and_full_main_skip_proof(
    inspection_provider,
):
    provider, target, checkout = inspection_provider
    retained = {}
    receipt = reuse.verify(provider, target, root=checkout, now=NOW, retained=retained)
    assert receipt["mode"] == reuse.INSPECTION_MODE and reuse.valid_receipt_mode(
        receipt
    )
    assert len(receipt["artifacts"]) == 7 and len(receipt["verified_jobs"]) == 9
    assert len(receipt["test_evidence"]["lanes"]) == 4 and len(retained) == 8
    assert sorted(
        value["path"] for value in receipt["target_owner_proof"]["changes"]
    ) == sorted([*INSPECTION_SOURCES, INSPECTION_TEST])
    assert receipt["donor"]["source_sha"] == DONOR["source_sha"] != target["source_sha"]
    python = next(
        value
        for value in receipt["test_evidence"]["lanes"]
        if value["lane"] == "python"
    )
    assert len(python["scope"]["planned"]) == 88
    assert reuse.recheck(receipt, provider, target, root=checkout, now=NOW) == receipt
    jobs = current_inputs(target)
    identity = {
        key: target[key] for key in ("source_sha", "head_sha", "run_id", "run_attempt")
    }
    timing = {
        **summarize(
            provider.run, jobs, profile="full", report_validation=True, reused=True
        ),
        **identity,
        "acceptance_mode": reuse.INSPECTION_MODE,
        "reuse": receipt,
    }
    needs = {
        name: {"result": "skipped" if name in reuse.EXPENSIVE_NEEDS else "success"}
        for name in SOURCE_NEEDS | CONTROL_NEEDS
    }
    needs["classify"]["outputs"] = {"acceptance": reuse.INSPECTION_MODE}
    result = evaluate(
        classification(target["head_sha"], target["before"]),
        needs,
        jobs,
        timing,
        identity,
        reuse=receipt,
    )
    assert result == (
        True,
        "verified-identical-tree-developer-inspection-pr-acceptance",
    )
    assert "test_evidence" not in timing
    for damage in ("owner", "base", "scope", "mode"):
        changed = copy.deepcopy(receipt)
        if damage == "owner":
            changed["target_owner_proof"]["changes"][0]["path"] = CACHE_SOURCE
        elif damage == "base":
            changed["target_owner_proof"]["base"] = "f" * 40
        elif damage == "scope":
            next(
                value
                for value in changed["test_evidence"]["lanes"]
                if value["lane"] == "python"
            )["scope"]["observed"][-1]["source_id"] = "f" * 64
        else:
            changed["mode"] = reuse.CACHE_MODE
        assert not reuse.valid_receipt_mode(changed)
        assert not evaluate(
            classification(target["head_sha"], target["before"]),
            needs,
            jobs,
            {**timing, "reuse": changed},
            identity,
            reuse=changed,
        )[0]


@pytest.mark.parametrize(
    "damage",
    [
        "helper-only",
        "mixed",
        "stale-policy",
        "mode",
        "deleted",
        "renamed",
        "symlink",
        "new",
        "dirty",
        "tip",
    ],
)
def test_inspection_reuse_requires_complete_exact_current_regular_owner_proof(
    inspection_provider, damage
):
    provider, target, checkout = inspection_provider
    source = checkout / sorted(INSPECTION_SOURCES)[0]
    if damage == "helper-only":
        for path in INSPECTION_SOURCES:
            (checkout / path).write_text("original cache fixture\n")
    elif damage == "mixed":
        (checkout / reuse.WORKFLOW_PATH).write_text("different workflow\n")
    elif damage == "stale-policy":
        (checkout / "scripts/ci/inspection-coverage.json").write_text(
            "stale inspection policy\n"
        )
    elif damage == "mode":
        source.chmod(0o755)
    elif damage == "deleted":
        source.unlink()
    elif damage == "renamed":
        source.rename(source.with_name("renamed.py"))
    elif damage == "symlink":
        source.unlink()
        source.symlink_to("foreign")
    elif damage == "new":
        (source.parent / "new.py").write_text("new owner\n")
    elif damage == "dirty":
        source.write_text("dirty current inspection source\n")
    else:
        provider.tip["object"]["sha"] = "f" * 40
    if damage not in {"dirty", "tip"}:
        subprocess.run(["git", "add", "-A"], cwd=checkout, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "damaged owner fixture"],
            cwd=checkout,
            check=True,
        )
        target.update(
            source_sha=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
            ).strip(),
            tree=subprocess.check_output(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=checkout, text=True
            ).strip(),
        )
        with pytest.raises(ValueError):
            reuse.target_owner_proof(target, checkout, profile=INSPECTION_PROFILE)
    else:
        with pytest.raises(ValueError):
            reuse.verify(provider, target, root=checkout, now=NOW)


@pytest.mark.parametrize(
    "damage",
    [
        "minimum",
        "foreign-extra",
        "skip",
        "retry",
        "missing-plan",
        "missing-attempt",
        "filtered",
        "browser-extra",
        "browser-skip",
        "jobs-cache",
        "jobs-full",
        "wrong-denominator",
        "postgres-executed",
        "missing-artifact",
        "extra-artifact",
        "classification",
        "comparison-base",
    ],
)
def test_inspection_donor_rejects_scope_topology_inventory_and_base_corruption(
    inspection_provider, damage
):
    provider, target, checkout = inspection_provider
    name = "python-timing-attempt-1"
    if damage.startswith("jobs-"):
        profile = CACHE_PROFILE if damage == "jobs-cache" else "full"
        provider.jobs = [
            job(key, DONOR, outcome)
            for key, outcome in reuse.donor_job_outcomes(profile).items()
        ]
    elif damage == "wrong-denominator":
        next(
            value
            for value in provider.jobs
            if value["name"].startswith("Assembled browser smoke")
        )["name"] = "Assembled browser smoke (desktop-chromium, shard 1/3)"
    elif damage == "postgres-executed":
        next(
            value
            for value in provider.jobs
            if value["name"] == "Real PostgreSQL metadata behavior"
        )["conclusion"] = "success"
    elif damage == "missing-artifact":
        provider.artifacts.pop()
    elif damage == "extra-artifact":
        provider.files["postgres-timing-attempt-1"] = {
            "postgres.jsonl": b"{}",
            "postgres-summary.json": b"{}",
        }
        provider.set_archive(100, "postgres-timing-attempt-1")
    elif damage in {"classification", "comparison-base"}:
        name = "ci-classification-attempt-1"
        value = json.loads(provider.files[name]["ci-classification.json"])
        value["profile" if damage == "classification" else "comparison_base"] = (
            CACHE_PROFILE if damage == "classification" else "f" * 40
        )
        provider.files[name]["ci-classification.json"] = encoded(value)
    else:
        if damage.startswith("browser-"):
            name = "browser-timing-desktop-chromium-shard-1-attempt-1"
        lane = "browser" if name.startswith("browser") else "python"
        records = [
            json.loads(line)
            for line in provider.files[name][lane + ".jsonl"].splitlines()
        ]
        attempts = [value for value in records if value["kind"] == "attempt"]
        chosen = (
            next(
                value
                for value in attempts
                if value["test_id"]
                == hashlib.sha256(b"new unfiltered helper case").hexdigest()
            )
            if lane == "python"
            else attempts[0]
        )
        if damage in {"minimum", "filtered"}:
            chosen = next(
                value for value in attempts if value["test_id"] != chosen["test_id"]
            )
            records = [
                value for value in records if value.get("test_id") != chosen["test_id"]
            ]
            records[0]["planned"] -= 1
        elif damage == "foreign-extra":
            chosen["source_id"] = hashlib.sha256(
                b"tests/test_route_inspection.py"
            ).hexdigest()
        elif damage in {"skip", "browser-skip"}:
            chosen.update(outcome="skipped", skip="declared-or-runtime")
        elif damage == "retry":
            chosen["attempt"] = 1
        elif damage == "missing-plan":
            records = [
                value
                for value in records
                if not (
                    value["kind"] == "plan" and value["test_id"] == chosen["test_id"]
                )
            ]
        elif damage == "missing-attempt":
            records.remove(chosen)
        else:
            extra = copy.deepcopy(chosen)
            extra["test_id"] = "f" * 64
            records.insert(
                -1,
                {
                    **{
                        key: records[0][key]
                        for key in (
                            "schema",
                            "source_sha",
                            "run_id",
                            "run_attempt",
                            "lane",
                            "project",
                            "shard",
                        )
                    },
                    "kind": "plan",
                    "test_id": extra["test_id"],
                },
            )
            records.insert(-1, extra)
            records[0]["planned"] += 1
        provider.files[name][lane + ".jsonl"] = b"\n".join(
            encoded(value) for value in records
        )
    if damage not in {
        "jobs-cache",
        "jobs-full",
        "wrong-denominator",
        "postgres-executed",
        "missing-artifact",
        "extra-artifact",
    }:
        artifact = next(value for value in provider.artifacts if value["name"] == name)
        provider.set_archive(artifact["id"], name)
    with pytest.raises(ValueError):
        reuse.verify(provider, target, root=checkout, now=NOW)


@pytest.mark.parametrize(
    "damage", ["owner-proof", "policy", "provider-base", "attempt", "raw"]
)
def test_inspection_gate_repeats_live_provenance_and_owner_checks(
    inspection_provider, damage
):
    provider, target, checkout = inspection_provider
    receipt = reuse.verify(provider, target, root=checkout, now=NOW)
    if damage == "owner-proof":
        receipt["target_owner_proof"]["policy_blobs"][reuse.WORKFLOW_PATH] = "f" * 40
    elif damage == "policy":
        (checkout / "scripts/ci/inspection-coverage.json").write_text(
            "changed current policy\n"
        )
    elif damage == "provider-base":
        provider.pr["base"]["sha"] = "f" * 40
    elif damage == "attempt":
        provider.run["run_attempt"] = 2
    else:
        provider.bytes[provider.artifacts[0]["id"]] = b"corrupt bytes"
    with pytest.raises(ValueError):
        reuse.recheck(receipt, provider, target, root=checkout, now=NOW)


@pytest.mark.parametrize("error", [False, True])
def test_inspection_validation_scratch_cleans_itself_on_success_and_rejection(
    inspection_provider, monkeypatch, tmp_path, error
):
    provider, target, checkout = inspection_provider
    monkeypatch.setattr(reuse.tempfile, "tempdir", str(tmp_path))
    observed = []
    original = provider.archive

    def archive(identity):
        observed.extend(tmp_path.glob("schemii-acceptance-*"))
        return original(identity)

    provider.archive = archive
    if error:
        (checkout / sorted(INSPECTION_SOURCES)[0]).write_text("dirty source\n")
        with pytest.raises(ValueError):
            reuse.verify(provider, target, root=checkout, now=NOW)
    else:
        reuse.verify(provider, target, root=checkout, now=NOW)
    assert observed and all(not path.exists() for path in observed)
