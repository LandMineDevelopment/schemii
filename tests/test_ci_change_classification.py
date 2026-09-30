"""A report shortcut must never hide a source change or absent required check."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci.classify_changes import classify, load_classification, readme_index_only
from scripts.ci.required_gate import CONTROL_NEEDS, SOURCE_NEEDS, evaluate
from scripts.ci.workflow_timing import (
    JOB_NAMES,
    REPORT_JOB_NAMES,
    summarize,
    test_evidence as collect_evidence,
)


REPORT = "docs/audits/2026-09-30-feedback-results.md"
ROOT = Path(__file__).resolve().parents[1]


def command(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(root):
    command(root, "add", "--all")
    command(root, "commit", "--quiet", "-m", "owned classifier regression")
    return command(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    command(tmp_path, "init", "--quiet", "--initial-branch=main")
    command(tmp_path, "config", "user.name", "CI classifier test")
    command(tmp_path, "config", "user.email", "ci-classifier@example.invalid")
    write(tmp_path, "README.md", "# Product\n\nOriginal contract.\n")
    write(tmp_path, REPORT, "# Feedback results\n\n[Product](../../README.md)\n")
    write(tmp_path, "src/app.py", "# source invariant\n")
    return tmp_path, commit(tmp_path)


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize(
    "path",
    [REPORT, "docs/audits/2026-09-30-new-report.md", "docs/browser-shard-balance.md"],
)
def test_selected_new_or_modified_reports_select_dependency_free_lane(
    repository, event, path
):
    root, base = repository
    write(root, path, "# Measured report\n\nUpdated evidence.\n")
    result = classify(root, event, base, commit(root))
    assert result["valid"] and result["lane"] == "reports"
    assert result["reason"] == "verified-report-only" and result["markdown"] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "testing/agents/README.md",
        "testing/fixtures/results.md",
        "src/app.py",
        "migrations/001.sql",
        "pyproject.toml",
        "package-lock.json",
        "dev/schemii.toml",
        "start.sh",
        ".github/workflows/ci.yml",
        "new-path.txt",
        "docs/native-qa-acceptance.md",
        "docs/testing-feedback.md",
        "docs/audits/README.md",
        "docs/audits/nested/2026-09-30-report.md",
    ],
)
def test_unknown_source_contract_fixture_dependency_and_config_paths_select_full(
    repository, path
):
    root, base = repository
    write(root, path, "# Changed contract or source\n")
    result = classify(root, "push", base, commit(root))
    assert result["valid"] and result["lane"] == "source"


def test_report_and_source_in_same_change_select_full(repository):
    root, base = repository
    write(root, REPORT, "# New report\n")
    write(root, "src/app.py", "# broken invariant\n")
    assert classify(root, "pull_request", base, commit(root))["lane"] == "source"


@pytest.mark.parametrize("old", ["src/app.py", REPORT])
def test_renames_include_old_source_or_report_path_and_always_select_full(
    repository, old
):
    root, base = repository
    new = "docs/audits/2026-09-30-renamed-report.md"
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    command(root, "mv", old, new)
    assert classify(root, "push", base, commit(root))["lane"] == "source"


@pytest.mark.parametrize("path", [REPORT, "src/app.py", "README.md"])
def test_deletion_cannot_enter_report_shortcut(repository, path):
    root, base = repository
    (root / path).unlink()
    assert classify(root, "push", base, commit(root))["lane"] == "source"


@pytest.mark.parametrize("kind", ["executable", "symlink", "mode-change"])
def test_markdown_extension_does_not_hide_executable_or_symlink_change(
    repository, kind
):
    root, base = repository
    path = root / (
        REPORT if kind == "mode-change" else "docs/audits/2026-09-30-program.md"
    )
    if kind == "symlink":
        path.symlink_to("../../src/app.py")
    else:
        path.write_text("# Executable report\n")
        path.chmod(0o755)
    assert classify(root, "push", base, commit(root))["lane"] == "source"


def test_main_push_compares_all_pushed_commits_not_just_last_parent(repository):
    root, base = repository
    write(root, "src/app.py", "# earlier source defect\n")
    commit(root)
    write(root, REPORT, "# Last commit only touches report\n")
    result = classify(root, "push", base, commit(root))
    assert result["lane"] == "source" and result["comparison_base"] == base


def test_pr_uses_fork_base_and_excludes_unrelated_new_main_source(repository):
    root, fork = repository
    command(root, "checkout", "--quiet", "-b", "report")
    write(root, REPORT, "# PR report\n")
    head = commit(root)
    command(root, "checkout", "--quiet", "main")
    write(root, "src/app.py", "# unrelated main advance\n")
    base = commit(root)
    result = classify(root, "pull_request", base, head)
    assert result["lane"] == "reports" and result["comparison_base"] == fork
    assert classify(root, "push", base, head)["valid"] is False


def test_pr_retains_earlier_source_commits_when_last_commit_is_report(repository):
    root, base = repository
    write(root, "src/app.py", "# earlier source change\n")
    commit(root)
    write(root, REPORT, "# Final report commit\n")
    assert classify(root, "pull_request", base, commit(root))["lane"] == "source"


@pytest.mark.parametrize("base", ["", "0" * 40, "f" * 40, "HEAD~1", None])
def test_invalid_or_unavailable_comparison_fails_closed(repository, base):
    root, head = repository
    result = classify(root, "push", base, head)
    assert result["lane"] == "source" and result["valid"] is False


def test_empty_comparison_selects_full_and_dispatch_is_explicit_full_route(repository):
    root, head = repository
    assert classify(root, "push", head, head)["lane"] == "source"
    assert (
        classify(root, "workflow_dispatch", "", head)["reason"]
        == "explicit-full-validation"
    )


def index(text="Measured results", target=REPORT):
    return f"\n## Reports\n\n- [{text}]({target})\n"


def test_readme_only_appended_report_index_selects_shortcut(repository):
    root, base = repository
    original = (root / "README.md").read_text()
    write(root, "README.md", original + index())
    result = classify(root, "pull_request", base, commit(root))
    assert result["lane"] == "reports"
    assert set(result["markdown"]) == {"README.md", REPORT}
    assert readme_index_only(
        original + index(), original + index("Updated report label")
    )


def test_missing_or_executable_index_target_cannot_claim_verified_reports(repository):
    root, base = repository
    original = (root / "README.md").read_text()
    write(
        root, "README.md", original + index(target="docs/audits/2026-09-30-missing.md")
    )
    assert classify(root, "push", base, commit(root))["lane"] == "source"
    (root / REPORT).chmod(0o755)
    base = commit(root)
    write(root, "README.md", original + index())
    assert classify(root, "push", base, commit(root))["lane"] == "source"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace("Original contract.", "Altered runtime contract."),
        lambda text: text + "\nThis report changes startup behavior.\n",
        lambda text: text.replace(REPORT, "start.sh"),
        lambda text: text.replace("## Reports", "## Reports\n\n## Reports"),
        lambda text: text + "- [Unknown](docs/unknown.md)\n",
    ],
)
def test_non_index_readme_edits_or_nonreport_index_targets_select_full(
    repository, mutation
):
    root, base = repository
    text = (root / "README.md").read_text() + index()
    write(root, "README.md", mutation(text))
    assert classify(root, "push", base, commit(root))["lane"] == "source"


def needs(lane):
    return {
        **{name: {"result": "success"} for name in CONTROL_NEEDS},
        **{
            name: {"result": "skipped" if lane == "reports" else "success"}
            for name in SOURCE_NEEDS
        },
    }


def jobs():
    return [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in JOB_NAMES
    ]


def gate(lane, required=None, observed=None, **classification):
    return evaluate(
        {
            "valid": True,
            "lane": lane,
            "reason": "verified-report-only",
            **classification,
        },
        needs(lane) if required is None else required,
        jobs() if observed is None else observed,
        {"complete": True, "lane": lane},
    )[0]


def test_source_gate_requires_all_seven_concrete_jobs_not_only_matrix_aggregate():
    assert gate("source")
    assert not gate("source", observed=jobs()[:-1])
    assert not gate("source", observed=jobs()[:-1] + [jobs()[0]])


@pytest.mark.parametrize(
    "outcome", ["failure", "cancelled", "skipped", "timed_out", None]
)
def test_failed_cancelled_skipped_or_unfinished_source_matrix_leg_fails_gate(outcome):
    observed = jobs()
    observed[-1]["conclusion"] = outcome
    assert not gate("source", observed=observed)


@pytest.mark.parametrize("lane", ["source", "reports"])
@pytest.mark.parametrize("job", sorted(CONTROL_NEEDS))
def test_classifier_docs_or_timing_failure_always_fails_gate(lane, job):
    required = needs(lane)
    required[job]["result"] = "failure"
    assert not gate(lane, required=required)


def test_report_source_skips_need_positive_classification_and_complete_control_jobs():
    assert gate("reports", observed=[])
    assert not gate("reports", valid=False)
    assert not gate("reports", reason="invalid-comparison")
    required = needs("reports")
    del required["browser-smoke"]
    assert not gate("reports", required=required)
    assert not gate("source", required=needs("reports"))


@pytest.mark.parametrize("lane", ["source", "reports"])
def test_incomplete_or_mismatched_timing_is_not_acceptance(lane):
    classification = {"valid": True, "lane": lane, "reason": "verified-report-only"}
    assert not evaluate(
        classification, needs(lane), jobs(), {"complete": False, "lane": lane}
    )[0]
    assert not evaluate(
        classification, needs(lane), jobs(), {"complete": True, "lane": "unknown"}
    )[0]


def test_report_timing_marks_source_tests_inapplicable_without_a_pass_denominator(
    tmp_path,
):
    run = {
        "created_at": "2026-09-30T00:00:00Z",
        "run_started_at": "2026-09-30T00:00:02Z",
    }
    observed = [
        {
            "name": name,
            "started_at": "2026-09-30T00:00:03Z",
            "completed_at": "2026-09-30T00:00:10Z",
            "conclusion": "success",
            "steps": [],
        }
        for name in REPORT_JOB_NAMES
    ]
    observed += [{"name": name, "conclusion": "skipped"} for name in JOB_NAMES]
    result = summarize(run, observed, lane="reports", report_validation=True)
    assert result["complete"] and result["lane"] == "reports"
    assert result["not_applicable_jobs"] == sorted(JOB_NAMES.values())
    evidence = collect_evidence(tmp_path, lane="reports")
    assert evidence["complete"] and evidence["applicable"] is False
    assert (
        evidence["first_attempt_eligible"] == 0
        and evidence["first_attempt_pass_rate"] is None
    )
    observed[1]["conclusion"] = "cancelled"
    assert (
        summarize(run, observed, lane="reports", report_validation=True)["complete"]
        is False
    )
    observed[1]["conclusion"] = "failure"
    # Fully observed failure is complete evidence, never a passing required gate.
    failed = summarize(run, observed, lane="reports", report_validation=True)
    assert failed["complete"] and failed["jobs"][1]["outcome"] == "failure"


def test_docs_receipts_reject_unexpected_source_evidence(tmp_path):
    (tmp_path / "unexpected.jsonl").write_text("{}\n")
    result = collect_evidence(tmp_path, lane="reports")
    assert result["complete"] is False and result["invalid_lanes"] == 1


def test_classifier_cli_needs_only_stdlib_and_exits_nonzero_for_bad_base(
    repository, tmp_path
):
    root, head = repository
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": "0" * 40, "after": head}))
    output = tmp_path / "classification.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/classify_changes.py"),
            "--output",
            str(output),
        ],
        cwd=root,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and load_classification(output)["lane"] == "source"


@pytest.mark.parametrize(
    "outcome,exit_code", [("success", 0), ("failure", 1), ("cancelled", 1)]
)
def test_actual_stdlib_report_gate_resolves_success_and_docs_failures(
    tmp_path, outcome, exit_code
):
    classification = tmp_path / "classification.json"
    classification.write_text(
        json.dumps(
            {
                "schema": 1,
                "lane": "reports",
                "valid": True,
                "reason": "verified-report-only",
                "base": "a" * 40,
                "head": "b" * 40,
                "comparison_base": "a" * 40,
                "markdown": [REPORT],
            }
        )
    )
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({"lane": "reports", "complete": True}))
    required = needs("reports")
    required["report-validation"]["result"] = outcome
    # Arbitrary provider/debug content must not escape through gate diagnostics.
    required["report-validation"]["debug"] = "PRIVATE_SENTINEL_DO_NOT_PUBLISH"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/required_gate.py"),
            "--classification",
            str(classification),
            "--timing",
            str(timing),
        ],
        env={**os.environ, "CI_NEEDS": json.dumps(required)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code
    assert "PRIVATE_SENTINEL" not in result.stdout + result.stderr


def test_actual_source_gate_cannot_pass_when_actions_evidence_is_unavailable(tmp_path):
    classification = tmp_path / "classification.json"
    classification.write_text(
        json.dumps(
            {
                "schema": 1,
                "lane": "source",
                "valid": True,
                "reason": "full-validation",
                "base": "a" * 40,
                "head": "b" * 40,
                "comparison_base": "a" * 40,
                "markdown": [],
            }
        )
    )
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({"lane": "source", "complete": True}))
    env = {
        **os.environ,
        "CI_NEEDS": json.dumps(needs("source")),
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    env.pop("GITHUB_TOKEN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/required_gate.py"),
            "--classification",
            str(classification),
            "--timing",
            str(timing),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1 and "required-evidence-unavailable" in result.stdout


@pytest.mark.parametrize(
    "mutation",
    [
        {"lane": "reports", "valid": False},
        {"lane": "reports", "reason": "unknown"},
        {"lane": "reports", "markdown": []},
        {"lane": "reports", "markdown": ["AGENTS.md"]},
        {"markdown": ["../outside.md"]},
        {"markdown": ["report.md\nsecret.md"]},
        {"schema": True},
    ],
)
def test_malformed_or_unproven_classification_receipt_is_rejected(tmp_path, mutation):
    path = tmp_path / "classification.json"
    value = {
        "schema": 1,
        "lane": "reports",
        "valid": True,
        "reason": "verified-report-only",
        "base": "a" * 40,
        "head": "b" * 40,
        "comparison_base": "a" * 40,
        "markdown": [REPORT],
        **mutation,
    }
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="classification"):
        load_classification(path)
