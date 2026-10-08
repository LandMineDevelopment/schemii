"""Read Actions lifecycle timestamps; publish fixed labels and numeric durations."""

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

if __package__:
    from .classify_changes import load_classification
    from .startup_timing import load_startup, validate_startup
    from .summary import load, summarize as lane_summary
    from .test_selection import (
        source_job_names,
        expected_jobs,
        expected_lanes,
        expected_scope,
        valid_scope,
    )
else:
    from classify_changes import load_classification
    from startup_timing import load_startup, validate_startup
    from summary import load, summarize as lane_summary
    from test_selection import (
        source_job_names,
        expected_jobs,
        expected_lanes,
        expected_scope,
        valid_scope,
    )


REPORT_JOB_NAMES = {
    "Classify complete change": "classification",
    "Report Markdown and local links": "reports",
}
# GitHub retains the literal matrix expression when job-level selection skips
# expansion. Only this exact current placeholder may represent an excluded lane.
SKIPPED_BROWSER = "Assembled browser smoke (${{ matrix.project }}, shard ${{ matrix.shard }}/${{ matrix.total }})"
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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def duration(start, end):
    if (
        start is None
        or end is None
        or not math.isfinite(start)
        or not math.isfinite(end)
        or end < start
    ):
        return None
    return round((end - start) * 1000, 3)


def sum_durations(values):
    return None if any(value is None for value in values) else sum(values)


STARTUP_FIELDS = {
    "startup_build_ms": "build",
    "startup_replacement_ms": "replacement",
    "startup_up_readiness_ms": "readiness",
}


def startup_durations(startup):
    phases = {
        value["phase"]: value["duration_ms"] for value in startup.get("phases", [])
    }
    return {field: phases.get(phase) for field, phase in STARTUP_FIELDS.items()}


def browser_summary(records, startup):
    """The sole approved extension of the existing browser summary archive member."""
    result = lane_summary(records)
    startup = validate_startup(startup)
    if (
        result["lane"] != "browser"
        or startup["source_kind"] != "hosted"
        or startup["outcome"] != "passed"
        or not matches_identity(
            startup,
            {
                key: result[key]
                for key in ("source_sha", "run_id", "run_attempt", "project", "shard")
            },
        )
    ):
        raise ValueError("Invalid browser startup identity")
    result["startup"] = startup
    return result


def startup_jobs(evidence, profile):
    """Bind measured phases to the selected topology, never an ambient shard count."""
    names = source_job_names(profile)
    result = {}
    for value in evidence["lanes"]:
        if "startup" not in value:
            continue
        prefix = f"Assembled browser smoke ({value['project']}, shard {value['shard']}/"
        selected = [label for name, label in names.items() if name.startswith(prefix)]
        if value["lane"] != "browser" or len(selected) != 1 or selected[0] in result:
            raise ValueError("Invalid startup topology")
        result[selected[0]] = validate_startup(value["startup"])
    return result


def valid_browser_jobs(jobs, profile, *, excluded=False):
    """Require one exact topology; a literal may replace all excluded legs only."""
    expected = {
        name
        for name in source_job_names(profile)
        if name.startswith("Assembled browser smoke (")
    }
    observed = [
        job
        for job in jobs
        if isinstance(job.get("name"), str)
        and job["name"].startswith("Assembled browser smoke (")
    ]
    if excluded and len(observed) == 1 and observed[0]["name"] == SKIPPED_BROWSER:
        return (
            observed[0].get("status") == "completed"
            and observed[0].get("conclusion") == "skipped"
        )
    names = [job["name"] for job in observed]
    return (
        len(names) == len(expected)
        and set(names) == expected
        and (
            not excluded
            or all(
                job.get("status") == "completed" and job.get("conclusion") == "skipped"
                for job in observed
            )
        )
    )


