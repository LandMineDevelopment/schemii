-- Analysis definitions v1 over the current fact projection in 001.
-- Current parents join by logical identity. Exact pinned revisions remain lineage.
CREATE VIEW devops.v1_ci_case_first_attempts AS
WITH cases AS (
    SELECT repository_id, installation_id, command_id, case_key,
           count(*) AS observed_attempts,
           count(*) FILTER (WHERE attempt_number > 0) AS retry_attempts,
           max(outcome) FILTER (WHERE attempt_number = 0) AS first_outcome,
           bool_or(attempt_number > 0 AND outcome = 'passed') AS observed_retry_pass
    FROM devops.current_test_attempts
    GROUP BY repository_id, installation_id, command_id, case_key
)
SELECT 1 AS definition_version, statement_timestamp() AS as_of,
       c.repository_id, c.installation_id, c.run_id, r.revision AS current_run_revision,
       c.id AS command_id, c.revision AS current_command_revision, a.case_key,
       c.slot_key, c.command_key, c.project, c.shard, c.inventory_sha256,
       r.started_at AS run_started_at, r.outcome AS run_outcome,
       CASE WHEN r.outcome = 'source-changed' OR c.outcome = 'source-changed'
            THEN 'source-changed' ELSE 'no-source-change-reported' END AS source_state,
       r.mode, r.profile, r.cohort_key, r.policy_sha256, r.base_sha,
       r.head_sha, r.source_fingerprint,
       a.observed_attempts, a.retry_attempts, a.first_outcome, a.observed_retry_pass,
       a.first_outcome IN ('passed', 'failed', 'timed-out') AS first_eligible,
       a.first_outcome IN ('failed', 'timed-out') AND a.observed_retry_pass
           AS observed_first_failure_recovered
FROM cases a
JOIN devops.current_commands c
    ON (c.repository_id, c.installation_id, c.id)
     = (a.repository_id, a.installation_id, a.command_id)
JOIN devops.current_ci_runs r
    ON (r.repository_id, r.installation_id, r.id)
     = (c.repository_id, c.installation_id, c.run_id)
WHERE c.disposition = 'executed';

CREATE VIEW devops.v1_ci_command_elapsed AS
SELECT 1 AS definition_version, statement_timestamp() AS as_of,
       c.*, r.revision AS current_run_revision, r.started_at AS run_started_at,
       r.head_sha, r.source_fingerprint, r.index_fingerprint, r.dirty,
       r.base_sha, r.policy_sha256, r.cohort_key, r.profile, r.mode,
       r.outcome AS run_outcome, r.completeness AS run_completeness,
       r.acceptance AS producer_acceptance,
       extract(epoch FROM (c.finished_at - c.started_at)) * 1000 AS wall_elapsed_ms,
       CASE
           WHEN c.disposition <> 'executed' THEN 'excluded'
           WHEN r.outcome = 'source-changed' OR c.outcome = 'source-changed' THEN 'invalid-source'
           WHEN c.outcome IN ('running', 'pending') THEN 'open'
           WHEN c.outcome IS NULL OR c.duration_ms IS NULL THEN 'unknown'
           ELSE 'measured'
       END AS duration_state
FROM devops.current_commands c
JOIN devops.current_ci_runs r
    ON (r.repository_id, r.installation_id, r.id) = (c.repository_id, c.installation_id, c.run_id);

CREATE VIEW devops.v1_ci_verification AS
WITH coverage AS (
    SELECT repository_id, installation_id, command_id,
           count(*) AS observed_cases,
           count(*) FILTER (WHERE first_outcome IS NULL) AS unknown_first_attempt_cases,
           count(*) FILTER (WHERE first_eligible IS FALSE) AS excluded_first_attempt_cases,
           count(*) FILTER (WHERE first_eligible) AS eligible_first_attempt_cases,
           count(*) FILTER (WHERE first_outcome = 'passed') AS first_passes,
           count(*) FILTER (WHERE first_outcome IN ('failed', 'timed-out')) AS first_failures,
           count(*) FILTER (WHERE retry_attempts > 0) AS cases_with_retries,
           count(*) FILTER (WHERE observed_first_failure_recovered) AS recovered_first_failures
    FROM devops.v1_ci_case_first_attempts
    GROUP BY repository_id, installation_id, command_id
)
SELECT c.*, b.source_kind AS import_source_kind, b.status AS pinned_import_status,
       CASE
           WHEN c.disposition <> 'executed' THEN c.disposition
           WHEN c.duration_state = 'invalid-source' THEN 'invalid-source'
           WHEN c.outcome = 'passed' THEN 'fresh-passed'
           WHEN c.outcome IN ('failed', 'error', 'timed-out', 'collection-error') THEN 'failed'
           WHEN c.outcome IN ('running', 'pending') THEN 'pending'
           WHEN c.outcome IN ('cancelled', 'incomplete', 'skipped') THEN c.outcome
           ELSE 'unknown'
       END AS verification_state,
       CASE
           WHEN c.disposition <> 'executed' THEN 'not-executed'
           WHEN c.expected_cases IS NULL THEN 'unknown-inventory'
           WHEN coalesce(a.observed_cases, 0) > c.expected_cases THEN 'count-mismatch'
           WHEN coalesce(a.observed_cases, 0) < c.expected_cases THEN 'missing-observations'
           WHEN a.unknown_first_attempt_cases > 0 THEN 'missing-first-attempts'
           WHEN c.expected_cases = 0 THEN 'empty-inventory'
           ELSE 'count-matches'
       END AS case_coverage_state,
       CASE WHEN c.disposition = 'executed' THEN coalesce(a.observed_cases, 0) END AS observed_cases,
       CASE WHEN c.disposition = 'executed' AND c.expected_cases >= coalesce(a.observed_cases, 0)
            THEN c.expected_cases - coalesce(a.observed_cases, 0) END AS missing_case_observations,
       coalesce(a.unknown_first_attempt_cases, 0) AS unknown_first_attempt_cases,
       coalesce(a.excluded_first_attempt_cases, 0) AS excluded_first_attempt_cases,
       coalesce(a.eligible_first_attempt_cases, 0) AS eligible_first_attempt_cases,
       coalesce(a.first_passes, 0) AS first_passes, coalesce(a.first_failures, 0) AS first_failures,
       coalesce(a.cases_with_retries, 0) AS cases_with_retries,
       coalesce(a.recovered_first_failures, 0) AS recovered_first_failures,
       a.first_failures::numeric / nullif(a.eligible_first_attempt_cases, 0) AS first_failure_fraction,
       a.first_passes::numeric / nullif(a.eligible_first_attempt_cases, 0) AS first_pass_fraction,
       a.recovered_first_failures::numeric / nullif(a.eligible_first_attempt_cases, 0)
           AS retry_recovery_fraction,
       a.recovered_first_failures::numeric / nullif(a.first_failures, 0) AS recovery_given_failure_fraction
