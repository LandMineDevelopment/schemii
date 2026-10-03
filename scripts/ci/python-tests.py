#!/usr/bin/env python3
"""Run independent pytest groups with isolated resources and one strict receipt.

No arguments preserves pytest's configured default discovery. Explicit paths or
node IDs select focused work using the same inspection-fixture boundary.
"""

import ast
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.summary import META, load, summarize  # noqa: E402
from scripts.ci.test_selection import INSPECTION_PROFILE, INSPECTION_PYTHON, valid_scope  # noqa: E402


INSPECTION = (
    "tests/test_database_inspection.py",
    "tests/test_developer_inspection.py",
    "tests/test_route_inspection.py",
    "tests/test_system_inspection.py",
)
SHUTDOWN_GRACE = 5


def partitions(root, selectors):
    """Keep shared inspection fixtures together without replacing discovery."""
    ignored = [f"--ignore={path}" for path in INSPECTION]
    if not selectors:
        for directory in ("tests", "testing"):
            for path in (root / directory).rglob("*.py"):
                if not (
                    path.name.startswith("test_") or path.name.endswith("_test.py")
                ):
                    continue
                try:
                    tree = ast.parse(path.read_text(), filename=str(path))
                except SyntaxError:
                    # Pytest owns collection errors and their original exit code.
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "pytest_plugins"
                        for target in node.targets
                    ):
                        if "inspection_fixtures" in ast.unparse(node.value):
                            if path.relative_to(root).as_posix() not in INSPECTION:
                                raise ValueError("Inspection fixture ownership changed")
        return [list(INSPECTION), ignored]

    normalized: list[tuple[Path, str]] = []
    for selector in selectors:
        file, *nodes = selector.split("::")
        path = (root / file).resolve()
        if selector.startswith("-") or not path.is_relative_to(root.resolve()):
            raise ValueError("Expected a repository test path or node ID")
        if not path.exists() or (nodes and not path.is_file()):
            raise ValueError("Selected test path does not exist")
        suffix = "::".join(nodes)
        for previous, previous_suffix in normalized:
            if (previous.is_dir() and path.is_relative_to(previous)) or (
                path.is_dir() and previous.is_relative_to(path)
            ):
                raise ValueError("Overlapping test selections")
            if previous == path and (
                not suffix
                or not previous_suffix
                or suffix == previous_suffix
                or suffix.startswith(previous_suffix + "::")
                or previous_suffix.startswith(suffix + "::")
            ):
                raise ValueError("Overlapping test selections")
        normalized.append((path, suffix))

    inspection: list[str] = []
    rest: list[str] = []
    for path, suffix in normalized:
        if path.is_dir():
            inspection.extend(
                item for item in INSPECTION if (root / item).is_relative_to(path)
            )
            rest.append(path.relative_to(root).as_posix())
        else:
            item = path.relative_to(root).as_posix()
            (inspection if item in INSPECTION else rest).append(
                item + (f"::{suffix}" if suffix else "")
            )
    return ([inspection] if inspection else []) + ([rest + ignored] if rest else [])


def expected_metadata(environment):
    sha = environment.get("CI_TELEMETRY_SHA", "")

    def numeric(key):
        value = environment.get(key, "")
        return (
            int(value)
            if value.isascii() and value.isdigit() and len(value) <= 20
            else 0
        )

    return {
        "schema": 1,
        "source_sha": sha
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
        else None,
        "run_id": numeric("CI_TELEMETRY_RUN_ID"),
        "run_attempt": numeric("CI_TELEMETRY_RUN_ATTEMPT"),
        "lane": environment.get("CI_TELEMETRY_LANE", "python"),
        "project": "none",
        "shard": 0,
    }


def merge(receipts, statuses, wall_ms, expected, cancelled=False):
    """Reject partial/duplicate inventories before publishing an aggregate."""
    if not receipts or len(receipts) != len(statuses):
        raise ValueError("Missing partition evidence")
    summaries = [summarize(records) for records in receipts]
    meta = {key: receipts[0][0][key] for key in META}
    if meta != expected or any(
        any(summary[key] != meta[key] for key in META) for summary in summaries
    ):
        raise ValueError("Mixed partition identities")
    plans = [
        record for records in receipts for record in records if record["kind"] == "plan"
    ]
    if len(plans) != len({record["test_id"] for record in plans}):
        raise ValueError("Overlapping partition inventory")
    attempts = [
        record
        for records in receipts
        for record in records
        if record["kind"] == "attempt"
    ]
    complete = all(summary["complete"] for summary in summaries)
    if cancelled:
        outcome = "cancelled"
    elif not complete or any(status not in {0, 1} for status in statuses):
        outcome = (
            "collection-error"
            if any(summary["outcome"] == "collection-error" for summary in summaries)
            else "error"
        )
    elif any(
        status == 1 or summary["outcome"] == "failed"
        for status, summary in zip(statuses, summaries)
    ):
        outcome = "failed"
    elif any(summary["outcome"] != "passed" for summary in summaries):
        outcome = "error"
    else:
        outcome = "passed"
    merged = [
        {
            **meta,
            "kind": "start",
            "planned": sum(summary["collected"] for summary in summaries),
        },
        *plans,
        *attempts,
        {**meta, "kind": "end", "outcome": outcome, "wall_ms": round(wall_ms, 3)},
    ]
    result = summarize(merged)
    if not cancelled and not complete and result["complete"]:
        raise ValueError("Incomplete partition became complete")
    return merged


