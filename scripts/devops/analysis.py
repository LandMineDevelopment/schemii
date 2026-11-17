"""Discover CI analysis v1 and prepare reads for Schemii Console or its assistant."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import UUID

# Closed query catalog. Only canonical UUIDs and UTC timestamps can enter SQL.
RECIPES = {
    "health": "Check import activity, source freshness and runs missing time-window attribution.",
    "batches": "Trace current import batches and inspect incomplete or failed collection.",
    "run-coverage": "Find runs with pending slots or incomplete producer verification.",
    "verification": "Inspect command slots, missing case evidence and exact counts and rates.",
    "slow-commands": "Rank recorded command durations within observed scope and outcome groups.",
    "run-elapsed": "Compare recorded run duration distributions by observed scope and outcome.",
    "case-hotspots": "Find repeated first failures and observed recoveries by case and command scope.",
    "attempt-contributors": "Trace exact evidence and pinned revisions for one command's observed attempts.",
}

# These grouping fields make known differences visible. A NULL cohort remains
# unknown even when all the available grouping fields happen to match.
RUN_SCOPE = "mode, profile, cohort_key, base_sha, policy_sha256"
COMMAND_SCOPE = f"command_key, project, shard, inventory_sha256, {RUN_SCOPE}"

HEALTH = """\
SELECT (SELECT count(*) FROM scoped) AS import_batches_in_window,
       (SELECT count(*) FROM scoped WHERE status = 'complete') AS complete_batches,
       (SELECT count(*) FROM scoped WHERE status IN ('partial','failed','cancelled'))
           AS partial_failed_or_cancelled_batches,
       (SELECT count(*) FROM scoped WHERE status = 'running') AS running_batches,
       (SELECT status FROM scoped ORDER BY observed_at DESC, id DESC LIMIT 1)
           AS last_batch_status,
       (SELECT id FROM scoped ORDER BY observed_at DESC, id DESC LIMIT 1)
           AS last_batch_id,
       (SELECT max(observed_at) FROM scoped) AS last_batch_observed_at,
       (SELECT max(finished_at) FROM scoped WHERE status = 'complete')
           AS last_complete_import_finished_at,
       count(*) FILTER (WHERE r.started_at >= s.window_start AND r.started_at < s.window_end)
           AS runs_in_window,
       count(*) FILTER (WHERE r.id IS NOT NULL AND r.started_at IS NULL) AS runs_without_start,
       max(r.started_at) FILTER (WHERE r.started_at < s.window_end) AS latest_run_started_before_end,
       'unknown' AS source_window_coverage
FROM scope s
LEFT JOIN devops.v1_ci_runs r USING (repository_id, installation_id)
"""

HOTSPOTS = f"""\
SELECT {COMMAND_SCOPE}, source_state, case_key,
       count(*) AS observed_command_cases,
       count(*) FILTER (WHERE first_eligible) AS eligible_first_attempts,
       count(*) FILTER (WHERE first_eligible IS FALSE) AS excluded_first_attempts,
       count(*) FILTER (WHERE first_eligible IS NULL) AS unknown_first_attempts,
       count(*) FILTER (WHERE first_outcome = 'passed') AS first_passes,
       count(*) FILTER (WHERE first_outcome IN ('failed','timed-out')) AS first_failures,
       count(*) FILTER (WHERE retry_attempts > 0) AS cases_with_retries,
       count(*) FILTER (WHERE observed_first_failure_recovered) AS recovered_first_failures,
       count(*) FILTER (WHERE first_outcome IN ('failed','timed-out'))::numeric
           / nullif(count(*) FILTER (WHERE first_eligible), 0) AS first_failure_fraction,
       count(*) FILTER (WHERE first_outcome = 'passed')::numeric
           / nullif(count(*) FILTER (WHERE first_eligible), 0) AS first_pass_fraction,
       count(*) FILTER (WHERE observed_first_failure_recovered)::numeric
           / nullif(count(*) FILTER (WHERE first_eligible), 0) AS retry_recovery_fraction,
       count(*) FILTER (WHERE observed_first_failure_recovered)::numeric
           / nullif(count(*) FILTER (WHERE first_outcome IN ('failed','timed-out')), 0)
           AS recovery_given_failure_fraction