FROM devops.v1_ci_command_elapsed c
JOIN devops.import_batches b
    ON (b.repository_id, b.installation_id, b.id, b.revision)
     = (c.repository_id, c.installation_id, c.import_batch_id, c.import_batch_revision)
LEFT JOIN coverage a
    ON (a.repository_id, a.installation_id, a.command_id) = (c.repository_id, c.installation_id, c.id);

CREATE VIEW devops.v1_ci_run_coverage AS
WITH slots AS (
    SELECT repository_id, installation_id, run_id,
           count(*) AS observed_slots,
           count(*) FILTER (WHERE disposition = 'executed') AS executed_slots,
           count(*) FILTER (WHERE disposition = 'pending') AS pending_slots,
           count(*) FILTER (WHERE disposition = 'reused') AS reused_slots,
           count(*) FILTER (WHERE disposition = 'excluded') AS excluded_slots
    FROM devops.current_commands
    GROUP BY repository_id, installation_id, run_id
)
SELECT r.*, statement_timestamp() AS as_of,
       coalesce(s.observed_slots, 0) AS observed_slots,
       coalesce(s.executed_slots, 0) AS executed_slots,
       coalesce(s.pending_slots, 0) AS pending_slots,
       coalesce(s.reused_slots, 0) AS reused_slots,
       coalesce(s.excluded_slots, 0) AS excluded_slots,
       CASE WHEN s.observed_slots IS NULL THEN 'no-visible-slots'
            ELSE 'plan-size-unknown' END AS slot_coverage_state,
       CASE
           WHEN r.outcome = 'source-changed' THEN 'invalid-source'
           WHEN r.outcome IN ('running', 'pending') THEN 'open'
           WHEN r.outcome IS NULL OR r.duration_ms IS NULL THEN 'unknown'
           ELSE 'measured'
       END AS duration_state
FROM devops.v1_ci_runs r
LEFT JOIN slots s
    ON (s.repository_id, s.installation_id, s.run_id) = (r.repository_id, r.installation_id, r.id);

CREATE VIEW devops.v1_ci_analysis_definitions AS
SELECT 1 AS definition_version, statement_timestamp() AS as_of, d.*
FROM (VALUES
    ('T01', 'Observed case coverage', 'Distinct observed cases per executed command compared to its recorded expected count; plan membership is unavailable.'),
    ('T02', 'First-attempt failure fraction', 'Failed or timed-out attempt zero / observed passed, failed or timed-out attempt zeros.'),
    ('T03', 'First-attempt pass fraction', 'Passed attempt zero / same eligible first attempts as T02.'),
    ('T04', 'Observed retry recovery', 'Eligible first failures with a later observed pass / eligible first attempts; conditional rate divides by first failures.'),
    ('T05', 'Recorded attempt evidence', 'Observed attempts and gaps are separate from run completeness and producer acceptance.'),
    ('T06', 'Recorded verification disposition', 'Count visible slots by disposition; no independent required manifest or reuse approval is inferred.'),
    ('T07', 'Run elapsed', 'Recorded monotonic milliseconds; open, source-changed and unknown durations are outside the measured population.'),
    ('T08', 'Command elapsed', 'Recorded monotonic milliseconds for measured executed slots; separate command scopes and outcomes.'),
    ('O01', 'Collection health subset', 'Current import batches by latest revision; last complete finish is distinct from last observed batch and source coverage is unknown.')
) AS d(metric_id, name, definition);

COMMENT ON VIEW devops.v1_ci_case_first_attempts IS 'One observed case per current executed slot. Join qualified command and case keys to v1_test_attempts for exact contributor lineage.';
COMMENT ON VIEW devops.v1_ci_command_elapsed IS 'Recorded monotonic command duration and separate wall clock elapsed, one visible slot per current run.';
COMMENT ON VIEW devops.v1_ci_verification IS 'One visible command slot per current run with observed case coverage and separate producer verification state.';
COMMENT ON VIEW devops.v1_ci_run_coverage IS 'One current CI run with visible slot counts and separate producer outcome, completeness, and acceptance.';

REVOKE ALL ON devops.v1_ci_case_first_attempts, devops.v1_ci_command_elapsed, devops.v1_ci_verification,
    devops.v1_ci_run_coverage, devops.v1_ci_analysis_definitions FROM PUBLIC;
GRANT SELECT ON devops.v1_ci_case_first_attempts, devops.v1_ci_command_elapsed, devops.v1_ci_verification,
    devops.v1_ci_run_coverage, devops.v1_ci_analysis_definitions
    TO devops_reader, devops_collector, devops_maintenance;
