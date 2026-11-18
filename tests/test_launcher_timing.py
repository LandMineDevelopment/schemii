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
            if key.startswith(("CI_TELEMETRY_", "GITHUB_", "COMPOSE_")):
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


def prepare_qa_overlay_fixture(root, environment):
    (root / "testing").mkdir()
    for name in ("launcher.sh", "compose.yaml", "egress.yaml"):
        shutil.copy2(ROOT / "testing" / name, root / "testing" / name)
    qa = Path(environment["SCHEMII_QA_STATE_DIRECTORY"])
    qa.mkdir(mode=0o700)
    registry = qa / "registry.json"
    mock_bin = Path(environment["PATH"].split(":")[0])
    docker = mock_bin / "docker"
    docker.write_text(docker.read_text().replace(
        'case "$*" in',
        'case "$*" in\n'
        '  info) if [ "$FAKE_PUBLISH_QA" = info ]; then '
        'printf "{}\\n" > "$SCHEMII_QA_STATE_DIRECTORY/registry.json"; fi ;;\n'
        "esac\n"
        'case "$*" in\n'
        "  *' config --format json') printf '%s\\n' "
        "'{\"services\":{\"schemii\":{\"environment\":"
        "{\"SCHEMII_ALLOWED_TARGET_HOSTS\":\"desktop.internal\"}}}}'; exit 0 ;;\n"
        "  *' build schemii ai-prototype-runtime') "
        'printf "%s|%s\\n" "$*" "${SCHEMII_QA_EFFECTIVE_ALLOWED_TARGET_HOSTS-}" '
        '>> "$FAKE_QA_BUILD_PROOF" ;;\n'
        "esac\n"
        'case "$*" in',
        1,
    ))
    openssl = shutil.which("openssl")
    assert openssl is not None
    shim = mock_bin / "openssl"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = req ] && [ "$FAKE_PUBLISH_QA" = openssl ]; then\n'
        '  printf "{}\\n" > "$SCHEMII_QA_STATE_DIRECTORY/registry.json"\n'
        "fi\n"
        f'exec "{openssl}" "$@"\n'
    )
    shim.chmod(0o755)
    proof = qa.parent / "qa-build-proof"
    return registry, proof


def test_guest_registry_publication_refuses_unapproved_overlay_or_keeps_safe_selection():
    for publication in ("info", "openssl"):
        with launcher_fixture() as (root, environment, commands, _):
            registry, proof = prepare_qa_overlay_fixture(root, environment)
            assert not registry.exists()
            result = run_launcher(
                root,
                environment,
                SCHEMII_QA_GUEST_TARGET_HOSTS="fixture.internal",
                SCHEMII_ALLOWED_TARGET_HOSTS="fixture.internal",
                FAKE_PUBLISH_QA=publication,
                FAKE_QA_BUILD_PROOF=str(proof),
            )
            assert registry.is_file()
            if publication == "info":
                assert result.returncode == 1
                assert "requires exact approval of qa-postgres" in result.stderr
                assert commands.read_text().splitlines() == ["version:disabled", "info:disabled"]
                assert not Path(environment["SCHEMII_SECRET_DIRECTORY"]).exists()
                assert not (registry.parent / "writable-credentials.tsv").exists()
                assert not proof.exists()
            else:
                assert result.returncode == 0, result.stderr
                args, hosts = proof.read_text().strip().split("|")
                assert "testing/compose.yaml" not in args
                assert "testing/egress.yaml" not in args
                assert hosts == ""


def test_registered_overlay_uses_approved_guest_hosts_and_preserves_desktop_union():
    for guest in (True, False):
        with launcher_fixture() as (root, environment, _, _):
            registry, proof = prepare_qa_overlay_fixture(root, environment)
            registry.write_text("{}\n")
            bound = "fixture.internal,qa-postgres"
            guest_options = {
                "SCHEMII_QA_GUEST_TARGET_HOSTS": bound,
                "SCHEMII_ALLOWED_TARGET_HOSTS": bound,
            } if guest else {}
            result = run_launcher(
                root, environment, FAKE_QA_BUILD_PROOF=str(proof), **guest_options
            )
            assert result.returncode == 0, result.stderr
            args, hosts = proof.read_text().strip().split("|")
            assert "testing/compose.yaml" in args
            assert "testing/egress.yaml" in args
            assert hosts == (bound if guest else "desktop.internal,qa-postgres")


