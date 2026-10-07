"""Local CI acceptance controls, using disposable Git source and child processes."""

import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import time

import pytest

from scripts.ci import local_ci as runner


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Local CI test")
    git(tmp_path, "config", "user.email", "local-ci@example.invalid")
    (tmp_path / ".gitignore").write_text(".schemii/\n")
    (tmp_path / "source.txt").write_text("original source\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "source")
    for name in runner.SCOPE_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "SCHEMII_TEST_METADATA_DSN",
        "SCHEMII_TEST_METADATA_PASSWORD",
        "SCHEMII_E2E_BASE_URL",
        "SCHEMII_TEST_APP_PORT",
        "SCHEMII_E2E_BOOTSTRAP",
        "SCHEMII_E2E_CREDENTIALS_FILE",
        "SCHEMII_E2E_USERNAME",
        "SCHEMII_E2E_PASSWORD",
        "SCHEMII_CI_QUALITY_PYTHON",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def selected(root, profile="frontend-tests", commands=None):
    return {
        "classification": {
            "valid": True,
            "profile": profile,
            "base": git(root, "rev-parse", "HEAD"),
        },
        "source": runner.source_identity(root),
        "layers": sorted(runner.layers(profile)),
        "commands": commands
        if commands is not None
        else runner.commands(profile, "HEAD"),
    }


def records(environment, lane=None, outcome="passed", skipped=False):
    meta = {
        "schema": 1,
        "source_sha": environment["CI_TELEMETRY_SHA"],
        "run_id": int(environment["CI_TELEMETRY_RUN_ID"]),
        "run_attempt": 1,
        "lane": lane or environment["CI_TELEMETRY_LANE"],
        "project": environment["CI_TELEMETRY_PROJECT"],
        "shard": int(environment["CI_TELEMETRY_SHARD"]),
    }
    test_id = hashlib.sha256(b"controlled test").hexdigest()
    return [
        {**meta, "kind": "start", "planned": 1},
        {**meta, "kind": "plan", "test_id": test_id},
        {
            **meta,
            "kind": "attempt",
            "test_id": test_id,
            "source_id": hashlib.sha256(b"source.txt").hexdigest(),
            "source_line": 1,
            "attempt": 0,
            "outcome": "skipped"
            if skipped
            else "passed"
            if outcome == "passed"
            else "failed",
            "skip": "declared-or-runtime" if skipped else "none",
            "setup_ms": 1,
            "execution_ms": 2,
            "teardown_ms": 1,
        },
        {**meta, "kind": "end", "outcome": outcome, "wall_ms": 4},
    ]


def controlled(
    monkeypatch,
    *,
    code=0,
    missing=False,
    mutate=False,
    mismatched=False,
    skipped=False,
    recovered=False,
):
    original = runner.invoke
    seen = []

    def invoke(root, argv, environment, lease=None):
        seen.append(argv)
        payload = (
            records(
                environment,
                outcome="passed" if code == 0 else "failed",
                skipped=skipped,
            )
            if environment.get("CI_TELEMETRY_FILE")
            else None
        )
        if mismatched:
            for record in payload:
                record["source_sha"] = "0" * 40
        if payload and recovered:
            payload[2]["outcome"] = "failed"
            payload.insert(-1, {**payload[2], "attempt": 1, "outcome": "passed"})
        body = "import json, os\nfrom pathlib import Path\n"
        if payload and not missing:
            body += f"Path(os.environ['CI_TELEMETRY_FILE']).write_text('\\n'.join(json.dumps(value) for value in {payload!r}) + '\\n')\n"
        if mutate:
            body += "Path('source.txt').write_text('changed during check')\n"
        body += f"raise SystemExit({code})\n"
        return original(root, [sys.executable, "-c", body], environment)

    monkeypatch.setattr(runner, "invoke", invoke)
    return seen


def execute(root, plan=None, feedback=False):
    code, path = runner.run_selected(
        root, plan or selected(root), feedback=feedback, base="HEAD"
    )
    return code, path, json.loads(path.read_text())


def test_selected_acceptance_has_unique_private_bound_receipts(repository, monkeypatch):
    controlled(monkeypatch)
    code, first_path, receipt = execute(repository)
    assert code == 0 and receipt["acceptance"] is True
    assert receipt["source"] == runner.source_identity(repository)
    assert receipt["pending_commands"] == receipt["pending_layers"] == []
    assert receipt["commands"][0]["duration_ms"] > 0
    summary = first_path.parent / "command-00/timing.summary.json"
    assert json.loads(summary.read_text())["complete"] is True
    code, second_path, _ = execute(repository)
    assert code == 0 and first_path != second_path
    for path in (first_path.parent.parent, first_path.parent, summary.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    for path in first_path.parent.rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert git(repository, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "profile,commands,pending",
    [
        ("frontend-tests", [["npm", "test"]], []),
        (
            "full",
            [
                ["npm", "test"],
                runner.planner.POSTGRES_COMMAND,
                runner.planner.BROWSER_BOUNDARY,
                ["./start.sh"],
            ],
            ["browser", "postgres"],
        ),
    ],
)
def test_feedback_never_proves_acceptance_even_when_all_selected_checks_pass(
    repository, monkeypatch, profile, commands, pending
):
    seen = controlled(monkeypatch)
    code, _, receipt = execute(
        repository, selected(repository, profile, commands), feedback=True
    )
    assert code == 0 and receipt["status"] == "feedback-passed"
    assert receipt["acceptance"] is False
    assert receipt["pending_layers"] == pending
    assert seen == [["npm", "test"]]


def test_failed_child_stops_and_retains_failed_attempt(repository, monkeypatch):
    seen = controlled(monkeypatch, code=7)
    code, path, receipt = execute(
        repository,
        selected(
            repository, "full", [["npm", "test"], runner.planner.POSTGRES_COMMAND]
        ),
    )
    assert code == 7 and receipt["acceptance"] is False
    assert receipt["status"] == "failed"
    assert seen == [["npm", "test"]]
    assert receipt["commands"][0]["exit_code"] == 7
    assert receipt["commands"][1]["status"] == "pending"
    assert (
        '"outcome": "failed"' in (path.parent / "command-00/timing.jsonl").read_text()
    )


@pytest.mark.parametrize("option", ["missing", "mismatched", "skipped"])
def test_zero_child_exit_without_usable_evidence_is_incomplete(
    repository, monkeypatch, option
):
    controlled(monkeypatch, **{option: True})
    code, _, receipt = execute(repository)
    assert code == 1 and receipt["acceptance"] is False
    assert receipt["status"] == "incomplete"
    assert receipt["commands"][0]["exit_code"] == 0
    assert receipt["pending_layers"] == ["node"]


@pytest.mark.parametrize("when", ["since-planning", "during-command"])
def test_changed_source_cannot_supply_acceptance(repository, monkeypatch, when):
    plan = selected(repository)
    seen = controlled(monkeypatch, mutate=when == "during-command")
    if when == "since-planning":
        (repository / "source.txt").write_text("changed before execution")
    code, _, receipt = execute(repository, plan)
    assert code == 1 and receipt["status"] == "source-changed"
    assert receipt["acceptance"] is False
    assert bool(seen) == (when == "during-command")


def test_fingerprint_covers_hidden_edits_untracked_files_and_index_state(repository):
    original = runner.source_identity(repository)
    git(repository, "update-index", "--assume-unchanged", "source.txt")
    (repository / "source.txt").write_text("hidden content change")
    hidden = runner.source_identity(repository)
    assert hidden["dirty"] is False
    assert hidden["fingerprint"] != original["fingerprint"]
    (repository / "untracked.py").write_text("new content")
    untracked = runner.source_identity(repository)
    assert untracked["fingerprint"] != hidden["fingerprint"]
    git(repository, "add", "untracked.py")
    staged = runner.source_identity(repository)
    assert staged["fingerprint"] == untracked["fingerprint"]
    assert staged["index_fingerprint"] != untracked["index_fingerprint"]


@pytest.mark.parametrize("variable", runner.SCOPE_ENV)
def test_ambient_scope_overrides_cannot_be_accepted(repository, monkeypatch, variable):
    seen = controlled(monkeypatch)
    monkeypatch.setenv(variable, "private-scope-sentinel")
    code, path, receipt = execute(repository)
    assert code == 2 and receipt["reason"] == "ambient-scope-override"
    assert receipt["acceptance"] is False and seen == []
    assert "private-scope-sentinel" not in path.read_text()


@pytest.mark.parametrize(
    "provided", ["SCHEMII_TEST_METADATA_DSN", "SCHEMII_TEST_METADATA_PASSWORD"]
)
def test_one_postgres_prerequisite_cannot_turn_skips_into_acceptance(
    repository, monkeypatch, provided
):
    monkeypatch.setenv(provided, "private-database-sentinel")
    seen = controlled(monkeypatch)
    code, path, receipt = execute(
        repository,
        selected(
            repository, "full", [["npm", "test"], runner.planner.POSTGRES_COMMAND]
        ),
    )
    assert code == 2 and receipt["status"] == "pending"
    assert receipt["commands"][0]["status"] == "passed"
    assert receipt["commands"][1]["status"] == "pending"
    assert seen == [["npm", "test"]]
    assert "private-database-sentinel" not in path.read_text()


def test_real_postgres_skip_cannot_supply_acceptance(repository, monkeypatch):
    monkeypatch.setenv("SCHEMII_TEST_METADATA_DSN", "owned-disposable-db")
    monkeypatch.setenv("SCHEMII_TEST_METADATA_PASSWORD", "private-db-password")
    controlled(monkeypatch, skipped=True)
    code, path, receipt = execute(
        repository, selected(repository, "full", [runner.planner.POSTGRES_COMMAND])
    )
    assert code == 1 and receipt["status"] == "incomplete"
    assert receipt["acceptance"] is False
    assert "private-db-password" not in path.read_text()


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {
            "SCHEMII_E2E_BASE_URL": "https://example.invalid",
            "SCHEMII_E2E_BOOTSTRAP": "1",
        },
        {"SCHEMII_TEST_APP_PORT": "9000", "SCHEMII_E2E_BOOTSTRAP": "1"},
    ],
)
def test_browser_prerequisites_block_before_install_or_launcher(
    repository, monkeypatch, environment
):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    seen = controlled(monkeypatch)
    code, _, receipt = execute(
        repository,
        selected(
            repository,
            "e2e-tests",
            [["npm", "test"], runner.planner.BROWSER_BOUNDARY, ["./start.sh"]],
        ),
    )
    assert code == 2 and receipt["status"] == "pending"
    assert receipt["pending_layers"] == ["browser"]
    assert seen == [["npm", "test"]]


