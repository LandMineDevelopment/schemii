"""Selected checks must retain ownership closure and fail closed on unknown changes."""

import importlib.util
import json
import os
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
def test_local_plan_includes_actual_uncommitted_state_and_unknown_work_uses_full(
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
    assert selected["classification"]["profile"] == (
        "full" if status == "untracked" else "frontend-tests"
    )
    assert str(path.relative_to(root)) in selected["changed_paths"]
    assert selected["local_status"] and path.name in " ".join(selected["local_status"])


@pytest.mark.parametrize("profile", sorted(PATHS))
@pytest.mark.parametrize("status", ["unstaged", "staged", "both"])
def test_local_owned_modifications_retain_complete_closure(repository, profile, status):
    root, base = repository
    path = sorted(PATHS[profile])[0]
    (root / path).write_text("staged content\n")
    if status != "unstaged":
        git(root, "add", path)
    if status == "both":
        (root / path).write_text("unstaged content\n")
    selected = local_selection.plan(root, base)
    assert selected["classification"]["valid"]
    assert selected["classification"]["profile"] == profile
    assert selected["classification"]["scope"] == "local-worktree"
    assert selected["changed_paths"] == [path]
    assert selected["commands"] == commands(profile, base)
    assert selected["feedback"]["acceptance"] is False
    assert selected["feedback"]["pending_layers"] == sorted(
        layers(profile) & {"postgres", "browser"}
    )
    manifest = root / "local-classification.json"
    manifest.write_text(json.dumps(selected["classification"]))
    with pytest.raises(ValueError, match="classification"):
        load_classification(manifest)


def test_local_staged_reversal_retains_complete_ownership_union(repository):
    root, base = repository
    frontend = sorted(PATHS["frontend-tests"])[0]
    native = sorted(PATHS["native"])[0]
    (root / frontend).write_text("committed frontend\n")
    commit(root)
    (root / native).write_text("staged native\n")
    git(root, "add", native)
    (root / native).write_text("original\n")
    # The net base-to-worktree diff hides native; the index remains part of the plan.
    assert git(root, "diff", "--name-only", base) == frontend
    selected = local_selection.plan(root, base)
    assert selected["classification"]["profile"] == "full"
    assert selected["changed_paths"] == sorted([frontend, native])


def test_local_same_owner_staged_reversal_remains_owned(repository):
    root, base = repository
    path = sorted(PATHS["frontend-tests"])[0]
    (root / path).write_text("changed\n")
    git(root, "add", path)
    (root / path).write_text("original\n")
    assert not git(root, "diff", "--name-only", base)
    selected = local_selection.plan(root, base)
    assert selected["classification"]["profile"] == "frontend-tests"
    assert selected["changed_paths"] == [path]


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_local_hidden_shared_index_edits_cannot_select_narrow_feedback(
    repository, flag
):
    root, _ = repository
    shared = "tests/conftest.py"
    (root / shared).write_text("original shared\n")
    base = commit(root)
    git(root, "update-index", flag, shared)
    (root / shared).write_text("hidden shared modification\n")
    name = sorted(PATHS["frontend-tests"])[0]
    (root / name).write_text("frontend modification\n")
    assert git(root, "diff", "--name-only", base) == name
    selected = local_selection.plan(root, base)
    assert selected["classification"]["profile"] == "full"
    assert selected["classification"]["reason"] == "unsupported-local-index"
    assert selected["changed_paths"] == [name]
    assert selected["unverified_paths"] == [shared]


@pytest.mark.parametrize(
    "change",
    [
        "new",
        "delete",
        "rename",
        "mode",
        "staged-mode-reversal",
        "ignored-mode",
        "symlink",
        "staged-symlink-reversal",
        "mixed",
        "shared",
        "conflict",
    ],
)
def test_local_unsafe_git_states_use_full_and_preserve_names(repository, change):
    root, base = repository
    name = sorted(PATHS["native"])[0]
    path = root / name
    expected = {name}
    if change == "new":
        path = root / sorted(PATHS["native"])[1]
        expected = {path.relative_to(root).as_posix()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("new frozen sibling\n")
        git(root, "add", str(path))
    elif change == "delete":
        path.unlink()
    elif change == "rename":
        target = path.with_name("renamed-owner")
        git(root, "mv", str(path), str(target))
        expected.add(target.relative_to(root).as_posix())
    elif change in {"mode", "staged-mode-reversal", "ignored-mode"}:
        if change == "ignored-mode":
            git(root, "config", "core.fileMode", "false")
            path.write_text("content ensures the physical mode is checked\n")
        path.chmod(0o755)
        if change == "staged-mode-reversal":
            git(root, "add", name)
            path.chmod(0o644)
    elif change in {"symlink", "staged-symlink-reversal"}:
        path.unlink()
        path.symlink_to("unowned-target")
        if change == "staged-symlink-reversal":
            git(root, "add", name)
            path.unlink()
            path.write_text("original\n")
    elif change in {"mixed", "shared"}:
        other = (
            sorted(PATHS["frontend-tests"])[0]
            if change == "mixed"
            else "tests/conftest.py"
        )
        if change == "shared":
            (root / other).write_text("original shared\n")
            base = commit(root)
        path.write_text("native modification\n")
        (root / other).write_text("other owner\n")
        expected.add(other)
    else:
        blob = git(root, "rev-parse", f"HEAD:{name}")
        subprocess.run(
            ["git", "update-index", "--index-info"],
            cwd=root,
            check=True,
            text=True,
            input=f"0 {'0' * 40}\t{name}\n"
            + "".join(f"100644 {blob} {stage}\t{name}\n" for stage in (1, 2, 3)),
        )
    selected = local_selection.plan(root, base)
    assert selected["classification"]["valid"]
    assert selected["classification"]["profile"] == "full"
    assert set(selected["changed_paths"]) == expected


def test_local_existing_executable_with_stable_mode_remains_owned(repository):
    root, base = repository
    name = sorted(PATHS["harness"])[0]
    path = root / name
    path.chmod(0o755)
    base = commit(root)
    path.write_text("modified executable\n")
    assert local_selection.plan(root, base)["classification"]["profile"] == "harness"


def test_local_bad_comparison_cannot_run_feedback(repository):
    root, _ = repository
    selected = local_selection.plan(root, "nonexistent-base")
    assert not selected["classification"]["valid"]
    assert selected["classification"]["profile"] == "full"
    assert local_selection.execute(root, selected, feedback=True) == 2


def test_local_feedback_cli_runs_owned_node_and_keeps_browser_pending(repository):
    root, base = repository
    name = sorted(PATHS["e2e-tests"])[0]
    (root / name).write_text("changed browser check\n")
    # An actual executable proves the CLI runs npm test and never reaches npm ci.
    bin_directory = root.with_name(root.name + "-bin")
    bin_directory.mkdir()
    npm = bin_directory / "npm"
    npm.write_text(
        f"#!{sys.executable}\nimport json,pathlib,sys\n"
        "assert sys.argv[1:] == ['test']\n"
        "pathlib.Path('executed.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    npm.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(bin_directory) + os.pathsep + os.environ["PATH"],
    }
    for key in (
        "SCHEMII_E2E_BOOTSTRAP",
        "SCHEMII_E2E_CREDENTIALS_FILE",
        "SCHEMII_E2E_USERNAME",
        "SCHEMII_E2E_PASSWORD",
    ):
        environment.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/test-changes.py"),
            "--base",
            base,
            "--feedback",
        ],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((root / "executed.json").read_text()) == ["test"]
    assert "full acceptance is not established" in result.stdout
    assert "Required pending acceptance layers: browser" in result.stdout


def test_local_clean_report_execution_retains_hosted_report_proof(repository):
    root, base = repository
    report = root / "docs/audits/2026-10-02-owned-report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Owned report\n")
    commit(root)
    selected = local_selection.plan(root, base)
    assert selected["classification"]["profile"] == "reports"
    selected["commands"] = [
        [
            sys.executable,
            "-c",
            "import json,pathlib; assert json.loads(pathlib.Path('.schemii/test-selection/classification.json').read_text())['schema'] == 2",
        ]
    ]
    assert local_selection.execute(root, selected) == 0
    assert (
        load_classification(root / ".schemii/test-selection/classification.json")[
            "profile"
        ]
        == "reports"
    )
    report.write_text("# Dirty report\n")
    assert local_selection.plan(root, base)["classification"]["profile"] == "full"


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
def test_real_layer_prerequisites_allow_earlier_feedback_but_block_acceptance(
    tmp_path, monkeypatch, capsys, profile
):
    for name in (
        "SCHEMII_TEST_METADATA_DSN",
        "SCHEMII_E2E_BOOTSTRAP",
        "SCHEMII_E2E_CREDENTIALS_FILE",
        "SCHEMII_E2E_USERNAME",
        "SCHEMII_E2E_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    sentinel = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('cheap-check').write_text('passed')",
    ]
    real = (
        [
            local_selection.POSTGRES_COMMAND,
            local_selection.BROWSER_BOUNDARY,
            ["./start.sh"],
        ]
        if profile == "full"
        else [local_selection.BROWSER_BOUNDARY, ["./start.sh"]]
    )
    selected = {
        "classification": {"valid": True, "profile": profile},
        "layers": sorted(layers(profile)),
        "commands": [sentinel, *real],
    }
    assert local_selection.execute(tmp_path, selected) == 2
    assert (tmp_path / "cheap-check").read_text() == "passed"
    assert "acceptance pending" in capsys.readouterr().err
    assert local_selection.execute(tmp_path, selected, feedback=True) == 0
    output = capsys.readouterr().out
    assert "full acceptance is not established" in output
    assert "browser" in output
    assert ("postgres" in output) == (profile == "full")


