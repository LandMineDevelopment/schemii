"""Private cold snapshots for the Debian 13/ARM64 Android preview.

This is a physical PostgreSQL 17 backup, not a portable SQL export.  Verification
checks integrity and safe contents; it is deliberately not a database-restore
acceptance claim.  Never restore a backup from an untrusted source.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, ContextManager, Protocol

FORMAT = "schemii-android-cold-backup-v1"
PROFILE = "debian13-postgresql17-v1"
ARCHIVE = "state.tar.gz"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 100_000
SECRETS = (
    "metadata_bootstrap_password",
    "target_bootstrap_password",
    "metadata_app_password",
    "target_password",
    "metadata_encryption_key",
    "account_setup_token",
    "opencode_password",
)
TLS_FILES = ("ca.key", "ca.crt", "server.key", "server.crt")
ROOTS = ("config.json", "secrets", "tls", "postgres/metadata", "postgres/target")
TRANSIENT = frozenset({"postmaster.pid", "postmaster.opts", "pg_internal.init"})
TAILNET_LOGS = frozenset(
    {
        "tailscaled.log.conf",
        "tailscaled.log1.txt",
        "tailscaled.log2.txt",
        "schemii.lock",
    }
)
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RecoveryError(ValueError):
    """A recovery operation refused unsafe or incompatible state."""


class RecoveryRuntime(Protocol):
    state: Path

    def recovery_lock(self) -> ContextManager[Any]: ...
    def stop(self) -> Any: ...
    def assert_stopped(self) -> None: ...


def _identity() -> dict[str, object]:
    machine = platform.machine().lower()
    return {
        "profile": PROFILE,
        "architecture": "aarch64" if machine in {"arm64", "aarch64"} else machine,
        "postgres_major": 17,
        "byteorder": sys.byteorder,
    }


def _supported_identity() -> dict[str, object]:
    return {
        "profile": PROFILE,
        "architecture": "aarch64",
        "postgres_major": 17,
        "byteorder": "little",
    }


def _runtime_fingerprint() -> dict[str, object]:
    path = Path("/opt/schemii-runtime.json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise RecoveryError(
            "The installed Android runtime identity is missing or unsafe"
        )
    try:
        raw = path.read_bytes()
        record = json.loads(raw)
        os_release = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise RecoveryError(
            "The installed Android runtime identity is invalid"
        ) from error
    expected = {
        "format": "schemii-android-install-v1",
        "debian": "13",
        "architecture": "arm64",
        "postgresql_major": 17,
        "python_minor": "3.13",
        "node": "22.22.2",
    }
    if not isinstance(record, dict) or any(
        record.get(key) != value for key, value in expected.items()
    ):
        raise RecoveryError(
            "The installed runtime is incompatible with this physical recovery format"
        )
    if (
        os_release.get("ID", "").strip('"') != "debian"
        or os_release.get("VERSION_ID", "").strip('"') != "13"
        or sys.version_info[:2] != (3, 13)
    ):
        raise RecoveryError(
            "Physical recovery must run inside the installed Debian 13 Python 3.13 environment"
        )
    image = record.get("image")
    if not isinstance(image, str) or not re.fullmatch(
        r"debian:trixie-slim@sha256:[0-9a-f]{64}", image
    ):
        raise RecoveryError("The installed runtime rootfs identity is invalid")
    return {
        **expected,
        "image": image,
        "installed_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _private_path(path: Path, *, directory: bool) -> os.stat_result:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_mode & 0o077:
        raise RecoveryError(
            "Recovery paths must be private regular files and directories, without symbolic links"
        )
    return info


def _plain_parents(path: Path) -> None:
    """Reject symlinked parent paths rather than silently resolving user data."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RecoveryError("Recovery paths must not contain symbolic links")


