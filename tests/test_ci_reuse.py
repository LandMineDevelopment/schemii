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
    SOURCE_NEEDS,
    expected_lanes,
    expected_jobs,
    required_needs,
    JOB_NAMES,
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
                "id": DONOR["run_id"],
                "head_sha": DONOR["head_sha"],
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
        len(receipt["artifacts"]) == 12 and len(receipt["test_evidence"]["lanes"]) == 9
    )
    assert len(retained) == 18 and all("dependencies" not in name for name in retained)
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


def current_inputs():
    return [
        job(
            name,
            TARGET,
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
    assert len(reuse.DONOR_JOBS) == 13 and len(reuse.ARTIFACT_FILES) == 12


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
        for name in JOB_NAMES
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
            placeholder["name"] = reuse.SKIPPED_BROWSER.replace("/3)", "/2)")
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
