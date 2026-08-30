#!/usr/bin/env python3
"""Fail when tracked source could publish private database or runtime data."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {
    ".dump", ".backup", ".bak", ".sqlite", ".sqlite3", ".db",
    ".tar", ".tgz", ".gz", ".zip",
}
FORBIDDEN_PARTS = {
    ".schemii", ".dev-test", "pgdata", "postgres-data", "dashboards",
    "credentials", "backups", "release",
}
FORBIDDEN_NAMES = {
    "postgres_profiles.json", "migration_history.json", ".env",
}
ALLOWED_SQL = {
    "examples/postgres/001_bookstore.sql",
    "docker/metadata/002_rotation_function.sql",
    "docker/metadata/verify_security.sql",
}
ALLOWED_SQL_PREFIXES = ("src/schemii/metadata/migrations/",)


def tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def violations(paths: list[str]) -> list[str]:
    problems = []
    for value in paths:
        path = PurePosixPath(value)
        if path.name in FORBIDDEN_NAMES:
            problems.append(f"runtime file is tracked: {value}")
        if any(part in FORBIDDEN_PARTS for part in path.parts):
            problems.append(f"runtime directory is tracked: {value}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"database/archive artifact is tracked: {value}")
        if path.suffix.lower() == ".sql" and value not in ALLOWED_SQL and not value.startswith(ALLOWED_SQL_PREFIXES):
            problems.append(f"SQL outside the metadata code or approved example seed is tracked: {value}")
    return problems


def main() -> None:
    problems = violations(tracked_paths())
    if problems:
        raise SystemExit("Release hygiene failed:\n- " + "\n- ".join(problems))
    print("Release hygiene passed: no private database or runtime data is tracked.")


if __name__ == "__main__":
    main()
