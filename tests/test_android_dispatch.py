"""The explicit Android launcher never enters the desktop Docker path."""

from pathlib import Path
import os
import shlex
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_android_dispatch_preserves_arguments_and_backend_failure(tmp_path):
    shutil.copy2(ROOT / "start.sh", tmp_path / "start.sh")
    backend = tmp_path / "dev/android/termux.sh"
    backend.parent.mkdir(parents=True)
    backend.write_text('printf "<%s>\\n" "$@"\nexit 37\n')
    result = subprocess.run(
        [
            "bash",
            str(tmp_path / "start.sh"),
            "--android",
            "logs",
            "argument with spaces",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 37
    assert result.stdout == "<logs>\n<argument with spaces>\n"
    assert result.stderr == ""


@pytest.mark.parametrize("gate_result,start_result", [(0, 0), (0, 37), (23, 0)])
def test_boot_script_gates_global_start_once_and_retains_failure(
    tmp_path, gate_result, start_result
):
    prefix = tmp_path / "prefix with spaces"
    command = prefix / "bin/schemii"
    command.parent.mkdir(parents=True)
    calls = tmp_path / "calls"
    command.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$1\" >> {shlex.quote(str(calls))}\n"
        f'if [[ "$1" == wait-network ]]; then exit {gate_result}; fi\n'
        f"exit {start_result}\n"
    )
    command.chmod(0o700)
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(ROOT / 'dev/android/termux.sh'))}\nmanage_boot enable-boot",
        ],
        env={**os.environ, "HOME": str(tmp_path), "PREFIX": str(prefix)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        ["bash", str(tmp_path / ".termux/boot/schemii")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (gate_result or start_result)
    assert calls.read_text().splitlines() == (
        ["wait-network"] if gate_result else ["wait-network", "start"]
    )


@pytest.mark.parametrize("arguments", [[], ["unexpected"]])
def test_wait_network_dispatches_readonly_native_gate_without_guest(
    tmp_path, arguments
):
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"""
source {shlex.quote(str(ROOT / "dev/android/termux.sh"))}
require_termux() {{ :; }}
require_proot() {{ :; }}
require_owned() {{ :; }}
native_host() {{ printf 'native:%s\\n' "$@"; return 37; }}
runtime() {{ printf 'unexpected guest call\\n' >&2; return 99; }}
main wait-network {shlex.join(arguments)}
""",
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (1 if arguments else 37)
    assert result.stdout == ("" if arguments else "native:wait-network\n")
    assert "unexpected guest" not in result.stderr
    assert not (tmp_path / ".local/share/schemii-android/launcher.lock").exists()
