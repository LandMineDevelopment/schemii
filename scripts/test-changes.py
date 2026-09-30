"""Print or run the complete checks selected from the actual local Git state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))
from classify_changes import classify, git  # noqa: E402
from test_selection import commands, layers  # noqa: E402


def plan(root: Path, base: str) -> dict:
    try:
        head = git(root, "rev-parse", "HEAD").decode().strip()
        base_sha = (
            git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
        )
        classification = classify(root, "pull_request", base_sha, head)
        committed = git(
            root,
            "diff",
            "--name-only",
            "-z",
            classification["comparison_base"] or base_sha,
            head,
        ).split(b"\0")
        dirty = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if dirty:
            classification.update(
                lane="source",
                profile="full",
                reason="local-uncommitted-change",
                markdown=[],
            )
        paths = sorted({os.fsdecode(path) for path in committed if path})
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
        paths, local_status = [], []
    return {
        "classification": classification,
        "changed_paths": paths,
        "local_status": local_status,
        "layers": sorted(layers(classification["profile"])),
        "commands": commands(classification["profile"], base),
    }


def execute(root: Path, selected: dict) -> int:
    if not selected["classification"]["valid"]:
        print("Cannot run checks without a valid Git comparison.", file=sys.stderr)
        return 2
    if "postgres" in selected["layers"] and not os.environ.get(
        "SCHEMII_TEST_METADATA_DSN"
    ):
        print(
            "Full checks require SCHEMII_TEST_METADATA_DSN for real PostgreSQL acceptance.",
            file=sys.stderr,
        )
        return 2
    if "browser" in selected["layers"] and not (
        os.environ.get("SCHEMII_E2E_BOOTSTRAP") == "1"
        or os.environ.get("SCHEMII_E2E_CREDENTIALS_FILE")
        or os.environ.get("SCHEMII_E2E_USERNAME")
        and os.environ.get("SCHEMII_E2E_PASSWORD")
    ):
        print(
            "Browser acceptance requires explicit owned credentials or SCHEMII_E2E_BOOTSTRAP=1; remaining real layers have not run.",
            file=sys.stderr,
        )
        return 2
    if selected["classification"]["profile"] == "reports":
        target = root / ".schemii/test-selection/classification.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(selected["classification"]) + "\n")
    for argv in selected["commands"]:
        print("+ " + shlex.join(argv), flush=True)
        result = subprocess.run(argv, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute every printed check, stopping on failure.",
    )
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    selected = plan(root, args.base)
    print(json.dumps(selected, indent=2))
    return (
        execute(root, selected)
        if args.run
        else (0 if selected["classification"]["valid"] else 2)
    )


if __name__ == "__main__":
    raise SystemExit(main())
