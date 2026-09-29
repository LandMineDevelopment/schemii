#!/usr/bin/env python3
"""Supervise one isolated Playwright MCP stdio connection and its temporary output."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import NamedTuple


PACKAGE = "@playwright/mcp@0.0.83"
REPOSITORY_ROOT = Path(__file__).absolute().parents[2]
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
SESSION_NAME = re.compile(r"session-[0-9a-f]{32}\Z")
OUTPUT_TTL_SECONDS = 600
OUTPUT_SWEEP_SECONDS = 30


class ArtifactError(RuntimeError):
    """The artifact path cannot safely hold this connection's output."""


class Session(NamedTuple):
    path: Path
    inode: tuple[int, int]
    metadata: dict


def process_birth_tick(pid: int) -> int | None:
    """Linux process start tick distinguishes a dead owner from a reused PID."""
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    return int(value.rsplit(")", 1)[1].split()[19])


def _open_repository(repository: Path) -> int:
    """Open every ancestor without resolving or following symlinks."""
    parts = repository.absolute().parts[1:]
    if ".." in parts:
        raise ArtifactError("Artifact ancestors must not contain traversal.")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in parts:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_directory(descriptor: int, *, private: bool) -> os.stat_result:
    info = os.fstat(descriptor)
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != os.getuid():
        raise ArtifactError("Artifact directories must belong to the current user.")
    if mode & (0o077 if private else 0o022):
        raise ArtifactError("Existing artifact directory permissions are unsafe.")
    if mode & 0o700 != 0o700:
        raise ArtifactError("Artifact directories require owner access.")
    return info


def _owned_directory(parent: int, name: str, *, private: bool) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    try:
        _validate_directory(descriptor, private=private)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _artifact_base(repository: Path):
    descriptors = []
    try:
        descriptors.append(_open_repository(repository))
        descriptors.append(
            _owned_directory(descriptors[-1], "artifacts", private=False)
        )
        descriptors.append(
            _owned_directory(descriptors[-1], "native-browsers", private=True)
        )
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _rmdir_owned(parent: int, name: str, inode: tuple[int, int]) -> None:
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != inode:
        raise ArtifactError("The generated cleanup directory was replaced.")
    # rmdir cannot recursively remove data if the name changes after this check.
    os.rmdir(name, dir_fd=parent)


def _remove_contents(descriptor: int) -> None:
    """Remove through the verified directory handle, never its mutable outer name."""
    for name in os.listdir(descriptor):
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                opened = _validate_directory(child, private=True)
                inode = (opened.st_dev, opened.st_ino)
                if inode != (info.st_dev, info.st_ino):
                    raise ArtifactError("The generated cleanup directory was replaced.")
                _remove_contents(child)
                _rmdir_owned(descriptor, name, inode)
            finally:
                os.close(child)
        else:
            # Unlink symlinks themselves; never follow their targets.
            os.unlink(name, dir_fd=descriptor)


def _remove_session(base: int, name: str, inode: tuple[int, int]) -> None:
    if not SESSION_NAME.fullmatch(name):
        raise ArtifactError("Only generated session names can be removed.")
    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=base)
    try:
        info = _validate_directory(descriptor, private=True)
        if (info.st_dev, info.st_ino) != inode:
            raise ArtifactError("The generated session directory was replaced.")
        _remove_contents(descriptor)
        _rmdir_owned(base, name, inode)
    finally:
        os.close(descriptor)


def _orphan_metadata(descriptor: int, name: str) -> dict | None:
    metadata_fd = os.open(
        "session.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
    )
    with os.fdopen(metadata_fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 4096
        ):
            return None
        data = json.loads(handle.read(4097))
    if not isinstance(data, dict) or set(data) != {
        "schema",
        "session_id",
        "package",
        "pid",
        "birth_tick",
        "started_at",
        "state",
        "child_pid",
        "child_birth_tick",
    }:
        return None
    if (
        type(data["schema"]) is not int
        or data["schema"] != 1
        or data["session_id"] != name
        or data["package"] != PACKAGE
        or data["state"] != "running"
        or not isinstance(data["started_at"], str)
    ):
        return None
    if not data["started_at"].endswith("+00:00"):
        return None
    datetime.fromisoformat(data["started_at"])
    if any(
        type(data[key]) is not int or data[key] <= 0
        for key in ("pid", "birth_tick", "child_pid", "child_birth_tick")
    ):
        return None
    return data


def _sweep_orphans(base: int) -> None:
    for name in os.listdir(base):
        if not SESSION_NAME.fullmatch(name):
            continue
        descriptor = None
        try:
            descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=base)
            info = _validate_directory(descriptor, private=True)
            data = _orphan_metadata(descriptor, name)
            if data is None:
                continue
            if (
                process_birth_tick(data["pid"]) != data["birth_tick"]
                and process_birth_tick(data["child_pid"]) != data["child_birth_tick"]
            ):
                _remove_session(base, name, (info.st_dev, info.st_ino))
        except (OSError, ArtifactError, ValueError, IndexError):
            # Unreadable or unknown ownership is never permission to delete.
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _write_metadata(descriptor: int, metadata: dict) -> None:
    metadata_fd = os.open(
        "session.json.tmp",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode=0o600,
        dir_fd=descriptor,
    )
    with os.fdopen(metadata_fd, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle)
        handle.write("\n")
    os.replace(
        "session.json.tmp",
        "session.json",
        src_dir_fd=descriptor,
        dst_dir_fd=descriptor,
    )


