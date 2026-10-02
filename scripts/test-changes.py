"""Print or run the complete checks selected from the actual local Git state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))
from classify_changes import classify, git  # noqa: E402
from test_selection import commands, layers, select_paths  # noqa: E402


POSTGRES_COMMAND = [
    "python",
    "-m",
    "pytest",
    "-q",
    "-p",
    "scripts.ci.pytest_timing",
    "tests/integration",
]
BROWSER_BOUNDARY = ["npm", "ci"]


def local_changes(root: Path, *arguments: str) -> tuple[list, bool]:
    """Keep both rename paths and modes from Git's NUL-delimited raw diff."""
    values = git(
        root, "diff", "--raw", "-z", "--no-abbrev", "--find-renames", *arguments, "--"
    ).split(b"\0")
    if values.pop() != b"":
        raise ValueError("Invalid local diff")
    result, safe = [], True
    while values:
        header = values.pop(0).decode("ascii").split()
        if len(header) != 5 or not header[0].startswith(":"):
            raise ValueError("Invalid local diff header")
        old_mode, new_mode, _, _, status = header
        count = 2 if status.startswith(("R", "C")) else 1
        if len(values) < count:
            raise ValueError("Incomplete local diff")
        paths = [os.fsdecode(values.pop(0)) for _ in range(count)]
        if any(not path for path in paths):
            raise ValueError("Invalid local path")
        safe = (
            safe
            and status == "M"
            and old_mode[1:] == new_mode
            and new_mode in {"100644", "100755"}
        )
        result.append((status, paths))
    return result, safe


def regular_local_file(root: Path, path: str, base: str, head: str) -> bool:
    """Prove the same regular mode in both commits, stage zero and the filesystem."""
    modes = []
    for revision in (base, head):
        entry = git(root, "ls-tree", "-z", revision, "--", path).split(b"\0")
        if len(entry) != 2 or entry[-1] != b"":
            return False
        row = entry[0].split(b"\t", 1)
        if len(row) != 2 or row[1] != os.fsencode(path):
            return False
        fields = row[0].split()
        if len(fields) != 3 or fields[1] != b"blob":
            return False
        modes.append(fields[0])
    index = git(root, "ls-files", "--stage", "-z", "--", path).split(b"\0")
    if len(index) != 2 or index[-1] != b"":
        return False
    row = index[0].split(b"\t", 1)
    if len(row) != 2 or row[1] != os.fsencode(path):
        return False
    fields = row[0].split()
    if len(fields) != 3 or fields[2] != b"0":
        return False
    modes.append(fields[0])
    file = root / path
    if any(
        parent.is_symlink()
        for parent in file.parents
        if parent != root and parent.is_relative_to(root)
    ):
        return False
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode):
        return False
    modes.append(b"100755" if info.st_mode & stat.S_IXUSR else b"100644")
    return modes[0] in {b"100644", b"100755"} and len(set(modes)) == 1


def feedback_commands(selected: list[list[str]]) -> list[list[str]]:
    """The existing deterministic prefix stops before either real-layer boundary."""
    result = []
    for argv in selected:
        if argv in (POSTGRES_COMMAND, BROWSER_BOUNDARY):
            break
        result.append(argv)
    return result


