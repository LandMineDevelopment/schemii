from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

import pytest

from scripts.ci import startup_timing as timing
from test_startup import (
    ROOT,
    _copy_launcher_worktree,
    _mock_launcher_environment,
    _run_mocked_launcher,
)


@contextmanager
def launcher_fixture():
    """Real shell, fake runtime commands, and automatically removed owned data."""
    with tempfile.TemporaryDirectory(prefix="schemii-launcher-timing-") as name:
        directory = Path(name)
        root = directory / "checkout"
        _copy_launcher_worktree(root)
        helper = root / "scripts/ci/startup_timing.py"
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/ci/startup_timing.py", helper)
        commands = directory / "commands.log"
        environment = _mock_launcher_environment(
            directory,
            [root],
            commands,
            tls_directory=directory / "tls",
            lock_file=directory / "launch.lock",
            qa_state_directory=directory / "qa",
        )
        for key in tuple(environment):
            if key.startswith("CI_TELEMETRY_") or key.startswith("GITHUB_"):
                environment.pop(key)
        environment["SCHEMII_SECRET_DIRECTORY"] = str(directory / "secrets")
        environment["FAKE_BLOCK_MARKER"] = str(directory / "blocked")
        environment["FAKE_TIMING_SNAPSHOT"] = str(directory / "checkpoint.jsonl")
        (directory / "mock-bin/docker").write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            "  *'compose version'*) tag=version ;;\n"
            "  info) tag=info ;;\n"
            "  *' build schemii ai-prototype-runtime') tag=build ;;\n"
            "  *' rm --stop --force'*) tag=replacement ;;\n"
            "  *'up --detach --remove-orphans --wait'*) tag=readiness ;;\n"
            "  *' run --rm --no-deps demo-fixture') tag=fixture ;;\n"
            "  *' logs '*) tag=diagnostics ;;\n"
            "  *' ps'*) tag=state ;;\n"
            "  *) tag=other ;;\n"
            "esac\n"
            "count=disabled\n"
            'if [ -n "$SCHEMII_START_TIMING_FILE" ] && [ -f "$SCHEMII_START_TIMING_FILE" ]; then\n'
            '  count=$(wc -l < "$SCHEMII_START_TIMING_FILE")\n'
            "fi\n"
            'printf \'%s:%s\\n\' "$tag" "$count" >> "$COMMAND_LOG"\n'
            'if [ "$tag" = build ]; then\n'
            '  if [ -n "$SCHEMII_START_TIMING_FILE" ]; then cp "$SCHEMII_START_TIMING_FILE" "$FAKE_TIMING_SNAPSHOT"; fi\n'
            '  if [ "$FAKE_BLOCK_BUILD" = 1 ]; then touch "$FAKE_BLOCK_MARKER"; sleep 30; fi\n'
            '  if [ "$FAKE_FAIL_BUILD" = 1 ]; then echo PRIVATE_FAILURE_SENTINEL >&2; exit 23; fi\n'
            '  if [ "$FAKE_BREAK_RECEIPT" = 1 ]; then chmod 400 "$SCHEMII_START_TIMING_FILE"; fi\n'
            "fi\n"
            'if [ "$tag" = replacement ] && [ "$FAKE_FAIL_REPLACEMENT" = 1 ]; then exit 27; fi\n'
            'if [ "$tag" = readiness ] && [ "$FAKE_FAIL_READINESS" = 1 ]; then echo PRIVATE_FAILURE_SENTINEL >&2; exit 31; fi\n'
            'if [ "$tag" = state ] && [ "$FAKE_BREAK_FINISH" = 1 ]; then chmod 400 "$SCHEMII_START_TIMING_FILE"; fi\n'
            'if [ "$tag" = fixture ]; then\n'
            '  if [ "$FAKE_FAIL_FIXTURE" = 1 ]; then exit 35; fi\n'
            "  printf 'SCHEMII_DEMO_WORKSPACE_ID=ws_0123456789abcdef0123456789abcdef\\n'\n"
            "fi\n",
            encoding="utf-8",
        )
        receipt = directory / "startup.jsonl"
        yield root, environment, commands, receipt
    assert not directory.exists(), (
        "TemporaryDirectory fixtures must remove their owned data"
    )


