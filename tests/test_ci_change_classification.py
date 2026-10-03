"""A report shortcut must never hide a source change or absent required check."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from scripts.ci.classify_changes import classify, load_classification, readme_index_only
from scripts.ci.test_selection import expected_lanes, source_job_names
from scripts.ci.required_gate import CONTROL_NEEDS, SOURCE_NEEDS, evaluate
from scripts.ci.workflow_timing import (
    REPORT_JOB_NAMES,
    summarize,
    test_evidence as collect_evidence,
)


REPORT = "docs/audits/2026-09-30-feedback-results.md"
ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"source_sha": "a" * 40, "head_sha": "b" * 40, "run_id": 1, "run_attempt": 2}


def identity_environment():
    return {
        "GITHUB_SHA": IDENTITY["source_sha"],
        "CI_TELEMETRY_SHA": IDENTITY["source_sha"],
        "CI_TELEMETRY_HEAD_SHA": IDENTITY["head_sha"],
        "GITHUB_RUN_ID": str(IDENTITY["run_id"]),
        "GITHUB_RUN_ATTEMPT": str(IDENTITY["run_attempt"]),
    }


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


def report_workflow(root, event, base, head):
    """Run the same dependency-free classifier and validator commands as CI."""
    payload = (
        {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
        if event == "pull_request"
        else {"before": base, "after": head}
    )
    event_path = root / "event.json"
    event_path.write_text(json.dumps(payload))
    classification = root / "classification.json"
    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(root / "github-output"),
    }
    classified = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/classify_changes.py"),
            "--output",
            str(classification),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert classified.returncode == 0, classified.stdout + classified.stderr
    output = root / "validation.json"
    validated = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/validate_reports.py"),
            "--classification",
            str(classification),
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (
        json.loads(classification.read_text()),
        validated.returncode,
        json.loads(output.read_text()),
    )


@pytest.mark.parametrize("path,lane", [("src/app.py", "source"), (REPORT, "reports")])
def test_workflow_checkout_runs_base_added_classifier_for_an_existing_pr(
    repository, path, lane
):
    root, fork = repository
    command(root, "checkout", "--quiet", "-b", "existing-pr")
    write(root, path, "# Existing PR change\n")
    head = commit(root)
    assert not (root / "scripts/ci/classify_changes.py").exists()

    command(root, "checkout", "--quiet", "main")
    for file in (
        "scripts/ci/classify_changes.py",
        "scripts/ci/test_selection.py",
        ".github/workflows/ci.yml",
    ):
        write(root, file, (ROOT / file).read_text())
    write(root, "src/base-only.py", "# Unrelated base advance\n")
    base = commit(root)
    command(root, "merge", "--quiet", "--no-ff", head, "-m", "prospective PR merge")
    merge = command(root, "rev-parse", "HEAD")
    assert command(root, "rev-list", "--parents", "-n", "1", merge).split() == [
        merge,
        base,
        head,
    ]

    # Read the actual merged workflow, not a duplicated checkout/command choice.
    workflow = (root / ".github/workflows/ci.yml").read_text()
    job = re.search(r"(?ms)^  classify:\n(.*?)(?=^  [\w-]+:|\Z)", workflow)[1]
    checkout = re.search(
        r"(?m)^      - uses: actions/checkout@v4\n((?:^        .*\n)*)", job
    )[1]
    options = dict(re.findall(r"(?m)^          ([\w-]+): (.+)$", checkout))
    assert options["fetch-depth"] == "0"
    revisions = {
        "": merge,  # actions/checkout defaults to the triggering github.sha.
        "${{ github.sha }}": merge,
        "${{ github.event.pull_request.head.sha || github.sha }}": head,
    }
    assert options.get("ref", "") in revisions
    revision = revisions[options.get("ref", "")]
    args = shlex.split(re.search(r"(?m)^        run: (.+)$", job)[1])
    assert args[:2] == ["python3", "scripts/ci/classify_changes.py"]
    args[:1] = [sys.executable, "-S"]
    output = root / args[args.index("--output") + 1]
    event = root / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}})
    )
    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": merge,
        "GITHUB_OUTPUT": str(root / "github-output"),
    }

    command(root, "checkout", "--quiet", "--detach", head)
    broken = subprocess.run(
        args, cwd=root, env=env, capture_output=True, text=True, timeout=10
    )
    assert broken.returncode == 2 and "No such file or directory" in broken.stderr
    assert not output.exists()

    command(root, "checkout", "--quiet", "--detach", revision)
    corrected = subprocess.run(
        args, cwd=root, env=env, capture_output=True, text=True, timeout=10
    )
    assert corrected.returncode == 0, corrected.stdout + corrected.stderr
    result = load_classification(output)
    assert command(root, "rev-parse", "HEAD") == merge
    assert result["valid"] and result["lane"] == lane
    assert result["base"] == base and result["head"] == head
    assert result["comparison_base"] == fork
    assert result["markdown"] == ([REPORT] if lane == "reports" else [])

    # Having the control script at the merge cannot excuse a missing event ref.
    for missing in ("base", "head"):
        payload = {"base": {"sha": base}, "head": {"sha": head}}
        payload[missing]["sha"] = "0" * 40
        event.write_text(json.dumps({"pull_request": payload}))
        rejected = subprocess.run(
            args, cwd=root, env=env, capture_output=True, text=True, timeout=10
        )
        invalid = load_classification(output)
        assert rejected.returncode == 1 and invalid["valid"] is False
        assert invalid["lane"] == "source" and invalid["markdown"] == []


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("change", ["modify", "rename", "delete"])
def test_existing_front_matter_skill_uses_full_ci_without_report_title_policy(
    repository, event, change
):
    root, _ = repository
    skill = ".agents/skills/stock-t3-agents/SKILL.md"
    original = (ROOT / skill).read_text()
    assert original.startswith("---\n")
    write(root, skill, original)
    base = commit(root)
    if change == "modify":
        write(root, skill, original + "\nPreserve explicit task ownership.\n")
    elif change == "rename":
        command(root, "mv", skill, ".agents/skills/stock-t3-agents/RENAMED.md")
    else:
        (root / skill).unlink()
    classified, status, receipt = report_workflow(root, event, base, commit(root))
    assert classified["valid"] and classified["lane"] == "source"
    assert classified["markdown"] == []
    assert status == 0 and receipt["outcome"] == "success" and receipt["files"] == 0


@pytest.mark.parametrize("change", ["rename", "delete"])
def test_report_rename_or_deletion_retains_full_ci_without_reading_missing_paths(
    repository, change
):
    root, base = repository
    if change == "rename":
        command(root, "mv", REPORT, "docs/audits/2026-09-30-renamed-report.md")
    else:
        (root / REPORT).unlink()
    classified, status, receipt = report_workflow(root, "push", base, commit(root))
    assert classified["valid"] and classified["lane"] == "source"
    assert classified["markdown"] == []
    assert status == 0 and receipt["outcome"] == "success" and receipt["files"] == 0


@pytest.mark.parametrize("source_change", [False, True])
@pytest.mark.parametrize(
    "report", ["Untitled report\n", "# Report\n\n[Missing](missing.md)\n"]
)
def test_workflow_keeps_report_title_and_link_failures_in_report_and_source_lanes(
    repository, source_change, report
):
    root, base = repository
    write(root, REPORT, report)
    if source_change:
        write(root, "src/app.py", "# Changed source invariant\n")
    classified, status, receipt = report_workflow(root, "push", base, commit(root))
    assert classified["valid"]
    assert classified["lane"] == ("source" if source_change else "reports")
    assert classified["markdown"] == [REPORT]
    assert status == 1 and receipt["outcome"] == "failure"


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
    assert result["markdown"] == []


def test_report_and_source_in_same_change_select_full(repository):
    root, base = repository
    write(root, REPORT, "# New report\n")
    write(root, "src/app.py", "# broken invariant\n")
    result = classify(root, "pull_request", base, commit(root))
    assert result["lane"] == "source" and result["markdown"] == [REPORT]


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
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            **{key: IDENTITY[key] for key in ("run_id", "run_attempt", "head_sha")},
        }
        for name in source_job_names("full")
    ]


def evidence(profile):
    return {
        "complete": True,
        "profile": profile,
        "lanes": [
            {
                "lane": lane,
                "project": project,
                "shard": shard,
                "complete": True,
                "outcome": "passed",
                "first_attempt_failures": 0,
                "retry_recovered": 0,
                **{
                    key: IDENTITY[key]
                    for key in ("source_sha", "run_id", "run_attempt")
                },
            }
            for lane, project, shard in sorted(expected_lanes(profile))
        ],
    }


def gate(lane, required=None, observed=None, **classification):
    return evaluate(
        {
            "valid": True,
            "lane": lane,
            "profile": "reports" if lane == "reports" else "full",
            "reason": "verified-report-only",
            "head": IDENTITY["head_sha"],
            **classification,
        },
        needs(lane) if required is None else required,
        jobs() if observed is None else observed,
        {
            "complete": True,
            "lane": lane,
            "profile": "reports" if lane == "reports" else "full",
            "test_evidence": evidence("reports" if lane == "reports" else "full"),
            **IDENTITY,
        },
        IDENTITY,
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


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "failed-job",
        "cancelled-job",
        "missing-job",
        "stale-attempt",
        "missing-test-evidence",
    ],
)
def test_unknown_step_measurement_preserves_strict_source_acceptance(damage, tmp_path):
    observed = jobs()
    for job in observed:
        job.update(
            started_at="2026-09-30T00:00:03Z",
            completed_at="2026-09-30T00:00:10Z",
            steps=[
                {
                    "name": "Exercise browser flows",
                    "started_at": "2026-09-30T00:00:04Z",
                    "completed_at": None,
                }
            ],
        )
    if damage in {"failed-job", "cancelled-job"}:
        observed[0]["conclusion"] = "failure" if damage == "failed-job" else "cancelled"
    elif damage == "missing-job":
        observed.pop()
    timing = {
        **summarize(
            {
                "created_at": "2026-09-30T00:00:00Z",
                "run_started_at": "2026-09-30T00:00:02Z",
            },
            observed,
        ),
        **IDENTITY,
    }
    assert all(
        job["test_steps_ms"] is None and job["setup_and_other_ms"] is None
        for job in timing["jobs"]
    )
    timing["test_evidence"] = evidence("full")
    if damage == "stale-attempt":
        timing["run_attempt"] = 1
    elif damage == "missing-test-evidence":
        # The real collector still requires all seven sanitized test lanes.
        timing["test_evidence"] = collect_evidence(tmp_path, identity=IDENTITY)
        timing["complete"] = timing["complete"] and timing["test_evidence"]["complete"]
    passed, _ = evaluate(
        {
            "valid": True,
            "lane": "source",
            "profile": "full",
            "head": IDENTITY["head_sha"],
        },
        needs("source"),
        observed,
        timing,
        IDENTITY,
    )
    assert passed is (damage == "none")


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
    classification = {
        "valid": True,
        "lane": lane,
        "profile": "reports" if lane == "reports" else "full",
        "reason": "verified-report-only",
        "head": IDENTITY["head_sha"],
    }
    assert not evaluate(
        classification,
        needs(lane),
        jobs(),
        {
            "complete": False,
            "lane": lane,
            "profile": "reports" if lane == "reports" else "full",
            **IDENTITY,
        },
        IDENTITY,
    )[0]
    assert not evaluate(
        classification,
        needs(lane),
        jobs(),
        {"complete": True, "lane": "unknown", **IDENTITY},
        IDENTITY,
    )[0]


@pytest.mark.parametrize("lane", ["source", "reports"])
@pytest.mark.parametrize(
    "field,stale",
    [
        ("source_sha", "c" * 40),
        ("head_sha", "c" * 40),
        ("run_id", 99),
        ("run_attempt", 1),
        ("run_attempt", True),
    ],
)
def test_stale_or_earlier_attempt_timing_cannot_establish_acceptance(
    lane, field, stale
):
    classification = {
        "valid": True,
        "lane": lane,
        "profile": "reports" if lane == "reports" else "full",
        "reason": "verified-report-only",
        "head": IDENTITY["head_sha"],
    }
    passed, reason = evaluate(
        classification,
        needs(lane),
        jobs(),
        {
            "complete": True,
            "lane": lane,
            "profile": "reports" if lane == "reports" else "full",
            **IDENTITY,
            field: stale,
        },
        IDENTITY,
    )
    assert not passed and reason == "stale-or-mismatched-workflow-evidence"


@pytest.mark.parametrize(
    "field,stale",
    [("head_sha", "c" * 40), ("run_id", 99), ("run_attempt", 1), ("run_attempt", True)],
)
def test_stale_earlier_attempt_or_other_head_api_jobs_fail_source_gate(field, stale):
    observed = jobs()
    observed[-1][field] = stale
    assert not gate("source", observed=observed)


def test_stale_classification_head_is_not_a_report_shortcut():
    assert not gate("reports", head="c" * 40)


@pytest.mark.parametrize(
    "stale,complete",
    [
        ("none", True),
        ("run", False),
        ("job", False),
        ("head", False),
        ("classification", False),
    ],
)
def test_actual_rollup_preserves_observations_but_rejects_stale_actions_responses(
    tmp_path, monkeypatch, stale, complete
):
    from scripts.ci import workflow_timing

    classification = tmp_path / "classification.json"
    classification.write_text(
        json.dumps(
            {
                "schema": 2,
                "lane": "reports",
                "profile": "reports",
                "valid": True,
                "reason": "verified-report-only",
                "base": "a" * 40,
                "head": IDENTITY["head_sha"],
                "comparison_base": "a" * 40,
                "markdown": [REPORT],
            }
        )
    )
    if stale == "classification":
        value = json.loads(classification.read_text())
        value.update(valid=False, lane="source", reason="invalid-comparison")
        classification.write_text(json.dumps(value))
    run = {
        "id": 1,
        "run_attempt": 2,
        "created_at": "2026-09-30T00:00:00Z",
        "run_started_at": "2026-09-30T00:00:02Z",
        "debug": "PRIVATE_SENTINEL",
    }
    observed = [
        {
            "name": name,
            "run_id": 1,
            "run_attempt": 2,
            "head_sha": IDENTITY["head_sha"],
            "started_at": "2026-09-30T00:00:03Z",
            "completed_at": "2026-09-30T00:00:10Z",
            "conclusion": "success",
            "steps": [],
            "debug": "PRIVATE_SENTINEL",
        }
        for name in REPORT_JOB_NAMES
    ]
    if stale == "run":
        run["run_attempt"] = 1
    elif stale == "job":
        observed[-1]["run_attempt"] = 1
    elif stale == "head":
        observed[-1]["head_sha"] = "c" * 40
    monkeypatch.setattr(
        workflow_timing,
        "api",
        lambda path: (
            {"total_count": len(observed), "jobs": observed}
            if "/jobs?" in path
            else run
        ),
    )
    for key, value in {
        **identity_environment(),
        "GITHUB_REPOSITORY": "example/project",
    }.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / "workflow.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "workflow_timing.py",
            "--classification",
            str(classification),
            "--inputs",
            str(tmp_path / "lanes"),
            "--output",
            str(output),
        ],
    )
    workflow_timing.main()
    result = json.loads(output.read_text())
    assert result["complete"] is complete and len(result["jobs"]) == 2
    assert all(result[key] == value for key, value in IDENTITY.items())
    assert "PRIVATE_SENTINEL" not in output.read_text()
    if stale == "classification":
        assert result["collection_error"] == "invalid-classification"


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
    observed += [
        {"name": name, "status": "completed", "conclusion": "skipped"}
        for name in source_job_names("reports")
    ]
    result = summarize(run, observed, lane="reports", report_validation=True)
    assert result["complete"] and result["lane"] == "reports"
    assert result["not_applicable_jobs"] == sorted(source_job_names("reports").values())
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
                "schema": 2,
                "lane": "reports",
                "profile": "reports",
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
    timing.write_text(
        json.dumps(
            {
                "lane": "reports",
                "profile": "reports",
                "complete": True,
                "test_evidence": evidence("reports"),
                **IDENTITY,
            }
        )
    )
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
        env={**os.environ, **identity_environment(), "CI_NEEDS": json.dumps(required)},
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
                "schema": 2,
                "lane": "source",
                "profile": "full",
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
    timing.write_text(
        json.dumps({"lane": "source", "profile": "full", "complete": True, **IDENTITY})
    )
    env = {
        **os.environ,
        **identity_environment(),
        "CI_NEEDS": json.dumps(needs("source")),
        "GITHUB_REPOSITORY": "example/project",
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
        {"lane": "reports", "profile": "reports", "valid": False},
        {"lane": "reports", "profile": "reports", "reason": "unknown"},
        {"lane": "reports", "profile": "reports", "markdown": []},
        {"lane": "reports", "profile": "reports", "markdown": ["AGENTS.md"]},
        {"markdown": ["../outside.md"]},
        {"markdown": ["report.md\nsecret.md"]},
        {"schema": True},
    ],
)
def test_malformed_or_unproven_classification_receipt_is_rejected(tmp_path, mutation):
    path = tmp_path / "classification.json"
    value = {
        "schema": 2,
        "lane": "reports",
        "profile": "reports",
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