def test_occupied_deployment_lease_blocks_browser_before_startup(
    repository, monkeypatch
):
    monkeypatch.setenv("SCHEMII_E2E_BOOTSTRAP", "1")
    seen = controlled(monkeypatch)
    with runner.deployment_lease(repository):
        code, _, receipt = execute(
            repository,
            selected(
                repository,
                "e2e-tests",
                [runner.planner.BROWSER_BOUNDARY, ["./start.sh"]],
            ),
        )
    assert code == 2 and receipt["reason"] == "deployment-leased"
    assert receipt["acceptance"] is False and seen == []


def test_launcher_receives_actual_owned_lease_descriptors(repository):
    launcher = repository / "start.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\nreadlink /proc/$$/fd/3 > .schemii/lease-observed\nreadlink /proc/$$/fd/4 > .schemii/gate-observed\n"
    )
    launcher.chmod(0o755)
    (repository / ".schemii").mkdir()
    with runner.deployment_lease(repository) as descriptors:
        assert (
            runner.invoke(repository, ["./start.sh"], dict(os.environ), descriptors)
            == 0
        )
    common = Path(
        git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    assert (repository / ".schemii/lease-observed").read_text().strip() == str(
        common / "qa-deployment.lock"
    )
    assert (repository / ".schemii/gate-observed").read_text().strip() == str(
        common / "qa-startup.lock"
    )


def test_backups_use_new_run_directory_and_hold_lease(repository, monkeypatch):
    monkeypatch.setenv("SCHEMII_E2E_BOOTSTRAP", "1")
    seen = []

    def invoke(root, command, environment, lease):
        assert lease is not None
        with pytest.raises(BlockingIOError), runner.deployment_lease(root):
            pass
        seen.append(command)
        return 0

    monkeypatch.setattr(runner, "invoke", invoke)
    monkeypatch.setattr(
        runner, "verify_deployment", lambda: [{"url": runner.LOCAL_URL, "status": 200}]
    )
    plan_commands = [
        runner.planner.BROWSER_BOUNDARY,
        ["./start.sh"],
        ["./start.sh", "--backup", ".schemii/test-selection/recovery"],
        ["./start.sh", "--verify-backup", ".schemii/test-selection/recovery"],
    ]
    paths = []
    for _ in range(2):
        code, path, receipt = execute(
            repository, selected(repository, "full", plan_commands)
        )
        assert code == 0 and receipt["acceptance"] is True
        paths.append(path)
        assert seen[-2][2] == seen[-1][2] == str(path.parent / "recovery")
    assert paths[0] != paths[1]
    assert (repository / "source.txt").read_text() == "original source\n"


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists()


def test_normal_child_exit_cleans_owned_orphan_without_harming_peer(repository):
    marker = repository / ".schemii/child.pid"
    marker.parent.mkdir()
    peer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        body = "import subprocess, sys; from pathlib import Path; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); Path('.schemii/child.pid').write_text(str(child.pid))"
        assert (
            runner.invoke(repository, [sys.executable, "-c", body], dict(os.environ))
            == 0
        )
        pid = int(marker.read_text())
        path = Path(f"/proc/{pid}/stat")
        assert not path.exists() or path.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        assert peer.poll() is None
    finally:
        peer.terminate()
        peer.wait(timeout=5)


def test_interrupt_retains_cancelled_receipt_and_cleans_owned_process(repository):
    marker = repository / ".schemii/active.pid"
    runner_root = Path(runner.__file__).resolve().parents[2]
    body = f"""
import sys
sys.path.insert(0, {str(runner_root)!r})
from pathlib import Path
from scripts.ci import local_ci as runner
root = Path({str(repository)!r})
selected = {{'classification': {{'valid': True, 'profile': 'frontend-tests', 'base': runner.source_identity(root)['commit']}}, 'source': runner.source_identity(root), 'layers': ['node'], 'commands': [['npm', 'test']]}}
original = runner.invoke
def invoke(root, argv, environment, lease=None):
    command = "import os, time; from pathlib import Path; Path('.schemii/active.pid').write_text(str(os.getpid())); time.sleep(30)"
    return original(root, [sys.executable, '-c', command], environment)
runner.invoke = invoke
code, receipt = runner.run_selected(root, selected, feedback=False, base='HEAD')
raise SystemExit(code)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", body], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        wait_for(marker)
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 143, (stdout, stderr)
        receipt_path = next(
            (repository / ".schemii/local-ci").glob("run-*/receipt.json")
        )
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "cancelled" and receipt["acceptance"] is False
        assert receipt["commands"][0]["status"] == "cancelled"
        assert receipt["commands"][0]["duration_ms"] > 0
        assert not Path(f"/proc/{marker.read_text()}").exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_forced_full_reuses_existing_complete_planner_topology(repository, monkeypatch):
    monkeypatch.setattr(runner.planner, "plan", lambda root, base: selected(root))
    plan = runner.selected_plan(repository, "HEAD", full=True)
    assert plan["commands"] == runner.commands("full", "HEAD")
    assert plan["layers"] == sorted(runner.layers("full"))
    assert plan["source"] == runner.source_identity(repository)
    assert (
        sum("scripts/ci/run-browser-shard.mjs" in argv for argv in plan["commands"])
        == 12
    )
    assert plan["commands"][-1][1] == "--verify-backup"


def test_legacy_planner_run_requires_postgres_password(repository, monkeypatch):
    monkeypatch.setenv("SCHEMII_TEST_METADATA_DSN", "owned-disposable-db")
    calls = []
    plan = selected(repository, "full", [runner.planner.POSTGRES_COMMAND])
    monkeypatch.setattr(
        runner.planner.subprocess, "run", lambda argv, **kwargs: calls.append(argv)
    )
    assert runner.planner.execute(repository, plan) == 2
    assert calls == []


def test_private_evidence_symlink_is_rejected_without_touching_peer(
    repository, tmp_path
):
    (repository / ".schemii").mkdir()
    peer = tmp_path / "peer-evidence"
    peer.mkdir()
    (repository / ".schemii/local-ci").symlink_to(peer, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        execute(repository)
    assert list(peer.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        "# Changed document\n[missing](missing-local-file.md)\n",
        "# Changed document\n```python\nunclosed code fence\n",
    ],
)
def test_dirty_general_markdown_is_validated_before_full_feedback(
    repository, monkeypatch, body
):
    document = repository / "README.md"
    document.write_text("# Original document\n")
    git(repository, "add", "README.md")
    git(repository, "commit", "-qm", "owned document")
    document.write_text(body)
    plan = runner.selected_plan(repository, "HEAD")
    assert plan["classification"]["profile"] == "full"
    seen = controlled(monkeypatch)
    code, _, receipt = execute(repository, plan, feedback=True)
    assert code == 1 and receipt["acceptance"] is False
    assert receipt["markdown_validation"]["status"] == "failed"
    assert receipt["markdown_validation"]["duration_ms"] > 0
    assert receipt["reason"] == "markdown-validation-failed" and seen == []


@pytest.mark.parametrize("choice", ["explicit", "checkout", "default"])
def test_only_static_command_uses_separate_quality_interpreter(
    repository, monkeypatch, choice
):
    interpreter = (
        repository / ".schemii/quality/bin/python"
        if choice == "explicit"
        else repository / ".venv-quality/bin/python"
    )
    if choice != "default":
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        if choice == "explicit":
            monkeypatch.setenv("SCHEMII_CI_QUALITY_PYTHON", str(interpreter))
    seen = controlled(monkeypatch)
    plan = selected(
        repository,
        "backend-tests",
        [
            ["python", "scripts/check_python_quality.py", "HEAD"],
            ["python", "-m", "compileall", "-q", "src"],
        ],
    )
    code, _, receipt = execute(repository, plan)
    assert code == 0 and receipt["acceptance"] is True
    assert seen[0] == [
        str(interpreter) if choice != "default" else sys.executable,
        "scripts/check_python_quality.py",
        plan["classification"]["base"],
    ]
    assert seen[1][0] == sys.executable


@pytest.mark.parametrize(
    "candidate", ["", "missing-quality-python", "./missing/bin/python"]
)
def test_explicit_unavailable_quality_interpreter_never_falls_back(
    repository, monkeypatch, candidate
):
    monkeypatch.setenv("SCHEMII_CI_QUALITY_PYTHON", candidate)
    seen = controlled(monkeypatch)
    code, _, receipt = execute(
        repository,
        selected(
            repository,
            "backend-tests",
            [["python", "scripts/check_python_quality.py", "HEAD"]],
        ),
    )
    assert code == 2 and receipt["status"] == "pending"
    assert receipt["reason"] == "quality-interpreter-unavailable"
    assert receipt["pending_layers"] == ["static"] and seen == []


def test_child_public_fixture_retains_caller_mask_while_owned_receipts_stay_private(
    repository, monkeypatch
):
    original_invoke = runner.invoke
    caller_mask = os.umask(0o022)

    def invoke(root, argv, environment, lease=None):
        payload = records(environment)
        body = "import json, os\nfrom pathlib import Path\n"
        body += "file = Path('.schemii/public-credential-fixture.json')\nfile.touch(mode=0o644)\nassert file.stat().st_mode & 0o777 == 0o644\n"
        body += f"Path(os.environ['CI_TELEMETRY_FILE']).write_text('\\n'.join(json.dumps(value) for value in {payload!r}) + '\\n')\n"
        return original_invoke(root, [sys.executable, "-c", body], environment)

    monkeypatch.setattr(runner, "invoke", invoke)
    try:
        code, path, receipt = execute(repository)
        assert code == 0 and receipt["acceptance"] is True
        assert (
            stat.S_IMODE(
                (repository / ".schemii/public-credential-fixture.json").stat().st_mode
            )
            == 0o644
        )
        for item in path.parent.rglob("*"):
            assert stat.S_IMODE(item.stat().st_mode) == (
                0o700 if item.is_dir() else 0o600
            )
    finally:
        os.umask(caller_mask)


def test_browser_retry_recovery_retains_failed_attempt_and_never_proves_acceptance(
    repository, monkeypatch
):
    monkeypatch.setenv("SCHEMII_E2E_BOOTSTRAP", "1")
    controlled(monkeypatch, recovered=True)
    monkeypatch.setattr(
        runner, "verify_deployment", lambda: [{"url": runner.LOCAL_URL, "status": 200}]
    )
    browser = [
        "node",
        "scripts/ci/run-browser-shard.mjs",
        "--project=desktop-chromium",
        "--shard=1/6",
        "--profile=full",
    ]
    plan = selected(
        repository, "full", [runner.planner.BROWSER_BOUNDARY, ["./start.sh"], browser]
    )
    code, path, receipt = execute(repository, plan)
    assert code == 1 and receipt["status"] == "incomplete"
    assert receipt["acceptance"] is False
    assert receipt["commands"][-1]["exit_code"] == 0
    raw_path = path.parent / receipt["commands"][-1]["timing_evidence"]
    summary = runner.summarize(runner.load(raw_path))
    assert summary["complete"] is True and summary["outcome"] == "passed"
    assert summary["first_attempt_failures"] == summary["retry_recovered"] == 1
    assert not raw_path.with_suffix(".summary.json").exists()
    for output in ("artifacts/playwright-results", "artifacts/playwright-auth"):
        assert stat.S_IMODE((repository / output).stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "credentials",
    [
        {"SCHEMII_E2E_USERNAME": "partial-user"},
        {"SCHEMII_E2E_PASSWORD": "private-partial-sentinel"},
        {"SCHEMII_E2E_USERNAME": "", "SCHEMII_E2E_PASSWORD": ""},
    ],
)
def test_bootstrap_consent_cannot_replace_partial_explicit_credentials(
    repository, monkeypatch, credentials
):
    monkeypatch.setenv("SCHEMII_E2E_BOOTSTRAP", "1")
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    seen = controlled(monkeypatch)
    code, path, receipt = execute(
        repository,
        selected(
            repository, "e2e-tests", [runner.planner.BROWSER_BOUNDARY, ["./start.sh"]]
        ),
    )
    assert code == 2 and receipt["status"] == "pending"
    assert receipt["reason"] == "missing-prerequisite" and seen == []
    assert not (path.parent / "browser-credentials.json").exists()
    assert "private-partial-sentinel" not in path.read_text()


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Actual account-auth helper requires Node"
)
@pytest.mark.parametrize("populated", [False, True])
def test_run_owned_bootstrap_file_supports_actual_helper_sequential_setup_and_login(
    repository, monkeypatch, populated, capsys
):
    monkeypatch.setenv("SCHEMII_E2E_BOOTSTRAP", "1")
    secret_dir = repository / ".schemii/secrets"
    secret_dir.mkdir(parents=True, mode=0o700)
    (secret_dir / "account_setup_token").write_text("controlled setup token")
    if populated:
        runner.write_json(
            repository / ".schemii/auth-state.json",
            {
                "username": "existing-user",
                "password": "existing-owned-password",
                "setup_calls": 0,
                "login_calls": 0,
            },
        )
    module = (
        Path(runner.__file__).resolve().parents[2] / "tests/e2e/helpers/account-auth.js"
    ).as_uri()
    node_control = """
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { authenticateBrowserAccount } from MODULE;
const path = '.schemii/auth-state.json';
const previous = existsSync(path) ? JSON.parse(readFileSync(path)) : null;
let state = previous;
const response = (ok, value = {}) => ({ ok: () => ok, status: () => ok ? 200 : 401, json: async () => value });
const context = {
  get: async url => { assert.equal(url, '/api/v1/auth/status'); return response(true, { enabled: true, setup_required: !state }); },
  post: async (url, { data }) => {
    if (url === '/api/v1/auth/setup') {
      assert.equal(state, null);
      assert.equal(data.setup_token, 'controlled setup token');
      state = { username: data.username, password: data.password, setup_calls: 1, login_calls: 0 };
    } else {
      assert.equal(url, '/api/v1/auth/login');
      if (!state || data.username !== state.username || data.password !== state.password) return response(false);
      state.login_calls += 1;
    }
    writeFileSync(path, JSON.stringify(state), { mode: 0o600 });
    return response(true, { is_admin: true });
  },
};
assert.equal(statSync(process.env.SCHEMII_E2E_CREDENTIALS_FILE).mode & 0o777, 0o600);
const { credentials, identity } = await authenticateBrowserAccount(context, process.env);
assert.equal(identity.is_admin, true);
assert.equal(credentials.password.length >= 40, true);
const meta = { schema: 1, source_sha: process.env.CI_TELEMETRY_SHA, run_id: Number(process.env.CI_TELEMETRY_RUN_ID), run_attempt: 1, lane: 'browser', project: process.env.CI_TELEMETRY_PROJECT, shard: Number(process.env.CI_TELEMETRY_SHARD) };
const hash = value => createHash('sha256').update(value).digest('hex');
const test_id = hash('bootstrap continuity');
const records = [
  { ...meta, kind: 'start', planned: 1 },
  { ...meta, kind: 'plan', test_id },
  { ...meta, kind: 'attempt', test_id, source_id: hash('source.txt'), source_line: 1, attempt: 0, outcome: 'passed', skip: 'none', setup_ms: 1, execution_ms: 1, teardown_ms: 0 },
  { ...meta, kind: 'end', outcome: 'passed', wall_ms: 2 },
];
writeFileSync(process.env.CI_TELEMETRY_FILE, records.map(record => JSON.stringify(record)).join('\\n') + '\\n');
""".replace("MODULE", json.dumps(module))
    paths = []
    original_invoke = runner.invoke

    def invoke(root, command, environment, lease=None):
        if "CI_TELEMETRY_FILE" not in environment:
            return 0
        paths.append(environment["SCHEMII_E2E_CREDENTIALS_FILE"])
        return original_invoke(
            root, ["node", "--input-type=module", "-e", node_control], environment
        )

    monkeypatch.setattr(runner, "invoke", invoke)
    monkeypatch.setattr(
        runner, "verify_deployment", lambda: [{"url": runner.LOCAL_URL, "status": 200}]
    )

    def browser(index):
        return [
            "node",
            "scripts/ci/run-browser-shard.mjs",
            "--project=desktop-chromium",
            f"--shard={index}/6",
            "--profile=full",
        ]

    plan = selected(
        repository,
        "full",
        [runner.planner.BROWSER_BOUNDARY, ["./start.sh"], browser(1), browser(2)],
    )
    code, receipt_path, receipt = execute(repository, plan)
    credentials_path = receipt_path.parent / "browser-credentials.json"
    assert receipt["browser_credentials"] == "browser-credentials.json"
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    credentials = json.loads(credentials_path.read_text())
    captured = capsys.readouterr()
    assert (
        credentials["password"]
        not in receipt_path.read_text() + captured.out + captured.err
    )
    assert (
        credentials["username"]
        not in receipt_path.read_text() + captured.out + captured.err
    )
    state = json.loads((repository / ".schemii/auth-state.json").read_text())
    if populated:
        assert code == 1 and receipt["acceptance"] is False
        assert state == {
            "username": "existing-user",
            "password": "existing-owned-password",
            "setup_calls": 0,
            "login_calls": 0,
        }
        assert paths == [str(credentials_path)]
    else:
        assert code == 0 and receipt["acceptance"] is True
        assert paths == [str(credentials_path), str(credentials_path)]
        assert state["setup_calls"] == state["login_calls"] == 1
