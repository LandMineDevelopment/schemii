"""Read Actions lifecycle timestamps; publish fixed labels and numeric durations."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

if __package__:
    from .classify_changes import load_classification
    from .summary import load, summarize as lane_summary
else:
    from classify_changes import load_classification
    from summary import load, summarize as lane_summary


JOB_NAMES = {
    "Incremental Python static quality": "static",
    "Node and deterministic Python behavior": "unit",
    "Real PostgreSQL metadata behavior": "postgres",
    **{
        f"Assembled browser smoke ({project}, shard {shard}/2)": f"browser-{project}-{shard}"
        for project in ("desktop-chromium", "android-chromium")
        for shard in (1, 2)
    },
}
REPORT_JOB_NAMES = {
    "Classify complete change": "classification",
    "Report Markdown and local links": "reports",
}
TEST_STEPS = {
    "Fast frontend and harness feedback",
    "Deterministic Python behavior",
    "Check Python quality against the change base",
    "Exercise metadata migrations, credentials, isolation, and atomic imports",
    "Exercise browser flows",
    "Verify metadata backup recovery in an isolated database",
    "Validate changed Markdown and local links",
}


def current_identity(environ):
    values = {
        "source_sha": environ.get("CI_TELEMETRY_SHA", environ.get("GITHUB_SHA", "")),
        "head_sha": environ.get("CI_TELEMETRY_HEAD_SHA", environ.get("GITHUB_SHA", "")),
        "run_id": environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": environ.get("GITHUB_RUN_ATTEMPT", ""),
    }
    if any(
        not re.fullmatch(r"[0-9a-f]{40}", values[key])
        for key in ("source_sha", "head_sha")
    ) or any(
        not re.fullmatch(r"[1-9]\d*", values[key]) for key in ("run_id", "run_attempt")
    ):
        raise ValueError("Invalid workflow identity")
    return {
        **values,
        "run_id": int(values["run_id"]),
        "run_attempt": int(values["run_attempt"]),
    }


def matches_identity(value, identity):
    return all(
        type(value.get(key)) is type(expected) and value[key] == expected
        for key, expected in identity.items()
    )


def current_jobs(jobs, identity):
    expected = {key: identity[key] for key in ("run_id", "run_attempt", "head_sha")}
    return all(matches_identity(job, expected) for job in jobs)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def duration(start, end):
    return (
        round(max(0, end - start) * 1000, 3)
        if start is not None and end is not None
        else None
    )


def summarize(run, jobs, *, lane="source", report_validation=False):
    created = timestamp(run.get("created_at"))
    dispatched = timestamp(run.get("run_started_at"))
    records = []
    labels = {**JOB_NAMES, **REPORT_JOB_NAMES}
    expected = set(JOB_NAMES.values()) if lane == "source" else set()
    if report_validation or lane == "reports":
        expected.update(REPORT_JOB_NAMES.values())
    not_applicable = []
    for job in jobs:
        label = labels.get(job.get("name"))
        if label is None:
            continue
        if lane == "reports" and label in JOB_NAMES.values():
            not_applicable.append({"job": label, "outcome": job.get("conclusion")})
            continue
        start = timestamp(job.get("started_at"))
        end = timestamp(job.get("completed_at"))
        wall = duration(start, end)
        execution = startup = 0
        early = None
        for step in job.get("steps", []):
            step_ms = (
                duration(
                    timestamp(step.get("started_at")),
                    timestamp(step.get("completed_at")),
                )
                or 0
            )
            if step.get("name") in TEST_STEPS:
                execution += step_ms
            elif step.get("name") == "Start the canonical application stack":
                startup += step_ms
            if step.get("name") == "Fast frontend and harness feedback":
                early = duration(start, timestamp(step.get("completed_at")))
        conclusion = job.get("conclusion")
        records.append(
            {
                "job": label,
                "outcome": conclusion
                if conclusion
                in {
                    "success",
                    "failure",
                    "cancelled",
                    "skipped",
                    "timed_out",
                    "neutral",
                    "action_required",
                }
                else "incomplete",
                "dispatch_to_start_ms": duration(created, start),
                "after_workflow_start_ms": duration(dispatched, start),
                "wall_ms": wall,
                "startup_ms": startup,
                "test_steps_ms": execution,
                "setup_and_other_ms": max(0, wall - execution - startup)
                if wall is not None
                else None,
                "first_node_feedback_ms": early,
            }
        )
    observed = {record["job"] for record in records}
    end_times = [
        timestamp(job.get("completed_at"))
        for job in jobs
        if labels.get(job.get("name")) in expected
    ]
    end_times = [value for value in end_times if value is not None]
    return {
        "schema": 1,
        "lane": lane,
        "workflow_dispatch_delay_ms": duration(created, dispatched),
        "complete": len(records) == len(expected)
        and observed == expected
        and all(
            record["wall_ms"] is not None
            and record["outcome"] not in {"incomplete", "cancelled", "skipped"}
            for record in records
        )
        and all(record["outcome"] == "skipped" for record in not_applicable),
        "missing_jobs": sorted(expected - observed),
        "jobs": sorted(records, key=lambda record: record["job"]),
        "not_applicable_jobs": sorted(JOB_NAMES.values()) if lane == "reports" else [],
        "critical_path_ms": duration(created, max(end_times)) if end_times else None,
        "total_job_minutes": round(
            sum(record["wall_ms"] or 0 for record in records) / 60000, 3
        ),
    }


def api(path):
    request = Request(
        "https://api.github.com/" + path,
        headers={
            "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def test_evidence(directory, *, lane="source", identity=None):
    expected = {
        ("node", "none", 0),
        ("python", "none", 0),
        ("postgres", "none", 0),
        *(
            ("browser", project, shard)
            for project in ("desktop-chromium", "android-chromium")
            for shard in (1, 2)
        ),
    }
    if lane == "reports":
        expected = set()
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
    cohorts = {
        (value["source_sha"], value["run_id"], value["run_attempt"]) for value in values
    }
    eligible = sum(value["first_attempt_eligible"] for value in values)
    passes = sum(value["first_attempt_passes"] for value in values)
    return {
        "applicable": lane == "source",
        "complete": set(observed) == expected
        and not invalid
        and (len(cohorts) == 1 if lane == "source" else not cohorts)
        and (
            identity is None
            or lane == "reports"
            or cohorts
            == {(identity["source_sha"], identity["run_id"], identity["run_attempt"])}
        )
        and all(value["complete"] for value in values),
        "missing_lanes": [
            f"{lane}-{project}-{shard}"
            for lane, project, shard in sorted(expected - set(observed))
        ],
        "invalid_lanes": invalid,
        "lanes": values,
        "first_attempt_eligible": eligible,
        "first_attempt_passes": passes,
        "first_attempt_failures": sum(
            value["first_attempt_failures"] for value in values
        ),
        "retry_recovered": sum(value["retry_recovered"] for value in values),
        "first_attempt_pass_rate": passes / eligible if eligible else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--classification", type=Path)
    args = parser.parse_args()
    identity = current_identity(os.environ)
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not run_id.isdigit()
        or not attempt.isdigit()
    ):
        raise ValueError("Invalid workflow identity")
    classification = {"lane": "source", "valid": False}
    try:
        classification = (
            load_classification(args.classification)
            if args.classification
            else {"lane": "source", "valid": True}
        )
    except (OSError, ValueError, TypeError):
        pass  # Still collect source observations; never turn an unknown into reports.
    classification_valid = classification["valid"]
    if not classification_valid:
        classification = {"lane": "source", "valid": False}
    try:
        run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
        jobs = api(
            f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        result = summarize(
            run,
            jobs["jobs"],
            lane=classification["lane"],
            report_validation=bool(args.classification),
        )
        # Pagination cannot be mistaken for a complete measured workflow.
        if (
            jobs.get("total_count", 101) > 100
            or run.get("id") != identity["run_id"]
            or run.get("run_attempt") != identity["run_attempt"]
            or not current_jobs(
                [
                    job
                    for job in jobs["jobs"]
                    if job.get("name") in JOB_NAMES
                    or job.get("name") in REPORT_JOB_NAMES
                ],
                identity,
            )
            or not classification_valid
            or args.classification
            and classification_valid
            and classification["head"] != identity["head_sha"]
        ):
            result["complete"] = False
    except Exception:
        # Never print provider responses/headers or exception objects.
        result = {
            "schema": 1,
            "complete": False,
            "collection_error": "actions-api-unavailable",
        }
    result.update(identity)
    result["classification_valid"] = classification_valid
    if not classification_valid:
        result["collection_error"] = "invalid-classification"
    evidence = test_evidence(
        args.inputs, lane=result.get("lane", "source"), identity=identity
    )
    result["test_evidence"] = evidence
    result["complete"] = result["complete"] and evidence["complete"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Workflow timing complete=" + str(result["complete"]))


if __name__ == "__main__":
    main()