def birth_tick(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except FileNotFoundError:
        return None


def signal_group(process, birth, signum):
    # The leader remains unreaped until group cleanup, preventing PID/group reuse.
    if birth is not None and birth_tick(process.pid) == birth:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass


def live_group(pid):
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pid and fields[0] not in {"Z", "X", "x"}:
            return True
    return False


def stop_children(children, requested):
    """Reap all owned groups, including descendants of an exited pytest leader."""
    for process, birth in children:
        signal_group(process, birth, requested or signal.SIGTERM)
    deadline = time.monotonic() + SHUTDOWN_GRACE
    escalated = False
    while any(live_group(process.pid) for process, _ in children):
        if not escalated and time.monotonic() >= deadline:
            for process, birth in children:
                signal_group(process, birth, signal.SIGKILL)
            escalated = True
        # Signal delivery is not proof of exit. Keep paths and the unreaped
        # leader identity until every owned group member is non-live.
        time.sleep(0.02)
    return [process.wait() for process, _ in children]


def error_status(statuses):
    return next(
        (
            status if status > 1 else 128 - status
            for status in statuses
            if status > 1 or status < 0
        ),
        3,
    )


def run(selectors=(), *, root=ROOT, environment=None, temporary_parent=None):
    root = Path(root).resolve()
    environment = dict(os.environ if environment is None else environment)
    destination = environment.get("CI_TELEMETRY_FILE")
    if destination:
        Path(destination).unlink(missing_ok=True)
    inspection = (
        environment.get("CI_TEST_PROFILE") == INSPECTION_PROFILE
        or tuple(selectors) == INSPECTION_PYTHON
    )
    try:
        if inspection and (
            tuple(selectors) != INSPECTION_PYTHON
            or environment.get("PYTEST_ADDOPTS", "").strip()
        ):
            raise ValueError("Inspection acceptance requires whole unfiltered files")
        groups = partitions(root, selectors)
    except (ValueError, SyntaxError, OSError):
        print("Python test selection is invalid; no tests were run.", file=sys.stderr)
        return 4

    requested = 0

    def interrupted(signum, _frame):
        nonlocal requested
        requested = requested or signum

    handlers = {
        signum: signal.signal(signum, interrupted)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    children = []
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(
            prefix="schemii-python-", dir=temporary_parent
        ) as directory:
            private = Path(directory)
            receipts = []
            try:
                for index, group in enumerate(groups):
                    if requested:
                        break
                    receipt = private / f"partition-{index}.jsonl"
                    receipts.append(receipt)
                    child_environment = {
                        **environment,
                        "CI_TELEMETRY_FILE": str(receipt),
                    }
                    # The ancestor is private; preserve fixture permission semantics
                    # by inheriting the caller's umask rather than changing it.
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "-p",
                            "scripts.ci.pytest_timing",
                            f"--basetemp={private / f'tmp-{index}'}",
                            "-o",
                            f"cache_dir={private / f'cache-{index}'}",
                            *(["-o", "addopts="] if inspection else []),
                            *group,
                        ],
                        cwd=root,
                        env=child_environment,
                        start_new_session=True,
                    )
                    children.append((process, birth_tick(process.pid)))
                while not requested and any(
                    os.waitid(
                        os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
                    )
                    is None
                    for process, _ in children
                ):
                    time.sleep(0.02)
            finally:
                # A second signal cannot interrupt cleanup or release temporary
                # paths while children still own them.
                for signum in handlers:
                    signal.signal(signum, signal.SIG_IGN)
                statuses = stop_children(children, requested)
            try:
                if len(receipts) != len(groups):
                    raise ValueError("Missing partition")
                merged = merge(
                    [load(path) for path in receipts],
                    statuses,
                    (time.perf_counter() - started) * 1000,
                    expected_metadata(environment),
                    bool(requested),
                )
                result = summarize(merged)
            except (ValueError, OSError):
                print("Python test evidence is incomplete or invalid.", file=sys.stderr)
                return 128 + requested if requested else error_status(statuses)
            # Retain the validated original outcomes before pass-only admission;
            # a failed or skipped selected case must remain available as evidence.
            if destination:
                output = Path(destination)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    "".join(
                        json.dumps(record, separators=(",", ":")) + "\n"
                        for record in merged
                    )
                )
            print(
                f"Python tests: {result['collected']} collected, {result['unique_observed']} observed; "
                f"complete={result['complete']}, wall={result['wall_ms'] / 1000:.3f}s"
            )
            if requested:
                return 128 + requested
            if not result["complete"]:
                return error_status(statuses)
            if inspection:
                try:
                    accepted = valid_scope(
                        INSPECTION_PROFILE,
                        ("python", "none", 0),
                        {
                            "profile": INSPECTION_PROFILE,
                            "planned": sorted(
                                record["test_id"]
                                for record in merged
                                if record["kind"] == "plan"
                            ),
                            "observed": [
                                {
                                    key: record[key]
                                    for key in (
                                        "test_id",
                                        "source_id",
                                        "outcome",
                                        "attempt",
                                    )
                                }
                                for record in merged
                                if record["kind"] == "attempt"
                            ],
                        },
                    )
                except (ValueError, OSError):
                    accepted = False
                if not accepted:
                    print(
                        "Python inspection acceptance inventory or outcomes are invalid.",
                        file=sys.stderr,
                    )
                    return 1
            return 1 if result["outcome"] == "failed" else 0
    except OSError:
        print(
            "Python test execution could not start or publish evidence.",
            file=sys.stderr,
        )
        return 3
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
