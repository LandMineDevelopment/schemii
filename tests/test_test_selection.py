"""Selected checks must retain ownership closure and fail closed on unknown changes."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci.classify_changes import classify, load_classification
from scripts.ci.required_gate import CONTROL_NEEDS, evaluate
from scripts.ci.test_selection import (
    JOB_NAMES,
    PATHS,
    PROFILES,
    SOURCE_NEEDS,
    PYTHON_PATHS,
    commands,
    expected_jobs,
    expected_lanes,
    layers,
    required_needs,
    select_paths,
)
from scripts.ci.workflow_timing import summarize, test_evidence as collect_evidence

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"source_sha": "a" * 40, "head_sha": "b" * 40, "run_id": 1, "run_attempt": 1}
spec = importlib.util.spec_from_file_location(
    "local_selection", ROOT / "scripts/test-changes.py"
)
local_selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_selection)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "owned fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "Owned fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    for profile, paths in PATHS.items():
        path = tmp_path / sorted(paths)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    return tmp_path, commit(tmp_path)


@pytest.mark.parametrize("profile", sorted(PATHS))
def test_existing_owned_modification_selects_only_for_pr(repository, profile):
    root, base = repository
    path = sorted(PATHS[profile])[0]
    (root / path).write_text("changed\n")
    head = commit(root)
    result = classify(root, "pull_request", base, head)
    assert (
        result["profile"] == profile and result["reason"] == "verified-owned-pr-change"
    )
    assert classify(root, "push", base, head)["profile"] == "full"
    assert classify(root, "workflow_dispatch", base, head)["profile"] == "full"
    assert select_paths([("M", [path]) for path in PATHS[profile]]) == profile


def test_frozen_paths_are_existing_disjoint_leaves_with_no_policy_or_shared_helpers():
    all_paths = [path for paths in PATHS.values() for path in paths]
    assert len(all_paths) == len(set(all_paths))
    assert all((ROOT / path).is_file() for path in all_paths)
    for path in (
        "tests/conftest.py",
        "tests/inspection_fixtures.py",
        "tests/test_ci_timing.py",
        "tests/test_test_selection.py",
        "testing/launcher.sh",
        "testing/compose.yaml",
        "tests/e2e/global-setup.js",
        "src/schemii/schemoo/web/model.js",
        "package.json",
    ):
        assert select_paths([("M", [path])]) == "full"
    assert PYTHON_PATHS["harness"] == PYTHON_PATHS["load"]
    assert "tests/test_load_planner.py" in PYTHON_PATHS["harness"]
    assert "python" not in layers("frontend-tests")
    assert "python" not in layers("e2e-tests")
    assert len(expected_lanes("full")) == 9
    assert len(expected_lanes("e2e-tests")) == 7


@pytest.mark.parametrize(
    "change", ["new", "delete", "rename", "mode", "symlink", "mixed", "unknown"]
)
def test_unsafe_status_or_mixed_owners_uses_full(repository, change):
    root, base = repository
    path = root / sorted(PATHS["native"])[0]
    if change == "delete":
        path.unlink()
    elif change == "rename":
        path.rename(path.with_name("new-owner.py"))
    elif change == "mode":
        path.chmod(0o755)
    elif change == "symlink":
        path.unlink()
        path.symlink_to("unknown")
    else:
        path.write_text("modified\n")
        other = root / (
            sorted(PATHS["frontend-tests"])[0]
            if change == "mixed"
            else "testing/agents/new-owner.py"
        )
        if change in {"new", "unknown", "mixed"}:
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_text("unknown\n")
    assert classify(root, "pull_request", base, commit(root))["profile"] == "full"


@pytest.mark.parametrize("status", ["dirty", "staged", "untracked"])
def test_local_plan_includes_actual_uncommitted_state_and_falls_back_full(
    repository, status
):
    root, base = repository
    path = root / sorted(PATHS["frontend-tests"])[0]
    path.write_text("changed\n")
    commit(root)
    assert (
        local_selection.plan(root, base)["classification"]["profile"]
        == "frontend-tests"
    )
    if status == "untracked":
        path = root / "untracked-test.py"
    path.write_text("local content\n")
    if status == "staged":
        git(root, "add", str(path))
    selected = local_selection.plan(root, base)
    assert selected["classification"]["profile"] == "full"
    assert selected["local_status"] and path.name in " ".join(selected["local_status"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": 1},
        {"base": 42},
        {"profile": "invented"},
        {"profile": "reports"},
        {"reason": "full-validation"},
        {"valid": False},
    ],
)
def test_selection_manifest_rejects_stale_unknown_or_unproven_profile(
    tmp_path, mutation
):
    value = {
        "schema": 2,
        "lane": "source",
        "profile": "native",
        "valid": True,
        "reason": "verified-owned-pr-change",
        "base": "a" * 40,
        "head": "b" * 40,
        "comparison_base": "a" * 40,
        "markdown": [],
        **mutation,
    }
    path = tmp_path / "classification.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="classification"):
        load_classification(path)


def raw_lane(key, outcome="passed"):
    lane, project, shard = key
    meta = {
        "schema": 1,
        **{key: IDENTITY[key] for key in ("source_sha", "run_id", "run_attempt")},
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
            "outcome": outcome,
            "skip": "none",
            "setup_ms": 1,
            "execution_ms": 2,
            "teardown_ms": 1,
        },
        {**meta, "kind": "end", "outcome": outcome, "wall_ms": 5},
    ]


def selected_evidence(root, profile):
    root.mkdir(exist_ok=True)
    for lane, project, shard in expected_lanes(profile):
        (root / f"{lane}-{project}-{shard}.jsonl").write_text(
            "\n".join(json.dumps(value) for value in raw_lane((lane, project, shard)))
        )
    return collect_evidence(root, profile=profile, identity=IDENTITY)


def job(name, outcome="success"):
    return {
        "name": name,
        "status": "completed",
        "conclusion": outcome,
        "started_at": "2026-09-30T00:00:02Z",
        "completed_at": "2026-09-30T00:00:10Z",
        **{key: IDENTITY[key] for key in ("head_sha", "run_id", "run_attempt")},
    }


def selected_gate(root, profile):
    classification = {
        "valid": True,
        "lane": "source",
        "profile": profile,
        "reason": "verified-owned-pr-change",
        "head": IDENTITY["head_sha"],
    }
    needs = {
        **{key: {"result": "success"} for key in CONTROL_NEEDS},
        **{
            key: {"result": "success" if key in required_needs(profile) else "skipped"}
            for key in SOURCE_NEEDS
        },
    }
    jobs = [
        job(name, "success" if name in expected_jobs(profile) else "skipped")
        for name in JOB_NAMES
    ]
    timing = {
        **summarize({"created_at": "2026-09-30T00:00:00Z"}, jobs, profile=profile),
        **IDENTITY,
        "test_evidence": selected_evidence(root, profile),
    }
    return classification, needs, jobs, timing, IDENTITY


@pytest.mark.parametrize("profile", sorted(PROFILES - {"reports", "full"}))
def test_selected_gate_requires_all_and_only_owned_layers(tmp_path, profile):
    args = selected_gate(tmp_path, profile)
    assert evaluate(*args)[0]
    classification, needs, jobs, timing, identity = args
    assert timing["not_applicable_jobs"] == sorted(
        set(JOB_NAMES.values()) - set(expected_jobs(profile).values())
    )
    for name in required_needs(profile):
        damaged = {**needs, name: {"result": "skipped"}}
        assert not evaluate(classification, damaged, jobs, timing, identity)[0]
    for name in SOURCE_NEEDS - required_needs(profile):
        damaged = {**needs, name: {"result": "success"}}
        assert not evaluate(classification, damaged, jobs, timing, identity)[0]
    for name in expected_jobs(profile):
        missing = [value for value in jobs if value["name"] != name]
        assert not evaluate(classification, needs, missing, timing, identity)[0]
    assert not evaluate(
        classification,
        needs,
        jobs + [job(next(iter(expected_jobs(profile))))],
        timing,
        identity,
    )[0]


@pytest.mark.parametrize(
    "damage",
    ["missing", "failed", "duplicate", "stale", "extra", "recovered", "profile"],
)
def test_browser_selected_receipts_cannot_hide_missing_third_shard_or_failed_attempt(
    tmp_path, damage
):
    classification, needs, jobs, timing, identity = selected_gate(tmp_path, "e2e-tests")
    evidence = timing["test_evidence"]
    receipt = next(value for value in evidence["lanes"] if value["shard"] == 3)
    if damage == "missing":
        evidence["lanes"].remove(receipt)
    elif damage == "duplicate":
        evidence["lanes"].append(dict(receipt))
    elif damage == "extra":
        evidence["lanes"].append(
            {**receipt, "lane": "python", "project": "none", "shard": 0}
        )
    elif damage == "stale":
        receipt["run_attempt"] = 2
    elif damage == "failed":
        receipt["outcome"] = "failed"
    elif damage == "recovered":
        receipt["first_attempt_failures"] = receipt["retry_recovered"] = 1
    else:
        timing["profile"] = "full"
    assert not evaluate(classification, needs, jobs, timing, identity)[0]


def test_receipt_collection_rejects_failed_complete_and_unselected_lanes(tmp_path):
    selected_evidence(tmp_path, "frontend-tests")
    node = tmp_path / "node-none-0.jsonl"
    node.write_text(
        "\n".join(
            json.dumps(value) for value in raw_lane(("node", "none", 0), "failed")
        )
    )
    assert (
        collect_evidence(tmp_path, profile="frontend-tests", identity=IDENTITY)[
            "complete"
        ]
        is False
    )
    node.write_text(
        "\n".join(json.dumps(value) for value in raw_lane(("node", "none", 0)))
    )
    (tmp_path / "extra.jsonl").write_text(
        "\n".join(json.dumps(value) for value in raw_lane(("python", "none", 0)))
    )
    assert (
        collect_evidence(tmp_path, profile="frontend-tests", identity=IDENTITY)[
            "invalid_lanes"
        ]
        == 1
    )


def test_local_execution_runs_required_sentinels_and_stops_on_actual_failure(tmp_path):
    # Execute actual argv rather than claiming selection from YAML string mentions.
    runner = tmp_path / "scripts/ci/python-tests.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "import pathlib,sys,subprocess\npathlib.Path('invocation.json').write_text(__import__('json').dumps(sys.argv[1:]))\nraise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q',*sys.argv[1:]]))\n"
    )
    for family in ("testing/agents", "testing/harness", "tests"):
        directory = tmp_path / family
        directory.mkdir(parents=True, exist_ok=True)
        (
            directory
            / (
                "test_load_planner.py"
                if family == "tests"
                else "test_" + directory.name + ".py"
            )
        ).write_text("def test_owned():\n    assert True\n")
    excluded = tmp_path / "tests/test_product.py"
    excluded.write_text(
        "def test_excluded():\n    raise AssertionError('excluded product sentinel')\n"
    )
    argv = next(
        argv
        for argv in commands("harness", "HEAD")
        if "scripts/ci/python-tests.py" in argv
    )
    argv[0] = sys.executable
    selected = {
        "classification": {"valid": True, "profile": "harness"},
        "layers": ["python"],
        "commands": [argv],
    }
    assert local_selection.execute(tmp_path, selected) == 0
    assert json.loads((tmp_path / "invocation.json").read_text()) == list(
        PYTHON_PATHS["harness"]
    )
    (tmp_path / "testing/harness/test_harness.py").write_text(
        "def test_owned():\n    assert False, 'planted selected failure'\n"
    )
    assert local_selection.execute(tmp_path, selected) == 1


def test_full_local_plan_preserves_launcher_pg_and_all_six_browser_commands():
    argv = commands("full", "origin/main")
    assert ["python", "scripts/ci/python-tests.py"] in argv
    assert ["./start.sh"] in argv
    browser = [value for value in argv if "scripts/ci/run-browser-shard.mjs" in value]
    assert len(browser) == 6 and len({tuple(value) for value in browser}) == 6
    assert any("tests/integration" in value for value in argv)
    assert all("docker" not in value and "sudo" not in value for value in argv)


@pytest.mark.parametrize(
    "damage",
    [
        None,
        1,
        "receipt",
        [1],
        {"complete": True, "profile": "e2e-tests", "lanes": "wrong"},
    ],
)
def test_malformed_receipt_values_fail_closed(tmp_path, damage):
    classification, needs, jobs, timing, identity = selected_gate(tmp_path, "e2e-tests")
    timing["test_evidence"] = damage
    assert not evaluate(classification, needs, jobs, timing, identity)[0]


def test_legacy_browser_job_cannot_join_a_complete_current_matrix(tmp_path):
    classification, needs, jobs, timing, identity = selected_gate(tmp_path, "e2e-tests")
    jobs.append(job("Assembled browser smoke (desktop-chromium, shard 1/2)"))
    assert not evaluate(classification, needs, jobs, timing, identity)[0]


def test_boolean_shard_or_failure_count_is_not_integer_evidence(tmp_path):
    classification, needs, jobs, timing, identity = selected_gate(tmp_path, "e2e-tests")
    receipt = timing["test_evidence"]["lanes"][0]
    receipt["first_attempt_failures"] = False
    assert not evaluate(classification, needs, jobs, timing, identity)[0]


@pytest.mark.parametrize("profile", ["full", "e2e-tests"])
def test_real_layer_prerequisites_block_execution_instead_of_claiming_acceptance(
    tmp_path, monkeypatch, profile
):
    for name in (
        "SCHEMII_TEST_METADATA_DSN",
        "SCHEMII_E2E_BOOTSTRAP",
        "SCHEMII_E2E_CREDENTIALS_FILE",
        "SCHEMII_E2E_USERNAME",
        "SCHEMII_E2E_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    selected = {
        "classification": {"valid": True, "profile": profile},
        "layers": sorted(layers(profile)),
        "commands": [["must-not-execute"]],
    }
    assert local_selection.execute(tmp_path, selected) == 2