def test_local_failed_cheap_check_stops_before_real_prerequisites(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("SCHEMII_TEST_METADATA_DSN", raising=False)
    selected = {
        "classification": {"valid": True, "profile": "full"},
        "layers": sorted(layers("full")),
        "commands": [
            [sys.executable, "-c", "raise SystemExit(7)"],
            local_selection.POSTGRES_COMMAND,
        ],
    }
    assert local_selection.execute(tmp_path, selected) == 7
    assert local_selection.execute(tmp_path, selected, feedback=True) == 7


def test_local_configured_pg_runs_before_missing_browser_boundary(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("SCHEMII_TEST_METADATA_DSN", "owned-test-dsn")
    for name in (
        "SCHEMII_E2E_BOOTSTRAP",
        "SCHEMII_E2E_CREDENTIALS_FILE",
        "SCHEMII_E2E_USERNAME",
        "SCHEMII_E2E_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    observed = []

    def run(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(local_selection.subprocess, "run", run)
    selected = {
        "classification": {"valid": True, "profile": "full"},
        "layers": sorted(layers("full")),
        "commands": [
            ["npm", "test"],
            local_selection.POSTGRES_COMMAND,
            local_selection.BROWSER_BOUNDARY,
            ["./start.sh"],
        ],
    }
    assert local_selection.execute(tmp_path, selected) == 2
    assert observed == [["npm", "test"], local_selection.POSTGRES_COMMAND]
    assert "Browser acceptance pending" in capsys.readouterr().err


@pytest.mark.parametrize("profile", sorted(PROFILES - {"reports"}))
def test_local_feedback_retains_every_selected_deterministic_command(profile):
    selected = commands(profile, "origin/main")
    feedback = local_selection.feedback_commands(selected)
    assert ["npm", "test"] in feedback
    if "python" in layers(profile):
        assert [
            "python",
            "scripts/ci/python-tests.py",
            *PYTHON_PATHS.get(profile, ()),
        ] in feedback
    assert all(argv not in feedback for argv in selected if argv[0] == "./start.sh")
    assert local_selection.POSTGRES_COMMAND not in feedback
    assert local_selection.BROWSER_BOUNDARY not in feedback
