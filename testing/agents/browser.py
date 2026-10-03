#!/usr/bin/env python3
"""Supervise one isolated Playwright MCP stdio connection and its temporary output."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import selectors
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
GUARDIAN_POLL_SECONDS = 1
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_RELAY_BYTES = 128 * 1024 * 1024
MAX_PENDING_REQUESTS = 256
INITIALIZE_TIMEOUT_SECONDS = 30


class ArtifactError(RuntimeError):
    """The artifact path cannot safely hold this connection's output."""


class ShutdownRequested(Exception):
    """A transport signal must enter bounded cleanup even if its child ignores it."""

    def __init__(self, signum: int):
        self.signum = signum


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
    fields = value.rsplit(")", 1)[1].split()
    # Zombies have exited and cannot write output, even before they are reaped.
    return None if fields[0] in {"Z", "X", "x"} else int(fields[19])


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
    required = {
        "schema",
        "session_id",
        "package",
        "pid",
        "birth_tick",
        "started_at",
        "state",
        "child_pid",
        "child_birth_tick",
    }
    guardian_fields = {"guardian_pid", "guardian_birth_tick"}
    if not isinstance(data, dict) or set(data) not in (
        required,
        required | guardian_fields,
    ):
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
    identity_fields = {"pid", "birth_tick", "child_pid", "child_birth_tick"}
    if guardian_fields <= set(data):
        identity_fields |= guardian_fields
    if any(type(data[key]) is not int or data[key] <= 0 for key in identity_fields):
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
            if "guardian_pid" in data and (
                process_birth_tick(data["guardian_pid"]) == data["guardian_birth_tick"]
            ):
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


def _update_metadata(session: Session, **updates) -> None:
    with _artifact_base(session.path.parents[2]) as base:
        descriptor = os.open(session.path.name, DIRECTORY_FLAGS, dir_fd=base)
        try:
            info = _validate_directory(descriptor, private=True)
            if (info.st_dev, info.st_ino) != session.inode:
                raise ArtifactError("The generated session directory was replaced.")
            session.metadata.update(updates)
            _write_metadata(descriptor, session.metadata)
        finally:
            os.close(descriptor)


def _record_child(session: Session, pid: int) -> None:
    birth_tick = process_birth_tick(pid)
    if birth_tick is None:
        raise ArtifactError("The child process owner cannot be verified.")
    _update_metadata(
        session, state="running", child_pid=pid, child_birth_tick=birth_tick
    )


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


def _guard_session(session: Session) -> None:
    """Watch only captured connection owners; finish cleanup even after SIGKILL."""
    next_expiry = time.monotonic() + OUTPUT_SWEEP_SECONDS
    while True:
        supervisor_alive = (
            process_birth_tick(session.metadata["pid"])
            == session.metadata["birth_tick"]
        )
        child_alive = (
            process_birth_tick(session.metadata["child_pid"])
            == session.metadata["child_birth_tick"]
        )
        if not supervisor_alive and not child_alive:
            cleanup_session(session)
            return
        if not supervisor_alive and time.monotonic() >= next_expiry:
            expire_output(session, time.time())
            next_expiry = time.monotonic() + OUTPUT_SWEEP_SECONDS
        time.sleep(GUARDIAN_POLL_SECONDS)


def _detach_guardian(ready: int) -> None:
    # Codex terminates the transport process group; this finite helper must survive it.
    os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    null = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null, descriptor)
    for name in os.listdir("/proc/self/fd"):
        descriptor = int(name)
        if descriptor > 2 and descriptor != ready:
            try:
                os.close(descriptor)
            except OSError:
                pass
    os.write(ready, b"1")
    os.close(ready)


def _stop_guardian(pid: int) -> None:
    # This is our unreaped fork child, so its PID cannot have been reused.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)


