#!/usr/bin/env python3
"""Emit public build provenance from explicitly copied source, never private state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

INPUTS = (
    "Dockerfile",
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
    paths = {root / name for name in INPUTS}
    for prefix in PREFIXES:
        paths.update(
            path
            for path in (root / prefix).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
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


def source_commit(root: Path) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root
        )
        return commit + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit", default="unknown")
    parser.add_argument("--print-commit", action="store_true")
    args = parser.parse_args()
    print(
        source_commit(args.root)
        if args.print_commit
        else json.dumps(manifest(args.root, args.source_commit), sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