def test_fixture_ca_requires_preheld_locks_and_ignores_late_compose_inputs():
    with launcher_fixture() as (root, environment, commands, _):
        _, proof = prepare_qa_overlay_fixture(root, environment)
        mock_bin = Path(environment["PATH"].split(":")[0])
        vm = mock_bin / "systemd-detect-virt"
        vm.write_text("#!/bin/sh\nexit 0\n")
        vm.chmod(0o755)
        node = mock_bin / "node"
        node.write_text(
            "#!/bin/sh\n"
            'mkdir -p .schemii\n'
            'printf "services: {}\\n" > .schemii/compose.local.yaml\n'
            'printf "SCHEMII_ALLOWED_TARGET_HOSTS=foreign.internal\\n" > .env\n'
            'printf "%s\\n" "$PWD/.schemii/staged-ca.json"\n'
        )
        node.chmod(0o755)
        binding = root.parent / "binding.json"
        binding.write_text("{}\n")
        env = {
            **environment,
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "SCHEMII_QA_FIXTURE_CA_BINDING": str(binding),
            "SCHEMII_QA_LEASE_FD": "3",
            "SCHEMII_QA_GATE_FD": "4",
            "SCHEMII_QA_LEASE_MODE": "exclusive",
            "FAKE_QA_BUILD_PROOF": str(proof),
        }
        for hold in ("", "flock -x 3; flock -x 4; "):
            result = subprocess.run(
                ["bash", "-ec",
                 'exec 3>"$FAKE_GIT_COMMON_DIRECTORY/qa-deployment.lock"; '
                 'exec 4>"$FAKE_GIT_COMMON_DIRECTORY/qa-startup.lock"; '
                 + hold + 'exec ./start.sh'],
                cwd=root, env=env, capture_output=True, text=True, timeout=10,
            )
            if not hold:
                assert result.returncode == 1
                assert "pre-held exclusive deployment and startup locks" in result.stderr
                assert not commands.exists()
            else:
                assert result.returncode == 0, result.stderr
                assert (root / ".schemii/compose.local.yaml").is_file()
                assert (root / ".env").is_file()
                build = proof.read_text()
                assert "--env-file /dev/null" in build
                assert "--file " + str(root / ".schemii/staged-ca.json") in build
                assert "compose.local.yaml" not in build


@pytest.mark.parametrize(
    "setting", ["COMPOSE_PROFILES", "COMPOSE_FILE", "COMPOSE_ENV_FILES", "COMPOSE_PROJECT_NAME"]
)
@pytest.mark.parametrize("value", ["", "PRIVATE_COMPOSE_SENTINEL"])
def test_fixture_ca_refuses_inherited_compose_settings_before_preflight(setting, value):
    with launcher_fixture() as (root, environment, commands, _):
        result = run_launcher(
            root,
            environment,
            SCHEMII_QA_FIXTURE_CA_BINDING="/private/fixture-binding.json",
            SCHEMII_QA_LEASE_FD="3",
            SCHEMII_QA_GATE_FD="4",
            SCHEMII_QA_LEASE_MODE="exclusive",
            DOCKER_HOST="unix:///var/run/docker.sock",
            **{setting: value},
        )
        assert result.returncode == 1
        assert "fixture CA refuses inherited Compose settings" in result.stderr
        assert "PRIVATE_COMPOSE_SENTINEL" not in result.stdout + result.stderr
        assert not commands.exists()
        assert not list(Path(environment["FAKE_GIT_COMMON_DIRECTORY"]).glob("qa-*.lock"))
        assert not Path(environment["SCHEMII_LAUNCH_LOCK_FILE"]).exists()
        assert not Path(environment["SCHEMII_SECRET_DIRECTORY"]).exists()