def _start_guardian(session: Session) -> int:
    read, ready = os.pipe()
    try:
        pid = os.fork()
    except BaseException:
        os.close(read)
        os.close(ready)
        raise
    if pid == 0:
        try:
            os.close(read)
            _detach_guardian(ready)
            _guard_session(session)
        except BaseException:
            os._exit(2)
        os._exit(0)
    os.close(ready)
    try:
        # Wait for the private readiness pipe to close as well as its one-byte ack.
        with os.fdopen(read, "rb") as handle:
            acknowledged = handle.read(2)
        birth_tick = process_birth_tick(pid)
        if acknowledged != b"1" or birth_tick is None:
            raise ArtifactError("The connection cleanup guardian could not start.")
        _update_metadata(session, guardian_pid=pid, guardian_birth_tick=birth_tick)
        return pid
    except BaseException:
        _stop_guardian(pid)
        raise


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
    if isinstance(getattr(process, "_schemii_group", None), dict):
        _stop_group(process)
        return
    if process.poll() is None:
        _signal_child(process, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_child(process, signal.SIGKILL)
            process.wait()


def _group_members(process: subprocess.Popen) -> dict[int, int]:
    captured = process._schemii_group
    verified = False
    for pid, tick in captured.items():
        try:
            if (
                process_birth_tick(pid) == tick
                and os.getpgid(pid) == process.pid
                and os.getsid(pid) == process.pid
            ):
                verified = True
                break
        except ProcessLookupError:
            pass
    members = {}
    for name in os.listdir("/proc"):
        if not name.isdecimal():
            continue
        pid = int(name)
        try:
            if os.getpgid(pid) == process.pid and os.getsid(pid) == process.pid:
                tick = process_birth_tick(pid)
                if tick is not None:
                    members[pid] = tick
        except ProcessLookupError:
            pass
    if members and not verified:
        raise ArtifactError("Live browser group ownership cannot be verified.")
    captured.update(members)
    return members


def _stop_group(process: subprocess.Popen) -> None:
    """An exited npm leader is not proof that its owned descendants exited."""
    members = _group_members(process)
    if members:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    timed_out = False
    started = time.monotonic()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        timed_out = True
    while not timed_out and _group_members(process):
        if time.monotonic() - started >= 5:
            timed_out = True
            break
        time.sleep(0.02)
    if timed_out:
        # A captured live member proves this group has not been reused.
        if _group_members(process):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _group_members(process):
            if time.monotonic() >= deadline:
                raise ArtifactError("Owned browser processes could not be stopped.")
            time.sleep(0.02)


class ProtocolError(RuntimeError):
    """A malformed or incompatible transport cannot be continued safely."""


def _request_id(message: dict) -> tuple[type, int | str]:
    value = message.get("id")
    if type(value) not in (int, str):
        raise ProtocolError("Invalid MCP request identity.")
    return type(value), value


class Frames:
    """Bounded newline-delimited JSON, the pinned SDK's stdio framing."""

    def __init__(self):
        self.buffer = bytearray()

    def receive(self, data: bytes):
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            boundary = self.buffer.index(b"\n")
            if boundary > MAX_MESSAGE_BYTES:
                raise ProtocolError("MCP message exceeds the framing limit.")
            raw = bytes(self.buffer[: boundary + 1])
            del self.buffer[: boundary + 1]
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise ProtocolError("Invalid MCP JSON frame.") from error
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ProtocolError("Invalid MCP envelope.")
            if "method" in message:
                if not isinstance(message["method"], str):
                    raise ProtocolError("Invalid MCP method.")
                if "id" in message:
                    _request_id(message)
            elif "id" not in message or ("result" in message) == ("error" in message):
                raise ProtocolError("Invalid MCP response.")
            yield message, raw
        if len(self.buffer) > MAX_MESSAGE_BYTES:
            raise ProtocolError("MCP message exceeds the framing limit.")


class Backend:
    """One generation's process group, guardian and inode-bound output."""

    def __init__(self, npx: str, chromium: str, environment: dict):
        self.npx, self.chromium, self.environment = npx, chromium, environment
        self.session = self.process = self.guardian = None

    def start(self):
        if self.session is not None:
            raise ProtocolError("A live browser generation cannot be replaced.")
        self.session = create_session(REPOSITORY_ROOT)
        self.process = subprocess.Popen(
            command(self.npx, self.chromium, self.session.path / "output"),
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            start_new_session=True,
        )
        _record_child(self.session, self.process.pid)
        # Only a verified new session leader establishes process-group ownership.
        if os.getsid(self.process.pid) == self.process.pid:
            self.process._schemii_group = {
                self.process.pid: self.session.metadata["child_birth_tick"]
            }
            _group_members(self.process)
        self.guardian = _start_guardian(self.session)

    def close(self):
        deferred_signal = []
        handlers = {}

        def defer(signum, frame):
            if not deferred_signal:
                deferred_signal.append(signum)

        # A release is a finalizer too: interruption cannot strand a partially
        # stopped generation after its ownership references have been cleared.
        for signum in (signal.SIGINT, signal.SIGTERM):
            handler = signal.getsignal(signum)
            if handler != signal.SIG_IGN:
                handlers[signum] = signal.signal(signum, defer)
        completed = False
        try:
            if self.process is not None:
                _stop_child(self.process)
            if self.session is not None:
                cleanup_session(self.session)
            completed = True
        finally:
            try:
                if self.guardian is not None:
                    _stop_guardian(self.guardian)
            finally:
                if self.process is not None:
                    for handle in (self.process.stdin, self.process.stdout):
                        if handle is not None:
                            handle.close()
                self.session = self.process = self.guardian = None
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
        if completed and deferred_signal:
            raise ShutdownRequested(deferred_signal[0])


class Relay:
    """Keep the native endpoint while explicitly closed generations are released."""

    def __init__(self, backend: Backend, incoming: int, outgoing: int):
        self.backend, self.incoming, self.outgoing = backend, incoming, outgoing
        self.selector = selectors.DefaultSelector()
        self.client_frames, self.backend_frames = Frames(), Frames()
        self.to_client, self.to_backend = bytearray(), bytearray()
        self.requests, self.inflight, self.callbacks = {}, {}, set()
        self.deferred = []
        self.closing = None
        self.initialize = self.identity = self.private_id = None
        self.initialized = None
        self.deadline = None
        self.expiry = time.monotonic() + OUTPUT_SWEEP_SECONDS
        self.original_blocking = {}
        for descriptor in (incoming, outgoing):
            self.original_blocking[descriptor] = os.get_blocking(descriptor)
            os.set_blocking(descriptor, False)

    def queue(self, target: bytearray, raw: bytes):
        target.extend(raw)
        self.check_budget()

    def check_budget(self):
        buffered = sum(
            map(
                len,
                (
                    self.to_client,
                    self.to_backend,
                    self.client_frames.buffer,
                    self.backend_frames.buffer,
                ),
            )
        )
        buffered += sum(len(raw) for _, raw in self.deferred)
        if self.closing is not None:
            buffered += len(self.closing[1]) + len(self.closing[2] or b"")
        if (
            buffered > MAX_RELAY_BYTES
            or len(self.requests) + len(self.callbacks) > MAX_PENDING_REQUESTS
        ):
            raise ProtocolError("MCP relay exceeds its bounded admission budget.")

    @staticmethod
    def encoded(message: dict) -> bytes:
        return json.dumps(message, separators=(",", ":")).encode() + b"\n"

    def error(self, identity, message: str, code: int = -32603):
        self.queue(
            self.to_client,
            self.encoded(
                {
                    "jsonrpc": "2.0",
                    "id": identity[1],
                    "error": {"code": code, "message": message},
                }
            ),
        )

    def forward(self, message: dict, raw: bytes):
        if "method" in message and "id" in message:
            self.inflight[_request_id(message)] = message["method"]
        self.queue(self.to_backend, raw)

    def client(self, message: dict, raw: bytes):
        if "method" not in message:
            identity = _request_id(message)
            if identity not in self.callbacks:
                raise ProtocolError("Unmatched MCP callback response.")
            self.callbacks.remove(identity)
            self.queue(self.to_backend, raw)
            return
        if "id" not in message:
            if message["method"] == "notifications/cancelled":
                params = message.get("params", {})
                if not isinstance(params, dict):
                    raise ProtocolError("Invalid MCP cancellation parameters.")
                cancelled = _request_id({"id": params.get("requestId")})
                if self.closing and _request_id(self.closing[0]) == cancelled:
                    self.closing[3] = True
                for index, (queued, _) in enumerate(self.deferred):
                    if "id" in queued and _request_id(queued) == cancelled:
                        self.deferred.pop(index)
                        self.requests.pop(cancelled)
                        self.error(
                            cancelled, "MCP request cancelled before execution.", -32800
                        )
                        return
            if message["method"] == "notifications/initialized":
                self.initialized = raw
                if self.backend.process is None or self.private_id is not None:
                    return
            if self.backend.process is None or self.private_id is not None:
                self.deferred.append((message, raw))
            else:
                self.queue(self.to_backend, raw)
            return
        identity = _request_id(message)
        if identity in self.requests:
            raise ProtocolError("Duplicate MCP request identity.")
        if self.backend.process is None and message["method"] == "ping":
            self.queue(
                self.to_client,
                self.encoded({"jsonrpc": "2.0", "id": message["id"], "result": {}}),
            )
            return
        self.requests[identity] = message["method"]
        self.check_budget()
        if self.closing or self.private_id is not None or self.backend.process is None:
            self.deferred.append((message, raw))
        elif (
            message["method"] == "tools/call"
            and isinstance(message.get("params"), dict)
            and message["params"].get("name") == "browser_close"
        ):
            self.closing = [message, raw, None, False]
        else:
            if message["method"] == "initialize":
                self.initialize = message.get("params")
            self.forward(message, raw)

    @staticmethod
    def negotiated(result):
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("protocolVersion"), str)
            or not isinstance(result.get("capabilities"), dict)
        ):
            raise ProtocolError("Invalid MCP initialization response.")
        return {
            key: result.get(key)
            for key in ("protocolVersion", "capabilities", "serverInfo")
        }

    def server(self, message: dict, raw: bytes):
        if "method" in message:
            if "id" in message:
                identity = _request_id(message)
                if identity in self.callbacks:
                    raise ProtocolError("Duplicate MCP callback identity.")
                self.callbacks.add(identity)
            self.queue(self.to_client, raw)
            return
        identity = _request_id(message)
        if identity == self.private_id:
            if (
                "error" in message
                or self.negotiated(message.get("result")) != self.identity
            ):
                raise ProtocolError("Recreated MCP capabilities are incompatible.")
            self.private_id = self.deadline = None
            if self.initialized:
                self.queue(self.to_backend, self.initialized)
            return
        if identity not in self.inflight:
            raise ProtocolError("Unmatched MCP backend response.")
        method = self.inflight.pop(identity)
        if method == "initialize" and "error" not in message:
            self.identity = self.negotiated(message.get("result"))
        if self.closing and identity == _request_id(self.closing[0]):
            result = message.get("result")
            succeeded = (
                "error" not in message
                and isinstance(result, dict)
                and isinstance(result.get("content"), list)
                and ("isError" not in result or result["isError"] is False)
            )
            if succeeded and not self.closing[3]:
                self.closing[2] = raw
                return
            self.closing = None
        self.requests.pop(identity)
        self.queue(self.to_client, raw)

    def progress(self):
        if (
            self.closing
            and not self.inflight
            and not self.callbacks
            and not self.to_backend
            and not self.backend_frames.buffer
        ):
            message, raw, response, cancelled = self.closing
            identity = _request_id(message)
            if response is not None:
                if cancelled:
                    self.closing = None
                    self.requests.pop(identity)
                    self.queue(self.to_client, response)
                    return
                try:
                    self.detach()
                    self.backend.close()
                except (OSError, ArtifactError, subprocess.TimeoutExpired):
                    self.requests.pop(identity)
                    self.closing = None
                    self.error(
                        identity, "Owned browser resources could not be released."
                    )
                    raise
                self.closing = None
                self.requests.pop(identity)
                self.queue(self.to_client, response)
            elif cancelled:
                self.closing = None
                self.requests.pop(identity)
                self.error(identity, "MCP request cancelled before execution.", -32800)
            else:
                self.forward(message, raw)
        if not self.closing and self.private_id is None and self.deferred:
            if self.backend.process is None:
                # Flush the close acknowledgement before allocating a follow-up.
                if self.to_client:
                    return
                if not any("id" in message for message, _ in self.deferred):
                    return
                if (
                    self.initialize is None
                    or self.identity is None
                    or self.initialized is None
                ):
                    raise ProtocolError(
                        "No successful MCP initialization can be restored."
                    )
                self.backend.start()
                self.backend_frames = Frames()
                self.expiry = time.monotonic() + OUTPUT_SWEEP_SECONDS
                value = f"schemii-private-{secrets.token_hex(16)}"
                self.private_id = (str, value)
                while (
                    self.private_id in self.requests
                    or self.private_id in self.callbacks
                ):
                    value = f"schemii-private-{secrets.token_hex(16)}"
                    self.private_id = (str, value)
                self.deadline = time.monotonic() + INITIALIZE_TIMEOUT_SECONDS
                self.queue(
                    self.to_backend,
                    self.encoded(
                        {
                            "jsonrpc": "2.0",
                            "id": value,
                            "method": "initialize",
                            "params": self.initialize,
                        }
                    ),
                )
                return
            while self.deferred and not self.closing:
                message, raw = self.deferred.pop(0)
                if "id" in message:
                    self.requests.pop(_request_id(message))
                self.client(message, raw)

    def detach(self):
        if self.backend.process is not None:
            for handle in (self.backend.process.stdin, self.backend.process.stdout):
                try:
                    self.selector.unregister(handle.fileno())
                except KeyError:
                    pass

    def interests(self):
        desired = {self.incoming: (selectors.EVENT_READ, "client")}
        if self.to_client:
            desired[self.outgoing] = (selectors.EVENT_WRITE, "output")
        if self.backend.process is not None:
            process = self.backend.process
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stdin.fileno(), False)
            desired[process.stdout.fileno()] = (selectors.EVENT_READ, "server")
            if self.to_backend:
                desired[process.stdin.fileno()] = (selectors.EVENT_WRITE, "backend")
        for descriptor in list(self.selector.get_map()):
            if descriptor not in desired:
                self.selector.unregister(descriptor)
        for descriptor, (events, label) in desired.items():
            if descriptor in self.selector.get_map():
                self.selector.modify(descriptor, events, label)
            else:
                self.selector.register(descriptor, events, label)

    def flush_failure(self):
        # An unresponsive upstream must not prevent bounded resource cleanup.
        deadline = time.monotonic() + 1
        with selectors.DefaultSelector() as writer:
            writer.register(self.outgoing, selectors.EVENT_WRITE)
            while self.to_client and time.monotonic() < deadline:
                if not writer.select(timeout=0.05):
                    continue
                try:
                    written = os.write(self.outgoing, self.to_client[:65536])
                except (BrokenPipeError, BlockingIOError):
                    break
                del self.to_client[:written]

    def run(self):
        try:
            while True:
                self.progress()
                self.check_budget()
                self.interests()
                for key, _ in self.selector.select(timeout=0.1):
                    if key.data in {"output", "backend"}:
                        target = (
                            self.to_client if key.data == "output" else self.to_backend
                        )
                        written = os.write(key.fd, target[:65536])
                        del target[:written]
                        continue
                    data = os.read(key.fd, 65536)
                    if not data:
                        if key.data == "client":
                            if self.client_frames.buffer:
                                raise ProtocolError("Truncated MCP client frame.")
                            return 0
                        raise ProtocolError("Browser backend ended unexpectedly.")
                    frames = (
                        self.client_frames
                        if key.data == "client"
                        else self.backend_frames
                    )
                    for message, raw in frames.receive(data):
                        if key.data == "client":
                            self.client(message, raw)
                        else:
                            self.server(message, raw)
                        self.check_budget()
                    if key.data == "server":
                        _group_members(self.backend.process)
                if self.deadline is not None and time.monotonic() > self.deadline:
                    raise ProtocolError("Browser initialization timed out.")
                if self.backend.session is not None and time.monotonic() >= self.expiry:
                    expire_output(self.backend.session, time.time())
                    self.expiry = time.monotonic() + OUTPUT_SWEEP_SECONDS
        except (ProtocolError, OSError, ArtifactError, subprocess.TimeoutExpired):
            for identity in self.requests:
                self.error(
                    identity, "Browser transport failed; the request was not replayed."
                )
            self.flush_failure()
            raise
        finally:
            self.selector.close()
            for descriptor, blocking in self.original_blocking.items():
                os.set_blocking(descriptor, blocking)


