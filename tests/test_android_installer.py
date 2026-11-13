"""Host-side installer controls; these do not claim Android/PRoot acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

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
    assert list(tmp_path.iterdir()) == []


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
    assert "/private/release with spaces:/opt/schemii" in args
    assert args[-4:] == ["--", "/bin/test", "a b", "literal;echo nope"]


def test_old_proot_cli_is_rejected_without_fallback() -> None:
    result = shell('proot-distro() { printf "old aliases only\\n"; }; require_proot')
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


def test_boot_script_quotes_release_path_and_disable_removes_only_owned(
    tmp_path: Path,
) -> None:
    result = shell(
        "PREFIX=/data/data/com.termux/files/usr\n"
        'SCHEMII_ANDROID_ROOT="/private/release with spaces"\nmanage_boot enable-boot',
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    boot = tmp_path / ".termux/boot/schemii"
    assert "release\\ with\\ spaces/start.sh" in boot.read_text()
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
