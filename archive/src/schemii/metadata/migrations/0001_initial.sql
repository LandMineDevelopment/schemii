CREATE TABLE metadata_applications (
    application_id text PRIMARY KEY CHECK (application_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_chats (
    chat_id uuid PRIMARY KEY,
    application_id text NOT NULL REFERENCES metadata_applications(application_id),
    resource_kind text NOT NULL CHECK (resource_kind ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    resource_id text NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 256),
    external_session_id text,
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('provisioning', 'active', 'deleting', 'deleted', 'failed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deleted_at timestamptz,
    UNIQUE (application_id, external_session_id),
    CHECK ((state = 'deleted') = (deleted_at IS NOT NULL))
);

CREATE TABLE metadata_targets (
    target_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL UNIQUE REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    profile_id text NOT NULL CHECK (length(profile_id) BETWEEN 1 AND 256),
    database_name text NOT NULL CHECK (length(database_name) BETWEEN 1 AND 63),
    namespace_name text NOT NULL CHECK (length(namespace_name) BETWEEN 1 AND 63),
    profile_fingerprint char(64) NOT NULL CHECK (profile_fingerprint ~ '^[0-9a-f]{64}$'),
    connected_target_fingerprint char(64) NOT NULL CHECK (connected_target_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_policy_versions (
    policy_version_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision > 0),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object' AND pg_column_size(policy) <= 1048576),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (chat_id, revision)
);

CREATE TABLE metadata_capabilities (
    capability_id uuid PRIMARY KEY,
    policy_version_id uuid NOT NULL REFERENCES metadata_policy_versions(policy_version_id) ON DELETE CASCADE,
    capability text NOT NULL CHECK (capability ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    grant_mode text NOT NULL CHECK (grant_mode IN ('deny', 'approval', 'once_per_chat', 'automatic')),
    UNIQUE (policy_version_id, capability)
);

CREATE TABLE metadata_grants (
    grant_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    capability text NOT NULL CHECK (capability ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    policy_revision integer NOT NULL CHECK (policy_revision > 0),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'consumed', 'revoked', 'expired')),
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_at timestamptz
);
CREATE UNIQUE INDEX metadata_grants_one_active ON metadata_grants(chat_id, capability) WHERE state = 'active';

CREATE TABLE metadata_proposals (
    proposal_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    capability text NOT NULL CHECK (capability ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    policy_revision integer NOT NULL CHECK (policy_revision > 0),
    action jsonb NOT NULL CHECK (jsonb_typeof(action) = 'object' AND pg_column_size(action) <= 1048576),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'authorized', 'rejected', 'expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

CREATE TABLE metadata_operations (
    operation_id uuid PRIMARY KEY,
    proposal_id uuid UNIQUE REFERENCES metadata_proposals(proposal_id),
    chat_id uuid NOT NULL REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    capability text NOT NULL CHECK (capability ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'running', 'succeeded', 'failed', 'uncertain')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_operation_approvals (
    approval_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL UNIQUE REFERENCES metadata_operations(operation_id) ON DELETE CASCADE,
    policy_revision integer NOT NULL CHECK (policy_revision > 0),
    decision text NOT NULL CHECK (decision IN ('automatic', 'grant', 'explicit')),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_operation_attempts (
    attempt_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL REFERENCES metadata_operations(operation_id) ON DELETE CASCADE,
    worker_id text NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 128),
    claim_token_hash char(64) NOT NULL CHECK (claim_token_hash ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'succeeded', 'failed', 'uncertain', 'abandoned')),
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX metadata_attempts_one_running ON metadata_operation_attempts(operation_id) WHERE state = 'running';

CREATE TABLE metadata_operation_outcomes (
    outcome_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL UNIQUE REFERENCES metadata_operations(operation_id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state IN ('succeeded', 'failed', 'uncertain')),
    result jsonb CHECK (result IS NULL OR (jsonb_typeof(result) = 'object' AND pg_column_size(result) <= 1048576)),
    error jsonb CHECK (error IS NULL OR (jsonb_typeof(error) = 'object' AND pg_column_size(error) <= 1048576)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((state = 'succeeded' AND result IS NOT NULL AND error IS NULL) OR (state <> 'succeeded' AND error IS NOT NULL))
);

CREATE TABLE metadata_query_result_references (
    result_ref_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES metadata_chats(chat_id) ON DELETE CASCADE,
    binding jsonb NOT NULL CHECK (jsonb_typeof(binding) = 'object' AND pg_column_size(binding) <= 1048576),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'reserved', 'delivering', 'consumed', 'uncertain', 'expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

CREATE TABLE metadata_query_result_payloads (
    result_ref_id uuid PRIMARY KEY REFERENCES metadata_query_result_references(result_ref_id) ON DELETE CASCADE,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 1048576),
    byte_count integer NOT NULL CHECK (byte_count BETWEEN 1 AND 1048576),
    scrubbed_at timestamptz
);

CREATE TABLE metadata_query_result_deliveries (
    delivery_id uuid PRIMARY KEY,
    result_ref_id uuid NOT NULL REFERENCES metadata_query_result_references(result_ref_id) ON DELETE CASCADE,
    reservation_token_hash char(64) NOT NULL CHECK (reservation_token_hash ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved', 'delivering', 'consumed', 'released', 'uncertain')),
    reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    dispatch_started_at timestamptz,
    finished_at timestamptz
);
CREATE UNIQUE INDEX metadata_deliveries_one_open ON metadata_query_result_deliveries(result_ref_id) WHERE state IN ('reserved', 'delivering');

CREATE TABLE metadata_authority_transitions (
    transition_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id text NOT NULL REFERENCES metadata_applications(application_id),
    aggregate_kind text NOT NULL CHECK (aggregate_kind IN ('chat', 'grant', 'proposal', 'operation', 'result')),
    aggregate_id uuid NOT NULL,
    from_state text,
    to_state text NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_migration_plans (
    plan_id uuid PRIMARY KEY,
    application_id text NOT NULL REFERENCES metadata_applications(application_id),
    resource_kind text NOT NULL CHECK (resource_kind IN ('schema', 'view', 'materialized_view')),
    resource_id text NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 256),
    resource_revision integer NOT NULL CHECK (resource_revision >= 0),
    layout_token text NOT NULL CHECK (length(layout_token) BETWEEN 1 AND 256),
    profile_id text NOT NULL CHECK (length(profile_id) BETWEEN 1 AND 256),
    database_name text NOT NULL CHECK (length(database_name) BETWEEN 1 AND 63),
    namespace_name text NOT NULL CHECK (length(namespace_name) BETWEEN 1 AND 63),
    profile_fingerprint char(64) NOT NULL CHECK (profile_fingerprint ~ '^[0-9a-f]{64}$'),
    connected_target_fingerprint char(64) NOT NULL CHECK (connected_target_fingerprint ~ '^[0-9a-f]{64}$'),
    live_fingerprint char(64) NOT NULL CHECK (live_fingerprint ~ '^[0-9a-f]{64}$'),
    desired_fingerprint char(64) NOT NULL CHECK (desired_fingerprint ~ '^[0-9a-f]{64}$'),
    private_payload jsonb NOT NULL CHECK (jsonb_typeof(private_payload) = 'object' AND pg_column_size(private_payload) <= 1048576),
    review_payload jsonb NOT NULL CHECK (jsonb_typeof(review_payload) = 'object' AND pg_column_size(review_payload) <= 1048576),
    review_digest char(64) NOT NULL CHECK (review_digest ~ '^[0-9a-f]{64}$'),
    destructive boolean NOT NULL,
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

CREATE TABLE metadata_migration_executions (
    execution_id uuid PRIMARY KEY,
    plan_id uuid NOT NULL UNIQUE REFERENCES metadata_migration_plans(plan_id),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'applying', 'succeeded', 'failed', 'uncertain')),
    confirmed_review_digest char(64) NOT NULL CHECK (confirmed_review_digest ~ '^[0-9a-f]{64}$'),
    destructive_confirmed boolean NOT NULL,
    target_xid text,
    target_identity jsonb CHECK (target_identity IS NULL OR (jsonb_typeof(target_identity) = 'object' AND pg_column_size(target_identity) <= 1048576)),
    intended_result jsonb CHECK (intended_result IS NULL OR (jsonb_typeof(intended_result) = 'object' AND pg_column_size(intended_result) <= 1048576)),
    commit_outcome text CHECK (commit_outcome IN ('committed', 'rolled_back', 'uncertain')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_migration_syncs (
    sync_id uuid PRIMARY KEY,
    execution_id uuid NOT NULL UNIQUE REFERENCES metadata_migration_executions(execution_id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state IN ('pending', 'succeeded', 'conflict', 'failed')),
    receipt jsonb CHECK (receipt IS NULL OR (jsonb_typeof(receipt) = 'object' AND pg_column_size(receipt) <= 1048576)),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata_migration_transitions (
    transition_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id uuid NOT NULL REFERENCES metadata_migration_executions(execution_id) ON DELETE CASCADE,
    from_state text,
    to_state text NOT NULL CHECK (to_state IN ('ready', 'applying', 'succeeded', 'failed', 'uncertain')),
    evidence jsonb CHECK (evidence IS NULL OR (jsonb_typeof(evidence) = 'object' AND pg_column_size(evidence) <= 1048576)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX metadata_chats_resource ON metadata_chats(application_id, resource_kind, resource_id);
CREATE INDEX metadata_proposals_chat_state ON metadata_proposals(chat_id, state);
CREATE INDEX metadata_operations_chat_state ON metadata_operations(chat_id, state);
CREATE INDEX metadata_attempts_heartbeat ON metadata_operation_attempts(state, heartbeat_at);
CREATE INDEX metadata_results_expiry ON metadata_query_result_references(state, expires_at);
CREATE INDEX metadata_plans_expiry ON metadata_migration_plans(state, expires_at);

INSERT INTO metadata_applications (application_id) VALUES ('schemii'), ('schemer');

CREATE FUNCTION metadata_current_application() RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT CASE session_user
        WHEN 'schemii_metadata_schemii' THEN 'schemii'
        WHEN 'schemii_metadata_schemer' THEN 'schemer'
        ELSE NULL
    END
$$;
REVOKE ALL ON FUNCTION metadata_current_application() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION metadata_current_application() TO schemii_metadata_schemii, schemii_metadata_schemer;

ALTER TABLE metadata_applications ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_applications_isolation ON metadata_applications
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());
ALTER TABLE metadata_chats ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_chats_isolation ON metadata_chats
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());

ALTER TABLE metadata_targets ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_targets_isolation ON metadata_targets USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_targets.chat_id
));
ALTER TABLE metadata_policy_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_policy_versions_isolation ON metadata_policy_versions USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_policy_versions.chat_id
));
ALTER TABLE metadata_capabilities ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_capabilities_isolation ON metadata_capabilities USING (EXISTS (
    SELECT 1 FROM metadata_policy_versions v WHERE v.policy_version_id = metadata_capabilities.policy_version_id
));
ALTER TABLE metadata_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_grants_isolation ON metadata_grants USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_grants.chat_id
));
ALTER TABLE metadata_proposals ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_proposals_isolation ON metadata_proposals USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_proposals.chat_id
));
ALTER TABLE metadata_operations ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_operations_isolation ON metadata_operations USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_operations.chat_id
));
ALTER TABLE metadata_operation_approvals ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_operation_approvals_isolation ON metadata_operation_approvals USING (EXISTS (
    SELECT 1 FROM metadata_operations o WHERE o.operation_id = metadata_operation_approvals.operation_id
));
ALTER TABLE metadata_operation_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_operation_attempts_isolation ON metadata_operation_attempts USING (EXISTS (
    SELECT 1 FROM metadata_operations o WHERE o.operation_id = metadata_operation_attempts.operation_id
));
ALTER TABLE metadata_operation_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_operation_outcomes_isolation ON metadata_operation_outcomes USING (EXISTS (
    SELECT 1 FROM metadata_operations o WHERE o.operation_id = metadata_operation_outcomes.operation_id
));
ALTER TABLE metadata_query_result_references ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_query_result_references_isolation ON metadata_query_result_references USING (EXISTS (
    SELECT 1 FROM metadata_chats c WHERE c.chat_id = metadata_query_result_references.chat_id
));
ALTER TABLE metadata_query_result_payloads ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_query_result_payloads_isolation ON metadata_query_result_payloads USING (EXISTS (
    SELECT 1 FROM metadata_query_result_references r WHERE r.result_ref_id = metadata_query_result_payloads.result_ref_id
));
ALTER TABLE metadata_query_result_deliveries ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_query_result_deliveries_isolation ON metadata_query_result_deliveries USING (EXISTS (
    SELECT 1 FROM metadata_query_result_references r WHERE r.result_ref_id = metadata_query_result_deliveries.result_ref_id
));
ALTER TABLE metadata_authority_transitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_authority_transitions_isolation ON metadata_authority_transitions
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());
ALTER TABLE metadata_migration_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_migration_plans_isolation ON metadata_migration_plans
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());
ALTER TABLE metadata_migration_executions ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_migration_executions_isolation ON metadata_migration_executions USING (EXISTS (
    SELECT 1 FROM metadata_migration_plans p WHERE p.plan_id = metadata_migration_executions.plan_id
));
ALTER TABLE metadata_migration_syncs ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_migration_syncs_isolation ON metadata_migration_syncs USING (EXISTS (
    SELECT 1 FROM metadata_migration_executions e WHERE e.execution_id = metadata_migration_syncs.execution_id
));
ALTER TABLE metadata_migration_transitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_migration_transitions_isolation ON metadata_migration_transitions USING (EXISTS (
    SELECT 1 FROM metadata_migration_executions e WHERE e.execution_id = metadata_migration_transitions.execution_id
));

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON metadata_schema_migrations
    FROM schemii_metadata_schemii, schemii_metadata_schemer;
GRANT SELECT ON metadata_schema_migrations TO schemii_metadata_schemii, schemii_metadata_schemer;