FROM scoped
GROUP BY {COMMAND_SCOPE}, source_state, case_key
"""


def elapsed(group: str) -> str:
    return f"""\
SELECT {group},
       count(*) AS observations,
       count(*) FILTER (WHERE duration_state = 'measured') AS duration_samples,
       count(*) FILTER (WHERE duration_state IN ('invalid-source','excluded')) AS excluded_durations,
       count(*) FILTER (WHERE duration_state = 'unknown') AS unknown_durations,
       count(*) FILTER (WHERE duration_state = 'open') AS open_executions,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY duration_ms)
           FILTER (WHERE duration_state = 'measured') AS p50_ms,
       percentile_disc(0.90) WITHIN GROUP (ORDER BY duration_ms)
           FILTER (WHERE duration_state = 'measured') AS p90_ms,
       max(duration_ms) FILTER (WHERE duration_state = 'measured') AS max_ms
FROM scoped
GROUP BY {group}
"""


def instant(value: str) -> datetime:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise argparse.ArgumentTypeError("use an ISO 8601 timestamp with explicit offset")


def recipe_sql(
    recipe: str,
    repository: UUID,
    installation: UUID,
    start: datetime,
    end: datetime,
    command: UUID | None,
) -> str:
    source, clock, select, order = {
        "health": ("v1_collection_health", "observed_at", HEALTH, ""),
        "batches": (
            "v1_collection_health",
            "observed_at",
            "SELECT id, revision, observed_at, source_kind, started_at, finished_at, "
            "status, accepted_count, rejected_count, reason_code, evidence_ref, evidence_sha256 FROM scoped",
            "observed_at DESC, id",
        ),
        "run-coverage": (
            "v1_ci_run_coverage",
            "started_at",
            "SELECT id, revision, started_at, head_sha, source_fingerprint, mode, profile, outcome, "
            "completeness, acceptance, observed_slots, executed_slots, pending_slots, reused_slots, "
            "excluded_slots, slot_coverage_state, duration_state, duration_ms, import_batch_id, "
            "import_batch_revision, evidence_ref, evidence_sha256 FROM scoped",
            "pending_slots DESC, started_at DESC, id",
        ),
        "verification": (
            "v1_ci_verification",
            "run_started_at",
            "SELECT run_id, current_run_revision, run_revision, id AS command_id, revision, slot_key, "
            "command_key, project, shard, inventory_sha256, run_started_at, run_outcome, "
            "run_completeness, producer_acceptance, disposition, outcome, verification_state, "
            "case_coverage_state, expected_cases, observed_cases, missing_case_observations, "
            "eligible_first_attempt_cases, excluded_first_attempt_cases, unknown_first_attempt_cases, "
            "first_passes, first_failures, cases_with_retries, recovered_first_failures, "
            "first_failure_fraction, first_pass_fraction, retry_recovery_fraction, "
            "recovery_given_failure_fraction, duration_state, duration_ms, donor_command_id, "
            "donor_command_revision, import_batch_id, import_batch_revision, pinned_import_status, "
            "evidence_ref, evidence_sha256 FROM scoped",
            "run_started_at DESC, run_id, slot_key",
        ),
        "slow-commands": (
            "v1_ci_command_elapsed",
            "run_started_at",
            elapsed(f"{COMMAND_SCOPE}, outcome"),
            f"p90_ms DESC NULLS LAST, {COMMAND_SCOPE}, outcome",
        ),
        "run-elapsed": (
            "v1_ci_run_coverage",
            "started_at",
            elapsed(f"{RUN_SCOPE}, inventory_sha256, outcome"),
            f"p90_ms DESC NULLS LAST, {RUN_SCOPE}, inventory_sha256, outcome",
        ),
        "case-hotspots": (
            "v1_ci_case_first_attempts",
            "run_started_at",
            HOTSPOTS,
            f"first_failures DESC, cases_with_retries DESC, {COMMAND_SCOPE}, source_state, case_key",
        ),
        "attempt-contributors": (
            "v1_test_attempts",
            "run_started_at",
            "SELECT run_id, run_started_at, command_id, command_revision, case_key, attempt_number, "
            "id AS attempt_id, revision, observed_at, outcome, setup_ms, call_ms, teardown_ms, "
            "import_batch_id, import_batch_revision, evidence_ref, evidence_sha256 FROM scoped",
            "case_key, attempt_number",
        ),
    }[recipe]
    relation = f"devops.{source} d"
    if recipe == "attempt-contributors":
        # Resolve the current parent window separately and preserve the attempt's
        # exact pinned command revision in the output.
        relation = """\
