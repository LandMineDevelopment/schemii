"""Cold snapshot safety checks; all databases and process state are fixtures."""

import base64
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "android_recovery", ROOT / "dev/android/recovery.py"
)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class FakeRuntime:
    def __init__(self, state):
        self.state = state
        self.calls = []

    @contextmanager
    def recovery_lock(self):
        self.calls.append("lock")
        try:
            yield
        finally:
            self.calls.append("unlock")

    def stop(self):
        self.calls.append("stop")

    def assert_stopped(self):
        self.calls.append("assert_stopped")


@pytest.fixture(autouse=True)
def supported_runtime(monkeypatch):
    monkeypatch.setattr(recovery, "_identity", recovery._supported_identity)
    monkeypatch.setattr(recovery, "_check_control", lambda _: None)
    monkeypatch.setattr(
        recovery,
        "_runtime_fingerprint",
        lambda: {
            "format": "schemii-android-install-v1",
            "debian": "13",
            "architecture": "arm64",
            "postgresql_major": 17,
            "python_minor": "3.13",
            "node": "22.22.2",
            "image": "debian:trixie-slim@sha256:" + "a" * 64,
            "installed_manifest_sha256": "b" * 64,
        },
    )


def write_private(path, data):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def make_state(path):
    path.mkdir(mode=0o700)
    write_private(
        path / "config.json",
        b'{"hostname":"phone.example.ts.net","tailnet_ip":"100.100.100.100"}',
    )
    for name in recovery.SECRETS:
        value = (
            base64.b64encode(b"original-key".ljust(32, b"!"))
            if name == "metadata_encryption_key"
            else b"original-private-secret"
        )
        write_private(path / "secrets" / name, value)
    for name in recovery.TLS_FILES:
        write_private(path / "tls" / name, b"original-private-TLS-fixture")
    for name in ("metadata", "target"):
        cluster = path / "postgres" / name
        write_private(cluster / "PG_VERSION", b"17\n")
        write_private(cluster / "global" / "pg_control", b"fixture-clean-control")
        write_private(
            cluster / "base" / "16384" / "16400", name.encode() + b"-original-row-data"
        )
        (cluster / "pg_wal").mkdir(mode=0o700)
    for directory in path.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o700)
    return FakeRuntime(path)


def make_bundle(tmp_path):
    runtime = make_state(tmp_path / "source")
    destination = tmp_path / "backup"
    recovery.backup(runtime, destination)
    return runtime, destination


def edit_manifest(bundle, edit):
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    edit(manifest)
    path.write_text(json.dumps(manifest))


def repack(bundle, transform):
    archive = bundle / recovery.ARCHIVE
    members = []
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            data = source.extractfile(member).read() if member.isfile() else b""
            members.append((copy.copy(member), data))
    members = transform(members)
    with tarfile.open(archive, "w:gz") as target:
        for member, data in members:
            target.addfile(member, io.BytesIO(data) if member.isfile() else None)
    edit_manifest(
        bundle,
        lambda manifest: manifest.update(
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest()
        ),
    )


def test_cold_snapshot_restores_both_clusters_and_original_keys_without_starting(
    tmp_path,
):
    runtime, bundle = make_bundle(tmp_path)
    assert runtime.calls == ["lock", "stop", "assert_stopped", "unlock"]
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in bundle.iterdir())
    target = FakeRuntime(tmp_path / "fresh")
    result = recovery.restore(target, bundle)
    assert target.calls == ["lock", "assert_stopped", "unlock"]
    assert result["runtime_stopped"] is True
    assert result["database_restore_verified"] is False
    for path in runtime.state.rglob("*"):
        if path.is_file():
            restored = target.state / path.relative_to(runtime.state)
            assert restored.read_bytes() == path.read_bytes()
            assert stat.S_IMODE(restored.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o700
        for path in target.state.rglob("*")
        if path.is_dir()
    )
    assert not any(tmp_path.glob(".fresh.restore-*"))
    assert runtime.state.exists() and bundle.exists()


