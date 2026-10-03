"""Public timing must detect retries/incompleteness without copying fixture secrets."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.ci.summary import summarize, valid
from scripts.ci.required_gate import CONTROL_NEEDS, evaluate
from scripts.ci.workflow_timing import (
    STARTUP_FIELDS,
    duration,
    startup_jobs,
    timestamp,
    summarize as workflow_summary,
    test_evidence as collect_evidence,
)


from scripts.ci.test_selection import (
    CACHE_PROFILE,
    SOURCE_NEEDS,
    source_job_names,
    INSPECTION_PROFILE,
    INSPECTION_PYTHON,
    INSPECTION_TEST,
    expected_scope,
    expected_jobs,
    expected_lanes,
    required_needs,
)


ROOT = Path(__file__).resolve().parents[1]
SECRET = "PLANTED_PASSWORD_TOKEN_AUTHORIZATION_cookie_7ce19"


def test_startup_collection_keeps_exact_browser_artifact_and_selected_topology():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    startup = workflow.split(
        "      - name: Start the canonical application stack\n", 1
    )[1].split("      - name:", 1)[0]
    assert (
        "SCHEMII_START_TIMING_FILE: ${{ github.workspace }}/artifacts/ci-timing/startup.jsonl"
        in startup
    )
    assert workflow.index("run: mkdir -p artifacts/ci-timing") < workflow.index(
        "      - name: Start the canonical application stack"
    )
    assert "CI_TELEMETRY_PROJECT: ${{ matrix.project }}" in startup
    assert "CI_TELEMETRY_SHARD: ${{ matrix.shard }}" in startup
    assert "node scripts/ci/prepare-browser-stack.mjs --discovery" in startup
    assert "            ./start.sh" in startup
    assert "matrix: ${{ fromJSON(needs.classify.outputs.browser_matrix) }}" in workflow
    assert "fail-fast: false" in workflow
    validation = workflow.split("      - name: Validate public browser timing\n", 1)[
        1
    ].split("      - name:", 1)[0]
    assert (
        '--startup "${{ github.workspace }}/artifacts/ci-timing/startup.jsonl"'
        in validation
    )
    publication = workflow.split(
        "      - name: Publish public browser timing, including failed first attempts\n",
        1,
    )[1].split("      - name:", 1)[0]
    assert "steps.browser-timing.outcome == 'success'" in publication
    assert "startup.json" not in publication
    assert "artifacts/ci-timing/browser.jsonl" in publication
    assert "artifacts/ci-timing/browser-summary.json" in publication
    assert "artifacts/ci-timing/browser-dependencies.json" in publication
    assert "retention-days: 7" in publication
    diagnostic = workflow.split(
        "      - name: Publish validated failed startup diagnostic\n", 1
    )[1].split("  timing-rollup:", 1)[0]
    assert "steps.canonical-startup.outcome == 'failure'" in diagnostic
    assert "steps.startup-diagnostic.outcome == 'success'" in diagnostic
    assert "name: startup-diagnostic-" in diagnostic
    assert "name: browser-timing-" not in diagnostic
    assert "path: artifacts/ci-timing/startup-diagnostic.json" in diagnostic


META = {
    "schema": 1,
    "source_sha": "a" * 40,
    "run_id": 1,
    "run_attempt": 1,
    "lane": "browser",
    "project": "desktop-chromium",
    "shard": 1,
}


def record(kind, **values):
    return {**META, "kind": kind, **values}


def attempt(index, outcome):
    return record(
        "attempt",
        test_id="1" * 64,
        source_id="2" * 64,
        source_line=1,
        attempt=index,
        outcome=outcome,
        skip="declared-or-runtime" if outcome == "skipped" else "none",
        setup_ms=2,
        execution_ms=3,
        teardown_ms=1,
    )


def records(*attempts):
    return [
        record("start", planned=1),
        record("plan", test_id="1" * 64),
        *attempts,
        record("end", outcome="passed", wall_ms=7),
    ]


def launcher_records():
    meta = {
        key: META[key]
        for key in ("schema", "source_sha", "run_id", "run_attempt", "project", "shard")
    }
    meta.update(source_kind="hosted", launcher_id="c" * 64)
    return [
        {**meta, "kind": "launcher-start"},
        *[
            {
                **meta,
                "kind": "launcher-phase",
                "phase": name,
                "outcome": "passed",
                "duration_ms": milliseconds,
            }
            for name, milliseconds in zip(
                ("preparation", "build", "replacement", "readiness", "post-start"),
                (2, 11, 0, 17, 3),
                strict=True,
            )
        ],
        {**meta, "kind": "launcher-end", "outcome": "passed", "wall_ms": 33},
    ]


def startup_cli(tmp_path, receipt, *, failed=False):
    path = tmp_path / "startup.jsonl"
    path.write_text("".join(json.dumps(value) + "\n" for value in receipt))
    public = tmp_path / "public"
    public.mkdir()
    raw_path = public / "browser.jsonl"
    raw_path.write_text(
        "".join(json.dumps(value) + "\n" for value in records(attempt(0, "passed")))
    )
    output = public / "browser-summary.json"
    output.write_text(SECRET)
    argv = [
        sys.executable,
        str(ROOT / "scripts/ci/workflow_timing.py"),
        "--startup",
        str(path),
        "--output",
        str(output),
    ]
    argv.extend(["--failed-startup"] if failed else ["--inputs", str(raw_path)])
    result = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "CI_TELEMETRY_SHA": META["source_sha"],
            "GITHUB_SHA": META["source_sha"],
            "CI_TELEMETRY_HEAD_SHA": META["source_sha"],
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "CI_TELEMETRY_PROJECT": META["project"],
            "CI_TELEMETRY_SHARD": "1",
            "PLANTED_SECRET": SECRET,
        },
    )
    assert SECRET not in result.stdout + result.stderr
    return result, output


def test_startup_cli_derives_bound_public_browser_summary_and_exact_zero(tmp_path):
    result, output = startup_cli(tmp_path, launcher_records())
    assert result.returncode == 0, result.stderr
    published = json.loads(output.read_text())
    assert SECRET not in output.read_text()
    assert set(published) == set(summarize(records(attempt(0, "passed")))) | {"startup"}
    assert published["startup"]["preflight"] == "unmeasured"
    assert published["startup"]["phases"][2]["duration_ms"] == 0
    assert published["startup"]["wall_ms"] == 33
    assert published["startup"]["source_sha"] == META["source_sha"]


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "run",
        "attempt",
        "project",
        "shard",
        "local",
        "extra",
        "failed",
        "partial",
    ],
)
def test_startup_cli_rejects_stale_private_or_incomplete_browser_measurements(
    tmp_path, damage
):
    receipt = launcher_records()
    replacement = {
        "source": ("source_sha", "b" * 40),
        "run": ("run_id", 2),
        "attempt": ("run_attempt", 2),
        "project": ("project", "android-chromium"),
        "shard": ("shard", 2),
    }
    if damage in replacement:
        key, value = replacement[damage]
        for record in receipt:
            record[key] = value
    elif damage == "local":
        for record in receipt:
            record.update(source_kind="local", source_sha=None, run_id=0, run_attempt=0)
    elif damage == "extra":
        receipt[2]["stderr"] = SECRET
    elif damage == "failed":
        receipt[5]["outcome"] = receipt[6]["outcome"] = "failed"
    else:
        receipt = receipt[:3]
    result, output = startup_cli(tmp_path, receipt)
    assert result.returncode != 0 and not output.exists()
    assert result.stderr.strip() == "Startup timing validation failed"


@pytest.mark.parametrize("terminal", [False, True])
def test_startup_failure_diagnostic_retains_failed_build_and_unknown_later_phases(
    tmp_path, terminal
):
    receipt = launcher_records()[:3]
    receipt[-1]["outcome"] = "failed"
    if terminal:
        receipt.append(
            {**receipt[0], "kind": "launcher-end", "outcome": "failed", "wall_ms": 13}
        )
    result, output = startup_cli(tmp_path, receipt, failed=True)
    assert result.returncode == 0, result.stderr
    diagnostic = json.loads(output.read_text())
    assert diagnostic["outcome"] == ("failed" if terminal else None)
    assert diagnostic["wall_ms"] == (13 if terminal else None)
    assert diagnostic["phases"][1] == {
        "phase": "build",
        "outcome": "failed",
        "duration_ms": 11,
    }
    assert all(
        phase["outcome"] is phase["duration_ms"] is None
        for phase in diagnostic["phases"][2:]
    )
    assert SECRET not in output.read_text()


def test_startup_failure_diagnostic_cannot_publish_a_successful_start(tmp_path):
    result, output = startup_cli(tmp_path, launcher_records(), failed=True)
    assert result.returncode != 0 and not output.exists()


@pytest.mark.parametrize("profile", ["full", CACHE_PROFILE, INSPECTION_PROFILE])
def test_startup_rollup_binds_selected_topology_and_preserves_absent_measurements(
    tmp_path, profile
):
    result, output = startup_cli(tmp_path, launcher_records())
    assert result.returncode == 0
    lane = json.loads(output.read_text())
    phases = startup_jobs({"lanes": [lane]}, profile)
    template = successful_workflow_jobs()[0]
    summary = workflow_summary(
        WORKFLOW_RUN,
        [
            {
                **template,
                "name": name,
                "conclusion": "success"
                if name in expected_jobs(profile)
                else "skipped",
            }
            for name in source_job_names(profile)
        ],
        profile=profile,
        startup=phases,
    )
    assert summary["complete"]
    observed = next(job for job in summary["jobs"] if job["job"] in phases)
    assert observed["startup_build_ms"] == 11
    assert observed["startup_replacement_ms"] == 0
    assert observed["startup_up_readiness_ms"] == 17
    assert observed["job"].endswith(
        "1-of-6"
        if profile == "full"
        else "1-of-3"
        if profile == CACHE_PROFILE
        else "1-of-1"
    )
    assert all(
        job[field] is None
        for job in summary["jobs"]
        if job["job"].startswith("browser-") and job["job"] not in phases
        for field in STARTUP_FIELDS
    )


@pytest.mark.parametrize("damage", ["private", "identity", "extra-summary"])
def test_downloaded_startup_summary_is_revalidated_before_workflow_publication(
    tmp_path, damage
):
    result, output = startup_cli(tmp_path, launcher_records())
    assert result.returncode == 0
    summary = json.loads(output.read_text())
    if damage == "private":
        summary["startup"]["phases"][1]["stdout"] = SECRET
    elif damage == "identity":
        summary["startup"]["shard"] = 2
    else:
        summary["unapproved"] = SECRET
    output.write_text(json.dumps(summary))
    evidence = collect_evidence(output.parent)
    assert evidence["invalid_lanes"] == 1 and evidence["complete"] is False
    assert SECRET not in json.dumps(evidence)


def test_retry_recovery_retains_failed_attempt_and_first_attempt_denominator():
    result = summarize(records(attempt(0, "failed"), attempt(1, "passed")))
    assert result["complete"] is True
    assert result["retry_recovered"] == result["first_attempt_failures"] == 1
    assert result["first_attempt_pass_rate"] == 0
    assert result["attempt_outcomes"]["failed"] == 1
    assert result["attempts"] == 2


@pytest.mark.parametrize(
    "outcome,eligible,complete",
    [
        ("passed", 1, True),
        ("failed", 1, True),
        ("skipped", 0, True),
        ("cancelled", 0, False),
        ("not-run", 0, False),
        ("timed-out", 1, True),
    ],
)
def test_outcomes_remain_distinct(outcome, eligible, complete):
    result = summarize(records(attempt(0, outcome)))
    assert result["attempt_outcomes"][outcome] == 1
    assert result["first_attempt_eligible"] == eligible
    assert result["complete"] is complete


def test_missing_footer_or_missing_planned_test_is_incomplete():
    assert summarize(records(attempt(0, "passed"))[:-1])["complete"] is False
    assert summarize(records())["missing"] == 1


@pytest.mark.parametrize(
    "terminal", ["cancelled", "timed-out", "collection-error", "error"]
)
@pytest.mark.parametrize("observed_pass", [True, False])
def test_terminal_shutdown_cannot_be_overridden_by_passes_or_empty_inventory(
    terminal, observed_pass
):
    evidence = (
        records(attempt(0, "passed"))
        if observed_pass
        else [record("start", planned=0), record("end", outcome="passed", wall_ms=7)]
    )
    evidence[-1]["outcome"] = terminal
    result = summarize(evidence)
    assert result["outcome"] == terminal
    assert result["complete"] is False
    assert result["first_attempt_passes"] == int(observed_pass)


def test_zero_collected_tests_do_not_establish_complete_acceptance():
    for terminal in ("passed", "failed"):
        assert (
            summarize(
                [record("start", planned=0), record("end", outcome=terminal, wall_ms=1)]
            )["complete"]
            is False
        )


@pytest.mark.parametrize(
    "field",
    ["title", "errors", "stdout", "attachments", "cookies", "Authorization", "fixture"],
)
def test_public_schema_rejects_every_unapproved_field(field):
    unsafe = attempt(0, "failed")
    unsafe[field] = SECRET
    assert valid(unsafe) is False
    with pytest.raises(ValueError, match="schema"):
        summarize(records(unsafe))


def test_public_schema_rejects_secret_identity_or_skip_metadata_and_nonfinite_duration():
    for field, value in [
        ("test_id", SECRET),
        ("source_id", SECRET),
        ("project", SECRET),
        ("skip", SECRET),
        ("execution_ms", float("nan")),
    ]:
        unsafe = attempt(0, "passed")
        unsafe[field] = value
        assert valid(unsafe) is False


@pytest.mark.parametrize(
    "field,digits", [("source_sha", 40), ("test_id", 64), ("source_id", 64)]
)
def test_numeric_hash_lookalikes_do_not_bypass_public_identity_types(field, digits):
    unsafe = attempt(0, "passed")
    unsafe[field] = int("1" * digits)
    assert valid(unsafe) is False
    with pytest.raises(ValueError, match="schema"):
        summarize(records(unsafe))


def test_duplicate_or_missing_retry_index_is_not_complete_evidence():
    for values in [
        (attempt(0, "failed"), attempt(0, "passed")),
        (attempt(1, "passed"),),
    ]:
        with pytest.raises(ValueError, match="attempt"):
            summarize(records(*values))


def run_pytest(tmp_path, source, extra_files=None, options=()):
    test_file = tmp_path / "test_synthetic.py"
    test_file.write_text(source)
    for name, content in (extra_files or {}).items():
        (tmp_path / name).write_text(content)
    timing_file = tmp_path / "timing.jsonl"
    env = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
        # These synthetic cases exercise this explicit plugin and pytest's own
        # lifecycle, independently of unrelated installed developer plugins.
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "CI_TELEMETRY_FILE": str(timing_file),
        "CI_TELEMETRY_LANE": "python",
        "CI_TELEMETRY_SHA": "a" * 40,
        "CI_TELEMETRY_RUN_ID": "1",
        "CI_TELEMETRY_RUN_ATTEMPT": "1",
        "SECRET_SENTINEL": SECRET,
    }
    target = tmp_path if extra_files else test_file
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "scripts.ci.pytest_timing",
            *options,
            str(target),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = timing_file.read_text()
    assert SECRET not in output
    return result, [json.loads(line) for line in output.splitlines()]


def test_real_pytest_setup_body_teardown_and_skip_emit_once_without_secrets(tmp_path):
    result, evidence = run_pytest(
        tmp_path,
        f'''
import pytest
import time
@pytest.fixture
def costly():
    time.sleep(0.01)
    yield "{SECRET}"
    time.sleep(0.01)
def test_pass(costly):
    assert costly
def test_fail():
    assert False, "{SECRET}"
@pytest.mark.skip(reason="{SECRET}")
def test_skip():
    pass
''',
    )
    assert result.returncode == 1
    summary = summarize(evidence)
    assert summary["collected"] == summary["attempts"] == 3
    assert (
        summary["attempt_outcomes"]["passed"]
        == summary["attempt_outcomes"]["failed"]
        == summary["attempt_outcomes"]["skipped"]
        == 1
    )
    passed = next(
        value
        for value in evidence
        if value["kind"] == "attempt" and value["outcome"] == "passed"
    )
    assert passed["setup_ms"] >= 10 and passed["teardown_ms"] >= 10
    assert summary["complete"] is True


def test_real_pytest_keyboard_interrupt_preserves_cancelled_and_unstarted(tmp_path):
    result, evidence = run_pytest(
        tmp_path,
        """
