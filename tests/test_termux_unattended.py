"""Exercise Termux boot dispatch and launcher ownership with local processes."""

import os
from pathlib import Path
import shlex
import subprocess

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "dev/android/termux.sh"


def shell(home: Path, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", code],
        env={**os.environ, "HOME": str(home), "PREFIX": str(home / "prefix")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("gate_result", [0, 23])
def test_boot_tracks_backend_in_termux_and_preserves_private_ssh_hook(
    tmp_path: Path, gate_result: int
) -> None:
    boot = tmp_path / ".termux/boot"
    boot.mkdir(parents=True)
    ssh_hook = boot / "schemii-ssh"
    ssh_hook.write_text("#!/bin/bash\nexit 99\n")
    ssh_hook.chmod(0o700)
    (boot / "schemii").write_text(
        "#!/bin/bash\n# Schemii Android preview boot v1\nexit 88\n"
    )
    command = tmp_path / "prefix/bin/schemii"
    command.parent.mkdir(parents=True)
    command.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/calls"\n'
        f"if [[ $1 == wait-network ]]; then exit {gate_result}; fi\nexit 37\n"
    )
    command.chmod(0o700)

    setup = shell(
        tmp_path, f"source {shlex.quote(str(LAUNCHER))}; manage_boot enable-boot"
    )
    assert setup.returncode == 0, setup.stderr
    result = shell(tmp_path, 'bash "$HOME/.termux/boot/schemii"')

    assert result.returncode == (gate_result or 37)
    assert (tmp_path / "calls").read_text().splitlines() == (
        ["wait-network"] if gate_result else ["wait-network", "start --foreground"]
    )
    assert sorted(path.name for path in boot.iterdir()) == ["schemii", "schemii-ssh"]
    assert (boot / "schemii").stat().st_mode & 0o777 == 0o700
    assert ssh_hook.read_text() == "#!/bin/bash\nexit 99\n"
    assert ssh_hook.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("runner_result", [0, 37])
def test_foreground_start_waits_for_its_child_without_holding_lifecycle_lock(
    tmp_path: Path, runner_result: int
) -> None:
    result = shell(
        tmp_path,
        f"""
source {shlex.quote(str(LAUNCHER))}
SCHEMII_ANDROID_ROOT=$HOME
SCHEMII_ANDROID_CONTROL=$HOME
runtime() {{
  [[ -f "$HOME/runner.json" ]] || return 1
  printf '{{"ready":true}}\n'
}}
host_running() {{ return 1; }}
native_mode() {{ return 0; }}
record_runner() {{ printf '%s\n' "$1" > "$HOME/runner.json"; }}
termux-wake-lock() {{ touch "$HOME/wake-lock"; }}
nohup() {{
  # Model a later stop action taking the launch lock before the runner exits.
  flock -w 2 "$HOME/launcher.lock" bash -c 'touch "$HOME/stop-lock-acquired"'
  exit {runner_result}
}}
exec 9>"$HOME/launcher.lock"
flock -n 9
start_runtime --foreground
""",
    )

    assert (tmp_path / "stop-lock-acquired").exists()
    assert not (tmp_path / "wake-lock").exists()
    assert result.returncode == runner_result
    assert '{"ready":true}' in result.stdout
    assert "background task" in result.stdout
    output = result.stdout + result.stderr
    assert f"status {runner_result}" in output
    log = (tmp_path / "launcher.log").read_text()
    assert "Termux background task attached" in log
    assert f"status {runner_result}" in log


def test_foreground_start_reports_ready_existing_backend_without_adopting_it(
    tmp_path: Path,
) -> None:
    result = shell(
        tmp_path,
        f"""
source {shlex.quote(str(LAUNCHER))}
SCHEMII_ANDROID_CONTROL=$HOME
runtime() {{ printf '{{"ready":true}}\n'; }}
nohup() {{ touch "$HOME/spawned"; }}
start_runtime --foreground
""",
    )

    assert result.returncode == 0, result.stderr
    assert '{"ready":true}' in result.stdout
    assert "already ready" in result.stdout
    assert "no background task attached" in result.stdout
    assert not (tmp_path / "spawned").exists()


def test_foreground_start_keeps_enrollment_pending_and_refuses_to_spawn(
    tmp_path: Path,
) -> None:
    result = shell(
        tmp_path,
        f"""
source {shlex.quote(str(LAUNCHER))}
SCHEMII_ANDROID_CONTROL=$HOME
runtime() {{
  printf '{{"state":"login_pending","supervisor_alive":true,"ready":false}}\n'
  return 1
}}
nohup() {{ touch "$HOME/spawned"; }}
start_runtime --foreground
""",
    )

    assert result.returncode == 2
    assert "schemii tailnet-login" in result.stdout
    assert "background task attached" not in result.stdout
    assert not (tmp_path / "spawned").exists()


@pytest.mark.parametrize("action", ["enable-boot", "disable-boot"])
def test_boot_management_preserves_dangling_foreign_link(
    tmp_path: Path, action: str
) -> None:
    boot = tmp_path / ".termux/boot"
    boot.mkdir(parents=True)
    foreign = tmp_path / "foreign"
    target = boot / "schemii"
    target.symlink_to(foreign)

    result = shell(
        tmp_path, f"source {shlex.quote(str(LAUNCHER))}; manage_boot {action}"
    )

    assert result.returncode == 1
    assert "preserving it" in result.stderr
    assert target.readlink() == foreign
    assert not foreign.exists()