def create_session(repository: Path) -> Session:
    repository = repository.absolute()
    with _artifact_base(repository) as base:
        _sweep_orphans(base)
        name = f"session-{secrets.token_hex(16)}"
        # A collision fails rather than opening another connection's output.
        os.mkdir(name, mode=0o700, dir_fd=base)
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=base)
        info = None
        try:
            info = _validate_directory(descriptor, private=True)
            os.mkdir("output", mode=0o700, dir_fd=descriptor)
            birth_tick = process_birth_tick(os.getpid())
            if birth_tick is None:
                raise ArtifactError("The supervisor process owner cannot be verified.")
            metadata = {
                "schema": 1,
                "session_id": name,
                "package": PACKAGE,
                # These identify processes, never a native agent or scheduler lane.
                "pid": os.getpid(),
                "birth_tick": birth_tick,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "state": "launching",
                "child_pid": None,
                "child_birth_tick": None,
            }
            _write_metadata(descriptor, metadata)
            return Session(
                repository / "artifacts" / "native-browsers" / name,
                (info.st_dev, info.st_ino),
                metadata,
            )
        except BaseException:
            if info is not None:
                _remove_session(base, name, (info.st_dev, info.st_ino))
            raise
        finally:
            os.close(descriptor)


def _record_child(session: Session, pid: int) -> None:
    with _artifact_base(session.path.parents[2]) as base:
        descriptor = os.open(session.path.name, DIRECTORY_FLAGS, dir_fd=base)
        try:
            info = _validate_directory(descriptor, private=True)
            if (info.st_dev, info.st_ino) != session.inode:
                raise ArtifactError("The generated session directory was replaced.")
            birth_tick = process_birth_tick(pid)
            if birth_tick is None:
                raise ArtifactError("The child process owner cannot be verified.")
            session.metadata.update(
                state="running", child_pid=pid, child_birth_tick=birth_tick
            )
            _write_metadata(descriptor, session.metadata)
        finally:
            os.close(descriptor)


def cleanup_session(session: Session) -> None:
    with _artifact_base(session.path.parents[2]) as base:
        _remove_session(base, session.path.name, session.inode)


def _expire_files(descriptor: int, cutoff: float) -> None:
    for name in os.listdir(descriptor):
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_uid != os.getuid():
                continue
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    _validate_directory(child, private=True)
                    _expire_files(child, cutoff)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                os.unlink(name, dir_fd=descriptor)
        except (OSError, ArtifactError):
            # Files may still be moving; never follow unknown or symlink paths.
            continue


def expire_output(session: Session, now: float) -> None:
    with _artifact_base(session.path.parents[2]) as base:
        descriptor = os.open(session.path.name, DIRECTORY_FLAGS, dir_fd=base)
        output = None
        try:
            info = _validate_directory(descriptor, private=True)
            if (info.st_dev, info.st_ino) != session.inode:
                raise ArtifactError("The generated session directory was replaced.")
            output = os.open("output", DIRECTORY_FLAGS, dir_fd=descriptor)
            _validate_directory(output, private=True)
            _expire_files(output, now - OUTPUT_TTL_SECONDS)
        finally:
            if output is not None:
                os.close(output)
            os.close(descriptor)


def command(npx: str, chromium: str, output_directory: Path) -> list[str]:
    return [
        npx,
        "--yes",
        "--offline",
        PACKAGE,
        "--isolated",
        "--headless",
        "--browser",
        "chromium",
        "--executable-path",
        chromium,
        "--ignore-https-errors",
        "--caps=vision",
        "--no-webmcp",
        "--output-max-size",
        "20971520",
        "--output-dir",
        str(output_directory),
    ]


def _signal_child(process: subprocess.Popen, signum: int) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass


def _stop_child(process: subprocess.Popen) -> None:
    if process.poll() is None:
        _signal_child(process, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_child(process, signal.SIGKILL)
            process.wait()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(
            "Schemii browser MCP accepts no arguments; its isolated stdio options are fixed.",
            file=sys.stderr,
        )
        return 2
    os.umask(0o077)
    npx = shutil.which("npx")
    chromium = shutil.which("chromium")
    if not npx or not shutil.which("node") or not chromium:
        print(
            "Schemii browser MCP requires Node.js, npx and Chromium on PATH. "
            "Check the documented prerequisites.",
            file=sys.stderr,
        )
        return 2
    session = None
    process = None
    handlers = {}
    result = 2
    try:
        session = create_session(REPOSITORY_ROOT)
        # Ambient MCP settings can override isolation even with fixed CLI flags.
        server_environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("PLAYWRIGHT_MCP_")
        }
        process = subprocess.Popen(
            command(npx, chromium, session.path / "output"),
            env=server_environment,
            stdin=None,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(
                signum, lambda received, frame: _signal_child(process, received)
            )
        _record_child(session, process.pid)
        while True:
            try:
                result = process.wait(timeout=OUTPUT_SWEEP_SECONDS)
                break
            except subprocess.TimeoutExpired:
                expire_output(session, time.time())
        result = 128 - result if result < 0 else result
    except (ArtifactError, OSError, ValueError, IndexError):
        print(
            "Schemii browser MCP setup failed. Check tool installation, the warmed package cache, "
            "checkout ownership and private artifact permissions.",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        result = 130
    finally:
        if process is not None:
            _stop_child(process)
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if session is not None:
            try:
                cleanup_session(session)
            except (ArtifactError, OSError):
                print(
                    "Schemii browser MCP could not remove its temporary session output. "
                    "Unrelated data was preserved.",
                    file=sys.stderr,
                )
                result = 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