def test_pass():
    pass
def test_interrupt():
    raise KeyboardInterrupt()
def test_unstarted():
    pass
""",
    )
    assert result.returncode == 2
    summary = summarize(evidence)
    assert summary["collected"] == summary["attempts"] == 3
    assert summary["attempt_outcomes"]["cancelled"] == 2
    assert summary["complete"] is False


@pytest.mark.parametrize(
    "valid_file,continue_after_error", [(False, False), (True, False), (True, True)]
)
def test_real_pytest_import_failure_is_collection_error_with_no_secret_output(
    tmp_path, valid_file, continue_after_error
):
    extras = {"test_valid.py": "def test_valid():\n    pass\n"} if valid_file else None
    result, evidence = run_pytest(
        tmp_path,
        f"import nonexistent_schemii_telemetry_repro_module  # {SECRET}\n",
        extra_files=extras,
        options=("--continue-on-collection-errors",) if continue_after_error else (),
    )
    assert result.returncode == (1 if continue_after_error else 2)
    summary = summarize(evidence)
    assert summary["outcome"] == "collection-error"
    assert summary["complete"] is False
    assert summary["collected"] == int(valid_file)
    assert summary["attempt_outcomes"]["cancelled"] == 0
    assert summary["attempt_outcomes"]["passed"] == int(continue_after_error)
    assert summary["attempt_outcomes"]["not-run"] == int(
        valid_file and not continue_after_error
    )


def test_real_pytest_stop_after_last_pass_remains_cancelled_and_incomplete(tmp_path):
    result, evidence = run_pytest(
        tmp_path,
        f'''
def test_last_pass(request):
    assert 1 == 1
    request.session.shouldstop = "{SECRET}"
''',
    )
    assert result.returncode == 2
    summary = summarize(evidence)
    assert summary["collected"] == summary["attempt_outcomes"]["passed"] == 1
    assert summary["attempt_outcomes"]["cancelled"] == 0
    assert summary["outcome"] == "cancelled" and summary["complete"] is False


@pytest.mark.parametrize(
    "broken_import,absolute_paths", [(True, False), (True, True), (False, False)]
)
def test_real_node_file_failure_is_incomplete_without_hiding_ordinary_test_failure(
    tmp_path, broken_import, absolute_paths
):
    (tmp_path / "good.mjs").write_text(
        "import {test} from 'node:test'; test('valid', () => {});\n"
    )
    broken = (
        f"import 'nonexistent_schemii_telemetry_repro_module'; // {SECRET}\n"
        if broken_import
        else (
            f"import {{test}} from 'node:test'; test('failure', () => {{ throw Error('{SECRET}'); }});\n"
        )
    )
    (tmp_path / "broken.mjs").write_text(broken)
    output = tmp_path / "node.jsonl"
    paths = [
        str(tmp_path / name) if absolute_paths else name
        for name in ("good.mjs", "broken.mjs")
    ]
    result = subprocess.run(
        [
            "node",
            "--test",
            "--test-reporter=" + str(ROOT / "scripts/ci/node-reporter.mjs"),
            "--test-reporter-destination=" + str(output),
            *paths,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1 and SECRET not in output.read_text()
    summary = summarize([json.loads(line) for line in output.read_text().splitlines()])
    assert summary["collected"] == summary["attempts"] == (1 if broken_import else 2)
    assert summary["attempt_outcomes"]["passed"] == 1
    assert summary["attempt_outcomes"]["failed"] == int(not broken_import)
    assert summary["outcome"] == ("error" if broken_import else "failed")
    assert summary["complete"] is (not broken_import)


def test_workflow_queue_setup_execution_missing_shards_and_secrets():
    run = {
        "created_at": "2026-09-29T10:00:00Z",
        "run_started_at": "2026-09-29T10:00:10Z",
        "secret": SECRET,
    }
    jobs = [
        {
            "name": name,
            "started_at": "2026-09-29T10:00:15Z",
            "completed_at": "2026-09-29T10:00:45Z",
            "conclusion": "success",
            "steps": [
                {
                    "name": "Exercise browser flows",
                    "started_at": "2026-09-29T10:00:30Z",
                    "completed_at": "2026-09-29T10:00:45Z",
                    "body": SECRET,
                }
            ],
        }
        for name in source_job_names("full")
    ]
    summary = workflow_summary(run, jobs)
    assert SECRET not in json.dumps(summary)
    assert summary["complete"] is True
    assert summary["workflow_dispatch_delay_ms"] == 10000
    assert summary["jobs"][0]["after_workflow_start_ms"] == 5000
    assert (
        summary["jobs"][0]["test_steps_ms"]
        == summary["jobs"][0]["setup_and_other_ms"]
        == 15000
    )
    assert summary["critical_path_ms"] == 45000
    assert summary["total_job_minutes"] == 7.5
    missing = workflow_summary(run, jobs[:-1])
    assert missing["complete"] is False and len(missing["missing_jobs"]) == 1
    cancelled = copy.deepcopy(jobs)
    cancelled[0]["conclusion"] = "cancelled"
    assert workflow_summary(run, cancelled)["complete"] is False


WORKFLOW_RUN = {
    "created_at": "2026-09-30T14:05:53Z",
    "run_started_at": "2026-09-30T14:05:53Z",
}


def successful_workflow_jobs(steps=()):
    return [
        {
            "name": name,
            "started_at": "2026-09-30T14:07:57Z",
            "completed_at": "2026-09-30T14:18:05Z",
            "status": "completed",
            "conclusion": "success",
            "steps": copy.deepcopy(list(steps)),
        }
        for name in source_job_names("full")
    ]


def test_completed_job_with_observed_missing_browser_step_ends_keeps_unknowns():
    # Actual retained run36726522134 Android shard2: successful completed job,
    # while Actions still supplied in_progress/pending test step metadata.
    steps = [
        {
            "name": "Start the canonical application stack",
            "started_at": "2026-09-30T14:11:01Z",
            "completed_at": "2026-09-30T14:11:59Z",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "name": "Exercise browser flows",
            "started_at": "2026-09-30T14:11:59Z",
            "completed_at": None,
            "status": "in_progress",
            "conclusion": None,
        },
        {
            "name": "Verify metadata backup recovery in an isolated database",
            "started_at": None,
            "completed_at": None,
            "status": "pending",
            "conclusion": None,
        },
    ]
    jobs = successful_workflow_jobs()
    next(job for job in jobs if job["name"].endswith("(android-chromium, shard 2/6)"))[
        "steps"
    ] = steps
    summary = workflow_summary(WORKFLOW_RUN, jobs)
    job = next(
        item
        for item in summary["jobs"]
        if item["job"] == "browser-android-chromium-2-of-6"
    )
    assert job["wall_ms"] == 608000 and job["startup_ms"] == 58000
    assert job["test_steps_ms"] is None and job["setup_and_other_ms"] is None
    assert job["outcome"] == "success" and summary["complete"] is True
    assert summary["critical_path_ms"] == 732000
    assert summary["total_job_minutes"] == round(15 * 608000 / 60000, 3)


@pytest.mark.parametrize("phase", ["test", "startup"])
@pytest.mark.parametrize(
    "start,end",
    [
        (None, "2026-09-30T14:12:00Z"),
        ("2026-09-30T14:11:59Z", None),
        (None, None),
        ("invalid", "2026-09-30T14:12:00Z"),
        ("2026-09-30T14:11:59Z", "invalid"),
        ("2026-09-30T14:12:00Z", "2026-09-30T14:11:59Z"),
        ("2026-09-30T14:11:59", "2026-09-30T14:12:00Z"),
    ],
)
def test_unknown_named_step_propagates_through_partial_phase_and_residual(
    phase, start, end
):
    steps = [
        {
            "name": "Exercise browser flows",
            "started_at": "2026-09-30T14:11:59Z",
            "completed_at": "2026-09-30T14:12:00Z",
        },
        {
            "name": "Start the canonical application stack",
            "started_at": "2026-09-30T14:11:01Z",
            "completed_at": "2026-09-30T14:11:59Z",
        },
        {
            "name": "Verify metadata backup recovery in an isolated database"
            if phase == "test"
            else "Start the canonical application stack",
            "started_at": start,
            "completed_at": end,
        },
    ]
    summary = workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))
    for job in summary["jobs"]:
        assert job["test_steps_ms"] == (None if phase == "test" else 1000)
        assert job["startup_ms"] == (None if phase == "startup" else 58000)
        assert job["setup_and_other_ms"] is None
        assert job["wall_ms"] == 608000 and job["outcome"] == "success"
    assert summary["complete"] is True


@pytest.mark.parametrize("phase", ["test", "startup"])
def test_genuine_zero_named_step_duration_remains_zero(phase):
    steps = [
        {
            "name": "Exercise browser flows"
            if phase == "test"
            else "Start the canonical application stack",
            "started_at": "2026-09-30T14:11:59Z",
            "completed_at": "2026-09-30T14:11:59Z",
        }
    ]
    summary = workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))
    assert summary["complete"] is True
    for job in summary["jobs"]:
        assert job["test_steps_ms"] == job["startup_ms"] == 0
        assert job["setup_and_other_ms"] == job["wall_ms"] == 608000


@pytest.mark.parametrize(
    "name",
    [
        "Verify metadata backup recovery in an isolated database",
        "Start the canonical application stack",
        "Fast frontend and harness feedback",
    ],
)
def test_explicitly_skipped_conditional_steps_have_zero_cost_without_timestamps(name):
    steps = [
        {
            "name": "Exercise browser flows",
            "started_at": "2026-09-30T14:11:59Z",
            "completed_at": "2026-09-30T14:12:00Z",
        },
        {
            "name": name,
            "status": "completed",
            "conclusion": "skipped",
            "started_at": None,
            "completed_at": None,
        },
    ]
    summary = workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))
    assert summary["complete"] is True
    for job in summary["jobs"]:
        assert job["test_steps_ms"] == 1000 and job["startup_ms"] == 0
        assert job["setup_and_other_ms"] == 607000
        assert job["first_node_feedback_ms"] is None


def test_skipped_node_step_does_not_claim_feedback_even_with_known_end():
    steps = [
        {
            "name": "Fast frontend and harness feedback",
            "status": "completed",
            "conclusion": "skipped",
            "started_at": "2026-09-30T14:08:08Z",
            "completed_at": "2026-09-30T14:08:09Z",
        }
    ]
    for job in workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))["jobs"]:
        assert job["test_steps_ms"] == 0 and job["setup_and_other_ms"] == 608000
        assert job["first_node_feedback_ms"] is None


def test_uninstrumented_jobs_ignore_unknown_unrelated_steps():
    summary = workflow_summary(
        WORKFLOW_RUN,
        successful_workflow_jobs(
            [
                {"name": "Set up job", "started_at": None, "completed_at": None},
            ]
        ),
    )
    assert summary["complete"] is True
    for job in summary["jobs"]:
        assert job["test_steps_ms"] == job["startup_ms"] == 0
        assert job["setup_and_other_ms"] == 608000


def test_known_independent_feedback_and_phase_durations_survive_missing_start():
    steps = [
        {
            "name": "Fast frontend and harness feedback",
            "started_at": None,
            "completed_at": "2026-09-30T14:08:09Z",
        },
        {
            "name": "Start the canonical application stack",
            "started_at": "2026-09-30T14:11:01Z",
            "completed_at": "2026-09-30T14:11:59Z",
        },
    ]
    summary = workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))
    assert summary["complete"] is True
    for job in summary["jobs"]:
        assert job["first_node_feedback_ms"] == 12000 and job["startup_ms"] == 58000
        assert job["test_steps_ms"] is None and job["setup_and_other_ms"] is None


def test_known_phases_exceeding_job_wall_leave_setup_residual_unknown():
    steps = [
        {
            "name": "Exercise browser flows",
            "started_at": "2026-09-30T14:07:57Z",
            "completed_at": "2026-09-30T14:18:06Z",
        }
    ]
    summary = workflow_summary(WORKFLOW_RUN, successful_workflow_jobs(steps))
    for job in summary["jobs"]:
        assert job["test_steps_ms"] == 609000 and job["wall_ms"] == 608000
        assert job["setup_and_other_ms"] is None


def test_reversed_job_interval_is_unknown_and_cannot_establish_completeness():
    jobs = successful_workflow_jobs()
    jobs[0]["completed_at"] = "2026-09-30T14:07:56Z"
    summary = workflow_summary(WORKFLOW_RUN, jobs)
    job = next(
        item
        for item in summary["jobs"]
        if item["job"] == source_job_names("full")[jobs[0]["name"]]
    )
    assert job["wall_ms"] is None and job["setup_and_other_ms"] is None
    assert summary["total_job_minutes"] is None and summary["complete"] is False
    assert summary["critical_path_ms"] == 732000


@pytest.mark.parametrize(
    "start,end", [(None, 1), (1, None), (2, 1), (float("nan"), 1), (1, float("inf"))]
)
def test_invalid_intervals_are_unknown_instead_of_clamped_to_zero(start, end):
    assert duration(start, end) is None


def test_timestamp_requires_timezone_and_preserves_equivalent_offsets():
    assert timestamp("2026-09-30T14:11:59") is None
    assert timestamp("2026-09-30T14:11:59Z") == timestamp("2026-09-30T15:11:59+01:00")
    assert duration(1, 1) == 0


def test_missing_or_mixed_source_evidence_cannot_establish_complete_run(tmp_path):
    assert collect_evidence(tmp_path)["complete"] is False
    assert len(collect_evidence(tmp_path)["missing_lanes"]) == 15
    expected = [
        ("node", "none", 0),
        ("python", "none", 0),
        ("postgres", "none", 0),
        *(
            ("browser", project, shard)
            for project in ("desktop-chromium", "android-chromium")
            for shard in (1, 2, 3, 4, 5, 6)
        ),
    ]
    for index, (lane, project, shard) in enumerate(expected):
        values = records(attempt(0, "passed"))
        for value in values:
            value.update(lane=lane, project=project, shard=shard)
        (tmp_path / f"lane-{index}.jsonl").write_text(
            "\n".join(json.dumps(value) for value in values)
        )
    assert collect_evidence(tmp_path)["complete"] is True
    path = tmp_path / "lane-6.jsonl"
    path.write_text(
        path.read_text().replace(
            '"source_sha": "' + "a" * 40, '"source_sha": "' + "b" * 40
        )
    )
    assert collect_evidence(tmp_path)["complete"] is False


@pytest.mark.parametrize(
    "field,current", [("source_sha", "b" * 40), ("run_id", 2), ("run_attempt", 2)]
)
def test_one_complete_but_stale_cohort_cannot_supply_current_attempt_evidence(
    tmp_path, field, current
):
    expected = [
        ("node", "none", 0),
        ("python", "none", 0),
        ("postgres", "none", 0),
        *(
            ("browser", project, shard)
            for project in ("desktop-chromium", "android-chromium")
            for shard in (1, 2, 3, 4, 5, 6)
        ),
    ]
    for index, (lane, project, shard) in enumerate(expected):
        values = records(attempt(0, "passed"))
        for value in values:
            value.update(lane=lane, project=project, shard=shard)
        (tmp_path / f"lane-{index}.jsonl").write_text(
            "\n".join(json.dumps(value) for value in values)
        )
    identity = {key: META[key] for key in ("source_sha", "run_id", "run_attempt")}
    assert collect_evidence(tmp_path, identity=identity)["complete"] is True
    assert collect_evidence(tmp_path)["complete"] is True
    stale = collect_evidence(tmp_path, identity={**identity, field: current})
    assert stale["complete"] is False
    assert (
        stale["first_attempt_passes"] == 15
    )  # Retain observations without calling them current acceptance.


def scoped_workflow_records(profile, key):
    inventory = expected_scope(profile, key)
    template = records(attempt(0, "passed"))
    for value in template:
        value.update(lane=key[0], project=key[1], shard=key[2])
    if inventory is None:
        return template
    meta = {field: template[0][field] for field in META}
    return [
        {**meta, "kind": "start", "planned": len(inventory)},
        *({**meta, "kind": "plan", "test_id": test} for test in inventory),
        *(
            {
                **template[2],
                "test_id": test,
                "source_id": owner["source_id"],
                "outcome": "skipped" if owner["allow_skip"] else "passed",
                "skip": "declared-or-runtime" if owner["allow_skip"] else "none",
            }
            for test, owner in inventory.items()
        ),
        template[-1],
    ]


def write_scoped_workflow_evidence(root, profile):
    for key in expected_lanes(profile):
        (root / f"{key[0]}-{key[1]}-{key[2]}.jsonl").write_text(
            "\n".join(
                json.dumps(value) for value in scoped_workflow_records(profile, key)
            )
        )


def scoped_workflow_gate(profile, evidence):
    identity = {
        **{field: META[field] for field in ("source_sha", "run_id", "run_attempt")},
        "head_sha": "b" * 40,
    }
    classification = {
        "valid": True,
        "lane": "source",
        "profile": profile,
        "reason": "verified-owned-pr-change",
        "head": identity["head_sha"],
    }
    needs = {
        **{name: {"result": "success"} for name in CONTROL_NEEDS},
        **{
            name: {
                "result": "success" if name in required_needs(profile) else "skipped"
            }
            for name in SOURCE_NEEDS
        },
    }
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success" if name in expected_jobs(profile) else "skipped",
            "started_at": "2026-09-30T00:00:02Z",
            "completed_at": "2026-09-30T00:00:10Z",
            **{
                field: identity[field]
                for field in ("head_sha", "run_id", "run_attempt")
            },
        }
        for name in source_job_names(profile)
    ]
    timing = {
        **workflow_summary(
            {"created_at": "2026-09-30T00:00:00Z"}, jobs, profile=profile
        ),
        **identity,
        "test_evidence": evidence,
    }
    return evaluate(classification, needs, jobs, timing, identity)


@pytest.mark.parametrize("profile", [INSPECTION_PROFILE, CACHE_PROFILE])
@pytest.mark.parametrize("outcome", ["failed", "recovered", "skipped"])
def test_rejected_scoped_lane_retains_original_reliability_and_gate_rejection(
    tmp_path, profile, outcome
):
    write_scoped_workflow_evidence(tmp_path, profile)
    identity = {field: META[field] for field in ("source_sha", "run_id", "run_attempt")}
    baseline = collect_evidence(tmp_path, profile=profile, identity=identity)
    assert baseline["complete"] and scoped_workflow_gate(profile, baseline)[0]
    path = tmp_path / "browser-android-chromium-1.jsonl"
    original = [json.loads(line) for line in path.read_text().splitlines()]
    first = next(
        value
        for value in original
        if value["kind"] == "attempt" and value["outcome"] == "passed"
    )
    first["outcome"] = "skipped" if outcome == "skipped" else "failed"
    if outcome == "skipped":
        first["skip"] = "declared-or-runtime"
    elif outcome == "recovered":
        original.insert(-1, {**first, "attempt": 1, "outcome": "passed"})
    else:
        original[-1]["outcome"] = "failed"
    path.write_text("\n".join(json.dumps(value) for value in original))

    evidence = collect_evidence(tmp_path, profile=profile, identity=identity)
    assert evidence["complete"] is False and evidence["invalid_lanes"] == 1
    assert evidence["missing_lanes"] == []
    retained = next(
        value
        for value in evidence["lanes"]
        if (value["lane"], value["project"], value["shard"])
        == ("browser", "android-chromium", 1)
    )
    expected = summarize(original)
    fields = (
        "first_attempt_eligible",
        "first_attempt_passes",
        "first_attempt_failures",
        "retry_recovered",
    )
    assert {field: retained[field] for field in fields} == {
        field: expected[field] for field in fields
    }
    assert retained["scope"]["observed"] == [
        {
            field: value[field]
            for field in ("test_id", "source_id", "outcome", "attempt")
        }
        for value in original
        if value["kind"] == "attempt"
    ]
    raw_totals = {
        field: sum(
            summarize([json.loads(line) for line in receipt.read_text().splitlines()])[
                field
            ]
            for receipt in tmp_path.glob("*.jsonl")
        )
        for field in fields
    }
    assert {field: evidence[field] for field in fields} == raw_totals
    assert evidence["first_attempt_pass_rate"] == (
        raw_totals["first_attempt_passes"] / raw_totals["first_attempt_eligible"]
    )
    assert not scoped_workflow_gate(profile, evidence)[0]
    forged = copy.deepcopy(evidence)
    forged["complete"] = True
    for value in forged["lanes"]:
        value.update(outcome="passed", first_attempt_failures=0, retry_recovered=0)
    assert not scoped_workflow_gate(profile, forged)[0]


@pytest.mark.parametrize(
    "damage",
    [
        "invalid-schema",
        "mixed-record-identity",
        "stale-cohort",
        "duplicate",
        "unsupported",
        "missing-end",
    ],
)
def test_scope_reliability_retention_keeps_invalid_evidence_rejection(tmp_path, damage):
    profile = CACHE_PROFILE
    write_scoped_workflow_evidence(tmp_path, profile)
    identity = {field: META[field] for field in ("source_sha", "run_id", "run_attempt")}
    baseline = collect_evidence(tmp_path, profile=profile, identity=identity)
    path = tmp_path / "browser-android-chromium-1.jsonl"
    original = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "invalid-schema":
        original[0]["secret"] = SECRET
    elif damage == "mixed-record-identity":
        original[1]["source_sha"] = "c" * 40
    elif damage == "stale-cohort":
        for value in original:
            value["source_sha"] = "c" * 40
    elif damage == "unsupported":
        for value in original:
            value["shard"] = 0
    elif damage == "duplicate":
        (tmp_path / "duplicate.jsonl").write_text(path.read_text())
    else:
        original.pop()
    path.write_text("\n".join(json.dumps(value) for value in original))
    evidence = collect_evidence(tmp_path, profile=profile, identity=identity)
    assert evidence["complete"] is False
    assert not scoped_workflow_gate(profile, evidence)[0]
    if damage in {"invalid-schema", "mixed-record-identity", "unsupported"}:
        assert evidence["invalid_lanes"] == 1
        assert "browser-android-chromium-1" in evidence["missing_lanes"]
    elif damage == "duplicate":
        assert evidence["invalid_lanes"] == 1
        assert evidence["first_attempt_eligible"] == baseline["first_attempt_eligible"]
    else:
        assert evidence["invalid_lanes"] == 0
        assert evidence["missing_lanes"] == []


def test_real_playwright_runner_retry_pass_failure_skip_and_safe_public_output(
    tmp_path,
):
    if not (ROOT / "node_modules/@playwright/test/cli.js").exists():
        pytest.skip(
            "Playwright reporter verification needs npm ci; no application or browser is started"
        )
    config = tmp_path / "playwright.config.mjs"
    config.write_text(
        "export default "
        + json.dumps(
            {
                "testDir": str(tmp_path),
                "workers": 1,
                "retries": 1,
                "outputDir": str(tmp_path / "results"),
                "reporter": [[str(ROOT / "scripts/ci/playwright-reporter.mjs")]],
                "projects": [{"name": "desktop-chromium"}],
                "shard": {"current": 1, "total": 1},
            }
        )
        + ";"
    )
    (tmp_path / "synthetic.spec.mjs").write_text(f"""
