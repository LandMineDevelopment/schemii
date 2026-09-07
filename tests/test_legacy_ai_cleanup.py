"""Exercise the launcher's destructive cleanup against a fake Docker executable."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "scenario,successful,removed",
    [("unused", True, True), ("absent", True, False),
     ("wrong-owner", False, False), ("mounted", False, False)],
)
def test_cleanup_only_removes_verified_unused_legacy_volume(tmp_path, scenario, successful, removed):
    executable = tmp_path / "docker"
    executable.write_text('''#!/usr/bin/env bash
set -eu
case "$1 ${2-}" in
  'compose version') exit 0 ;;
  'volume ls')
    [[ "$SCENARIO" == absent ]] || echo schemii-test_schemii-test-opencode-data ;;
  'volume inspect')
    if [[ "$SCENARIO" == wrong-owner ]]; then echo unrelated/data;
    else echo schemii-test/schemii-test-opencode-data; fi ;;
  'ps --all') [[ "$SCENARIO" != mounted ]] || echo container-id ;;
  'volume rm') echo "$3" > "$REMOVAL_LOG" ;;
  *) [[ "$1" == info ]] || exit 90 ;;
esac
exit 0
''')
    executable.chmod(0o755)
    removal_log = tmp_path / "removed"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "SCENARIO": scenario, "REMOVAL_LOG": str(removal_log),
           "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "start.lock")}
    result = subprocess.run(
        [str(ROOT / "start.sh"), "--remove-legacy-ai-data"],
        env=env, capture_output=True, text=True,
    )
    assert (result.returncode == 0) is successful, result.stderr
    assert removal_log.exists() is removed
    if removed:
        assert removal_log.read_text().strip() == "schemii-test_schemii-test-opencode-data"
