"""Resolve intentional report skips while requiring every source matrix leg."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

if __package__:
    from .classify_changes import load_classification
    from .test_selection import (
        SOURCE_NEEDS,
        PROFILES,
        expected_jobs,
        expected_lanes,
        required_needs,
        source_job_names,
        valid_scope,
    )
    from .workflow_timing import (
        SKIPPED_BROWSER,
        api,
        current_identity,
        current_jobs,
        matches_identity,
        valid_browser_jobs,
    )
else:
    from classify_changes import load_classification
    from test_selection import (
        SOURCE_NEEDS,
        PROFILES,
        expected_jobs,
        expected_lanes,
        required_needs,
        source_job_names,
        valid_scope,
    )
    from workflow_timing import (
        SKIPPED_BROWSER,
        api,
        current_identity,
        current_jobs,
        matches_identity,
        valid_browser_jobs,
    )


CONTROL_NEEDS = {"classify", "report-validation", "timing-rollup"}


def evaluate(
    classification: dict,
    needs: dict,
    jobs: list[dict],
    timing: dict,
    identity: dict,
    *,
    reuse=None,
) -> tuple[bool, str]:
    if (
        not all(
            isinstance(value, dict)
            for value in (classification, needs, timing, identity)
        )
        or not isinstance(jobs, list)
        or any(not isinstance(job, dict) for job in jobs)
    ):
        return False, "invalid-required-evidence"
    profile = classification.get("profile")
    if (
        not isinstance(profile, str)
        or profile not in PROFILES
        or (classification.get("lane") == "reports") != (profile == "reports")
    ):
        return False, "invalid-classification"
    if (
        profile not in {"full", "reports"}
        and classification.get("reason") != "verified-owned-pr-change"
    ):
        return False, "invalid-classification"
    if not classification.get("valid") or classification.get("lane") not in {
        "source",
        "reports",
    }:
        return False, "invalid-classification"
    if set(needs) != SOURCE_NEEDS | CONTROL_NEEDS or any(
        not isinstance(value, dict) for value in needs.values()
    ):
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
        or timing.get("profile") != profile
    ):
        return False, "incomplete-or-mismatched-timing"
    if reuse is not None:
        return evaluate_reused(classification, needs, jobs, timing, identity, reuse)
    if "acceptance_mode" in timing or "reuse" in timing:
        return False, "unverified-reuse-proof"
    evidence = timing.get("test_evidence", {})
    if not isinstance(evidence, dict):
        return False, "incomplete-or-failed-selected-test-evidence"
    receipts = evidence.get("lanes", [])
    if not isinstance(receipts, list) or any(
        not isinstance(value, dict)
        or not isinstance(value.get("lane"), str)
        or not isinstance(value.get("project"), str)
        or type(value.get("shard")) is not int
        for value in receipts
    ):
        return False, "incomplete-or-failed-selected-test-evidence"
    expected_receipts = expected_lanes(profile)
    keys = [
        (value.get("lane"), value.get("project"), value.get("shard"))
        for value in receipts
    ]
    if (
        evidence.get("complete") is not True
        or evidence.get("profile") != profile
        or len(keys) != len(expected_receipts)
        or set(keys) != expected_receipts
        or any(
            value.get("complete") is not True
            or value.get("outcome") != "passed"
            or type(value.get("first_attempt_failures")) is not int
            or value["first_attempt_failures"] != 0
            or type(value.get("retry_recovered")) is not int
            or value["retry_recovered"] != 0
            or not matches_identity(
                value,
                {key: identity[key] for key in ("source_sha", "run_id", "run_attempt")},
            )
            for value in receipts
        )
    ):
        return False, "incomplete-or-failed-selected-test-evidence"
    try:
        if any(
            not valid_scope(profile, key, value.get("scope"))
            for key, value in zip(keys, receipts, strict=True)
        ):
            return False, "incomplete-selected-coverage"
    except (ValueError, TypeError, KeyError, OSError):
        return False, "invalid-selected-coverage-policy"
    selected_needs = required_needs(profile)
    if any(needs[name].get("result") != "success" for name in selected_needs):
        return False, "source-job-failed-cancelled-or-skipped"
    if any(
        needs[name].get("result") != "skipped" for name in SOURCE_NEEDS - selected_needs
    ):
        return False, "unexpected-source-outcome"
    source_names = source_job_names(profile)
    selected_jobs = expected_jobs(profile)
    browser_excluded = not any(
        value.startswith("browser-") for value in selected_jobs.values()
    )
    if (profile != "reports" or jobs) and not valid_browser_jobs(
        jobs, profile, excluded=browser_excluded
    ):
        return False, "unexpected-source-matrix-leg"
    observed = [
        job
        for job in jobs
        if job.get("name") in source_names
        or browser_excluded
        and job.get("name") == SKIPPED_BROWSER
    ]
    executed = [job for job in observed if job["name"] in selected_jobs]
    if len(executed) != len(selected_jobs) or {job["name"] for job in executed} != set(
        selected_jobs
    ):
        return False, "missing-or-duplicate-source-matrix-leg"
    if any(
        job.get("status") != "completed" or job.get("conclusion") != "success"
        for job in executed
    ):
        return False, "source-matrix-leg-not-successful"
    excluded = [job for job in observed if job["name"] not in selected_jobs]
    if (
        len({job["name"] for job in excluded}) != len(excluded)
        or any(
            job.get("conclusion") != "skipped" or job.get("status") != "completed"
            for job in excluded
        )
        or any(
            job.get("name", "").startswith("Assembled browser smoke (")
            and job["name"] not in source_names
            and not (browser_excluded and job["name"] == SKIPPED_BROWSER)
            for job in jobs
        )
    ):
        return False, "unexpected-source-matrix-leg"
    if not current_jobs(observed, identity):
        return False, "stale-or-mismatched-source-matrix-leg"
    if profile == "reports":
        if classification.get("reason") != "verified-report-only":
            return False, "report-classification-not-positive"
        return True, "verified-report-validation"
    return True, "all-selected-source-layers-passed"


def evaluate_reused(classification, needs, jobs, timing, identity, reuse):
    if __package__:
        from .reuse_acceptance import (
            CACHE_MODE,
            INSPECTION_MODE,
            NATIVE_MODE,
            EXPENSIVE_NEEDS,
            current_reuse_jobs,
            valid_receipt_mode,
        )
    else:
        from reuse_acceptance import (
            CACHE_MODE,
            INSPECTION_MODE,
            NATIVE_MODE,
            EXPENSIVE_NEEDS,
            current_reuse_jobs,
            valid_receipt_mode,
        )
    if (
        classification.get("profile") != "full"
        or classification.get("lane") != "source"
        or not isinstance(reuse, dict)
        or not valid_receipt_mode(reuse)
        or timing.get("acceptance_mode") != reuse.get("mode")
        or timing.get("reuse") != reuse
        or "test_evidence" in timing
        or not matches_identity(reuse.get("target", {}), identity)
        or needs["classify"].get("outputs", {}).get("acceptance") != reuse.get("mode")
    ):
        return False, "invalid-reuse-proof"
    if (
        needs["static-quality"].get("result") != "success"
        or any(needs[name].get("result") != "skipped" for name in EXPENSIVE_NEEDS)
        or not current_reuse_jobs(jobs, identity)
    ):
        return False, "unexpected-reused-source-outcome"
    return True, (
        "verified-identical-tree-schemer-result-cache-pr-acceptance"
        if reuse["mode"] == CACHE_MODE
        else "verified-identical-tree-developer-inspection-pr-acceptance"
        if reuse["mode"] == INSPECTION_MODE
        else "verified-identical-tree-native-pr-acceptance"
        if reuse["mode"] == NATIVE_MODE
        else "verified-identical-tree-full-pr-acceptance"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--reuse", type=Path)
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
        reuse = None
        if args.reuse:
            if __package__:
                from .reuse_acceptance import GitHub, context, recheck
            else:
                from reuse_acceptance import GitHub, context, recheck
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            target = context(os.environ, event, classification, Path.cwd())
            reuse = recheck(
                json.loads(args.reuse.read_text()),
                GitHub(target["repository"], os.environ["GITHUB_TOKEN"]),
                target,
            )
        passed, reason = evaluate(
            classification, needs, jobs, timing, identity, reuse=reuse
        )
    except Exception:
        # Do not print API exception bodies, request headers or provider content.
        passed, reason = False, "required-evidence-unavailable"
    print("CI validation: " + ("passed" if passed else "failed") + "; reason=" + reason)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
