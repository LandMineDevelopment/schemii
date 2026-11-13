"""Host-side installer controls; these do not claim Android/PRoot acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "dev/android/termux.sh"


def shell(
    code: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(WRAPPER))}\n{code}"],
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
    )


def test_help_has_no_install_or_control_directory_side_effect(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(WRAPPER), "--help"],
        env={**os.environ, "HOME": str(tmp_path), "PREFIX": ""},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "configure --tailnet-ip" in result.stdout
    assert "reset-password USERNAME" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_start_returns_actionable_auth_pending_without_waiting_or_login_url(tmp_path):
    result = shell(
        'SCHEMII_ANDROID_ROOT="$HOME"\nSCHEMII_ANDROID_CONTROL="$HOME"\n'
        'runtime() { printf \'{"state":"login_pending","supervisor_alive":true,"ready":false}\'; return 1; }\n'
        "host_running() { return 1; }; record_runner() { :; }; nohup() { :; }\n"
        "start_runtime",
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode == 2, result.stderr
    assert "schemii tailnet-login" in result.stdout
    assert "databases and remaining services after approval" in result.stdout
    assert "https://login." not in result.stdout + result.stderr


@pytest.mark.parametrize("arguments", [["kasey"], ["--", "-abc"]])
def test_password_recovery_refuses_non_tty_before_touching_home(tmp_path, arguments):
    result = subprocess.run(
        ["bash", str(WRAPPER), "reset-password", *arguments],
        input="private-input-test\n",
        env={**os.environ, "HOME": str(tmp_path), "PREFIX": ""},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "interactive Termux terminal" in result.stderr
    assert "private-input-test" not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_password_recovery_does_not_echo_extra_password_argument(tmp_path):
    result = subprocess.run(
        ["bash", str(WRAPPER), "reset-password", "kasey", "private-argument-test"],
        env={**os.environ, "HOME": str(tmp_path), "PREFIX": ""},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "hidden prompts" in result.stderr
    assert "private-argument-test" not in result.stdout + result.stderr


def test_password_recovery_forwards_terminal_and_username_to_guest(tmp_path):
    import pty
    import select

    executable = tmp_path / "proot-distro"
    executable.write_text(
        f"#!{sys.executable}\nimport sys,json; "
        "print(json.dumps({'tty':sys.stdin.isatty(),'args':sys.argv[1:]}))\n"
    )
    executable.chmod(0o700)
    code = (
        f"source {shlex.quote(str(WRAPPER))}\n"
        "require_termux() { :; }; require_proot() { :; }; require_owned() { :; }\n"
        "main reset-password -- -abc"
    )
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["bash", "-c", code],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )
    os.close(slave)
    output = bytearray()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            elif process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, output.decode(errors="replace")
        forwarded = json.loads(output)
        assert forwarded["tty"] is True
        assert forwarded["args"][-3:] == ["reset-password", "--", "-abc"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        os.close(master)


def test_desktop_install_fails_before_touching_home(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(WRAPPER), "install"],
        env={**os.environ, "HOME": str(tmp_path), "PREFIX": "/usr"},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "inside the official Termux" in result.stderr
    assert list(tmp_path.iterdir()) == []


def cli_fixture(tmp_path, tag="android-v0.1.0-preview.5"):
    home = tmp_path / "private home"
    prefix = tmp_path / "prefix"
    source = home / ".local/share/schemii/releases" / tag
    source.mkdir(parents=True, exist_ok=True)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    bash = prefix / "bin/bash"
    if not bash.exists():
        bash.symlink_to("/bin/bash")
    (source / "ANDROID_RELEASE.json").write_text(
        json.dumps({"schema": 1, "tag": tag, "experimental": True})
    )
    (source / "start.sh").write_text(
        "python3 - \"$@\" <<'PY'\n"
        "import json,os,pathlib,sys\n"
        "pathlib.Path(os.environ['CLI_RECORD']).write_text(json.dumps("
        "{'source':str(pathlib.Path.cwd()),'arguments':sys.argv[1:]}))\n"
        "PY\n"
    )
    return home, prefix, source


def cli_action(home, prefix, source, action="activate", extra_env=None):
    return shell(
        f"SCHEMII_ANDROID_ROOT={shlex.quote(str(source))}\nmanage_cli {action}",
        env={
            "HOME": str(home),
            "PREFIX": str(prefix),
            **(extra_env or {}),
        },
    )


def test_global_cli_runs_from_any_directory_and_preserves_arguments(tmp_path):
    home, prefix, source = cli_fixture(tmp_path)
    result = cli_action(home, prefix, source)
    assert result.returncode == 0, result.stderr
    assert (home / "schemii").readlink() == source
    command = prefix / "bin/schemii"
    assert command.stat().st_mode & 0o777 == 0o700
    unrelated = tmp_path / "unrelated directory"
    unrelated.mkdir()
    record = tmp_path / "cli.json"
    for action in ("start", "stop", "restart", "status", "logs", "--help"):
        result = subprocess.run(
            [str(command), action, "literal argument; no execution"],
            cwd=unrelated,
            env={**os.environ, "CLI_RECORD": str(record)},
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(record.read_text())["arguments"] == [
            "--android",
            action,
            "literal argument; no execution",
        ]


def test_cli_upgrade_atomically_selects_new_owned_release(tmp_path):
    home, prefix, old = cli_fixture(tmp_path, "android-v0.1.0-preview.4")
    assert cli_action(home, prefix, old).returncode == 0
    wrapper = (prefix / "bin/schemii").read_bytes()
    _, _, new = cli_fixture(tmp_path)
    assert cli_action(home, prefix, new, "check").returncode == 0
    assert (home / "schemii").readlink() == old
    upgraded = cli_action(home, prefix, new)
    assert upgraded.returncode == 0, upgraded.stderr
    assert (home / "schemii").readlink() == new
    assert (prefix / "bin/schemii").read_bytes() == wrapper
    assert old.is_dir()
    assert not list(home.glob(".schemii-active-*"))
    assert not list((prefix / "bin").glob(".schemii-command-*"))


@pytest.mark.parametrize(
    "conflict",
    [
        "folder",
        "outside-link",
        "relative-link",
        "unowned-release-link",
        "invalid-release-manifest",
        "foreign-command",
        "command-symlink",
        "modified-command",
    ],
)
def test_cli_preflight_preserves_foreign_paths(tmp_path, conflict):
    home, prefix, source = cli_fixture(tmp_path)
    active, command = home / "schemii", prefix / "bin/schemii"
    outside = tmp_path / "user files"
    outside.mkdir()
    (outside / "important.txt").write_text("keep")
    if conflict == "folder":
        active.mkdir()
        (active / "important.txt").write_text("keep")
    elif conflict == "outside-link":
        active.symlink_to(outside)
    elif conflict == "relative-link":
        active.symlink_to(source.relative_to(home))
    elif conflict == "unowned-release-link":
        unowned = source.parent / "android-v0.1.0-preview.3"
        unowned.mkdir()
        active.symlink_to(unowned)
    elif conflict == "invalid-release-manifest":
        invalid = source.parent / "android-v0.1.0-preview.3"
        invalid.mkdir()
        (invalid / "ANDROID_RELEASE.json").write_text("[]")
        (invalid / "start.sh").write_text("user-owned source")
        active.symlink_to(invalid)
    elif conflict == "command-symlink":
        command.symlink_to(outside / "important.txt")
    elif conflict == "modified-command":
        assert cli_action(home, prefix, source).returncode == 0
        command.write_text(command.read_text() + "# user's changes\n")
    else:
        command.write_text("user command")
    old_link = os.readlink(active) if active.is_symlink() else None
    old_command = command.read_bytes() if command.is_file() else None
    result = cli_action(home, prefix, source, "check")
    assert result.returncode != 0
    assert "preserved" in result.stderr
    assert (outside / "important.txt").read_text() == "keep"
    if old_link is not None:
        assert active.is_symlink() and os.readlink(active) == old_link
    if old_command is not None:
        assert command.read_bytes() == old_command
    if conflict == "folder":
        assert (active / "important.txt").read_text() == "keep"


def test_cli_conflict_blocks_install_before_package_or_guest_mutation(tmp_path):
    home, prefix, source = cli_fixture(tmp_path)
    control = home / ".local/share/schemii-android"
    control.mkdir()
    (home / "schemii").mkdir()
    called = tmp_path / "mutated"
    result = shell(
        f"SCHEMII_ANDROID_ROOT={shlex.quote(str(source))}\n"
        f"SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}\n"
        f"pkg() {{ touch {shlex.quote(str(called))}; }}\n"
        f"guest() {{ touch {shlex.quote(str(called))}; }}\n"
        "install_runtime",
        env={"HOME": str(home), "PREFIX": str(prefix)},
    )
    assert result.returncode != 0
    assert "Existing ~/schemii" in result.stderr
    assert not called.exists()


def test_cli_pointer_failure_preserves_previous_active_release(tmp_path):
    home, prefix, old = cli_fixture(tmp_path, "android-v0.1.0-preview.4")
    assert cli_action(home, prefix, old).returncode == 0
    wrapper = (prefix / "bin/schemii").read_bytes()
    _, _, new = cli_fixture(tmp_path)
    tools_dir = tmp_path / "injected tools"
    tools_dir.mkdir()
    shim = tools_dir / "python"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os,pathlib,sys\n"
        "sys.argv=sys.argv[1:]\n"
        "original=os.replace\n"
        "def replace(source,destination):\n"
        "    if str(destination)==os.environ['FAIL_ACTIVE']:\n"
        "        raise OSError('injected active pointer failure')\n"
        "    return original(source,destination)\n"
        "os.replace=replace\n"
        "exec(compile(sys.stdin.read(),'cli-activation','exec'))\n"
    )
    shim.chmod(0o700)
    result = cli_action(
        home,
        prefix,
        new,
        extra_env={
            "PATH": f"{tools_dir}:{os.environ['PATH']}",
            "FAIL_ACTIVE": str(home / "schemii"),
        },
    )
    assert result.returncode != 0
    assert "injected active pointer failure" in result.stderr
    assert (home / "schemii").readlink() == old
    assert (prefix / "bin/schemii").read_bytes() == wrapper
    assert not list(home.glob(".schemii-active-*"))
    assert not list((prefix / "bin").glob(".schemii-command-*"))


@pytest.mark.parametrize("guest_status", [0, 7])
def test_installer_selects_new_cli_only_after_dependencies_succeed(
    tmp_path, guest_status
):
    home, prefix, old = cli_fixture(tmp_path, "android-v0.1.0-preview.4")
    assert cli_action(home, prefix, old).returncode == 0
    _, _, new = cli_fixture(tmp_path)
    control = home / ".local/share/schemii-android"
    control.mkdir()
    (prefix / "var/lib/proot-distro/containers/schemii-android/rootfs/opt").mkdir(
        parents=True
    )
    record = tmp_path / "install-actions"
    result = shell(
        f"SCHEMII_ANDROID_ROOT={shlex.quote(str(new))}\n"
        f"SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}\n"
        "require_proot() { :; }\n"
        "require_owned() { :; }\n"
        "host_running() { return 1; }\n"
        f"pkg() {{ printf 'packages\\n' >> {shlex.quote(str(record))}; }}\n"
        f"guest() {{ printf 'dependencies\\n' >> {shlex.quote(str(record))}; return {guest_status}; }}\n"
        "install_runtime",
        env={"HOME": str(home), "PREFIX": str(prefix)},
    )
    assert result.returncode == guest_status, result.stderr
    assert record.read_text().splitlines() == ["packages", "dependencies"]
    assert (home / "schemii").readlink() == (new if guest_status == 0 else old)


def test_restart_stops_then_starts_under_one_host_lifecycle_lock(tmp_path):
    record = tmp_path / "lifecycle"
    result = shell(
        "require_termux() { :; }\n"
        "require_proot() { :; }\n"
        "require_owned() { :; }\n"
        "require_installed_source() { :; }\n"
        "host_running() { return 1; }\n"
        f"""runtime() {{
  [[ "$1" == stop ]]
  if flock -n "$SCHEMII_ANDROID_CONTROL/launcher.lock" -c true; then return 91; fi
  printf 'stop\\n' >> {shlex.quote(str(record))}
}}
start_runtime() {{
  if flock -n "$SCHEMII_ANDROID_CONTROL/launcher.lock" -c true; then return 92; fi
  printf 'start\\n' >> {shlex.quote(str(record))}
}}
main restart
""",
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines() == ["stop", "start"]


def test_restart_does_not_start_when_stop_fails(tmp_path):
    record = tmp_path / "lifecycle"
    result = shell(
        f"runtime() {{ printf 'stop\\n' >> {shlex.quote(str(record))}; return 17; }}\n"
        f"start_runtime() {{ printf 'start\\n' >> {shlex.quote(str(record))}; }}\n"
        "restart_runtime"
    )
    assert result.returncode == 17
    assert record.read_text().splitlines() == ["stop"]


def test_stop_waits_for_outer_owned_runner_before_restart(tmp_path):
    result = shell(
        'runtime() { [[ "$1" == stop ]]; }\n'
        "polls=0\n"
        "host_running() { polls=$((polls+1)); ((polls<3)); }\n"
        "sleep() { :; }\n"
        "stop_runtime\n"
        '[[ "$polls" == 3 ]]'
    )
    assert result.returncode == 0, result.stderr


def test_restart_refuses_to_start_if_outer_session_has_not_closed(tmp_path):
    record = tmp_path / "lifecycle"
    result = shell(
        f"runtime() {{ printf 'stop\\n' >> {shlex.quote(str(record))}; }}\n"
        "SECONDS=0\n"
        "host_running() { SECONDS=20; return 0; }\n"
        f"start_runtime() {{ printf 'start\\n' >> {shlex.quote(str(record))}; }}\n"
        "restart_runtime"
    )
    assert result.returncode != 0
    assert "PRoot session is still closing" in result.stderr
    assert record.read_text().splitlines() == ["stop"]


def test_guest_preserves_each_argument_and_uses_isolated_userland(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "proot-distro"
    executable.write_text(
        f"#!{sys.executable}\nimport sys,json; print(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o700)
    result = shell(
        'SCHEMII_ANDROID_ROOT="/private/release with spaces"\n'
        'SCHEMII_ANDROID_CONTROL="/private/control"\n'
        'guest schemii /bin/test "a b" "literal;echo nope"',
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[:4] == ["login", "--isolated", "--user", "schemii"]
    assert args[args.index("--env") + 1] == "PROOT_DONT_SHARE_LIBANDROID_SHMEM=1"
    assert "/private/release with spaces:/opt/schemii" in args
    assert args[-4:] == ["--", "/bin/test", "a b", "literal;echo nope"]


def test_background_supervisor_uses_builtin_ipc_in_the_initial_proot_environment(
    tmp_path,
):
    executable = tmp_path / "proot-distro"
    record = tmp_path / "invocation.json"
    executable.write_text(
        f"#!{sys.executable}\nimport json,os,sys\nopen(os.environ['INVOCATION_RECORD'],'w').write(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o700)
    control = tmp_path / "control"
    control.mkdir()
    result = shell(
        f"""SCHEMII_ANDROID_ROOT=/private/source
SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}
calls=0
runtime() {{
  calls=$((calls+1))
  if ((calls==1)); then return 1; fi
  if ((calls==2)); then printf '{{"state":"starting","startup_stage":"metadata init"}}\\n'; return 1; fi
  printf '{{"ready":true}}\\n'
}}
host_running() {{ ((calls>1)); }}
record_runner() {{ wait "$1"; }}
termux-wake-lock() {{ :; }}
sleep() {{ :; }}
start_runtime
""",
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "INVOCATION_RECORD": str(record),
        },
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(record.read_text())
    assert args[args.index("--env") + 1] == "PROOT_DONT_SHARE_LIBANDROID_SHMEM=1"
    assert args[-1] == "run"
    assert "Startup: metadata init" in result.stdout


def test_readonly_guest_status_has_an_outer_timeout_even_if_proot_hangs(tmp_path):
    executable = tmp_path / "proot-distro"
    executable.write_text("#!/bin/sh\nexec sleep 60\n")
    executable.chmod(0o700)
    started = time.monotonic()
    result = shell(
        "SCHEMII_ANDROID_ROOT=/private/source\nSCHEMII_ANDROID_CONTROL=/private/control\nruntime status",
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert result.returncode == 124
    assert time.monotonic() - started < 17


def test_old_proot_cli_is_rejected_without_fallback() -> None:
    result = shell('proot-distro() { printf "old aliases only\\n"; }; require_proot')
    assert result.returncode != 0
    assert "OCI-capable" in result.stderr


@pytest.mark.parametrize("stream", [1, 2])
def test_supported_proot_help_accepts_stdout_and_actual_stderr_channel(stream):
    # Official 5.10.3 uses msg() on stderr for successful command help.
    result = shell(f"""proot-distro() {{
  case "$1" in
    install) printf 'Usage: proot-distro install --name NAME IMAGE\\n' >&{stream};;
    login) printf 'Usage: proot-distro login --isolated --bind PATH --user USER --work-dir DIR --env KEY=VALUE\\n' >&{stream};;
  esac
}}
require_proot
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("failed_command", ["install", "login"])
def test_failed_help_cannot_pass_by_printing_supported_options(failed_command):
    result = shell(f"""proot-distro() {{
  printf '%s\\n' '--name --isolated --bind --user --work-dir --env' >&2
  [[ "$1" != {shlex.quote(failed_command)} ]]
}}
require_proot
""")
    assert result.returncode != 0
    assert f"Could not inspect PRoot-Distro {failed_command} options" in result.stderr
    assert "Update the proot-distro" not in result.stderr