def run_launcher(root, environment, receipt=None, **overrides):
    environment = {**environment, **overrides}
    if receipt is not None:
        environment["SCHEMII_START_TIMING_FILE"] = str(receipt)
    return _run_mocked_launcher(root, environment)


def test_disabled_launcher_needs_no_python_or_helper_and_retains_command_order():
    with launcher_fixture() as (root, environment, commands, _):
        (root / "scripts/ci/startup_timing.py").unlink()
        python = Path(environment["PATH"].split(":")[0]) / "python3"
        python.write_text(
            "#!/bin/sh\nprintf 'unexpected-python\\n' >> \"$COMMAND_LOG\"\nexit 63\n"
        )
        python.chmod(0o755)
        for configured in ({}, {"SCHEMII_START_TIMING_FILE": ""}):
            result = run_launcher(root, environment, **configured)
            assert result.returncode == 0, result.stderr
        events = commands.read_text().splitlines()
        assert "unexpected-python" not in events
        half = len(events) // 2
        assert events[:half] == events[half:]
        assert events[:half] == [
            "version:disabled",
            "info:disabled",
            "build:disabled",
            "replacement:disabled",
            "replacement:disabled",
            "other:disabled",
            "readiness:disabled",
            "state:disabled",
        ]


def test_enabled_launcher_records_ordered_phases_and_only_public_fields():
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(
            root, environment, receipt, PRIVATE_ENV="PRIVATE_ENV_SENTINEL"
        )
        assert result.returncode == 0, result.stderr
        value = timing.load_startup(receipt)
        assert value["outcome"] == "passed"
        assert [phase["phase"] for phase in value["phases"]] == list(timing.PHASES)
        assert all(phase["outcome"] == "passed" for phase in value["phases"])
        assert value["source_kind"] == "local" and value["source_sha"] is None
        assert value["run_id"] == value["run_attempt"] == 0
        assert value["preflight"] == "unmeasured"
        assert sum(phase["duration_ms"] for phase in value["phases"]) == pytest.approx(
            value["wall_ms"], abs=0.01
        )
        assert commands.read_text().splitlines() == [
            "version:disabled",
            "info:disabled",
            "build:2",
            "replacement:3",
            "replacement:3",
            "other:3",
            "readiness:4",
            "state:5",
        ]
        assert receipt.stat().st_size <= timing.MAX_BYTES
        assert receipt.stat().st_mode & 0o777 == 0o600
        assert "PRIVATE_ENV_SENTINEL" not in receipt.read_text()
        assert str(root) not in receipt.read_text()
        assert not list(
            (Path(environment["SCHEMII_TLS_DIRECTORY"])).glob(".generate.*")
        )
        assert not list(Path(environment["SCHEMII_SECRET_DIRECTORY"]).glob(".*"))


@pytest.mark.parametrize("enabled", [False, True])
def test_failed_build_keeps_running_deployment_and_original_failure(enabled):
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(
            root, environment, receipt if enabled else None, FAKE_FAIL_BUILD="1"
        )
        assert result.returncode == 1
        assert "the running deployment was left unchanged" in result.stderr
        assert not any(
            event.startswith(("replacement:", "readiness:"))
            for event in commands.read_text().splitlines()
        )
        if enabled:
            value = timing.load_startup(receipt)
            assert value["outcome"] == "failed"
            assert [phase["outcome"] for phase in value["phases"]] == [
                "passed",
                "failed",
                None,
                None,
                None,
            ]
            assert "PRIVATE_FAILURE_SENTINEL" not in receipt.read_text()


def test_failed_readiness_records_diagnostics_and_terminal_failure():
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(root, environment, receipt, FAKE_FAIL_READINESS="1")
        assert result.returncode == 1
        assert "the application service did not become healthy" in result.stderr
        value = timing.load_startup(receipt)
        assert value["outcome"] == "failed"
        assert [phase["outcome"] for phase in value["phases"]] == [
            "passed",
            "passed",
            "passed",
            "failed",
            "failed",
        ]
        assert any(
            line == "diagnostics:5" for line in commands.read_text().splitlines()
        )
        assert "PRIVATE_FAILURE_SENTINEL" not in receipt.read_text()


