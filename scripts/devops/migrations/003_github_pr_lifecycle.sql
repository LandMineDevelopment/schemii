-- Saved PR lifecycle receipts, scoped independently of local CI intake.
CREATE TABLE devops.github_pr_lifecycle_attempts (
    repository_id uuid NOT NULL REFERENCES devops.repositories(id),
    installation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    source_kind text NOT NULL CHECK (source_kind = 'github-pr-lifecycle-v1'),
    format_version integer NOT NULL CHECK (format_version = 1),
    provider_repository_id varchar(160) NOT NULL CHECK (provider_repository_id ~ '^[A-Za-z0-9_+=/:-]{1,160}$'),
    window_start timestamptz NOT NULL,
    overlap_us bigint NOT NULL CHECK (overlap_us BETWEEN 1 AND 2592000000000),
    prior_through timestamptz NOT NULL,
    prior_attempt_id uuid,
    prior_evidence_sha256 devops.sha256,
    scan_from timestamptz NOT NULL,
    through timestamptz NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    requests integer NOT NULL CHECK (requests BETWEEN 0 AND 10000),
    status text NOT NULL CHECK (status IN ('complete', 'partial')),
    failure_reason devops.safe_key CHECK (failure_reason IN (
        'conditional-cache-miss', 'connection-count-changed', 'connection-count-mismatch',
        'endpoint-hidden-or-missing', 'graphql-errors', 'http-error', 'invalid-pagination',
        'invalid-provider-data', 'missing-provider-field', 'network-error', 'network-timeout',
        'observation-clock-regressed', 'pagination-cursor-cycle', 'permission-denied',
        'pr-changed-during-traversal', 'pr-hidden-or-missing', 'pr-identity-changed',
        'provider-fact-changed', 'rate-limited', 'repository-changed-during-traversal',
        'repository-hidden-or-missing', 'repository-identity-changed',
        'request-budget-exhausted', 'timeline-changed-during-traversal', 'unsupported-timeline-event'
    )),
    failure_operation text CHECK (failure_operation IN ('listing', 'listing-verification', 'timeline', 'reviews')),
    failure_number integer CHECK (failure_number > 0),
    failure_request integer CHECK (failure_request BETWEEN 1 AND requests),
    failure_observed_from timestamptz,
    failure_observed_at timestamptz,
    failure_http_status integer CHECK (failure_http_status BETWEEN 100 AND 599),
    failure_cached_from_attempt uuid,
    failure_cached_from_request integer CHECK (failure_cached_from_request BETWEEN 1 AND 10000),
    failure_timeline_updated_at timestamptz,
    failure_partial_data boolean,
    failure_clock_regressed boolean,
    rate_remaining bigint CHECK (rate_remaining BETWEEN 0 AND 9999999999),
    rate_retry_after_seconds bigint CHECK (rate_retry_after_seconds BETWEEN 0 AND 9999999999),
    rate_reset_epoch_seconds bigint CHECK (rate_reset_epoch_seconds BETWEEN 0 AND 9999999999),
    listing_state text NOT NULL CHECK (listing_state IN ('partial', 'complete')),
    verification_state text NOT NULL CHECK (verification_state IN ('pending', 'partial', 'complete')),
    listing_count integer CHECK (listing_count >= 0),
    listing_evidence_sha256 devops.sha256,
    verification_count integer CHECK (verification_count >= 0),
    verification_evidence_sha256 devops.sha256,
    report_sha256 devops.sha256 NOT NULL,
    envelope_sha256 devops.sha256 NOT NULL,
    PRIMARY KEY (repository_id, installation_id, attempt_id),
    UNIQUE (repository_id, installation_id, attempt_id, through, report_sha256, status),
    CHECK ((prior_attempt_id IS NULL) = (prior_evidence_sha256 IS NULL)),
    CHECK (prior_attempt_id IS DISTINCT FROM attempt_id),
    CHECK (prior_attempt_id IS NOT NULL OR prior_through = window_start),
    CHECK (window_start <= scan_from AND scan_from <= prior_through
        AND prior_through < through AND through <= started_at AND started_at <= finished_at),
    CHECK (scan_from = greatest(window_start, prior_through - overlap_us * interval '1 microsecond')),
    CHECK ((status = 'partial') = (failure_reason IS NOT NULL)),
    CHECK ((status = 'complete') = (verification_state = 'complete')),
    CHECK ((status = 'partial') = (failure_operation IS NOT NULL)),
    CHECK (status <> 'complete' OR (requests >= 2 AND num_nonnulls(
        failure_request, failure_observed_from, failure_observed_at, failure_http_status,
        failure_cached_from_attempt, failure_cached_from_request, failure_timeline_updated_at,
        failure_partial_data, failure_clock_regressed
    ) = 0)),
    CHECK ((failure_number IS NOT NULL) = COALESCE(failure_operation IN ('timeline', 'reviews'), false)),
    CHECK ((failure_request IS NULL) = (failure_observed_from IS NULL)),
    CHECK (failure_request IS NULL OR failure_request = requests),
    CHECK (failure_observed_at IS NULL OR failure_observed_from IS NOT NULL),
    CHECK (failure_observed_from BETWEEN started_at AND finished_at),
    CHECK (failure_observed_at BETWEEN failure_observed_from AND finished_at),
    CHECK ((failure_cached_from_attempt IS NULL) = (failure_cached_from_request IS NULL)),
    CHECK (failure_cached_from_attempt IS NULL OR failure_http_status IS NOT DISTINCT FROM 304),
    CHECK ((listing_count IS NULL) = (listing_evidence_sha256 IS NULL)),
    CHECK ((listing_state = 'complete') = (listing_count IS NOT NULL)),
    CHECK ((verification_count IS NULL) = (verification_evidence_sha256 IS NULL)),
    CHECK (verification_state = 'pending' OR listing_state = 'complete'),
    CHECK (verification_state <> 'pending' OR verification_count IS NULL),
    CHECK (status <> 'complete' OR (
        verification_count IS NOT NULL AND verification_count = listing_count
        AND verification_evidence_sha256 = listing_evidence_sha256
    )),
    prior_status text GENERATED ALWAYS AS ('complete'::text) STORED,
    FOREIGN KEY (repository_id, installation_id, prior_attempt_id, prior_through, prior_evidence_sha256, prior_status)
        REFERENCES devops.github_pr_lifecycle_attempts
        (repository_id, installation_id, attempt_id, through, report_sha256, status)
);

