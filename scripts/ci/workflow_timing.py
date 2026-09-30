"""Read Actions lifecycle timestamps; publish fixed labels and numeric durations."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

if __package__:
    from .summary import load, summarize as lane_summary
else:
    from summary import load, summarize as lane_summary


JOB_NAMES = {
    "Incremental Python static quality": "static",
    "Node and deterministic Python behavior": "unit",
    "Real PostgreSQL metadata behavior": "postgres",
    **{f"Assembled browser smoke ({project}, shard {shard}/2)": f"browser-{project}-{shard}"
       for project in ("desktop-chromium", "android-chromium") for shard in (1, 2)},
}
TEST_STEPS = {
    "Fast frontend and harness feedback", "Deterministic Python behavior",
    "Check Python quality against the change base",
    "Exercise metadata migrations, credentials, isolation, and atomic imports",
    "Exercise browser flows", "Verify metadata backup recovery in an isolated database",
}


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def duration(start, end):
    return round(max(0, end - start) * 1000, 3) if start is not None and end is not None else None


def summarize(run, jobs):
    created = timestamp(run.get("created_at"))
    dispatched = timestamp(run.get("run_started_at"))
    records = []
    for job in jobs:
        label = JOB_NAMES.get(job.get("name"))
        if label is None:
            continue
        start = timestamp(job.get("started_at"))
        end = timestamp(job.get("completed_at"))
        wall = duration(start, end)
        execution = startup = 0
        early = None
        for step in job.get("steps", []):
            step_ms = duration(timestamp(step.get("started_at")), timestamp(step.get("completed_at"))) or 0
            if step.get("name") in TEST_STEPS:
                execution += step_ms
            elif step.get("name") == "Start the canonical application stack":
                startup += step_ms
            if step.get("name") == "Fast frontend and harness feedback":
                early = duration(start, timestamp(step.get("completed_at")))
        conclusion = job.get("conclusion")
        records.append({"job": label, "outcome": conclusion if conclusion in {
            "success", "failure", "cancelled", "skipped", "timed_out", "neutral", "action_required"
        } else "incomplete", "dispatch_to_start_ms": duration(created, start),
            "after_workflow_start_ms": duration(dispatched, start), "wall_ms": wall,
            "startup_ms": startup, "test_steps_ms": execution,
            "setup_and_other_ms": max(0, wall - execution - startup) if wall is not None else None,
            "first_node_feedback_ms": early})
    observed = {record["job"] for record in records}
    expected = set(JOB_NAMES.values())
    end_times = [timestamp(job.get("completed_at")) for job in jobs if job.get("name") in JOB_NAMES]
    end_times = [value for value in end_times if value is not None]
    return {"schema": 1, "workflow_dispatch_delay_ms": duration(created, dispatched),
            "complete": len(records) == len(expected) and observed == expected
            and all(record["wall_ms"] is not None and record["outcome"] not in {"incomplete", "cancelled", "skipped"} for record in records),
            "missing_jobs": sorted(expected - observed), "jobs": sorted(records, key=lambda record: record["job"]),
            "critical_path_ms": duration(created, max(end_times)) if end_times else None,
            "total_job_minutes": round(sum(record["wall_ms"] or 0 for record in records) / 60000, 3)}


def api(path):
    request = Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def test_evidence(directory):
    expected = {("node", "none", 0), ("python", "none", 0), ("postgres", "none", 0),
                *(("browser", project, shard) for project in ("desktop-chromium", "android-chromium") for shard in (1, 2))}
    observed = {}
    invalid = 0
    for path in directory.rglob("*.jsonl"):
        try:
            result = lane_summary(load(path))
            key = result["lane"], result["project"], result["shard"]
            if key not in expected or key in observed:
                raise ValueError("Invalid lane evidence")
            observed[key] = result
        except (ValueError, OSError, KeyError, TypeError):
            invalid += 1
    values = list(observed.values())
    cohorts = {(value["source_sha"], value["run_id"], value["run_attempt"]) for value in values}
    eligible = sum(value["first_attempt_eligible"] for value in values)
    passes = sum(value["first_attempt_passes"] for value in values)
    return {"complete": set(observed) == expected and not invalid and len(cohorts) == 1
            and all(value["complete"] for value in values),
            "missing_lanes": [f"{lane}-{project}-{shard}" for lane, project, shard in sorted(expected - set(observed))],
            "invalid_lanes": invalid, "lanes": values,
            "first_attempt_eligible": eligible, "first_attempt_passes": passes,
            "first_attempt_failures": sum(value["first_attempt_failures"] for value in values),
            "retry_recovered": sum(value["retry_recovered"] for value in values),
            "first_attempt_pass_rate": passes / eligible if eligible else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("Invalid workflow identity")
    try:
        run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
        jobs = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
        result = summarize(run, jobs["jobs"])
        # Pagination cannot be mistaken for a complete measured workflow.
        if jobs.get("total_count", 0) > 100:
            result["complete"] = False
    except Exception:
        # Never print provider responses/headers or exception objects.
        result = {"schema": 1, "complete": False, "collection_error": "actions-api-unavailable"}
    evidence = test_evidence(args.inputs)
    result["test_evidence"] = evidence
    result["complete"] = result["complete"] and evidence["complete"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Workflow timing complete=" + str(result["complete"]))


if __name__ == "__main__":
    main()