def test_unconditional_replacement_error_preserves_exit_code():
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(root, environment, receipt, FAKE_FAIL_REPLACEMENT="1")
        assert result.returncode == 27
        value = timing.load_startup(receipt)
        assert value["outcome"] == "failed"
        assert value["phases"][2]["outcome"] == "failed"
        assert value["phases"][3]["duration_ms"] is None
        assert not any(
            line.startswith("readiness:") for line in commands.read_text().splitlines()
        )


@pytest.mark.parametrize("failed", [False, True])
def test_optional_demo_work_is_in_post_start(failed):
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(
            root,
            environment,
            receipt,
            SCHEMII_RESET_MIGRATION_DEMO="1",
            FAKE_FAIL_FIXTURE=str(int(failed)),
        )
        assert result.returncode == int(failed), result.stderr
        value = timing.load_startup(receipt)
        assert value["phases"][-1]["outcome"] == ("failed" if failed else "passed")
        assert "fixture:5" in commands.read_text().splitlines()
        assert "ws_0123456789abcdef0123456789abcdef" not in receipt.read_text()


@pytest.mark.parametrize(
    "unsafe",
    [
        "existing",
        "symlink",
        "parent-symlink",
        "relative",
        "missing-parent",
        "directory",
    ],
)
def test_opt_in_refuses_unsafe_paths_before_build(unsafe):
    with launcher_fixture() as (root, environment, commands, receipt):
        sentinel = receipt.parent / "retained"
        sentinel.write_text("PRIVATE_RETAINED_SENTINEL")
        output = receipt
        if unsafe == "existing":
            output = sentinel
        elif unsafe == "symlink":
            output.symlink_to(sentinel)
        elif unsafe == "parent-symlink":
            parent = receipt.parent / "linked"
            parent.symlink_to(receipt.parent, target_is_directory=True)
            output = parent / receipt.name
        elif unsafe == "relative":
            output = Path("receipt.jsonl")
        elif unsafe == "missing-parent":
            output = receipt.parent / "missing/receipt.jsonl"
        elif unsafe == "directory":
            output.mkdir()
        result = run_launcher(root, environment, output)
        assert result.returncode == 1
        assert "receipt could not be created" in result.stderr
        assert sentinel.read_text() == "PRIVATE_RETAINED_SENTINEL"
        assert str(output) not in result.stderr
        assert not any(
            line.startswith("build:") for line in commands.read_text().splitlines()
        )


@pytest.mark.parametrize("finish", [False, True])
def test_opt_in_write_failure_is_explicit_and_retains_partial_data(finish):
    with launcher_fixture() as (root, environment, commands, receipt):
        result = run_launcher(
            root,
            environment,
            receipt,
            **{"FAKE_BREAK_FINISH" if finish else "FAKE_BREAK_RECEIPT": "1"},
        )
        assert result.returncode == 1
        assert "launcher timing" in result.stderr
        value = timing.load_startup(receipt)
        assert value["outcome"] is None and value["wall_ms"] is None
        if not finish:
            assert not any(
                line.startswith("replacement:")
                for line in commands.read_text().splitlines()
            )


