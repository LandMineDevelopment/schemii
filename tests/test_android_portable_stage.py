"""Stage-only launcher dispatch, ownership, and disposable PostgreSQL checks."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess

import pytest

from dev.android import portable_stage as stage, recovery, runtime
from test_android_portable_restore import export_fixture, write_private
from test_android_portable_restore import postgres_bin as postgres_bin


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def destination(tmp_path, monkeypatch):
    active = runtime.Runtime(state=tmp_path / "destination/active", profile="termux")
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    monkeypatch.setattr(runtime, "DEFAULT_STATE", active.state)
    monkeypatch.setattr(stage, "CONTROL_BIND", control)
    monkeypatch.setattr(recovery, "_runtime_fingerprint", lambda: {"fixture": True})
    return active, control


def stage_path(active):
    return list(active.state.parent.glob(".schemii-portable-*"))


@pytest.mark.parametrize(
    "lease", ["native-host", "supervisor", "recovery", "lifecycle"]
)
def test_busy_destination_refuses_before_opening_bundle(destination, tmp_path, lease):
    active, control = destination
    with active._file_lock(lease), pytest.raises(runtime.RuntimeFailure, match="busy"):
        stage._stage(active, tmp_path / "missing-bundle", control, runtime.PG_BIN)
    assert not active.state.exists()
    assert not stage_path(active)


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("live-runner", "destination is active"),
        ("unreadable-owner", "ownership is unverifiable"),
        ("incomplete-runner", "incomplete Termux runner"),
        ("bad-receipt", "ownership record is unverifiable"),
        ("stale-postmaster", "uncleared postmaster.pid"),
    ],
)
def test_unverified_destination_never_opens_transfer(
    destination, tmp_path, monkeypatch, marker, expected
):
    active, control = destination
    owner = runtime.process_identity(os.getpid())
    if marker in {"live-runner", "unreadable-owner"}:
        write_private(
            control / "runner.json",
            json.dumps(
                {
                    "pid": owner["pid"],
                    "start_tick": str(owner["start_ticks"]),
                    "boot_id": owner["boot_id"],
                }
            ).encode(),
        )
        if marker == "unreadable-owner":
            monkeypatch.setattr(runtime, "process_identity", lambda _: None)
    elif marker == "incomplete-runner":
        write_private(control / "runner.tmp", b"partial")
    elif marker == "bad-receipt":
        write_private(control / "host.json", b"[]")
    else:
        pidfile = active.state / "postgres/target/postmaster.pid"
        pidfile.parent.mkdir(parents=True)
        write_private(pidfile, b"unverified")
    with pytest.raises((runtime.RuntimeFailure, stage.StageError), match=expected):
        stage._stage(active, tmp_path / "missing-bundle", control, runtime.PG_BIN)
    assert not stage_path(active)


def test_cli_keeps_stage_out_of_apk_and_other_guest_paths(destination):
    active, _ = destination
    with pytest.raises(stage.StageError, match="installed Termux launcher"):
        stage.stage_portable(active, Path("/tmp/other-bundle"))
    with pytest.raises(stage.StageError, match="installed Termux launcher"):
        stage.stage_portable(
            runtime.Runtime(state=active.state, profile="apk"), stage.BUNDLE_BIND
        )


@pytest.fixture
def host_paths(tmp_path):
    home = tmp_path / "home"
    paths = home, tmp_path / "prefix", home / "source", home / "control"
    for path in paths:
        path.mkdir(mode=0o700)
    return paths


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "symlink",
        "link-parent",
        "public",
        "release",
        "control",
        "outside",
        "colon",
        "dotdot",
    ],
)
def test_host_accepts_only_a_separate_absolute_private_bundle(
    host_paths, tmp_path, case
):
    home, _, release, control = host_paths
    valid = home / "bundle"
    valid.mkdir(mode=0o700)
    stage.check_host_bundle(str(valid), *host_paths)
    path = {
        "relative": "bundle",
        "symlink": home / "alias",
        "link-parent": home / "alias/subdir",
        "public": home / "public",
        "release": release / "bundle",
        "control": control / "bundle",
        "outside": tmp_path / "outside",
        "colon": home / "bundle:extra",
        "dotdot": home / "../home/bundle",
    }[case]
    if case in {"symlink", "link-parent"}:
        (home / "alias").symlink_to(valid, target_is_directory=True)
        (valid / "subdir").mkdir(mode=0o700)
    elif case in {"public", "release", "control", "outside", "colon"}:
        path.mkdir(mode=0o755 if case == "public" else 0o700)
        if case == "public":
            path.chmod(0o755)
    with pytest.raises(recovery.RecoveryError):
        stage.check_host_bundle(str(path), *host_paths)


@pytest.mark.parametrize(
    ("action", "bundle_args", "installed", "expected"),
    [
        ("stage-portable", ["ABSOLUTE"], True, ["source-check", "transfer", "proot"]),
        ("stage-portable", ["ABSOLUTE"], False, ["source-check"]),
        ("stage-portable", [], True, []),
        ("stage-portable", ["relative"], True, []),
        ("stage-portable", ["ABSOLUTE", "extra"], True, []),
        ("restore", ["ABSOLUTE"], True, ["proot"]),
        ("start", [], True, ["source-check", "normal-start"]),
    ],
)
def test_termux_dispatches_stage_only_after_installed_source(
    host_paths, action, bundle_args, installed, expected
):
    home, prefix, _, _ = host_paths
    bundle = home / "bundle with spaces"
    arguments = [str(bundle) if arg == "ABSOLUTE" else arg for arg in bundle_args]
    shell = f"""