@pytest.mark.parametrize(
    "missing", ["--name", "--isolated", "--bind", "--user", "--work-dir", "--env"]
)
def test_missing_proot_capability_is_still_rejected(missing):
    install_options = "--name" if missing != "--name" else ""
    login_options = " ".join(
        option
        for option in ["--isolated", "--bind", "--user", "--work-dir", "--env"]
        if option != missing
    )
    result = shell(f"""proot-distro() {{
  if [[ "$1" == install ]]; then printf '%s\\n' {shlex.quote(install_options)} >&2
  else printf '%s\\n' {shlex.quote(login_options)} >&2; fi
}}
require_proot
""")
    assert result.returncode != 0
    assert "OCI-capable" in result.stderr


@pytest.mark.parametrize(
    "name", ["launcher.lock", "launcher.log", "status.json", "runner.tmp"]
)
def test_control_symlinks_are_rejected_without_overwrite(
    tmp_path: Path, name: str
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("preserve")
    control = tmp_path / "control"
    control.mkdir()
    (control / name).symlink_to(victim)
    result = shell(
        f"SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}\nsafe_control_paths"
    )
    assert result.returncode != 0
    assert victim.read_text() == "preserve"


def test_unowned_proot_alias_is_preserved(tmp_path: Path) -> None:
    rootfs = tmp_path / "var/lib/proot-distro/containers/schemii-android/rootfs"
    rootfs.mkdir(parents=True)
    result = shell(f"PREFIX={shlex.quote(str(tmp_path))}\nrequire_owned")
    assert result.returncode != 0
    assert "no Schemii ownership marker" in result.stderr
    assert list(rootfs.iterdir()) == []


def test_wrong_image_identity_is_refused(tmp_path: Path) -> None:
    rootfs = tmp_path / "var/lib/proot-distro/containers/schemii-android/rootfs"
    (rootfs / "opt").mkdir(parents=True)
    (rootfs / "opt/schemii-install-owned").write_text("unrelated-image\n")
    result = shell(f"PREFIX={shlex.quote(str(tmp_path))}\nrequire_owned")
    assert result.returncode != 0
    assert "image identity differs" in result.stderr


def test_install_refuses_live_runner_before_package_mutation(tmp_path: Path) -> None:
    control = tmp_path / "control"
    control.mkdir()
    # The shell process must remain live for the whole installation invocation.
    code = f"""SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}
python() {{ command python3 "$@"; }}
record_runner $$
pkg() {{ touch {shlex.quote(str(tmp_path / "pkg-called"))}; }}
install_runtime
"""
    result = shell(code)
    assert result.returncode != 0
    assert "Stop it before installing" in result.stderr
    assert not (tmp_path / "pkg-called").exists()


def test_stale_runner_record_does_not_match_reused_pid(tmp_path: Path) -> None:
    (tmp_path / "runner.json").write_text(
        json.dumps({"pid": os.getpid(), "start_tick": "wrong"})
    )
    result = shell(
        f"SCHEMII_ANDROID_CONTROL={shlex.quote(str(tmp_path))}\n"
        'python() { command python3 "$@"; }; host_running'
    )
    assert result.returncode != 0


def test_boot_enable_preserves_foreign_script(tmp_path: Path) -> None:
    boot = tmp_path / ".termux/boot"
    boot.mkdir(parents=True)
    (boot / "schemii").write_text("#!/bin/sh\necho user-owned\n")
    result = shell(
        "PREFIX=/data/data/com.termux/files/usr\n"
        'SCHEMII_ANDROID_ROOT="/private/release"\nmanage_boot enable-boot',
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "user-owned" in (boot / "schemii").read_text()


def test_boot_script_uses_global_start_and_disable_removes_only_owned(
    tmp_path: Path,
) -> None:
    result = shell(
        "PREFIX=/data/data/com.termux/files/usr\n"
        'SCHEMII_ANDROID_ROOT="/private/release with spaces"\nmanage_boot enable-boot',
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    boot = tmp_path / ".termux/boot/schemii"
    assert "exec /data/data/com.termux/files/usr/bin/schemii start" in boot.read_text()
    assert "/private/release" not in boot.read_text()
    unrelated = boot.parent / "other"
    unrelated.write_text("preserve")
    result = shell("manage_boot disable-boot", env={"HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert not boot.exists()
    assert unrelated.read_text() == "preserve"


def test_guest_installer_cannot_run_directly_on_host() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "dev/android/install-guest.sh")],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert (
        "Dedicated runtime ownership" in result.stderr
        or "dedicated PRoot root" in result.stderr
    )


def test_guest_live_state_blocks_install_even_without_host_runner(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    rootfs = tmp_path / "prefix/var/lib/proot-distro/containers/schemii-android/rootfs"
    (rootfs / "opt").mkdir(parents=True)
    (rootfs / "opt/schemii-runtime.json").write_text("{}")
    result = shell(f"""SCHEMII_ANDROID_CONTROL={shlex.quote(str(control))}
PREFIX={shlex.quote(str(tmp_path / "prefix"))}
pkg() {{ touch {shlex.quote(str(tmp_path / "pkg-called"))}; }}
require_proot() {{ :; }}
require_owned() {{ :; }}
runtime() {{ [[ $1 == assert-stopped ]]; printf 'Guest supervisor is live\\n' >&2; return 9; }}
install_runtime
""")
    assert result.returncode == 9
    assert "Guest supervisor is live" in result.stderr
    assert not (tmp_path / "pkg-called").exists()


def test_boot_temporary_symlink_is_preserved(tmp_path: Path) -> None:
    boot = tmp_path / ".termux/boot"
    boot.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("preserve")
    (boot / "schemii.tmp").symlink_to(victim)
    result = shell(
        "PREFIX=/data/data/com.termux/files/usr\n"
        'SCHEMII_ANDROID_ROOT="/private/release"\nmanage_boot enable-boot',
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert victim.read_text() == "preserve"
    assert (boot / "schemii.tmp").is_symlink()


def activation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    base = tmp_path / "guest"
    source = base / "opt/schemii"
    for name in [
        "constraints.docker.txt",
        "pyproject.toml",
        "ai/prototype/package-lock.json",
        "ai/prototype/package.json",
        "ai/prototype/server.js",
        "src/schemii/main.py",
    ]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    old = base / "opt/old-environment"
    new = base / "opt/new-environment"
    old_node = base / "opt/old-node"
    new_node = base / "opt/new-node"
    for directory in [old, new, old_node, new_node]:
        directory.mkdir()
        (directory / "executable").write_text("preserve\n")
    (base / "opt/schemii-venv").symlink_to(old)
    (base / "opt/node").symlink_to(old_node)
    (base / "opt/schemii-runtime.json").write_text('{"previous":true}\n')
    tools = tmp_path / "tools"
    tools.mkdir()
    query = tools / "dpkg-query"
    query.write_text("#!/bin/sh\nprintf 'postgresql-17=17.fixture\\n'\n")
    query.chmod(0o700)
    return base, new, new_node, tools


def activate(
    base: Path, new: Path, node: Path, tools: Path
) -> subprocess.CompletedProcess[str]:
    script = ROOT / "dev/android/install-guest.sh"
    return subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(script))}\n"
            f"activate_runtime {shlex.quote(str(base))} "
            f"{shlex.quote(str(new))} {shlex.quote(str(node))}",
        ],
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )


def test_activation_manifest_failure_rolls_back_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    base, new, node, tools = activation_fixture(tmp_path)
    old_venv = (base / "opt/schemii-venv").readlink()
    old_node = (base / "opt/node").readlink()
    shim = tools / "python3"
    shim.write_text(f"""#!{sys.executable}
import os,pathlib,sys
sys.argv=sys.argv[1:]
original=os.replace
failed=False
def replace(source,destination):
    global failed
    if pathlib.Path(destination).name=='schemii-runtime.json' and not failed:
        failed=True
        raise OSError('injected manifest activation failure')
    return original(source,destination)
os.replace=replace
exec(compile(sys.stdin.read(),'activation-helper','exec'))
""")
    shim.chmod(0o700)
    result = activate(base, new, node, tools)
    assert result.returncode != 0
    assert "injected manifest activation failure" in result.stderr
    assert (base / "opt/schemii-venv").readlink() == old_venv
    assert (base / "opt/node").readlink() == old_node
    assert (base / "opt/schemii-runtime.json").read_text() == '{"previous":true}\n'
    assert (old_venv / "executable").read_text() == "preserve\n"
    assert (new / "executable").read_text() == "preserve\n"
    assert not list((base / "opt").glob(".schemii-activate-*"))
    shim.unlink()
    result = activate(base, new, node, tools)
    assert result.returncode == 0, result.stderr
    assert (base / "opt/schemii-venv").readlink() == new
    assert (base / "opt/node").readlink() == node
    manifest = json.loads((base / "opt/schemii-runtime.json").read_text())
    assert manifest["venv"] == str(new)
    assert manifest["postgresql_major"] == 17


def test_activation_manifest_stage_failure_keeps_executable_links(
    tmp_path: Path,
) -> None:
    base, new, node, tools = activation_fixture(tmp_path)
    old_venv = (base / "opt/schemii-venv").readlink()
    old_node = (base / "opt/node").readlink()
    shim = tools / "python3"
    shim.write_text(f"""#!{sys.executable}
import os,sys
sys.argv=sys.argv[1:]
def fail(*args):
    raise OSError('injected manifest fsync failure')
os.fsync=fail
exec(compile(sys.stdin.read(),'activation-helper','exec'))
""")
    shim.chmod(0o700)
    result = activate(base, new, node, tools)
    assert result.returncode != 0
    assert "injected manifest fsync failure" in result.stderr
    assert (base / "opt/schemii-venv").readlink() == old_venv
    assert (base / "opt/node").readlink() == old_node
    assert (base / "opt/schemii-runtime.json").read_text() == '{"previous":true}\n'
    assert not list((base / "opt").glob(".schemii-activate-*"))


def test_failed_rollback_retains_active_environment_for_repair(tmp_path: Path) -> None:
    base, new, node, tools = activation_fixture(tmp_path)
    old_venv = (base / "opt/schemii-venv").readlink()
    shim = tools / "python3"
    shim.write_text(f"""#!{sys.executable}
import os,pathlib,sys
sys.argv=sys.argv[1:]
original=os.replace
def replace(source,destination):
    destination=pathlib.Path(destination)
    if destination.name=='schemii-runtime.json':
        raise OSError('injected manifest rename failure')
    if destination.name=='schemii-venv' and os.readlink(source)=={str(old_venv)!r}:
        raise OSError('injected rollback failure')
    return original(source,destination)
os.replace=replace
exec(compile(sys.stdin.read(),'activation-helper','exec'))
""")
    shim.chmod(0o700)
    result = activate(base, new, node, tools)
    assert result.returncode != 0
    assert "Activation rollback incomplete" in result.stderr
    assert (base / "opt/schemii-venv").readlink() == new
    # Exercise the same cleanup function used by the real installation EXIT trap.
    cleanup = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(ROOT / 'dev/android/install-guest.sh'))}\n"
            f"discard_unactivated_environment {shlex.quote(str(new))} "
            f"{shlex.quote(str(base / 'opt/schemii-venv'))}",
        ],
        text=True,
        capture_output=True,
    )
    assert cleanup.returncode == 0, cleanup.stderr
    assert (new / "executable").read_text() == "preserve\n"
    shim.unlink()
    repaired = activate(base, new, node, tools)
    assert repaired.returncode == 0, repaired.stderr
    assert json.loads((base / "opt/schemii-runtime.json").read_text())["venv"] == str(
        new
    )


def test_python_install_uses_only_clean_manifest_source_not_stale_build(tmp_path):
    source = tmp_path / "release"
    source.mkdir()
    names = [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "constraints.docker.txt",
        "src/schemii/main.py",
    ]
    files = {}
    import hashlib

    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("verified source " + name)
        files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (source / "ANDROID_RELEASE.json").write_text(
        json.dumps({"schema": 1, "files": files})
    )
    stale = source / "build/lib/schemii/extra_unreleased.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("unreleased wheel injection")
    generated = source / "src/schemii.egg-info/SOURCES.txt"
    generated.parent.mkdir()
    generated.write_text(str(stale))
    clean = tmp_path / "clean-source"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(ROOT / 'dev/android/install-guest.sh'))}\nprepare_python_source {shlex.quote(str(source))} {shlex.quote(str(clean))}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert {
        str(path.relative_to(clean)) for path in clean.rglob("*") if path.is_file()
    } == set(names)
    assert stale.is_file() and generated.is_file()
    assert not (clean / "build").exists()
