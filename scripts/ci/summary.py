"""Validate the entire public artifact schema, then publish only numeric summaries.

Raw Playwright reports/traces/screenshots/logs are deliberately not inputs.
"""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re


META = {"schema", "source_sha", "run_id", "run_attempt", "lane", "project", "shard"}
FIELDS = {
    "start": {"kind", "planned"},
    "plan": {"kind", "test_id"},
    "attempt": {
        "kind",
        "test_id",
        "source_id",
        "source_line",
        "attempt",
        "outcome",
        "skip",
        "setup_ms",
        "execution_ms",
        "teardown_ms",
    },
    "end": {"kind", "outcome", "wall_ms"},
}
OUTCOMES = {"passed", "failed", "timed-out", "skipped", "cancelled", "not-run"}
TERMINAL_OUTCOMES = {
    "passed",
    "failed",
    "timed-out",
    "cancelled",
    "collection-error",
    "error",
}


def number(value, integer=False):
    return (
        type(value) in ({int} if integer else {int, float})
        and 0 <= value <= 10**15
        and math.isfinite(value)
    )


def valid(record):
    kind = record.get("kind")
    if (
        not isinstance(kind, str)
        or kind not in FIELDS
        or set(record) != META | FIELDS[kind]
    ):
        return False
    if record["schema"] != 1 or type(record["schema"]) is not int:
        return False
    if record["source_sha"] is not None and (
        not isinstance(record["source_sha"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", record["source_sha"])
    ):
        return False
    if not all(number(record[key], True) for key in ("run_id", "run_attempt", "shard")):
        return False
    if not isinstance(record["lane"], str) or record["lane"] not in {
        "node",
        "python",
        "postgres",
        "browser",
    }:
        return False
    if not isinstance(record["project"], str):
        return False
    if record["lane"] == "browser":
        if record["project"] not in {"desktop-chromium", "android-chromium"} or record[
            "shard"
        ] not in {0, 1, 2, 3}:
            return False
    elif record["project"] != "none" or record["shard"] != 0:
        return False
    if kind == "start":
        return record["planned"] is None or number(record["planned"], True)
    if kind in {"plan", "attempt"} and (
        not isinstance(record["test_id"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", record["test_id"])
    ):
        return False
    if kind == "plan":
        return True
    if kind == "attempt":
        return (
            isinstance(record["source_id"], str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", record["source_id"]))
            and all(number(record[key], True) for key in ("source_line", "attempt"))
            and isinstance(record["outcome"], str)
            and record["outcome"] in OUTCOMES
            and record["skip"]
            == ("declared-or-runtime" if record["outcome"] == "skipped" else "none")
            and all(
                number(record[key])
                for key in ("setup_ms", "execution_ms", "teardown_ms")
            )
        )
    return (
        isinstance(record["outcome"], str)
        and record["outcome"] in TERMINAL_OUTCOMES
        and number(record["wall_ms"])
    )


def summarize(records):
    if not records or not all(valid(record) for record in records):
        raise ValueError("Public timing schema validation failed")
    meta = {key: records[0][key] for key in META}
    if any(
        any(record[key] != value for key, value in meta.items()) for record in records
    ):
        raise ValueError("Mixed timing lane identities")
    starts = [record for record in records if record["kind"] == "start"]
    ends = [record for record in records if record["kind"] == "end"]
    plans = [record["test_id"] for record in records if record["kind"] == "plan"]
    if len(starts) != 1 or len(ends) > 1 or len(plans) != len(set(plans)):
        raise ValueError("Duplicate timing lifecycle records")
    attempts = defaultdict(list)
    for record in records:
        if record["kind"] == "attempt":
            attempts[record["test_id"]].append(record)
    if set(attempts) - set(plans):
        raise ValueError("Unplanned timing attempt")
    for values in attempts.values():
        indices = [record["attempt"] for record in values]
        if indices != list(range(len(indices))):
            raise ValueError("Duplicate or missing timing attempt")
    counts = dict.fromkeys(sorted(OUTCOMES), 0)
    first_passes = first_failures = recovered = executed = 0
    phase_totals = dict.fromkeys(("setup_ms", "execution_ms", "teardown_ms"), 0)
    for values in attempts.values():
        for record in values:
            counts[record["outcome"]] += 1
            for phase in phase_totals:
                phase_totals[phase] += record[phase]
        first = values[0]["outcome"]
        if first not in {"skipped", "not-run", "cancelled"}:
            executed += 1
            first_passes += first == "passed"
            first_failures += first in {"failed", "timed-out"}
        recovered += (
            first in {"failed", "timed-out"} and values[-1]["outcome"] == "passed"
        )
    missing = len(set(plans) - set(attempts))
    # Individual test receipts do not establish completion of their enclosing
    # lifecycle. Cancellation/timeout/collection failure can happen after every
    # observed test passed, and zero collected tests provide no executed inventory.
    complete = (
        bool(plans)
        and bool(ends)
        and ends[0]["outcome"] in {"passed", "failed"}
        and not missing
        and not counts["cancelled"]
        and not counts["not-run"]
    )
    if starts[0]["planned"] is not None and starts[0]["planned"] != len(plans):
        complete = False
    return {
        **meta,
        "collected": len(plans),
        "unique_observed": len(attempts),
        "attempts": sum(counts.values()),
        "attempt_outcomes": counts,
        "first_attempt_eligible": executed,
        "first_attempt_passes": first_passes,
        "first_attempt_failures": first_failures,
        "retry_recovered": recovered,
        "first_attempt_pass_rate": first_passes / executed if executed else None,
        "complete": complete,
        "missing": missing,
        "outcome": ends[0]["outcome"] if ends else "not-run",
        "wall_ms": ends[0]["wall_ms"] if ends else None,
        "phase_totals_ms": {
            key: round(value, 3) for key, value in phase_totals.items()
        },
    }


def load(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Timing evidence exceeds size budget")
    try:
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid timing evidence") from error
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("Invalid timing evidence")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    # Remove only this task's previous publication if a validation fails. The
    # upload points solely at this file and the validated input, never a directory.
    args.output.unlink(missing_ok=True)
    summary = summarize(load(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"Timing: {summary['collected']} collected, {summary['unique_observed']} observed, "
        f"{summary['retry_recovered']} retry-recovered; complete={summary['complete']}"
    )


if __name__ == "__main__":
    main()