(SELECT a.*, r.started_at AS run_started_at
 FROM devops.v1_test_attempts a JOIN devops.v1_ci_runs r
   ON (a.repository_id, a.installation_id, a.run_id)
    = (r.repository_id, r.installation_id, r.id)) d"""
    extra_filter = {
        "attempt-contributors": f"\n      AND d.command_id = '{command}'::uuid",
        "health": "\n      AND d.source_kind = 'local-ci-v1'",
    }.get(recipe, "")
    ordering = f"\nORDER BY {order}" if order else ""
    return f"""\
WITH scope AS (
    SELECT 1 AS definition_version, statement_timestamp() AS as_of,
           '{repository}'::uuid AS repository_id, '{installation}'::uuid AS installation_id,
           '{start.isoformat()}'::timestamptz AS window_start,
           '{end.isoformat()}'::timestamptz AS window_end
), scoped AS (
    SELECT d.*
    FROM {relation} JOIN scope s USING (repository_id, installation_id)
    WHERE d.{clock} >= s.window_start AND d.{clock} < s.window_end{extra_filter}
)
SELECT s.*, result.*
FROM scope s CROSS JOIN (
{select.rstrip()}
) result{ordering};
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    catalog = sub.add_parser("catalog", help="List the available analysis queries")
    catalog.add_argument(
        "--json", action="store_true", help="Emit the analysis catalog v1"
    )
    render = sub.add_parser("sql", help="Print a single SELECT with an explicit scope")
    render.add_argument("recipe", choices=RECIPES)
    render.add_argument("--repository-id", type=UUID, required=True)
    render.add_argument("--installation-id", type=UUID, required=True)
    render.add_argument("--from", dest="start", type=instant, required=True)
    render.add_argument("--until", dest="end", type=instant, required=True)
    render.add_argument("--command-id", type=UUID)
    render.add_argument(
        "--format",
        choices=("sql", "agent-json"),
        default="sql",
        help="Emit SQL or arguments for the existing schemii_read_query assistant tool",
    )
    args = parser.parse_args(argv)

    if args.action == "catalog":
        if args.json:
            print(
                json.dumps(
                    {
                        "definition_version": 1,
                        "minimum_schema_version": 2,
                        "agent_tool": "schemii_read_query",
                        "recipes": RECIPES,
                    },
                    indent=2,
                )
            )
        else:
            for name, question in RECIPES.items():
                print(f"{name:22} {question}")
        return 0

    if args.start >= args.end:
        render.error("--from must be earlier than --until")
    if bool(args.command_id) != (args.recipe == "attempt-contributors"):
        render.error("--command-id is required for attempt-contributors only")
    query = recipe_sql(
        args.recipe,
        args.repository_id,
        args.installation_id,
        args.start,
        args.end,
        args.command_id,
    )
    if args.format == "agent-json":
        print(
            json.dumps(
                {
                    "summary": RECIPES[args.recipe],
                    "queries": [{"label": args.recipe, "sql": query}],
                },
                indent=2,
            )
        )
    else:
        print(query, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
