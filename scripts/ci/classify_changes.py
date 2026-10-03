"""Positively identify independent PR owners; unknown changes use full source CI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


if __package__:
    from .test_selection import (
        NATIVE_MARKDOWN,
        PROFILES,
        layers,
        select_paths,
        PYTHON_PATHS,
        browser_matrix,
    )
else:
    from test_selection import (
        NATIVE_MARKDOWN,
        PROFILES,
        layers,
        select_paths,
        PYTHON_PATHS,
        browser_matrix,
    )


REPORTS = {"docs/browser-shard-balance.md"}
AUDIT_REPORT = re.compile(r"docs/audits/\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
SHA = re.compile(r"[0-9a-f]{40}")
INDEX_HEADING = "\n## Reports\n"
INDEX_ENTRY = re.compile(r"- \[([\w .,/:()#-]+)\]\(([^\s()]+)\)")


def report_path(path: str) -> bool:
    return path in REPORTS or AUDIT_REPORT.fullmatch(path) is not None


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, timeout=20
    ).stdout


def readme_index_only(before: str, after: str) -> bool:
    """Only the final, list-only Reports section can change."""
    if before.count(INDEX_HEADING) > 1 or after.count(INDEX_HEADING) != 1:
        return False
    new_prefix, new_index = after.split(INDEX_HEADING)
    if INDEX_HEADING in before:
        old_prefix, old_index = before.split(INDEX_HEADING)
        if old_prefix != new_prefix:
            return False
    else:
        # Append a section without changing even one byte of the existing README.
        if not before.endswith("\n") or new_prefix != before:
            return False
        old_index = ""
    for index in (old_index, new_index):
        entries = [line for line in index.splitlines() if line]
        if index == new_index and not entries:
            return False
        targets = []
        for line in entries:
            match = INDEX_ENTRY.fullmatch(line)
            if match is None or not report_path(match[2]):
                return False
            targets.append(match[2])
        if len(targets) != len(set(targets)):
            return False
    return True


def changes(root: Path, base: str, head: str) -> list[tuple[str, list[str]]]:
    values = git(
        root, "diff", "--name-status", "-z", "--find-renames", base, head, "--"
    ).split(b"\0")
    if values.pop() != b"":
        raise ValueError("Invalid diff")
    result = []
    while values:
        status = values.pop(0).decode("ascii")
        count = 2 if status.startswith(("R", "C")) else 1
        if len(values) < count:
            raise ValueError("Incomplete diff")
        paths = [values.pop(0).decode("utf-8") for _ in range(count)]
        if any(not path or any(ord(char) < 32 for char in path) for path in paths):
            raise ValueError("Invalid path")
        result.append((status, paths))
    return result


def classify(root: Path, event: str, base: str, head: str) -> dict:
    result = {
        "schema": 2,
        "lane": "source",
        "profile": "full",
        "valid": True,
        "reason": "full-validation",
        "base": base,
        "head": head,
        "comparison_base": "",
        "markdown": [],
    }
    if event == "workflow_dispatch":
        result["reason"] = "explicit-full-validation"
        return result
    try:
        if event not in {"pull_request", "push"} or not all(
            SHA.fullmatch(value or "") for value in (base, head)
        ):
            raise ValueError("Unknown comparison")
        for value in (base, head):
            git(root, "rev-parse", "--verify", value + "^{commit}")
        if event == "pull_request":
            # Exclude unrelated commits that landed on base after the PR forked.
            merge_bases = (
                git(root, "merge-base", "--all", base, head).decode().splitlines()
            )
            if len(merge_bases) != 1:
                raise ValueError("Ambiguous merge base")
            comparison_base = merge_bases[0]
        else:
            # Every commit in a main push matters, not just HEAD's parent.
            git(root, "merge-base", "--is-ancestor", base, head)
            comparison_base = base
        result["comparison_base"] = comparison_base
        diff = changes(root, comparison_base, head)
        if not diff:
            result["reason"] = "empty-comparison"
            return result
        eligible = True
        safe_modes = True
        for status, paths in diff:
            path = paths[-1]
            # A profile never authorizes renamed/deleted/symlink or changed executable inputs.
            if status == "M":
                old_entry = git(
                    root, "ls-tree", "-z", comparison_base, "--", path
                ).split(b"\0")
                new_entry = git(root, "ls-tree", "-z", head, "--", path).split(b"\0")
                regular_mode = (
                    len(old_entry) == len(new_entry) == 2
                    and old_entry[0][:6] == new_entry[0][:6]
                    and new_entry[0][:6] in {b"100644", b"100755"}
                )
                safe_modes = safe_modes and regular_mode
                if regular_mode and path in NATIVE_MARKDOWN:
                    # Source ownership does not exempt its documentation from
                    # the existing mandatory Markdown/link validation job.
                    result["markdown"].append(path)
            else:
                safe_modes = False
            if status in {"A", "M"}:
                tree = git(root, "ls-tree", "-z", head, "--", path).split(b"\0")
                regular = len(tree) == 2 and tree[0].startswith(b"100644 blob ")
                if status == "M":
                    old_tree = git(
                        root, "ls-tree", "-z", comparison_base, "--", path
                    ).split(b"\0")
                    regular = (
                        regular
                        and len(old_tree) == 2
                        and old_tree[0].startswith(b"100644 blob ")
                    )
                if regular and report_path(path):
                    result["markdown"].append(path)
                    continue
                if regular and status == "M" and path == "README.md":
                    before = git(root, "show", comparison_base + ":README.md").decode(
                        "utf-8"
                    )
                    after = git(root, "show", head + ":README.md").decode("utf-8")
                    if readme_index_only(before, after):
                        targets = [
                            INDEX_ENTRY.fullmatch(line)[2]
                            for line in after.split(INDEX_HEADING)[1].splitlines()
                            if line
                        ]
                        target_trees = [
                            git(root, "ls-tree", "-z", head, "--", target).split(b"\0")
                            for target in targets
                        ]
                        if all(
                            len(tree) == 2 and tree[0].startswith(b"100644 blob ")
                            for tree in target_trees
                        ):
                            # Validate index destinations as reports, not only their existence.
                            result["markdown"].append(path)
                            result["markdown"].extend(targets)
                            continue
            # Includes both ends of renames, all deletions, mode changes and unknowns.
            eligible = False
        result["markdown"] = sorted(set(result["markdown"]))
        result.update(
            lane="reports" if eligible else "source",
            reason="verified-report-only" if eligible else "source-or-unknown-change",
            profile="reports" if eligible else "full",
        )
        if not eligible and event == "pull_request" and safe_modes:
            profile = select_paths(diff)
            if profile != "full":
                result.update(profile=profile, reason="verified-owned-pr-change")
    except (ValueError, TypeError, UnicodeError, subprocess.SubprocessError, OSError):
        result.update(
            valid=False,
            lane="source",
            profile="full",
            reason="invalid-comparison",
            markdown=[],
        )
    return result


def load_classification(path: Path) -> dict:
    value = json.loads(path.read_text())
    fields = {
        "schema",
        "lane",
        "profile",
        "valid",
        "reason",
        "base",
        "head",
        "comparison_base",
        "markdown",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["schema"]) is not int
        or value["schema"] != 2
        or type(value["valid"]) is not bool
        or value["lane"] not in ("reports", "source")
        or not isinstance(value["profile"], str)
        or value["profile"] not in PROFILES
        or (value["lane"] == "reports") != (value["profile"] == "reports")
        or any(
            not isinstance(value[field], str)
            for field in ("reason", "base", "head", "comparison_base")
        )
        or value["profile"] not in {"full", "reports"}
        and (
            not value["valid"]
            or value["reason"] != "verified-owned-pr-change"
            or not all(
                SHA.fullmatch(value[field])
                for field in ("base", "head", "comparison_base")
            )
        )
        or not value["valid"]
        and value["profile"] != "full"
        or not isinstance(value["markdown"], list)
        or any(
            not isinstance(item, str)
            or not item.endswith(".md")
            or Path(item).is_absolute()
            or ".." in Path(item).parts
            or any(ord(char) < 32 for char in item)
            for item in value["markdown"]
        )
        or value["lane"] == "reports"
        and (
            not value["valid"]
            or value["reason"] != "verified-report-only"
            or not value["markdown"]
            or any(
                not (report_path(item) or item == "README.md")
                for item in value["markdown"]
            )
            or not all(
                SHA.fullmatch(value[field])
                for field in ("base", "head", "comparison_base")
            )
        )
    ):
        raise ValueError("Invalid classification")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    try:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if event == "pull_request":
            base, head = (
                payload["pull_request"]["base"]["sha"],
                payload["pull_request"]["head"]["sha"],
            )
        else:
            base, head = (
                payload.get("before", ""),
                payload.get("after", os.environ.get("GITHUB_SHA", "")),
            )
        result = classify(Path.cwd(), event, base, head)
    except (KeyError, OSError, ValueError, TypeError):
        result = classify(Path.cwd(), "invalid", "", "")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write("lane=" + result["lane"] + "\n")
            output.write("profile=" + result["profile"] + "\n")
            output.write(
                "browser_matrix="
                + json.dumps(browser_matrix(result["profile"]), separators=(",", ":"))
                + "\n"
            )
            for layer in ("static", "node", "python", "postgres", "browser"):
                output.write(
                    layer + "=" + str(layer in layers(result["profile"])).lower() + "\n"
                )
            output.write(
                "python_paths="
                + " ".join(PYTHON_PATHS.get(result["profile"], ()))
                + "\n"
            )
    print("CI lane=" + result["lane"] + "; reason=" + result["reason"])
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