def test_stock_launcher_retains_compose_configuration_without_fixture_ca():
    with launcher_fixture() as (root, environment, _, _):
        _, proof = prepare_qa_overlay_fixture(root, environment)
        result = run_launcher(
            root,
            environment,
            COMPOSE_PROFILES="PRIVATE_COMPOSE_SENTINEL",
            COMPOSE_FILE="PRIVATE_COMPOSE_SENTINEL",
            FAKE_QA_BUILD_PROOF=str(proof),
        )
        assert result.returncode == 0, result.stderr
        assert "--file " + str(root / "compose.test.yaml") in proof.read_text()
        assert "PRIVATE_COMPOSE_SENTINEL" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "failure", ["", "staging", "local-bases", "configuration", "build", "compose-drift"]
)
def test_offline_guest_never_falls_back_and_cleans_before_replacement(failure):
    with launcher_fixture() as (root, environment, commands, _):
        mock_bin = Path(environment["PATH"].split(":")[0])
        vm = mock_bin / "systemd-detect-virt"
        vm.write_text("#!/bin/sh\nexit 0\n")
        vm.chmod(0o755)
        staging = root.parent / "staging-path"
        node = mock_bin / "node"
        node.write_text(
            "#!/bin/sh\n"
            'case "$2" in\n'
            "  stage-offline-build)\n"
            '    printf "%s\n" "$4" > "$FAKE_STAGE_PATH"\n'
            '    if [ "$FAKE_OFFLINE_FAILURE" = staging ]; then exit 1; fi\n'
            '    mkdir "$4/context"\n'
            '    cp "$3/compose.test.yaml" "$4/compose.test.yaml"\n'
            '    printf "{}\n" > "$4/compose.json" ;;\n'
            '  require-offline-local-bases) [ "$FAKE_OFFLINE_FAILURE" != local-bases ] ;;\n'
            '  verify-offline-compose) cmp -s "$3/compose.test.yaml" "$4/compose.test.yaml" ;;\n'
            "  verify-offline-build)\n"
            '    [ "$FAKE_OFFLINE_FAILURE" != configuration ] && [ "$(cat)" = "{}" ] ;;\n'
            "  *) exit 72 ;;\n"
            "esac\n"
        )
        node.chmod(0o755)
        docker = mock_bin / "docker"
        docker.write_text(
            docker.read_text()
            .replace(
                'case "$*" in',
                'stage=$(cat "$FAKE_STAGE_PATH")\n'
                'case "$*" in\n'
                '  *" config --format json"|*" build schemii ai-prototype-runtime"|*" rm --stop --force"*|*" up --detach "*)\n'
                '    case "$*" in *"--project-directory $PWD --file $stage/compose.test.yaml --env-file /dev/null"*) ;; *) exit 71 ;; esac ;;&\n'
                '  *" config --format json")\n'
                '    case "$*" in *"--file $stage/compose.json"*) ;; *) exit 71 ;; esac\n'
                '    printf "{}\n"; exit 0 ;;\n'
                '  *" build schemii ai-prototype-runtime")\n'
                '    case "$*" in *"--file $stage/compose.json"*) ;; *) exit 71 ;; esac\n'
                '    if [ "$FAKE_OFFLINE_FAILURE" = build ]; then\n'
                '      printf "build:failed\n" >> "$COMMAND_LOG"; exit 1\n'
                "    fi\n"
                '    if [ "$FAKE_OFFLINE_FAILURE" = compose-drift ]; then\n'
                '      printf "# late drift\n" >> "$PWD/compose.test.yaml"\n'
                "    fi ;;\n"
                '  *" rm --stop --force"*)\n'
                '    [ ! -e "$stage/context" ] && [ ! -e "$stage/compose.json" ] || exit 71 ;;\n'
                '  *" up --detach "*)\n'
                '    case "$*" in *"--no-build --pull never") ;; *) exit 71 ;; esac\n'
                '    case "$*" in *"--file $stage/compose.json"*) exit 71 ;; esac ;;\n'
                "esac\n"
                'case "$*" in',
                1,
            )
            .replace("#!/bin/sh", "#!/bin/bash", 1)
        )
        binding = root.parent / "binding.json"
        binding.write_text("{}\n")
        config = root.parent / "client-config"
        config.mkdir(mode=0o700)
        env = {
            **environment,
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_CONFIG": str(config),
            "SCHEMII_TEST_APP_PORT": "8001",
            "SCHEMII_QA_OFFLINE_BUILD_BINDING": str(binding),
            "SCHEMII_QA_LEASE_FD": "3",
            "SCHEMII_QA_GATE_FD": "4",
            "SCHEMII_QA_LEASE_MODE": "exclusive",
            "FAKE_STAGE_PATH": str(staging),
            "FAKE_OFFLINE_FAILURE": failure,
        }
        result = subprocess.run(
            [
                "bash",
                "-ec",
                'exec 3>"$FAKE_GIT_COMMON_DIRECTORY/qa-deployment.lock"; '
                'exec 4>"$FAKE_GIT_COMMON_DIRECTORY/qa-startup.lock"; '
                "flock -x 3; flock -x 4; exec ./start.sh",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert staging.is_file(), result.stderr
        assert not Path(staging.read_text().strip()).exists()
        observed = commands.read_text() if commands.exists() else ""
        if failure:
            assert result.returncode == 1, result.stderr
            assert "replacement" not in observed
            assert "readiness" not in observed
            assert ("build:disabled" in observed) == (failure == "compose-drift")
            assert observed.count("build:failed") == (failure == "build")
            if failure in {"staging", "local-bases"}:
                assert observed == ""
                assert not Path(environment["SCHEMII_SECRET_DIRECTORY"]).exists()
            if failure == "local-bases":
                assert "no verified local-only builder source" in result.stderr
            if failure == "compose-drift":
                assert "Compose inputs changed before runtime" in result.stderr
        else:
            assert result.returncode == 0, result.stderr
            assert observed.count("build:disabled") == 1
            assert "replacement" in observed
            assert "readiness" in observed