@pytest.mark.parametrize(
    "retained", ["existing-file", "existing-config", "empty-cluster", "symlink"]
)
def test_restore_never_overwrites_existing_installation(tmp_path, retained):
    _, bundle = make_bundle(tmp_path)
    state = tmp_path / "retained"
    if retained == "existing-file":
        state.write_text("preserve")
    elif retained == "symlink":
        state.symlink_to(tmp_path / "source", target_is_directory=True)
    else:
        state.mkdir()
        if retained == "existing-config":
            (state / "config.json").write_text("preserve")
        else:
            (state / "postgres").mkdir()
    before = (tmp_path / "source/config.json").read_bytes()
    with pytest.raises(recovery.RecoveryError, match="existing|symbolic"):
        recovery.restore(FakeRuntime(state), bundle)
    assert (tmp_path / "source/config.json").read_bytes() == before
    assert state.exists()
    assert not any(tmp_path.glob(".retained.restore-*"))


def test_restore_accepts_empty_state_and_checks_integrity_before_destination_mutation(
    tmp_path,
):
    _, bundle = make_bundle(tmp_path)
    state = tmp_path / "fresh"
    state.mkdir(mode=0o700)
    recovery.restore(FakeRuntime(state), bundle)
    assert (state / "postgres/target/PG_VERSION").read_text().strip() == "17"
    archive = bundle / recovery.ARCHIVE
    archive.write_bytes(archive.read_bytes()[:-20] + b"corrupt")
    other = FakeRuntime(tmp_path / "other")
    with pytest.raises(recovery.RecoveryError, match="checksum"):
        recovery.restore(other, bundle)
    assert other.calls == []
    assert not other.state.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_verify_rejects_links_and_special_archive_entries_even_with_resealed_hash(
    tmp_path, kind
):
    _, bundle = make_bundle(tmp_path)

    def transform(members):
        for member, _ in members:
            if member.name == "config.json":
                member.type = kind
                member.linkname = "../../escape"
                member.size = 0
        return members

    repack(bundle, transform)
    with pytest.raises(recovery.RecoveryError, match="link|match"):
        recovery.verify(bundle)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape",
        "/absolute",
        "secrets/../../escape",
        "secrets//double",
        "secrets/./dot",
        "secrets\\escape",
    ],
)
def test_verify_rejects_archive_path_traversal_before_extracting(tmp_path, unsafe):
    _, bundle = make_bundle(tmp_path)

    def transform(members):
        for member, _ in members:
            if member.name == "config.json":
                member.name = unsafe
        return members

    repack(bundle, transform)
    with pytest.raises(recovery.RecoveryError, match="unsafe|unexpected"):
        recovery.verify(bundle)
    assert not (tmp_path / "escape").exists()


def test_verify_rejects_duplicate_missing_and_checksum_mismatched_files(tmp_path):
    _, bundle = make_bundle(tmp_path)
    original = (bundle / recovery.ARCHIVE).read_bytes()
    manifest = (bundle / "manifest.json").read_bytes()
    repack(bundle, lambda members: members + [members[0]])
    with pytest.raises(recovery.RecoveryError, match="duplicate"):
        recovery.verify(bundle)
    (bundle / recovery.ARCHIVE).write_bytes(original)
    (bundle / "manifest.json").write_bytes(manifest)
    repack(
        bundle,
        lambda members: [item for item in members if item[0].name != "config.json"],
    )
    with pytest.raises(recovery.RecoveryError, match="missing"):
        recovery.verify(bundle)
    (bundle / recovery.ARCHIVE).write_bytes(original)
    (bundle / "manifest.json").write_bytes(manifest)

    def corrupt(members):
        return [
            (member, b"x" * len(data) if member.name == "config.json" else data)
            for member, data in members
        ]

    repack(bundle, corrupt)
    with pytest.raises(recovery.RecoveryError, match="file checksum"):
        recovery.verify(bundle)


