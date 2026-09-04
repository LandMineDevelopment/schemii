"""The optional experiment must not mutate the running application or its data."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("build_status,run_status", [(0, 0), (1, 0), (0, 7)])
def test_prototype_launcher_isolated_and_propagates_failure(tmp_path, build_status, run_status):
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'case "$*" in\n'
        '  *"build ai-prototype") exit "$BUILD_STATUS";;\n'
        '  *"run --rm --no-deps -T ai-prototype") exit "$RUN_STATUS";;\n'
        'esac\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    command_log = tmp_path / "commands"
    result = subprocess.run(
        [str(ROOT / "start.sh"), "--test-ai-prototype"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMMAND_LOG": str(command_log),
            "BUILD_STATUS": str(build_status),
            "RUN_STATUS": str(run_status),
            "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "lock"),
            "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
            "SCHEMII_TLS_DIRECTORY": str(tmp_path / "tls"),
        },
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (1 if build_status else run_status)
    commands = command_log.read_text()
    assert "--profile ai-prototype build ai-prototype" in commands
    assert ("run --rm --no-deps -T ai-prototype" in commands) == (build_status == 0)
    assert "up --detach" not in commands
    assert "rm --stop" not in commands
    assert not (tmp_path / "secrets").exists()
    assert not (tmp_path / "tls").exists()


def test_prototype_container_has_no_access_to_credentials_or_databases():
    compose = (ROOT / "compose.test.yaml").read_text()
    prototype = compose.split("  ai-prototype:\n")[1].split("\n  schemii:")[0]
    for setting in ("profiles: [ai-prototype]", "network_mode: none", "user: node", "read_only: true", "mem_limit: 512m"):
        assert setting in prototype
    for forbidden in ("volumes:", "ports:", "environment:", "depends_on:"):
        assert forbidden not in prototype