import {{ test, expect }} from '{ROOT}/node_modules/@playwright/test/index.mjs';
test.beforeEach(async () => {{ await new Promise(resolve => setTimeout(resolve, 10)); }});
test.afterEach(async () => {{ await new Promise(resolve => setTimeout(resolve, 10)); }});
test('retry {SECRET}', async ({{}}, info) => {{ expect(info.retry, '{SECRET}').toBe(1); }});
test('pass {SECRET}', async () => {{ expect(1).toBe(1); }});
test('fail {SECRET}', async () => {{ expect(false, '{SECRET}').toBe(true); }});
test.skip('skip {SECRET}', async () => {{}});
""")
    output = tmp_path / "browser.jsonl"
    env = {**os.environ, "CI_TELEMETRY_FILE": str(output)}
    result = subprocess.run(
        [
            "node",
            str(ROOT / "node_modules/@playwright/test/cli.js"),
            "test",
            "--config",
            str(config),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert SECRET not in output.read_text()
    summary = summarize([json.loads(line) for line in output.read_text().splitlines()])
    assert summary["complete"] is True
    assert summary["collected"] == 4 and summary["attempts"] == 6
    assert summary["retry_recovered"] == 1
    assert (
        summary["first_attempt_eligible"] == 3 and summary["first_attempt_passes"] == 1
    )
    assert (
        summary["attempt_outcomes"]["failed"] == 3
        and summary["attempt_outcomes"]["skipped"] == 1
    )
    assert summary["phase_totals_ms"]["setup_ms"] >= 50
    assert summary["phase_totals_ms"]["teardown_ms"] >= 50


def test_real_playwright_global_teardown_error_keeps_passing_receipt_but_is_incomplete(
    tmp_path,
):
    if not (ROOT / "node_modules/@playwright/test/cli.js").exists():
        pytest.skip(
            "Playwright reporter verification needs npm ci; no application or browser is started"
        )
    teardown = tmp_path / "global-teardown.mjs"
    teardown.write_text(
        f"export default async () => {{ throw new Error('{SECRET}'); }};\n"
    )
    config = tmp_path / "playwright.config.mjs"
    config.write_text(
        "export default "
        + json.dumps(
            {
                "testDir": str(tmp_path),
                "outputDir": str(tmp_path / "results"),
                "workers": 1,
                "globalTeardown": str(teardown),
                "reporter": [[str(ROOT / "scripts/ci/playwright-reporter.mjs")]],
                "projects": [{"name": "desktop-chromium"}],
                "shard": {"current": 1, "total": 1},
            }
        )
        + ";"
    )
    (tmp_path / "synthetic.spec.mjs").write_text(f"""
