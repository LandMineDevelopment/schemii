"""The optional experiment must not mutate the running application or its data."""
from pathlib import Path
import subprocess

import pytest

from test_startup import _mock_launcher_environment

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("build_status,run_status", [(0, 0), (1, 0), (0, 7)])
def test_prototype_launcher_isolated_and_propagates_failure(tmp_path, build_status, run_status):
    command_log = tmp_path / "commands"
    lock_log = tmp_path / "locks"
    environment = _mock_launcher_environment(
        tmp_path,
        [tmp_path / "primary"],
        command_log,
        tls_directory=tmp_path / "tls",
        lock_file=tmp_path / "lock",
        qa_state_directory=tmp_path / "qa-state",
    )
    environment.update({
        "BUILD_STATUS": str(build_status),
        "RUN_STATUS": str(run_status),
        "LOCK_LOG": str(lock_log),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
    })
    docker = tmp_path / "mock-bin/docker"
    docker.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'readlink -- /proc/$$/fd/3 /proc/$$/fd/4 >> "$LOCK_LOG"\n'
        'case "$*" in\n'
        '  *"build ai-prototype") exit "$BUILD_STATUS";;\n'
        '  *"run --rm --no-deps -T ai-prototype") exit "$RUN_STATUS";;\n'
        'esac\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "start.sh"), "--test-ai-prototype"],
        cwd=ROOT,
        env=environment,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (1 if build_status else run_status)
    commands = command_log.read_text()
    assert "--profile ai-prototype build ai-prototype" in commands
    assert ("run --rm --no-deps -T ai-prototype" in commands) == (build_status == 0)
    assert "up --detach" not in commands
    assert "rm --stop" not in commands
    assert set(lock_log.read_text().splitlines()) == {
        str(tmp_path / "shared-git/qa-deployment.lock"),
        str(tmp_path / "shared-git/qa-startup.lock"),
    }
    assert not (tmp_path / "secrets").exists()
    assert not (tmp_path / "tls").exists()


def test_prototype_container_has_no_access_to_credentials_or_databases():
    compose = (ROOT / "compose.test.yaml").read_text()
    prototype = compose.split("  ai-prototype:\n")[1].split("\n  ai-prototype-runtime:")[0]
    for setting in ("profiles: [ai-prototype]", "network_mode: none", "user: node", "read_only: true", "mem_limit: 512m"):
        assert setting in prototype
    for forbidden in ("volumes:", "ports:", "environment:", "depends_on:"):
        assert forbidden not in prototype
