from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .file_lock import set_file_mode


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":  # pragma: no cover - directory handles are not fsync-compatible on Windows.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove_file(path: str | os.PathLike[str]) -> None:
    destination = Path(path)
    destination.unlink(missing_ok=True)
    _sync_directory(destination.parent)


def write_json(
    destination: str | os.PathLike[str],
    payload: Any,
    *,
    mode: int | None = None,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    path = Path(destination)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        if mode is not None:
            set_file_mode(descriptor, temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, indent=indent, sort_keys=sort_keys)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _sync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