-- One complete successor of each checkpoint, including the initial checkpoint.
CREATE UNIQUE INDEX github_pr_lifecycle_successor ON devops.github_pr_lifecycle_attempts
    (repository_id, installation_id, prior_attempt_id) NULLS NOT DISTINCT WHERE status = 'complete';
CREATE INDEX github_pr_lifecycle_cursor ON devops.github_pr_lifecycle_attempts
    (repository_id, installation_id, through DESC) WHERE status = 'complete';

CREATE TABLE devops.github_pr_lifecycle_coverage (
    repository_id uuid NOT NULL,
    installation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    pull_request_id varchar(160) NOT NULL CHECK (pull_request_id ~ '^[A-Za-z0-9_+=/:-]{1,160}$'),
    number integer NOT NULL CHECK (number > 0),
    timeline_state text NOT NULL CHECK (timeline_state IN ('pending', 'partial', 'complete')),
    review_state text NOT NULL CHECK (review_state IN ('pending', 'partial', 'complete')),
    CHECK (review_state = 'pending' OR timeline_state = 'complete'),
    PRIMARY KEY (repository_id, installation_id, attempt_id, pull_request_id),
    UNIQUE (repository_id, installation_id, attempt_id, number),
    FOREIGN KEY (repository_id, installation_id, attempt_id)
        REFERENCES devops.github_pr_lifecycle_attempts (repository_id, installation_id, attempt_id)
);