source {shlex.quote(str(ROOT / "dev/android/termux.sh"))}
require_termux() {{ :; }}
require_proot() {{ :; }}
require_owned() {{ :; }}
require_installed_source() {{ printf 'source-check\\n'; return {0 if installed else 1}; }}
start_runtime() {{ printf 'normal-start\\n'; }}
python() {{ printf 'transfer\\n'; }}
proot-distro() {{ printf 'proot\\n'; printf '[%s]\\n' "$@"; }}
main "$@"
"""
    result = subprocess.run(
        ["bash", "-c", shell, "termux", action, *arguments],
        env={**os.environ, "HOME": str(home), "PREFIX": str(prefix)},
        capture_output=True,
        text=True,
        check=False,
    )
    lines = result.stdout.splitlines()
    assert [line for line in lines if not line.startswith("[")] == expected
    assert result.returncode == (
        0
        if "proot" in expected or "normal-start" in expected
        else (1 if expected else 2)
    )
    if "proot" in expected:
        assert "[PROOT_DONT_SHARE_LIBANDROID_SHMEM=1]" in lines
        assert "[schemii]" in lines
        if action == "stage-portable":
            assert f"[{bundle}:{stage.BUNDLE_BIND}]" in lines
            assert lines[-3:] == [
                "[/opt/schemii/dev/android/runtime.py]",
                "[stage-portable]",
                f"[{stage.BUNDLE_BIND}]",
            ]
        else:
            assert all(str(stage.BUNDLE_BIND) not in line for line in lines)
            assert lines[-2:] == ["[restore]", f"[{bundle}]"]


def test_termux_stage_holds_host_launcher_lock(host_paths):
    home, prefix, _, _ = host_paths
    shell = f"""