def plan(root: Path, base: str) -> dict:
    try:
        head = git(root, "rev-parse", "HEAD").decode().strip()
        base_sha = (
            git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
        )
        classification = classify(root, "pull_request", base_sha, head)
        comparison_base = classification["comparison_base"] or base_sha
        committed, committed_safe = local_changes(root, comparison_base, head)
        staged, staged_safe = local_changes(root, "--cached", head)
        unstaged, unstaged_safe = local_changes(root)
        untracked = [
            os.fsdecode(path)
            for path in git(
                root, "ls-files", "--others", "--exclude-standard", "-z"
            ).split(b"\0")
            if path
        ]
        index_flags = git(root, "ls-files", "-v", "-z").split(b"\0")
        if index_flags.pop() != b"" or any(
            len(item) < 3 or item[1:2] != b" " for item in index_flags
        ):
            raise ValueError("Invalid local index flags")
        # Lowercase flags and S can hide assume-unchanged/skip-worktree edits.
        unverified = sorted(
            {os.fsdecode(item[2:]) for item in index_flags if item[:2] != b"H "}
        )
        # These settings can hide mode/type changes on shared paths absent from diff.
        reliable_discovery = all(
            git(root, "config", "--bool", "--get", "--default", "true", option).strip()
            == b"true"
            for option in ("core.fileMode", "core.symlinks")
        )
        dirty = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        changed = committed + staged + unstaged + [("?", [path]) for path in untracked]
        paths = sorted({path for _, names in changed for path in names})
        profile = select_paths(changed)
        safe = False
        if (
            classification["valid"]
            and profile != "full"
            and not unverified
            and reliable_discovery
        ):
            safe = (
                committed_safe
                and staged_safe
                and unstaged_safe
                and all(
                    regular_local_file(root, path, comparison_base, head)
                    for path in paths
                )
            )
        if (
            dirty
            or unverified
            or not reliable_discovery
            or (classification["profile"] not in {"full", "reports"} and not safe)
        ):
            classification.update(
                # Local proof describes the index/worktree, not an immutable hosted SHA.
                schema=1,
                scope="local-worktree",
                lane="source",
                profile=profile if safe and classification["valid"] else "full",
                reason="unreliable-local-file-discovery"
                if not reliable_discovery
                else "unsupported-local-index"
                if unverified
                else "verified-owned-local-change"
                if safe
                else "local-source-or-unknown-change",
                markdown=[],
            )
        # Preserve rename/copy status and both paths without trusting porcelain text as a selector.
        local_status = [os.fsdecode(item) for item in dirty.split(b"\0") if item]
    except (OSError, ValueError, subprocess.SubprocessError):
        classification = {
            "schema": 2,
            "valid": False,
            "lane": "source",
            "profile": "full",
            "reason": "invalid-local-comparison",
            "base": base,
            "head": "",
            "comparison_base": "",
            "markdown": [],
        }
        paths, local_status, unverified = [], [], []
    selected_commands = commands(classification["profile"], base)
    return {
        "classification": classification,
        "changed_paths": paths,
        "local_status": local_status,
        "unverified_paths": unverified,
        "layers": sorted(layers(classification["profile"])),
        "commands": selected_commands,
        "feedback": {
            "commands": feedback_commands(selected_commands),
            "pending_layers": sorted(
                layers(classification["profile"]) & {"postgres", "browser"}
            ),
            "acceptance": False,
        },
    }


def execute(root: Path, selected: dict, *, feedback: bool = False) -> int:
    if not selected["classification"]["valid"]:
        print("Cannot run checks without a valid Git comparison.", file=sys.stderr)
        return 2
    if selected["classification"]["profile"] == "reports":
        target = root / ".schemii/test-selection/classification.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(selected["classification"]) + "\n")
    selected_commands = selected["commands"]
    if feedback:
        pending = sorted(set(selected["layers"]) & {"postgres", "browser"})
        print(
            "Development feedback only; full acceptance is not established.", flush=True
        )
        print(
            "Required pending acceptance layers: " + (", ".join(pending) or "none"),
            flush=True,
        )
        selected_commands = feedback_commands(selected_commands)
    for argv in selected_commands:
        if argv == POSTGRES_COMMAND and not os.environ.get("SCHEMII_TEST_METADATA_DSN"):
            print(
                "PostgreSQL acceptance pending: SCHEMII_TEST_METADATA_DSN is required; remaining checks have not run.",
                file=sys.stderr,
            )
            return 2
        if argv == BROWSER_BOUNDARY and not (
            os.environ.get("SCHEMII_E2E_BOOTSTRAP") == "1"
            or os.environ.get("SCHEMII_E2E_CREDENTIALS_FILE")
            or os.environ.get("SCHEMII_E2E_USERNAME")
            and os.environ.get("SCHEMII_E2E_PASSWORD")
        ):
            print(
                "Browser acceptance pending: explicit owned credentials or SCHEMII_E2E_BOOTSTRAP=1 are required; remaining checks have not run.",
                file=sys.stderr,
            )
            return 2
        print("+ " + shlex.join(argv), flush=True)
        result = subprocess.run(argv, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--run",
        action="store_true",
        help="Execute every printed check, stopping on failure.",
    )
    execution.add_argument(
        "--feedback",
        action="store_true",
        help="Run deterministic development feedback; real acceptance layers remain pending.",
    )
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    selected = plan(root, args.base)
    print(json.dumps(selected, indent=2))
    return (
        execute(root, selected, feedback=args.feedback)
        if args.run or args.feedback
        else (0 if selected["classification"]["valid"] else 2)
    )


if __name__ == "__main__":
    raise SystemExit(main())
