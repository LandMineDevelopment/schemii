"""Run the incremental Python lint, format and typing boundary used by CI."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SEED_MODULES = (
    "src/schemii/schemoo/join_types.py",
    "src/schemii/schemer/time_analysis.py",
)


def git_paths(*arguments: str) -> set[str]:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True
    )
    return {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}


def changed_python_files(base: str) -> tuple[list[str], list[str]]:
    changed = git_paths("diff", "--name-only", "-z", "--diff-filter=ACMR", base, "--")
    untracked = git_paths("ls-files", "--others", "--exclude-standard", "-z", "--")
    base_files = git_paths("ls-tree", "-r", "--name-only", "-z", base)
    paths = {
        path
        for path in changed | untracked
        if path.endswith(".py") and (ROOT / path).is_file()
    }
    new_files = sorted(path for path in paths if path not in base_files)
    modified_files = sorted(path for path in paths if path in base_files)
    return new_files, modified_files


def run(arguments: Sequence[str]) -> None:
    print("+", " ".join(arguments), flush=True)
    result = subprocess.run(arguments, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    if len(sys.argv) > 2:
        raise SystemExit("Usage: python scripts/check_python_quality.py [BASE_REF]")
    if re.fullmatch(r"0+", base):
        parent = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if parent.returncode:
            raise SystemExit(
                "The push base is all zeros and HEAD has no parent; refusing to "
                "skip changed Python files. Supply an explicit base ref."
            )
        base = parent.stdout.strip()
        print(
            f"Push base was all zeros; comparing against HEAD parent {base}.",
            flush=True,
        )
    subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    new_files, modified_files = changed_python_files(base)
    seed_modules = list(SEED_MODULES)
    full_lint_files = sorted(set(new_files) | set(seed_modules))
    legacy_files = sorted(set(modified_files) - set(seed_modules))

    if full_lint_files:
        run([sys.executable, "-m", "ruff", "check", *full_lint_files])
        run([sys.executable, "-m", "ruff", "format", "--check", *full_lint_files])
    if legacy_files:
        run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--select",
                "F821,F823",
                *legacy_files,
            ]
        )

    run([sys.executable, "-m", "mypy", *seed_modules])
    if not new_files and not modified_files:
        print("No changed Python files; checked the selected formatter/type boundary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