source {shlex.quote(str(ROOT / "dev/android/termux.sh"))}
require_termux() {{ :; }}
require_proot() {{ :; }}
require_owned() {{ :; }}
require_installed_source() {{ :; }}
python() {{ :; }}
proot-distro() {{
    if flock -n "$SCHEMII_ANDROID_CONTROL/launcher.lock" true 9>&-; then
        exit 91
    fi
}}
main stage-portable "$HOME/bundle"
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={**os.environ, "HOME": str(home), "PREFIX": str(prefix)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_payload_changed_between_verification_and_copy(tmp_path):
    transfer = tmp_path / "transfer"
    transfer.mkdir(mode=0o700)
    identities = {}
    for plane in recovery.PORTABLE_CLUSTERS:
        identities[plane] = {
            "bootstrap_user": "bootstrap",
            "app_user": "fixture",
            "primary_database": "fixture",
            "databases": ["fixture"],
        }
        (transfer / plane).mkdir(mode=0o700)
        write_private(transfer / plane / "roles.sql", b"fixture")
        write_private(transfer / plane / "fixture.dump", b"PGDMPfixture")
    (transfer / "secrets").mkdir(mode=0o700)
    for secret in recovery.SECRETS:
        value = b"A" * 43 + b"=" if secret == "metadata_encryption_key" else b"fixture"
        write_private(transfer / "secrets" / secret, value)
    recovery.seal_portable(transfer, identities)
    verified = recovery.verify_portable(transfer)
    write_private(transfer / "target/fixture.dump", b"PGDMPchanged")
    with pytest.raises(recovery.RecoveryError, match="checksum"):
        stage._copy_bundle(transfer, tmp_path / "copy", verified)


@pytest.fixture(scope="module")
def fixture_bundle(tmp_path_factory, postgres_bin):
    bundle = tmp_path_factory.mktemp("transfer") / "portable"
    export_fixture(postgres_bin, bundle, "linux")
    return bundle


@pytest.mark.parametrize("mode", ["success", "late-archive", "shutdown"])
def test_postgres_stage_publishes_only_a_clean_dual_plane_pair(
    destination, fixture_bundle, postgres_bin, monkeypatch, capsys, mode
):
    active, _ = destination
    monkeypatch.setattr(stage, "BUNDLE_BIND", fixture_bundle)
    monkeypatch.setattr(runtime, "PG_BIN", postgres_bin)
    active.state.mkdir(parents=True)
    sentinel = active.state / "config.json"
    write_private(sentinel, b"untouched")
    stale = active.state.parent / ".schemii-portable-stale"
    stale.symlink_to(active.state, target_is_directory=True)
    # An inherited libpq service must not divert any restore connection.
    monkeypatch.setenv("PGSERVICE", "invalid-portable-stage-fixture")
    monkeypatch.setenv("PGOPTIONS", "-c default_transaction_read_only=on")
    events = []
    original_tool = stage.Stage._tool
    original_shutdown = stage.Stage.check_shutdown

    def tool(self, name, *args, **kwargs):
        if (
            name == "pg_restore"
            and self.phase == "target-archives"
            and mode == "late-archive"
        ):
            # Use a real failing postgres client command after the target has started.
            args = (*args[:-1], self.root / "missing.dump")
        return original_tool(self, name, *args, **kwargs)

    def shutdown(self, plane, identifier):
        original_shutdown(self, plane, identifier)
        events.append(plane)
        if plane == "target" and mode == "shutdown":
            raise stage.StageError("injected shutdown verification failure")

    monkeypatch.setattr(stage.Stage, "_tool", tool)
    monkeypatch.setattr(stage.Stage, "check_shutdown", shutdown)
    before = recovery.verify_portable(fixture_bundle)
    assert runtime.main(["stage-portable", str(fixture_bundle)]) == (
        0 if mode == "success" else 1
    )
    captured = capsys.readouterr()
    [owned] = [path for path in stage_path(active) if path != stale]
    assert sentinel.read_bytes() == b"untouched"
    assert list(active.state.iterdir()) == [sentinel]
    assert stale.is_symlink() and stale.resolve() == active.state
    assert recovery.verify_portable(fixture_bundle) == before
    for name in recovery.SECRETS:
        original = (fixture_bundle / "secrets" / name).read_bytes()
        assert (owned / "bundle/secrets" / name).read_bytes() == original
        assert original.decode().strip() not in captured.out + captured.err
    assert stat.S_IMODE(owned.stat().st_mode) == 0o700
    assert not list(owned.glob("*-pgdata/postmaster.pid"))
    assert not any(owned.glob("*.pgpass"))
    attempt = json.loads((owned / "attempt.json").read_text())
    assert attempt["processes"] == {}
    assert not {"config.json", "secrets", "postgres", "run", "tls"}.intersection(
        path.name for path in owned.iterdir()
    )
    receipt = owned / stage.RECEIPT
    if mode == "success":
        assert not captured.err
        assert json.loads(captured.out) == {"inspection_receipt": str(receipt)}
        assert attempt["status"] == "verified"
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
        record = json.loads(receipt.read_text())
        assert record["clean_shutdown"] is True
        assert (
            record["bundle_manifest_sha256"]
            == hashlib.sha256(
                (fixture_bundle / "manifest.json").read_bytes()
            ).hexdigest()
        )
        assert record["installed_runtime"] == {"fixture": True}
        assert (
            len(
                {
                    cluster["system_identifier"]
                    for cluster in record["clusters"].values()
                }
            )
            == 2
        )
        for plane, cluster in record["clusters"].items():
            del cluster["system_identifier"]
            assert cluster == before["clusters"][plane]
        assert events == ["metadata", "target", "metadata", "target"]
    else:
        assert not captured.out
        assert not receipt.exists()
        assert attempt["status"] == "quarantined"
        assert attempt["phase"] in captured.err
        assert events == (
            ["metadata"] if mode == "late-archive" else ["metadata", "target"]
        )
