from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Hashable, Iterator

if os.name == "nt":  # pragma: no cover - imported on Windows.
    import msvcrt
else:  # pragma: no cover - imported on POSIX.
    import fcntl


def set_file_mode(descriptor: int, path: str | os.PathLike[str], mode: int) -> None:
    """Apply restrictive POSIX modes without assuming fchmod exists on Windows."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)
    else:  # pragma: no cover - Windows does not expose POSIX descriptor modes.
        os.chmod(path, mode)


def _lock(handle: BinaryIO) -> None:
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EDEADLK} and getattr(error, "winerror", None) not in {33, 36}:
                raise
            time.sleep(0.05)


def _unlock(handle: BinaryIO) -> None:
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def exclusive_file_lock(path: str | os.PathLike[str], *, mode: int = 0o600) -> Iterator[None]:
    """Hold an exclusive advisory lock shared by processes on POSIX and Windows."""
    lock_path = Path(path)
    with lock_path.open("a+b") as handle:
        set_file_mode(handle.fileno(), lock_path, mode)
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


class RefCountedKeyedFileGuard:
    """Keyed reentrant thread guards with one outer cross-process file lock."""

    def __init__(self, lock_path: Callable[[Hashable], str | os.PathLike[str]]):
        self._lock_path = lock_path
        self._registry_lock = threading.Lock()
        self._entries: dict[Hashable, dict[str, Any]] = {}
        self._local = threading.local()

    @contextmanager
    def thread(self, key: Hashable) -> Iterator[None]:
        lock = self._retain(key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            self._release(key, lock)

    @contextmanager
    def exclusive(self, key: Hashable) -> Iterator[None]:
        lock = self._retain(key)
        lock.acquire()
        depths = getattr(self._local, "depths", {})
        depth = depths.get(key, 0)
        depths[key] = depth + 1
        self._local.depths = depths
        try:
            if depth:
                yield
            else:
                with exclusive_file_lock(self._lock_path(key)):
                    yield
        finally:
            if depth:
                depths[key] = depth
            else:
                depths.pop(key, None)
            lock.release()
            self._release(key, lock)

    def _retain(self, key: Hashable) -> threading.RLock:
        with self._registry_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = {"lock": threading.RLock(), "references": 0}
                self._entries[key] = entry
            entry["references"] += 1
            return entry["lock"]

    def _release(self, key: Hashable, lock: threading.RLock) -> None:
        with self._registry_lock:
            entry = self._entries.get(key)
            if entry is None or entry["lock"] is not lock:
                return
            entry["references"] -= 1
            if entry["references"] == 0:
                del self._entries[key]
