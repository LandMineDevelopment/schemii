from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from pathlib import Path

import schemii
from schemii.build_identity import build_identity


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: verify-installed-package.py <expected-version> <expected-revision> <source-root>")
    expected_version, expected_revision = sys.argv[1:3]
    source_root = Path(sys.argv[3]).resolve()
    if importlib.metadata.version("schemii") != expected_version:
        raise SystemExit("Installed package version does not match VERSION")
    if build_identity() != {"version": expected_version, "revision": expected_revision}:
        raise SystemExit("Installed package build identity does not match the tested release")

    entry_points = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="console_scripts")
        if entry.name in {"schemii", "schemer"}
    }
    if entry_points != {"schemii": "schemii.server:main", "schemer": "schemii.schemer_server:main"}:
        raise SystemExit("Installed console entry points are incomplete or incorrect")

    installed_root = Path(schemii.__file__).resolve().parent
    expected_root = source_root / "src/schemii"
    for relative in ("web", "schemer_web", "shared_web", "metadata/migrations"):
        expected = file_manifest(expected_root / relative)
        installed = file_manifest(installed_root / relative)
        if not expected or installed != expected:
            raise SystemExit(f"Installed package assets differ from source: {relative}")


if __name__ == "__main__":
    main()
