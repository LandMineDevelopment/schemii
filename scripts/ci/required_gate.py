"""Resolve intentional report skips while requiring every source matrix leg."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

if __package__:
    from .classify_changes import load_classification
    from .workflow_timing import (
        JOB_NAMES,
        api,
        current_identity,
        current_jobs,
        matches_identity,
    )
else:
    from classify_changes import load_classification
    from workflow_timing import (
        JOB_NAMES,
        api,
        current_identity,
        current_jobs,
        matches_identity,
    )


SOURCE_NEEDS = {"static-quality", "test", "postgres-integration", "browser-smoke"}
CONTROL_NEEDS = {"classify", "report-validation", "timing-rollup"}


def evaluate(
    classification: dict, needs: dict, jobs: list[dict], timing: dict, identity: dict
) -> tuple[bool, str]:
    if not classification.get("valid") or classification.get("lane") not in {
        "source",
        "reports",
    }:
        return False, "invalid-classification"
    if set(needs) != SOURCE_NEEDS | CONTROL_NEEDS:
        return False, "missing-required-job"
    if any(needs[name].get("result") != "success" for name in CONTROL_NEEDS):
        return False, "classification-docs-or-timing-failed"
    if (
        not matches_identity(timing, identity)
        or classification.get("head") != identity["head_sha"]
    ):
        return False, "stale-or-mismatched-workflow-evidence"
    if (
        timing.get("complete") is not True
        or timing.get("lane") != classification["lane"]
    ):
        return False, "incomplete-or-mismatched-timing"
    if classification["lane"] == "reports":
        if classification.get("reason") != "verified-report-only":
            return False, "report-classification-not-positive"
        if any(needs[name].get("result") != "skipped" for name in SOURCE_NEEDS):
            return False, "unexpected-source-outcome"
        return True, "verified-report-validation"
    if any(needs[name].get("result") != "success" for name in SOURCE_NEEDS):
        return False, "source-job-failed-cancelled-or-skipped"
    observed = [job for job in jobs if job.get("name") in JOB_NAMES]
    if len(observed) != len(JOB_NAMES) or {job["name"] for job in observed} != set(
        JOB_NAMES
    ):
        return False, "missing-or-duplicate-source-matrix-leg"
    if any(
        job.get("status") != "completed" or job.get("conclusion") != "success"
        for job in observed
    ):
        return False, "source-matrix-leg-not-successful"
    if not current_jobs(observed, identity):
        return False, "stale-or-mismatched-source-matrix-leg"
    return True, "all-source-layers-passed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    args = parser.parse_args()
    try:
        identity = current_identity(os.environ)
        classification = load_classification(args.classification)
        timing = json.loads(args.timing.read_text())
        needs = json.loads(os.environ["CI_NEEDS"])
        jobs = []
        if classification["lane"] == "source":
            repository = os.environ["GITHUB_REPOSITORY"]
            run_id, attempt = (
                os.environ["GITHUB_RUN_ID"],
                os.environ["GITHUB_RUN_ATTEMPT"],
            )
            # The fixed matrix is far below one page; truncated API evidence fails.
            if (
                not run_id.isdigit()
                or not attempt.isdigit()
                or repository.count("/") != 1
            ):
                raise ValueError("Invalid workflow identity")
            response = api(
                f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
            )
            if response.get("total_count", 101) > 100:
                raise ValueError("Truncated job evidence")
            jobs = response["jobs"]
        passed, reason = evaluate(classification, needs, jobs, timing, identity)
    except Exception:
        # Do not print API exception bodies, request headers or provider content.
        passed, reason = False, "required-evidence-unavailable"
    print("CI validation: " + ("passed" if passed else "failed") + "; reason=" + reason)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
