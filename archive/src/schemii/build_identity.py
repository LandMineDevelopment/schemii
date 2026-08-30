from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path


_REVISION_FILE = Path(__file__).with_name("build_revision.txt")
_REVISION_RE = re.compile(r"^(?:development|[0-9a-f]{40})$")


def _version() -> str:
    try:
        return importlib.metadata.version("schemii")
    except importlib.metadata.PackageNotFoundError:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()


def build_identity() -> dict[str, str]:
    revision = _REVISION_FILE.read_text(encoding="utf-8").strip()
    if not _REVISION_RE.fullmatch(revision):
        raise RuntimeError("Packaged build revision is invalid")
    return {"version": _version(), "revision": revision}