def test_backup_refuses_retained_postmaster_even_if_runtime_stop_returns(tmp_path):
    runtime = make_state(tmp_path / "state")
    write_private(runtime.state / "postgres/target/postmaster.pid", b"999999\n")
    with pytest.raises(recovery.RecoveryError, match="postmaster"):
        recovery.backup(runtime, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert (runtime.state / "postgres/target/postmaster.pid").exists()


def test_backup_preserves_foreign_runtime_and_does_not_create_bundle(tmp_path):
    runtime = make_state(tmp_path / "state")

    def reject():
        raise recovery.RecoveryError("foreign running process")

    runtime.assert_stopped = reject
    with pytest.raises(recovery.RecoveryError, match="foreign"):
        recovery.backup(runtime, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize(
    "retained", ["symlink", "missing-key", "wrong-pg-major", "invalid-key"]
)
def test_snapshot_rejects_incomplete_or_unsafe_source_without_replacing_data(
    tmp_path, retained
):
    runtime = make_state(tmp_path / "state")
    if retained == "symlink":
        (runtime.state / "postgres/target/pg_wal/external").symlink_to(
            tmp_path / "elsewhere"
        )
    elif retained == "missing-key":
        (runtime.state / "secrets/metadata_encryption_key").unlink()
    elif retained == "invalid-key":
        (runtime.state / "secrets/metadata_encryption_key").write_bytes(b"not-base64")
    else:
        (runtime.state / "postgres/target/PG_VERSION").write_bytes(b"18")
    with pytest.raises((recovery.RecoveryError, FileNotFoundError)):
        recovery.backup(runtime, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert runtime.state.exists()


def test_transient_files_and_logs_are_excluded_from_complete_database_snapshot(
    tmp_path,
):
    runtime = make_state(tmp_path / "state")
    write_private(runtime.state / "logs/private.log", b"private-request-details")
    write_private(runtime.state / "runtime.json", b'{"pid":123}')
    write_private(
        runtime.state / "postgres/metadata/postmaster.opts", b"runtime arguments"
    )
    write_private(
        runtime.state / "postgres/target/base/16384/pg_internal.init", b"cache"
    )
    write_private(runtime.state / "postgres/target/pg_stat_tmp/temp", b"statistics")
    recovery.backup(runtime, tmp_path / "backup")
    manifest = json.loads((tmp_path / "backup/manifest.json").read_text())
    assert not any(
        "log" in name
        or "runtime" in name
        or "postmaster" in name
        or "pg_stat_tmp" in name
        or "pg_internal.init" in name
        for name in manifest["entries"]
    )
    assert "postgres/target/base/16384/16400" in manifest["entries"]


def test_manifest_permission_size_and_runtime_identity_gates(tmp_path, monkeypatch):
    _, bundle = make_bundle(tmp_path)
    path = bundle / "manifest.json"
    path.chmod(0o644)
    with pytest.raises(recovery.RecoveryError, match="private"):
        recovery.verify(bundle)
    path.chmod(0o600)
    monkeypatch.setattr(recovery, "MAX_MANIFEST_BYTES", 64)
    with pytest.raises(recovery.RecoveryError, match="size"):
        recovery.verify(bundle)
    monkeypatch.setattr(recovery, "MAX_MANIFEST_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(
        recovery,
        "_identity",
        lambda: {**recovery._supported_identity(), "architecture": "x86_64"},
    )
    assert recovery.verify(bundle)["integrity_verified"] is True
    with pytest.raises(recovery.RecoveryError, match="incompatible"):
        recovery.restore(FakeRuntime(tmp_path / "fresh"), bundle)


def test_runtime_source_and_security_update_provenance_does_not_prevent_compatible_restore(
    tmp_path, monkeypatch
):
    _, bundle = make_bundle(tmp_path)
    current = recovery._runtime_fingerprint()
    monkeypatch.setattr(
        recovery,
        "_runtime_fingerprint",
        lambda: {
            **current,
            "installed_manifest_sha256": "c" * 64,
            "image": "debian:trixie-slim@sha256:" + "d" * 64,
        },
    )
    runtime = FakeRuntime(tmp_path / "fresh")
    recovery.restore(runtime, bundle)
    assert (runtime.state / "postgres/metadata/PG_VERSION").read_text().strip() == "17"


@pytest.mark.parametrize(
    "name,value,error",
    [
        ("postgres/target/PG_VERSION", b"18\n", "PostgreSQL 17"),
        ("secrets/metadata_encryption_key", b"x" * 44, "encryption key"),
    ],
)
def test_verify_checks_database_major_and_key_contents_even_with_resealed_hashes(
    tmp_path, name, value, error
):
    _, bundle = make_bundle(tmp_path)

    def transform(members):
        for member, _ in members:
            if member.name == name:
                member.size = len(value)
        return [
            (member, value if member.name == name else data) for member, data in members
        ]

    repack(bundle, transform)

    def update(manifest):
        entry = manifest["entries"][name]
        old_size = entry["size"]
        entry["size"] = len(value)
        entry["sha256"] = hashlib.sha256(value).hexdigest()
        manifest["total_bytes"] += len(value) - old_size

    edit_manifest(bundle, update)
    with pytest.raises(recovery.RecoveryError, match=error):
        recovery.verify(bundle)


def test_backup_destination_must_be_new_and_outside_state(tmp_path):
    runtime = make_state(tmp_path / "state")
    existing = tmp_path / "retained"
    existing.mkdir()
    for destination in (existing, runtime.state / "backup"):
        with pytest.raises(recovery.RecoveryError, match="new|outside"):
            recovery.backup(runtime, destination)
    assert runtime.calls == []


def test_nested_backup_destination_creates_private_parents_without_changing_user_directory(
    tmp_path,
):
    runtime = make_state(tmp_path / "state")
    public_parent = tmp_path / "user-owned"
    public_parent.mkdir(mode=0o755)
    destination = public_parent / "backups" / "phone-first"
    recovery.backup(runtime, destination)
    assert recovery.verify(destination)["integrity_verified"] is True
    assert stat.S_IMODE(public_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_failed_staged_restore_preserves_original_and_private_evidence(
    tmp_path, monkeypatch
):
    _, bundle = make_bundle(tmp_path)
    original_validate = recovery._validate_state

    def reject_stage(state, *, controls):
        if ".restore-" in state.name:
            raise recovery.RecoveryError("staged control failure")
        return original_validate(state, controls=controls)

    monkeypatch.setattr(recovery, "_validate_state", reject_stage)
    runtime = FakeRuntime(tmp_path / "fresh")
    with pytest.raises(recovery.RecoveryError, match="staged"):
        recovery.restore(runtime, bundle)
    assert not runtime.state.exists()
    stages = list(tmp_path.glob(".fresh.restore-*"))
    assert len(stages) == 1 and stat.S_IMODE(stages[0].stat().st_mode) == 0o700
    assert (tmp_path / "source/secrets/metadata_encryption_key").exists()
    assert recovery.verify(bundle)["integrity_verified"] is True


@pytest.mark.parametrize(
    "control_state", ["shut down", "in production", "shut down in recovery"]
)
def test_pg_control_requires_clean_shutdown_without_exposing_command_output(
    tmp_path, monkeypatch, control_state
):
    specification = importlib.util.spec_from_file_location(
        "android_recovery_control", ROOT / "dev/android/recovery.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=f"Database cluster state:          {control_state}\n",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", command)
    if control_state == "shut down":
        module._check_control(tmp_path)
    else:
        with pytest.raises(module.RecoveryError, match="clean shutdown"):
            module._check_control(tmp_path)
    assert calls[0][0] == ["/usr/lib/postgresql/17/bin/pg_controldata", str(tmp_path)]
    assert calls[0][1]["env"]["LC_ALL"] == "C"
