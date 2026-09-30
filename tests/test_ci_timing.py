"""Public timing must detect retries/incompleteness without copying fixture secrets."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci.summary import summarize, valid
from scripts.ci.workflow_timing import (
    JOB_NAMES,
    summarize as workflow_summary,
    test_evidence as collect_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
SECRET = "PLANTED_PASSWORD_TOKEN_AUTHORIZATION_cookie_7ce19"
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
        for name in JOB_NAMES
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
    assert summary["total_job_minutes"] == 3.5
    missing = workflow_summary(run, jobs[:-1])
    assert missing["complete"] is False and len(missing["missing_jobs"]) == 1
    cancelled = copy.deepcopy(jobs)
    cancelled[0]["conclusion"] = "cancelled"
    assert workflow_summary(run, cancelled)["complete"] is False


def test_missing_or_mixed_source_evidence_cannot_establish_complete_run(tmp_path):
    assert collect_evidence(tmp_path)["complete"] is False
    assert len(collect_evidence(tmp_path)["missing_lanes"]) == 7
    expected = [
        ("node", "none", 0),
        ("python", "none", 0),
        ("postgres", "none", 0),
        *(
            ("browser", project, shard)
            for project in ("desktop-chromium", "android-chromium")
            for shard in (1, 2)
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
            for shard in (1, 2)
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
        stale["first_attempt_passes"] == 7
    )  # Retain observations without calling them current acceptance.


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