def test_non_start_action_ignores_timing_path():
    with launcher_fixture() as (root, environment, _, receipt):
        receipt.write_text("retained")
        (root / "scripts/ci/startup_timing.py").unlink()
        result = subprocess.run(
            [str(root / "start.sh"), "--test-ai-prototype"],
            env={**environment, "SCHEMII_START_TIMING_FILE": str(receipt)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert receipt.read_text() == "retained"


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_behavior_matches_disabled_launcher_and_retains_partial_attempt(signum):
    outcomes = []
    for enabled in (False, True):
        with launcher_fixture() as (root, environment, _, receipt):
            environment = {**environment, "FAKE_BLOCK_BUILD": "1"}
            if enabled:
                environment["SCHEMII_START_TIMING_FILE"] = str(receipt)
            process = subprocess.Popen(
                [str(root / "start.sh")],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while (
                    not Path(environment["FAKE_BLOCK_MARKER"]).exists()
                    and time.monotonic() < deadline
                ):
                    assert process.poll() is None
                    time.sleep(0.01)
                assert Path(environment["FAKE_BLOCK_MARKER"]).exists()
                os.killpg(process.pid, signum)
                process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
            outcomes.append(process.returncode)
            if enabled:
                value = timing.load_startup(receipt)
                # Native termination need not execute EXIT. Never turn a
                # checkpoint into a completed observation or add signal traps.
                assert value["outcome"] in (None, "cancelled")
                assert value["phases"][0]["outcome"] == "passed"
                assert value["phases"][1]["outcome"] in (None, "cancelled")
    assert outcomes[0] == outcomes[1]


def test_partial_checkpoints_have_explicit_unknown_phases_and_terminal():
    with launcher_fixture() as (root, environment, _, receipt):
        assert run_launcher(root, environment, receipt).returncode == 0
        snapshot = timing.load_startup(environment["FAKE_TIMING_SNAPSHOT"])
        assert snapshot["phases"][0]["outcome"] == "passed"
        assert all(
            phase["outcome"] is None and phase["duration_ms"] is None
            for phase in snapshot["phases"][1:]
        )
        assert snapshot["outcome"] is snapshot["wall_ms"] is None
        assert timing.validate_startup(snapshot) == snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "secret-outcome",
        "nan",
        "boolean",
        "wrong-order",
        "gap",
        "hosted-local",
        "inconsistent-total",
    ],
)
def test_consolidated_validator_rejects_unclosed_or_invalid_schema(mutation):
    with launcher_fixture() as (root, environment, _, receipt):
        assert run_launcher(root, environment, receipt).returncode == 0
        value = copy.deepcopy(timing.load_startup(receipt))
        if mutation == "extra":
            value["environment"] = "PRIVATE_ENV_SENTINEL"
        elif mutation == "secret-outcome":
            value["phases"][0]["outcome"] = "PRIVATE_FAILURE_SENTINEL"
        elif mutation == "nan":
            value["phases"][0]["duration_ms"] = float("nan")
        elif mutation == "boolean":
            value["phases"][0]["duration_ms"] = True
        elif mutation == "wrong-order":
            value["phases"].reverse()
        elif mutation == "gap":
            value["phases"][0].update(outcome=None, duration_ms=None)
        elif mutation == "hosted-local":
            value["source_sha"] = "a" * 40
        elif mutation == "inconsistent-total":
            value["wall_ms"] += 100
        with pytest.raises(ValueError):
            timing.validate_startup(value)


def test_raw_validator_rejects_oversize_and_build_failure_progression():
    with launcher_fixture() as (root, environment, _, receipt):
        assert run_launcher(root, environment, receipt).returncode == 0
        data = receipt.read_bytes()
        with pytest.raises(ValueError):
            timing.validate_records(data + b" " * timing.MAX_BYTES)
        records = [json.loads(line) for line in data.splitlines()]
        records[2]["outcome"] = "failed"
        with pytest.raises(ValueError):
            timing.validate_records(
                b"".join(json.dumps(record).encode() + b"\n" for record in records)
            )


@pytest.mark.parametrize("clean", [False, True])
def test_only_clean_matching_hosted_source_can_claim_immutable_identity(clean):
    with launcher_fixture() as (root, environment, _, receipt):
        fake_git = Path(environment["PATH"].split(":")[0]) / "git"
        original = fake_git.read_text()
        fake_git.write_text(
            original.replace(
                "  *) exit 64 ;;",
                "  *'rev-parse HEAD'*) printf '%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;\n  *'status --porcelain'*) "
                + ("exit 0" if clean else "printf ' M local-file\\n'")
                + " ;;\n  *) exit 64 ;;",
            )
        )
        result = run_launcher(
            root,
            environment,
            receipt,
            GITHUB_ACTIONS="true",
            CI_TELEMETRY_SHA="a" * 40,
            CI_TELEMETRY_RUN_ID="123",
            CI_TELEMETRY_RUN_ATTEMPT="1",
            CI_TELEMETRY_PROJECT="desktop-chromium",
            CI_TELEMETRY_SHARD="2",
        )
        assert result.returncode == 0, result.stderr
        value = timing.load_startup(receipt)
        assert value["source_kind"] == ("hosted" if clean else "local")
        assert value["source_sha"] == ("a" * 40 if clean else None)
        assert value["run_id"] == (123 if clean else 0)
        assert value["project"] == "desktop-chromium" and value["shard"] == 2