CREATE TABLE devops.github_pr_lifecycle_observations (
    repository_id uuid NOT NULL,
    installation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    evidence_ref devops.safe_key NOT NULL,
    logical_id uuid NOT NULL,
    source_key devops.safe_key NOT NULL,
    evidence_sha256 devops.sha256 NOT NULL,
    stream text NOT NULL CHECK (stream IN ('snapshot', 'timeline', 'reviews')),
    provenance text NOT NULL CHECK (provenance IN ('listing', 'listing-verification', 'timeline', 'reviews')),
    pull_request_id varchar(160) NOT NULL CHECK (pull_request_id ~ '^[A-Za-z0-9_+=/:-]{1,160}$'),
    number integer NOT NULL CHECK (number > 0),
    provider_id varchar(160) NOT NULL CHECK (provider_id ~ '^[A-Za-z0-9_+=/:-]{1,160}$'),
    request integer NOT NULL CHECK (request BETWEEN 1 AND 10000),
    observed_from timestamptz NOT NULL,
    observed_at timestamptz NOT NULL CHECK (observed_at >= observed_from),
    http_status integer NOT NULL CHECK (http_status IN (200, 304)),
    timeline_updated_at timestamptz,
    cached_from_attempt uuid,
    cached_from_request integer CHECK (cached_from_request BETWEEN 1 AND 10000),
    pr_state text CHECK (pr_state IN ('open', 'closed', 'merged')),
    draft boolean,
    head_sha devops.git_sha,
    created_at timestamptz,
    updated_at timestamptz,
    closed_at timestamptz,
    merged_at timestamptz,
    event_kind text CHECK (event_kind IN (
        'ready-for-review', 'convert-to-draft', 'closed', 'reopened', 'merged', 'head-force-pushed'
    )),
    occurred_at timestamptz,
    before_sha devops.git_sha,
    after_sha devops.git_sha,
    review_state text CHECK (review_state IN ('approved', 'changes_requested', 'commented', 'dismissed', 'pending')),
    submitted_at timestamptz,
    reviewed_sha devops.git_sha,
    PRIMARY KEY (repository_id, installation_id, attempt_id, evidence_ref),
    FOREIGN KEY (repository_id, installation_id, attempt_id)
        REFERENCES devops.github_pr_lifecycle_attempts (repository_id, installation_id, attempt_id),
    CHECK ((http_status = 304) = (cached_from_attempt IS NOT NULL)),
    CHECK ((cached_from_attempt IS NULL) = (cached_from_request IS NULL)),
    CHECK (cached_from_attempt IS DISTINCT FROM attempt_id OR cached_from_request < request),
    CHECK ((stream = 'timeline') = (timeline_updated_at IS NOT NULL)),
    CHECK (stream = 'snapshot' OR stream = provenance),
    CHECK ((stream = 'snapshot') = (pr_state IS NOT NULL)
        AND (stream = 'snapshot') = (draft IS NOT NULL)
        AND (stream = 'snapshot') = (head_sha IS NOT NULL)
        AND (stream = 'snapshot') = (created_at IS NOT NULL)
        AND (stream = 'snapshot') = (updated_at IS NOT NULL)),
    CHECK (stream <> 'snapshot' OR provider_id = pull_request_id),
    CHECK (stream = 'snapshot' OR (closed_at IS NULL AND merged_at IS NULL)),
    CHECK (created_at <= updated_at AND created_at <= closed_at),
    CHECK (merged_at IS NULL OR (pr_state = 'merged' AND created_at <= merged_at)),
    CHECK ((stream = 'timeline') = (event_kind IS NOT NULL)
        AND (stream = 'timeline') = (occurred_at IS NOT NULL)),
    CHECK ((before_sha IS NULL AND after_sha IS NULL) OR event_kind IS NOT DISTINCT FROM 'head-force-pushed'),
    CHECK ((stream = 'reviews') = (review_state IS NOT NULL)),
    CHECK (stream = 'reviews' OR (submitted_at IS NULL AND reviewed_sha IS NULL)),
    CHECK (review_state <> 'pending' OR submitted_at IS NULL)
);

CREATE VIEW devops.v1_github_pr_lifecycle_attempts AS
SELECT a.*, a.status = 'complete' AND a.attempt_id = (
    SELECT latest.attempt_id FROM devops.github_pr_lifecycle_attempts AS latest
    WHERE latest.repository_id = a.repository_id AND latest.installation_id = a.installation_id
        AND latest.status = 'complete'
    ORDER BY latest.through DESC LIMIT 1
) AS is_current_checkpoint
FROM devops.github_pr_lifecycle_attempts AS a;

CREATE VIEW devops.v1_github_pr_lifecycle_coverage AS
SELECT * FROM devops.github_pr_lifecycle_coverage;

CREATE VIEW devops.v1_github_pr_lifecycle_observations AS
SELECT * FROM devops.github_pr_lifecycle_observations;

REVOKE ALL ON devops.github_pr_lifecycle_attempts, devops.github_pr_lifecycle_coverage,
    devops.github_pr_lifecycle_observations, devops.v1_github_pr_lifecycle_attempts,
    devops.v1_github_pr_lifecycle_coverage, devops.v1_github_pr_lifecycle_observations FROM PUBLIC;
GRANT SELECT, INSERT ON devops.github_pr_lifecycle_attempts, devops.github_pr_lifecycle_coverage,
    devops.github_pr_lifecycle_observations TO devops_collector;
GRANT SELECT ON devops.github_pr_lifecycle_attempts, devops.github_pr_lifecycle_coverage,
    devops.github_pr_lifecycle_observations TO devops_maintenance;
GRANT SELECT ON devops.v1_github_pr_lifecycle_attempts, devops.v1_github_pr_lifecycle_coverage,
    devops.v1_github_pr_lifecycle_observations TO devops_reader, devops_maintenance;