def relay(backend: Backend) -> int:
    return Relay(backend, sys.stdin.fileno(), sys.stdout.fileno()).run()


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
    backend = None
    handlers = {}
    result = 2
    try:
        # Ambient MCP settings can override isolation even with fixed CLI flags.
        server_environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("PLAYWRIGHT_MCP_")
        }
        backend = Backend(npx, chromium, server_environment)

        def shutdown(received, frame):
            # Repeated cancellation must not interrupt the bounded finalizer.
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            if backend.process is not None:
                _signal_child(backend.process, received)
            raise ShutdownRequested(received)

        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, shutdown)
        backend.start()
        result = relay(backend)
        result = 128 - result if result < 0 else result
    except ShutdownRequested as shutdown:
        result = 128 + shutdown.signum
    except (
        ArtifactError,
        ProtocolError,
        OSError,
        ValueError,
        IndexError,
        subprocess.TimeoutExpired,
    ):
        print(
            "Schemii browser MCP setup failed. Check tool installation, the warmed package cache, "
            "checkout ownership and private artifact permissions.",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        result = 130
    finally:
        for signum in handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if backend is not None:
                try:
                    backend.close()
                except (ArtifactError, OSError, subprocess.TimeoutExpired):
                    print(
                        "Schemii browser MCP could not remove its temporary session output. "
                        "Unrelated data was preserved.",
                        file=sys.stderr,
                    )
                    result = 2
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