import {{ test, expect }} from '{ROOT}/node_modules/@playwright/test/index.mjs';
test('valid', async () => {{ expect(1).toBe(1); }});
""")
    output = tmp_path / "browser.jsonl"
    result = subprocess.run(
        [
            "node",
            str(ROOT / "node_modules/@playwright/test/cli.js"),
            "test",
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        env={**os.environ, "CI_TELEMETRY_FILE": str(output)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and SECRET not in output.read_text()
    summary = summarize([json.loads(line) for line in output.read_text().splitlines()])
    assert summary["attempts"] == summary["attempt_outcomes"]["passed"] == 1
    assert summary["outcome"] == "error" and summary["complete"] is False


def inspection_runner():
    spec = importlib.util.spec_from_file_location(
        "inspection_python_runner", ROOT / "scripts/ci/python-tests.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "selectors,addopts",
    [
        (INSPECTION_PYTHON[:-1], ""),
        ((*INSPECTION_PYTHON[:-1], INSPECTION_PYTHON[-1] + "::test_filtered"), ""),
        ((*INSPECTION_PYTHON, "-k", "one"), ""),
        (INSPECTION_PYTHON, "-k one"),
        (INSPECTION_PYTHON, "--deselect=tests/test_source_inspection.py::test_new"),
    ],
)
def test_inspection_python_runner_rejects_filtered_or_partial_acceptance_before_execution(
    tmp_path, monkeypatch, selectors, addopts
):
    module = inspection_runner()

    def forbidden(*args, **kwargs):
        raise AssertionError("Filtered selection started a child")

    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    assert (
        module.run(
            selectors,
            root=tmp_path,
            environment={
                "CI_TEST_PROFILE": INSPECTION_PROFILE,
                "PYTEST_ADDOPTS": addopts,
            },
        )
        == 4
    )


def test_inspection_python_runner_keeps_installed_four_and_whole_dynamic_helper_groups(
    tmp_path,
):
    module = inspection_runner()
    for file in INSPECTION_PYTHON:
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_owned_whole_file(): pass\n")
    groups = module.partitions(tmp_path, INSPECTION_PYTHON)
    assert len(groups) == 2 and set(groups[0]) == set(module.INSPECTION)
    assert INSPECTION_PYTHON[0] in groups[1]
    selected = [
        item for group in groups for item in group if not item.startswith("--ignore=")
    ]
    assert sorted(selected) == sorted(INSPECTION_PYTHON) and all(
        "::" not in item for item in selected
    )


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped"])
def test_inspection_python_runner_retains_complete_original_evidence_before_acceptance(
    tmp_path, monkeypatch, outcome
):
    module = inspection_runner()
    environment = {
        "CI_TEST_PROFILE": INSPECTION_PROFILE,
        "CI_TELEMETRY_FILE": str(tmp_path / "published.jsonl"),
        "CI_TELEMETRY_SHA": "a" * 40,
        "CI_TELEMETRY_RUN_ID": "1",
        "CI_TELEMETRY_RUN_ATTEMPT": "1",
    }
    meta = module.expected_metadata(environment)
    inventory = expected_scope(INSPECTION_PROFILE, ("python", "none", 0))
    extra = hashlib.sha256(
        b"tests/test_source_inspection.py::test_new_case"
    ).hexdigest()
    inventory[extra] = {
        "source_id": hashlib.sha256(INSPECTION_TEST.encode()).hexdigest(),
        "allow_skip": False,
    }
    planned = sorted(inventory)
    original = [
        {**meta, "kind": "start", "planned": len(planned)},
        *({**meta, "kind": "plan", "test_id": test} for test in planned),
        *(
            {
                **meta,
                "kind": "attempt",
                "test_id": test,
                "source_id": inventory[test]["source_id"],
                "source_line": 1,
                "attempt": 0,
                "outcome": outcome if test == extra else "passed",
                "skip": "declared-or-runtime"
                if test == extra and outcome == "skipped"
                else "none",
                "setup_ms": 1,
                "execution_ms": 1,
                "teardown_ms": 1,
            }
            for test in planned
        ),
        {
            **meta,
            "kind": "end",
            "outcome": "failed" if outcome == "failed" else "passed",
            "wall_ms": 0,
        },
    ]
    original_jsonl = "".join(
        json.dumps(value, separators=(",", ":")) + "\n" for value in original
    )
    private_directories = []

    def completed_partition(*args, **kwargs):
        receipt = Path(kwargs["env"]["CI_TELEMETRY_FILE"])
        private_directories.append(receipt.parent)
        receipt.write_text(original_jsonl)
        return SimpleNamespace(pid=123456)

    monkeypatch.setattr(module, "partitions", lambda root, selectors: [list(selectors)])
    monkeypatch.setattr(module.subprocess, "Popen", completed_partition)
    monkeypatch.setattr(module, "birth_tick", lambda pid: None)
    monkeypatch.setattr(module.os, "waitid", lambda *args: object())
    monkeypatch.setattr(
        module, "stop_children", lambda children, requested: [int(outcome == "failed")]
    )
    monkeypatch.setattr(module.time, "perf_counter", lambda: 0)
    status = module.run(
        INSPECTION_PYTHON,
        root=tmp_path,
        environment=environment,
        temporary_parent=tmp_path,
    )

    output = Path(environment["CI_TELEMETRY_FILE"])
    assert output.exists(), "A complete original receipt was discarded"
    assert [json.loads(line) for line in output.read_text().splitlines()] == original
    assert output.read_text().splitlines()[1:-1] == original_jsonl.splitlines()[1:-1]
    assert status == (0 if outcome == "passed" else 1)
    assert summarize(original)["complete"] is True
    assert private_directories and all(
        not directory.exists() for directory in private_directories
    )