def _read_private(path: Path, maximum: int) -> bytes:
    info = _private_path(path, directory=False)
    if info.st_size > maximum:
        raise RecoveryError("Recovery metadata exceeds the supported size")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        result = stream.read(maximum + 1)
    if len(result) > maximum:
        raise RecoveryError("Recovery metadata exceeds the supported size")
    return result


def _digest(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    hashed = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(chunk)
    return hashed.hexdigest()


def _check_control(cluster: Path) -> None:
    """Only a cleanly stopped cluster is admitted to a cold physical backup."""
    try:
        result = subprocess.run(
            ["/usr/lib/postgresql/17/bin/pg_controldata", str(cluster)],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError(
            "PostgreSQL 17 control verification is unavailable; no snapshot was made"
        ) from error
    if result.returncode or not re.search(
        r"^Database cluster state:\s+shut down\s*$", result.stdout, re.MULTILINE
    ):
        raise RecoveryError(
            "Cold backup requires both PostgreSQL clusters to report a clean shutdown"
        )


def _content_limit(name: str) -> int | None:
    if name.startswith("tailnet/"):
        return 16 * 1024 * 1024 if name.endswith("tailscaled.state") else 1024 * 1024
    if name == "config.json" or name in {f"tls/{item}" for item in TLS_FILES}:
        return 1024 * 1024
    if name in {f"secrets/{item}" for item in SECRETS}:
        return 4096
    if name in {f"postgres/{item}/PG_VERSION" for item in ("metadata", "target")}:
        return 16
    return None


def _validate_content(name: str, data: bytes) -> None:
    if not data.strip():
        raise RecoveryError("A required original Android recovery file is empty")
    if name == "config.json":
        try:
            config = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RecoveryError("Android configuration is invalid") from error
        if not isinstance(config, dict) or not config:
            raise RecoveryError("Android configuration is missing")
    elif name.endswith("/PG_VERSION") and data.strip() != b"17":
        raise RecoveryError("Physical recovery supports PostgreSQL 17 only")
    elif name == "secrets/metadata_encryption_key":
        try:
            valid = len(base64.b64decode(data.strip(), validate=True)) == 32
        except ValueError:
            valid = False
        if not valid:
            raise RecoveryError("The original metadata encryption key is invalid")
    elif name == "tailnet/config.json":
        try:
            value = json.loads(data)
            valid = isinstance(value, dict) and bool(
                re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value.get("hostname", "")
                )
            )
        except (ValueError, TypeError, UnicodeDecodeError):
            valid = False
        if not valid:
            raise RecoveryError("Retained tailnet configuration is invalid")


def _validate_state(state: Path, *, controls: bool) -> None:
    _private_path(state, directory=True)
    for name in ("secrets", "tls", "postgres", "postgres/metadata", "postgres/target"):
        _private_path(state / name, directory=True)
    _validate_content("config.json", _read_private(state / "config.json", 1024 * 1024))
    if (state / "tailnet").exists() or (state / "tailnet").is_symlink():
        _private_path(state / "tailnet", directory=True)
        for path in (state / "tailnet").rglob("*"):
            name = path.relative_to(state).as_posix()
            if path.parent == state / "tailnet/node" and path.name in TAILNET_LOGS:
                continue
            _safe_name(name)
            _private_path(path, directory=path.is_dir())
            if path.is_file():
                _validate_content(
                    name, _read_private(path, _content_limit(name) or 1024 * 1024)
                )
    for name in SECRETS:
        _validate_content(
            "secrets/" + name, _read_private(state / "secrets" / name, 4096)
        )
    for name in TLS_FILES:
        _validate_content(
            "tls/" + name, _read_private(state / "tls" / name, 1024 * 1024)
        )
    for name in ("metadata", "target"):
        cluster = state / "postgres" / name
        if (cluster / "postmaster.pid").exists() or (
            cluster / "postmaster.pid"
        ).is_symlink():
            raise RecoveryError(
                "Cold recovery refuses a PostgreSQL postmaster.pid, including stale or foreign state"
            )
        _validate_content(
            f"postgres/{name}/PG_VERSION", _read_private(cluster / "PG_VERSION", 16)
        )
        _private_path(cluster / "global", directory=True)
        if (
            _private_path(cluster / "global" / "pg_control", directory=False).st_size
            == 0
        ):
            raise RecoveryError("PostgreSQL control data is incomplete")
        _private_path(cluster / "base", directory=True)
        _private_path(cluster / "pg_wal", directory=True)
        if controls:
            _check_control(cluster)


def _allowed(name: str) -> bool:
    if name in {
        "tailnet",
        "tailnet/config.json",
        "tailnet/node",
        "tailnet/node/tailscaled.state",
        "tailnet/node/certs",
        "tailnet/node/certs/acme-account.key.pem",
    }:
        return True
    if name.startswith("tailnet/node/certs/"):
        leaf = name.removeprefix("tailnet/node/certs/")
        return (
            len(leaf) <= 257
            and ".." not in leaf
            and bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.ts\.net\.(?:crt|key)", leaf))
        )
    return name in ROOTS or any(name.startswith(root + "/") for root in ROOTS[1:])


def _safe_name(name: object) -> str:
    if not isinstance(name, str) or len(name) > 4096 or "\\" in name or "\x00" in name:
        raise RecoveryError("Backup contains an unsafe member path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise RecoveryError("Backup contains an unsafe member path")
    if path.as_posix() != name or not _allowed(name):
        raise RecoveryError("Backup contains an unexpected member path")
    if path.name in TRANSIENT or "pg_stat_tmp" in path.parts:
        raise RecoveryError("Backup contains transient PostgreSQL state")
    return name


def _snapshot_entries(state: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}

    def record(path: Path) -> None:
        name = _safe_name(path.relative_to(state).as_posix())
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            entries[name] = {"type": "directory", "mode": 0o700}
        elif stat.S_ISREG(info.st_mode):
            entries[name] = {
                "type": "file",
                "mode": 0o600,
                "size": info.st_size,
                "sha256": _digest(path),
            }
        else:
            raise RecoveryError(
                "Physical backup refuses symbolic links, tablespaces, and special files"
            )
        if len(entries) > MAX_ENTRIES:
            raise RecoveryError("Backup has too many entries for this preview format")

    roots = (*ROOTS, "tailnet") if (state / "tailnet").exists() else ROOTS
    for root in roots:
        path = state / root
        record(path)
        if path.is_dir():
            for current, directories, files in os.walk(path, followlinks=False):
                directories.sort()
                files.sort()
                if "pg_stat_tmp" in directories:
                    directories.remove("pg_stat_tmp")
                for item in directories:
                    record(Path(current) / item)
                for item in files:
                    if Path(current) == state / "tailnet/node" and item in TAILNET_LOGS:
                        continue
                    if item not in TRANSIENT:
                        record(Path(current) / item)
    return entries


def _required_entries(entries: dict[str, Any]) -> None:
    required_files = [
        "config.json",
        *(f"secrets/{name}" for name in SECRETS),
        *(f"tls/{name}" for name in TLS_FILES),
    ]
    required_dirs = ["secrets", "tls"]
    if any(name.startswith("tailnet/") for name in entries):
        required_dirs.append("tailnet")
    if any(name.startswith("tailnet/node/") for name in entries):
        required_dirs.append("tailnet/node")
    if any(name.startswith("tailnet/node/certs/") for name in entries):
        required_dirs.append("tailnet/node/certs")
        required_files.append("tailnet/node/tailscaled.state")
    for name in ("metadata", "target"):
        root = f"postgres/{name}"
        required_files.extend((root + "/PG_VERSION", root + "/global/pg_control"))
        required_dirs.extend((root, root + "/global", root + "/base", root + "/pg_wal"))
    if any(entries.get(name, {}).get("type") != "file" for name in required_files):
        raise RecoveryError(
            "Backup is missing required configuration, keys, or database control files"
        )
    if any(entries.get(name, {}).get("type") != "directory" for name in required_dirs):
        raise RecoveryError("Backup is missing required database directories")
    if any(entries[name].get("size", 0) == 0 for name in required_files):
        raise RecoveryError("Backup has empty required files")


def _load_manifest(bundle: Path) -> dict[str, Any]:
    _plain_parents(bundle)
    _private_path(bundle, directory=True)
    if {item.name for item in bundle.iterdir()} != {"manifest.json", ARCHIVE}:
        raise RecoveryError("Backup directory is incomplete or has unexpected files")
    try:
        manifest = json.loads(
            _read_private(bundle / "manifest.json", MAX_MANIFEST_BYTES)
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RecoveryError("Backup manifest is invalid") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RecoveryError("Unsupported Android backup format")
    if (
        manifest.get("identity") != _supported_identity()
        or manifest.get("cold_shutdown") is not True
    ):
        raise RecoveryError(
            "Backup is not a clean PostgreSQL 17 ARM64 Debian 13 snapshot"
        )
    fingerprint = manifest.get("runtime")
    if (
        not isinstance(fingerprint, dict)
        or fingerprint.get("format") != "schemii-android-install-v1"
        or fingerprint.get("debian") != "13"
        or fingerprint.get("architecture") != "arm64"
        or fingerprint.get("postgresql_major") != 17
        or fingerprint.get("python_minor") != "3.13"
        or fingerprint.get("node") != "22.22.2"
    ):
        raise RecoveryError("Backup runtime fingerprint is incompatible or incomplete")
    if not isinstance(fingerprint.get("image"), str) or not re.fullmatch(
        r"debian:trixie-slim@sha256:[0-9a-f]{64}", fingerprint["image"]
    ):
        raise RecoveryError("Backup runtime rootfs fingerprint is invalid")
    if not isinstance(
        fingerprint.get("installed_manifest_sha256"), str
    ) or not SHA256.fullmatch(fingerprint["installed_manifest_sha256"]):
        raise RecoveryError("Backup installed-runtime fingerprint is invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or not entries or len(entries) > MAX_ENTRIES:
        raise RecoveryError("Backup manifest entries are invalid")
    if not isinstance(manifest.get("archive_sha256"), str) or not SHA256.fullmatch(
        manifest["archive_sha256"]
    ):
        raise RecoveryError("Backup archive checksum is invalid")
    total = 0
    for name, entry in entries.items():
        _safe_name(name)
        if not isinstance(entry, dict):
            raise RecoveryError("Backup entry is invalid")
        kind = entry.get("type")
        if kind == "directory":
            if set(entry) != {"type", "mode"} or entry["mode"] != 0o700:
                raise RecoveryError("Backup directory permissions are invalid")
        elif kind == "file":
            if (
                set(entry) != {"type", "mode", "size", "sha256"}
                or entry["mode"] != 0o600
            ):
                raise RecoveryError("Backup file metadata is invalid")
            if type(entry["size"]) is not int or entry["size"] < 0:
                raise RecoveryError("Backup file size is invalid")
            if not isinstance(entry["sha256"], str) or not SHA256.fullmatch(
                entry["sha256"]
            ):
                raise RecoveryError("Backup file checksum is invalid")
            limit = _content_limit(name)
            if limit is not None and entry["size"] > limit:
                raise RecoveryError("Backup metadata exceeds the supported size")
            total += entry["size"]
        else:
            raise RecoveryError("Backup entry type is invalid")
    if manifest.get("total_bytes") != total:
        raise RecoveryError("Backup total size is invalid")
    _required_entries(entries)
    _private_path(bundle / ARCHIVE, directory=False)
    return manifest


def _make_directories(path: Path, root: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            _private_path(current, directory=True)


def _new_parent_directories(path: Path) -> None:
    """Create missing private parents without chmodding existing user paths."""
    _plain_parents(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise RecoveryError("Backup destination parent is not a directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _payload(
    bundle: Path, manifest: dict[str, Any], destination: Path | None = None
) -> None:
    archive = bundle / ARCHIVE
    if _digest(archive) != manifest["archive_sha256"]:
        raise RecoveryError("Backup archive checksum failed")
    seen: set[str] = set()
    try:
        with tarfile.open(archive, mode="r|gz") as source:
            for member in source:
                name = _safe_name(member.name)
                if name in seen or name not in manifest["entries"]:
                    raise RecoveryError(
                        "Backup contains duplicate or unexpected entries"
                    )
                seen.add(name)
                entry = manifest["entries"][name]
                if member.issym() or member.islnk() or member.sparse is not None:
                    raise RecoveryError(
                        "Backup contains a link or unsupported sparse file"
                    )
                if member.mode != entry["mode"]:
                    raise RecoveryError(
                        "Backup archive permissions do not match its manifest"
                    )
                target = destination / name if destination is not None else None
                if entry["type"] == "directory":
                    if not member.isdir() or member.size:
                        raise RecoveryError(
                            "Backup directory does not match its manifest"
                        )
                    if target is not None and destination is not None:
                        _make_directories(target, destination)
                    continue
                if not member.isfile() or member.size != entry["size"]:
                    raise RecoveryError("Backup file does not match its manifest")
                stream = source.extractfile(member)
                if stream is None:
                    raise RecoveryError("Backup file data is missing")
                output = None
                if target is not None and destination is not None:
                    _make_directories(target.parent, destination)
                    output = os.fdopen(
                        os.open(
                            target,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                        ),
                        "wb",
                    )
                try:
                    hashed = hashlib.sha256()
                    length = 0
                    content = bytearray() if _content_limit(name) is not None else None
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        length += len(chunk)
                        hashed.update(chunk)
                        if content is not None:
                            content.extend(chunk)
                        if output is not None:
                            output.write(chunk)
                    if length != entry["size"] or hashed.hexdigest() != entry["sha256"]:
                        raise RecoveryError("Backup file checksum failed")
                    if content is not None:
                        _validate_content(name, bytes(content))
                    if output is not None:
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    stream.close()
                    if output is not None:
                        output.close()
    except (tarfile.TarError, EOFError) as error:
        raise RecoveryError("Backup archive is invalid or truncated") from error
    if seen != set(manifest["entries"]):
        raise RecoveryError("Backup archive is missing manifest entries")


def _summary(bundle: Path, manifest: dict[str, Any]) -> dict[str, object]:
    return {
        "format": FORMAT,
        "bundle": str(bundle),
        "file_count": len(manifest["entries"]),
        "total_bytes": manifest["total_bytes"],
        "integrity_verified": True,
        "database_restore_verified": False,
        "warning": "Sensitive unencrypted physical backup. PostgreSQL restore and application checks remain required.",
    }


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify(bundle: Path | str) -> dict[str, object]:
    """Read-only validation; no database or runtime process is started."""
    root = Path(os.path.abspath(bundle))
    manifest = _load_manifest(root)
    _payload(root, manifest)
    return _summary(root, manifest)


def backup(runtime: RecoveryRuntime, destination: Path | str) -> dict[str, object]:
    """Stop the owned runtime and snapshot both clusters. It remains stopped."""
    state = Path(os.path.abspath(runtime.state))
    root = Path(os.path.abspath(destination))
    _plain_parents(state)
    _plain_parents(root)
    if root == state or state in root.parents:
        raise RecoveryError("Backup destination must be outside the installation state")
    if root.exists() or root.is_symlink():
        raise RecoveryError("Backup destination must be a new directory")
    if _identity() != _supported_identity():
        raise RecoveryError(
            "Physical Android backups require the Debian 13 ARM64 PostgreSQL 17 runtime"
        )
    fingerprint = _runtime_fingerprint()
    with runtime.recovery_lock():
        runtime.stop()
        runtime.assert_stopped()
        _validate_state(state, controls=True)
        entries = _snapshot_entries(state)
        _required_entries(entries)
        _new_parent_directories(root.parent)
        root.mkdir(mode=0o700)
        partial = root / (ARCHIVE + ".partial")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with (
            os.fdopen(descriptor, "wb") as stream,
            tarfile.open(fileobj=stream, mode="w|gz") as archive,
        ):
            for name, entry in sorted(entries.items()):
                path = state / name
                info = path.lstat()
                member = tarfile.TarInfo(name)
                member.mode = int(entry["mode"])
                member.mtime = int(info.st_mtime)
                if entry["type"] == "directory":
                    if not stat.S_ISDIR(info.st_mode):
                        raise RecoveryError(
                            "Installation state changed during cold backup"
                        )
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                else:
                    if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"]:
                        raise RecoveryError(
                            "Installation state changed during cold backup"
                        )
                    member.size = int(entry["size"])
                    source_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(source_fd, "rb") as source:
                        archive.addfile(member, source)
        partial.rename(root / ARCHIVE)
        with (root / ARCHIVE).open("rb") as archive_stream:
            os.fsync(archive_stream.fileno())
        manifest = {
            "format": FORMAT,
            "identity": _identity(),
            "cold_shutdown": True,
            "runtime": fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive_sha256": _digest(root / ARCHIVE),
            "entries": entries,
            "total_bytes": sum(int(entry.get("size", 0)) for entry in entries.values()),
        }
        encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise RecoveryError("Backup manifest exceeds the supported size")
        descriptor = os.open(
            root / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(root)
        _sync_directory(root.parent)
        result = verify(root)
        result["runtime_stopped"] = True
        return result


def _fresh_state(state: Path) -> None:
    if state.is_symlink() or (
        state.exists() and (not state.is_dir() or any(state.iterdir()))
    ):
        raise RecoveryError(
            "Fresh restore refuses any existing installation state; preserve it and choose a fresh location"
        )


def restore(runtime: RecoveryRuntime, bundle: Path | str) -> dict[str, object]:
    """Validate first, then atomically install into an absent or empty state root."""
    state = Path(os.path.abspath(runtime.state))
    root = Path(os.path.abspath(bundle))
    _plain_parents(state)
    _plain_parents(root)
    manifest = _load_manifest(root)
    if manifest["identity"] != _identity():
        raise RecoveryError(
            "Physical backup runtime architecture/profile is incompatible with this installation"
        )
    # The exact source/package/image digests are provenance, not a reason to
    # reject a compatible fresh installation after security/minor updates.
    _runtime_fingerprint()
    _payload(root, manifest)
    with runtime.recovery_lock():
        _fresh_state(state)
        runtime.assert_stopped()
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            shutil.disk_usage(state.parent).free
            < manifest["total_bytes"] + 16 * 1024 * 1024
        ):
            raise RecoveryError(
                "Insufficient free storage for a staged physical restore"
            )
        stage = Path(
            tempfile.mkdtemp(prefix=f".{state.name}.restore-", dir=state.parent)
        )
        # Failed staged restores remain private for diagnosis; no existing state
        # is removed. Validation below completes before the final rename.
        _payload(root, manifest, stage)
        _validate_state(stage, controls=True)
        for directory in sorted(
            (path for path in stage.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            _sync_directory(directory)
        _sync_directory(stage)
        _fresh_state(state)
        if state.exists():
            state.rmdir()  # Only the verified empty directory is removed.
        stage.rename(state)
        _sync_directory(state.parent)
        result = _summary(root, manifest)
        result.update({"restored_state": str(state), "runtime_stopped": True})
        return result