def summarize(
    run,
    jobs,
    *,
    lane="source",
    profile=None,
    report_validation=False,
    reused=False,
    startup=None,
):
    profile = profile or ("reports" if lane == "reports" else "full")
    if (lane == "reports") != (profile == "reports"):
        raise ValueError("Mismatched profile")
    created = timestamp(run.get("created_at"))
    dispatched = timestamp(run.get("run_started_at"))
    records = []
    source_names = source_job_names(profile)
    labels = {**source_names, **REPORT_JOB_NAMES}
    expected = {"static"} if reused else set(expected_jobs(profile).values())
    browser_excluded = reused or not any(
        value.startswith("browser-") for value in expected
    )
    if browser_excluded:
        labels[SKIPPED_BROWSER] = "browser-not-selected"
    if report_validation or lane == "reports":
        expected.update(REPORT_JOB_NAMES.values())
    not_applicable = []
    unexpected_source_jobs = sum(
        isinstance(job.get("name"), str)
        and job["name"].startswith("Assembled browser smoke (")
        and job["name"] not in source_names
        and not (browser_excluded and job["name"] == SKIPPED_BROWSER)
        for job in jobs
    )
    for job in jobs:
        label = labels.get(job.get("name"))
        if label is None:
            continue
        if (
            label in source_names.values() or label == "browser-not-selected"
        ) and label not in expected:
            not_applicable.append({"job": label, "outcome": job.get("conclusion")})
            continue
        start = timestamp(job.get("started_at"))
        end = timestamp(job.get("completed_at"))
        wall = duration(start, end)
        execution_steps = []
        startup_steps = []
        early = None
        for step in job.get("steps", []):
            step_ms = (
                0
                if step.get("conclusion") == "skipped"
                else duration(
                    timestamp(step.get("started_at")),
                    timestamp(step.get("completed_at")),
                )
            )
            if step.get("name") in TEST_STEPS:
                execution_steps.append(step_ms)
            elif step.get("name") == "Start the canonical application stack":
                startup_steps.append(step_ms)
            if (
                step.get("name") == "Fast frontend and harness feedback"
                and step.get("conclusion") != "skipped"
            ):
                early = duration(start, timestamp(step.get("completed_at")))
        execution = sum_durations(execution_steps)
        startup_ms = sum_durations(startup_steps)
        setup = None
        if wall is not None and execution is not None and startup_ms is not None:
            residual = wall - execution - startup_ms
            setup = residual if residual >= 0 else None
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
                "startup_ms": startup_ms,
                "test_steps_ms": execution,
                "setup_and_other_ms": setup,
                "first_node_feedback_ms": early,
            }
        )
        if startup is not None and label.startswith("browser-"):
            records[-1].update(startup_durations(startup.get(label, {})))
    observed = {record["job"] for record in records}
    end_times = [
        timestamp(job.get("completed_at"))
        for job in jobs
        if labels.get(job.get("name")) in expected
    ]
    end_times = [value for value in end_times if value is not None]
    total_job_ms = sum_durations([record["wall_ms"] for record in records])
    return {
        "schema": 1,
        "lane": lane,
        "profile": profile,
        "workflow_dispatch_delay_ms": duration(created, dispatched),
        "unexpected_source_jobs": unexpected_source_jobs,
        "complete": not unexpected_source_jobs
        and (
            profile == "reports"
            and not any(
                isinstance(job.get("name"), str)
                and job["name"].startswith("Assembled browser smoke (")
                for job in jobs
            )
            or valid_browser_jobs(jobs, profile, excluded=browser_excluded)
        )
        and len({record["job"] for record in not_applicable}) == len(not_applicable)
        and len(records) == len(expected)
        and observed == expected
        and all(
            record["wall_ms"] is not None
            and record["outcome"] not in {"incomplete", "cancelled", "skipped"}
            for record in records
        )
        and all(record["outcome"] == "skipped" for record in not_applicable),
        "missing_jobs": sorted(expected - observed),
        "jobs": sorted(records, key=lambda record: record["job"]),
        "not_applicable_jobs": sorted(set(source_names.values()) - expected),
        "critical_path_ms": duration(created, max(end_times)) if end_times else None,
        "total_job_minutes": round(total_job_ms / 60000, 3)
        if total_job_ms is not None
        else None,
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


def test_evidence(directory, *, lane="source", profile=None, identity=None):
    profile = profile or ("reports" if lane == "reports" else "full")
    if (lane == "reports") != (profile == "reports"):
        raise ValueError("Mismatched profile")
    expected = expected_lanes(profile)
    observed = {}
    invalid = 0
    for path in directory.rglob("*.jsonl"):
        try:
            records = load(path)
            result = lane_summary(records)
            summary_path = path.with_name(path.stem + "-summary.json")
            if result["lane"] == "browser" and summary_path.is_file():
                if summary_path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("Timing evidence exceeds size budget")
                saved = json.loads(summary_path.read_text())
                if "startup" in saved:
                    result = browser_summary(records, saved["startup"])
                    if json.dumps(saved, sort_keys=True, allow_nan=False) != json.dumps(
                        result, sort_keys=True, allow_nan=False
                    ):
                        raise ValueError("Invalid browser summary extension")
            key = result["lane"], result["project"], result["shard"]
            if key not in expected or key in observed:
                raise ValueError("Invalid lane evidence")
            # Valid known-lane observations retain their original reliability
            # metrics even when stricter selected acceptance rejects the scope.
            observed[key] = result
            if expected_scope(profile, key) is not None:
                result["scope"] = {
                    "profile": profile,
                    "planned": sorted(
                        record["test_id"]
                        for record in records
                        if record["kind"] == "plan"
                    ),
                    "observed": [
                        {
                            field: record[field]
                            for field in ("test_id", "source_id", "outcome", "attempt")
                        }
                        for record in records
                        if record["kind"] == "attempt"
                    ],
                }
                if not valid_scope(profile, key, result["scope"]):
                    raise ValueError("Incomplete selected coverage")
        except (ValueError, OSError, KeyError, TypeError):
            invalid += 1
    values = list(observed.values())
    cohorts = {
        (value["source_sha"], value["run_id"], value["run_attempt"]) for value in values
    }
    eligible = sum(value["first_attempt_eligible"] for value in values)
    passes = sum(value["first_attempt_passes"] for value in values)
    return {
        "applicable": bool(expected),
        "profile": profile,
        "complete": set(observed) == expected
        and not invalid
        and (len(cohorts) == 1 if lane == "source" else not cohorts)
        and (
            identity is None
            or lane == "reports"
            or cohorts
            == {(identity["source_sha"], identity["run_id"], identity["run_attempt"])}
        )
        and all(
            value["complete"]
            and value["outcome"] == "passed"
            and value["first_attempt_failures"] == 0
            and value["retry_recovered"] == 0
            for value in values
        ),
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
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--startup", type=Path)
    parser.add_argument("--failed-startup", action="store_true")
    parser.add_argument("--classification", type=Path)
    parser.add_argument("--reuse", type=Path)
    args = parser.parse_args()
    if args.startup:
        args.output.unlink(missing_ok=True)
        try:
            identity = current_identity(os.environ)
            startup = load_startup(args.startup)
            expected = {
                key: identity[key] for key in ("source_sha", "run_id", "run_attempt")
            }
            if startup["source_kind"] != "hosted" or not matches_identity(
                startup, expected
            ):
                raise ValueError("Invalid startup cohort")
            if args.failed_startup:
                if (
                    args.inputs is not None
                    or startup["outcome"] == "passed"
                    or startup["project"] != os.environ.get("CI_TELEMETRY_PROJECT")
                    or str(startup["shard"]) != os.environ.get("CI_TELEMETRY_SHARD")
                ):
                    raise ValueError("Invalid failed startup identity")
                result = startup
            elif args.inputs is not None:
                result = browser_summary(load(args.inputs), startup)
            else:
                raise ValueError("Missing browser timing")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise SystemExit("Startup timing validation failed") from None
        print("Startup timing validated")
        return
    if args.inputs is None or args.failed_startup:
        parser.error("workflow rollup requires --inputs")
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
    classification = {"lane": "source", "profile": "full", "valid": False}
    try:
        classification = (
            load_classification(args.classification)
            if args.classification
            else {"lane": "source", "profile": "full", "valid": True}
        )
    except (OSError, ValueError, TypeError):
        pass  # Still collect source observations; never turn an unknown into reports.
    classification_valid = classification["valid"]
    if not classification_valid:
        classification = {"lane": "source", "profile": "full", "valid": False}
    reuse = None
    if args.reuse:
        reuse = json.loads(args.reuse.read_text())
        if __package__:
            from .reuse_acceptance import current_reuse_jobs, valid_receipt_mode
        else:
            from reuse_acceptance import current_reuse_jobs, valid_receipt_mode
        if not valid_receipt_mode(reuse) or any(
            reuse.get("target", {}).get(key) != value for key, value in identity.items()
        ):
            raise ValueError("Invalid reuse identity")
    try:
        run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
        jobs = api(
            f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        result = summarize(
            run,
            jobs["jobs"],
            lane=classification["lane"],
            profile=classification["profile"],
            report_validation=bool(args.classification),
            reused=bool(reuse),
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
                    if job.get("name") in source_job_names(classification["profile"])
                    or job.get("name") in REPORT_JOB_NAMES
                ],
                identity,
            )
            or not classification_valid
            or reuse
            and not current_reuse_jobs(jobs["jobs"], identity)
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
    result["lane"] = classification["lane"]
    result["profile"] = classification["profile"]
    result["classification_valid"] = classification_valid
    if not classification_valid:
        result["collection_error"] = "invalid-classification"
    if reuse:
        # Donor tests/cost retain their original cohort; current execution totals
        # include only the fresh classification, reports and static controls.
        result["acceptance_mode"] = reuse["mode"]
        result["reuse"] = reuse
    else:
        evidence = test_evidence(
            args.inputs,
            lane=classification["lane"],
            profile=classification["profile"],
            identity=identity,
        )
        result["test_evidence"] = evidence
        # A fresh receipt format publishes unknown measurements explicitly. Donor
        # validation can still recompute a retained older format without relabeling it.
        measured_startup = startup_jobs(evidence, classification["profile"])
        for job in result.get("jobs", []):
            if job["job"].startswith("browser-"):
                job.update(startup_durations(measured_startup.get(job["job"], {})))
        result["complete"] = result["complete"] and evidence["complete"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Workflow timing complete=" + str(result["complete"]))


if __name__ == "__main__":
    main()
