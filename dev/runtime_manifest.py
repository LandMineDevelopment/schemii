#!/usr/bin/env python3
"""Emit public build provenance from explicitly copied source, never private state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

INPUTS = (
    "Dockerfile",
    "README.md",
    "LICENSE",
    "compose.test.yaml",
    "pyproject.toml",
    "constraints.docker.txt",
    "dev/runtime-contract.json",
    "dev/runtime_manifest.py",
    "dev/schemii.toml",
    "dev/ingress/nginx.conf",
    "start.sh",
    "ai/prototype/Dockerfile",
    "ai/prototype/package.json",
    "ai/prototype/package-lock.json",
)
PREFIXES = ("src", "ai/prototype", "dev/postgres")


def manifest(root: Path, commit: str) -> dict:
    if not re.fullmatch(r"(?:[0-9a-f]{40}(?:\+dirty)?|unknown)", commit):
        raise ValueError("Invalid source commit identity")
    contract = json.loads((root / "dev/runtime-contract.json").read_text())
    excluded = set(contract["private_state"]["release_excludes"])
    private_names = set(contract["private_state"]["release_private_names"])
    private_suffixes = tuple(contract["private_state"]["release_private_suffixes"])
    paths = {root / name for name in INPUTS}
    for prefix in PREFIXES:
        paths.update(
            path
            for path in (root / prefix).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
            and path.name not in private_names
            and not path.name.endswith(private_suffixes)
            and (
                prefix != "ai/prototype"
                or (path.parent == root / prefix and path.suffix == ".js")
            )
            and (
                prefix != "dev/postgres"
                or (path.parent == root / prefix and path.suffix in (".sh", ".sql"))
            )
            and not excluded.intersection(path.relative_to(root).parts)
            and not any(
                part.startswith((".env", ".venv"))
                for part in path.relative_to(root).parts
            )
            and path.suffix
            not in (".key", ".pem", ".log", ".sqlite", ".db", ".dump", ".backup")
        )
    hashes = {}
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError("Build source cannot be a symlink")
        hashes[str(path.relative_to(root))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return {
        "schema": 1,
        "platform": "docker",
        "commit": commit,
        "source_files": hashes,
        "runtime_contract": json.loads(
            (root / "dev/runtime-contract.json").read_text()
        ),
        "live_acceptance": "pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit", default="unknown")
    args = parser.parse_args()
    print(json.dumps(manifest(args.root, args.source_commit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
