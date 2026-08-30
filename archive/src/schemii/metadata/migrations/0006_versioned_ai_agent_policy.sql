CREATE TABLE metadata_agent_settings (
    application_id text NOT NULL REFERENCES metadata_applications(application_id),
    agent_id text NOT NULL CHECK (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    current_revision bigint NOT NULL CHECK (current_revision > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (application_id, agent_id)
);

CREATE TABLE metadata_agent_policy_revisions (
    agent_policy_revision_id uuid PRIMARY KEY,
    application_id text NOT NULL,
    agent_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    schema_version integer NOT NULL CHECK (schema_version > 0),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object' AND pg_column_size(policy) <= 65536),
    policy_digest char(64) NOT NULL CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (application_id, agent_id, revision),
    FOREIGN KEY (application_id, agent_id) REFERENCES metadata_agent_settings(application_id, agent_id)
);

ALTER TABLE metadata_agent_settings
    ADD CONSTRAINT metadata_agent_settings_current_revision
    FOREIGN KEY (application_id, agent_id, current_revision)
    REFERENCES metadata_agent_policy_revisions(application_id, agent_id, revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE metadata_agent_policy_capabilities (
    agent_policy_revision_id uuid NOT NULL REFERENCES metadata_agent_policy_revisions(agent_policy_revision_id) ON DELETE RESTRICT,
    capability text NOT NULL CHECK (capability ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    configured_mode text NOT NULL CHECK (configured_mode IN ('disabled', 'every_action', 'once_per_chat', 'automatic')),
    effective_mode text NOT NULL CHECK (effective_mode IN ('disabled', 'every_action', 'once_per_chat', 'automatic')),
    safety_floor text NOT NULL CHECK (safety_floor IN ('disabled', 'every_action', 'once_per_chat', 'automatic')),
    PRIMARY KEY (agent_policy_revision_id, capability)
);

CREATE TABLE metadata_agent_policy_bounds (
    agent_policy_revision_id uuid PRIMARY KEY REFERENCES metadata_agent_policy_revisions(agent_policy_revision_id) ON DELETE RESTRICT,
    rows_disclosed integer CHECK (rows_disclosed IS NULL OR rows_disclosed BETWEEN 1 AND 10000),
    rows_written integer CHECK (rows_written IS NULL OR rows_written BETWEEN 1 AND 10000),
    pages_inspected integer CHECK (pages_inspected IS NULL OR pages_inspected BETWEEN 1 AND 100),
    raw_statements integer CHECK (raw_statements IS NULL OR raw_statements BETWEEN 1 AND 20),
    operation_timeout_ms integer CHECK (operation_timeout_ms IS NULL OR operation_timeout_ms BETWEEN 1000 AND 300000),
    agent_concurrency integer CHECK (agent_concurrency IS NULL OR agent_concurrency BETWEEN 1 AND 16)
);

CREATE TABLE metadata_ai_operation_usage (
    operation_id uuid NOT NULL REFERENCES metadata_operations(operation_id) ON DELETE RESTRICT,
    bound_name text NOT NULL CHECK (bound_name IN ('rowsDisclosed', 'rowsWritten', 'pagesInspected')),
    used bigint NOT NULL CHECK (used >= 0),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array' AND pg_column_size(evidence) <= 65536),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (operation_id, bound_name)
);

ALTER TABLE metadata_policy_versions
    ADD COLUMN agent_policy_revision_id uuid REFERENCES metadata_agent_policy_revisions(agent_policy_revision_id) ON DELETE RESTRICT,
    ADD COLUMN agent_policy_schema_version integer CHECK (agent_policy_schema_version IS NULL OR agent_policy_schema_version > 0),
    ADD CONSTRAINT metadata_chat_agent_policy_link_complete CHECK (
        (agent_policy_revision_id IS NULL) = (agent_policy_schema_version IS NULL)
    );

CREATE FUNCTION metadata_chat_agent_policy_link_valid() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.agent_policy_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM metadata_agent_policy_revisions r
        JOIN metadata_chats c ON c.chat_id = NEW.chat_id
        WHERE r.agent_policy_revision_id = NEW.agent_policy_revision_id
          AND r.application_id = c.application_id
          AND r.schema_version = NEW.agent_policy_schema_version
    ) THEN
        RAISE EXCEPTION 'chat agent policy link is invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER metadata_chat_agent_policy_link_guard
    BEFORE INSERT OR UPDATE ON metadata_policy_versions
    FOR EACH ROW EXECUTE FUNCTION metadata_chat_agent_policy_link_valid();

ALTER TABLE metadata_proposals DROP CONSTRAINT metadata_proposals_state_check;
ALTER TABLE metadata_proposals
    ADD CONSTRAINT metadata_proposals_state_check CHECK (state IN ('ready', 'authorized', 'rejected', 'expired', 'revoked')),
    ADD COLUMN revoked_at timestamptz,
    ADD COLUMN revocation_reason text CHECK (revocation_reason IS NULL OR length(revocation_reason) BETWEEN 1 AND 256),
    ADD COLUMN revocation_evidence jsonb CHECK (
        revocation_evidence IS NULL OR (jsonb_typeof(revocation_evidence) = 'object' AND pg_column_size(revocation_evidence) <= 16384)
    ),
    ADD CONSTRAINT metadata_proposal_revocation_complete CHECK (
        (state = 'revoked') = (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL AND revocation_evidence IS NOT NULL)
    );

ALTER TABLE metadata_query_result_references DROP CONSTRAINT metadata_query_result_references_state_check;
ALTER TABLE metadata_query_result_references
    ADD CONSTRAINT metadata_query_result_references_state_check CHECK (state IN ('ready', 'reserved', 'delivering', 'consumed', 'uncertain', 'expired', 'revoked')),
    ADD COLUMN revoked_at timestamptz,
    ADD COLUMN revocation_reason text CHECK (revocation_reason IS NULL OR length(revocation_reason) BETWEEN 1 AND 256),
    ADD CONSTRAINT metadata_result_revocation_complete CHECK (
        (state = 'revoked') = (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)
    );

CREATE FUNCTION metadata_agent_policy_immutable() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'agent policy revisions are immutable' USING ERRCODE = '23514';
END
$$;
CREATE TRIGGER metadata_agent_policy_revisions_immutable
    BEFORE UPDATE OR DELETE ON metadata_agent_policy_revisions
    FOR EACH ROW EXECUTE FUNCTION metadata_agent_policy_immutable();
CREATE TRIGGER metadata_agent_policy_capabilities_immutable
    BEFORE UPDATE OR DELETE ON metadata_agent_policy_capabilities
    FOR EACH ROW EXECUTE FUNCTION metadata_agent_policy_immutable();
CREATE TRIGGER metadata_agent_policy_bounds_immutable
    BEFORE UPDATE OR DELETE ON metadata_agent_policy_bounds
    FOR EACH ROW EXECUTE FUNCTION metadata_agent_policy_immutable();

ALTER TABLE metadata_agent_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_agent_settings_isolation ON metadata_agent_settings
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());
ALTER TABLE metadata_agent_policy_revisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_agent_policy_revisions_isolation ON metadata_agent_policy_revisions
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());
ALTER TABLE metadata_agent_policy_capabilities ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_agent_policy_capabilities_isolation ON metadata_agent_policy_capabilities USING (EXISTS (
    SELECT 1 FROM metadata_agent_policy_revisions r
    WHERE r.agent_policy_revision_id = metadata_agent_policy_capabilities.agent_policy_revision_id
));
ALTER TABLE metadata_agent_policy_bounds ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_agent_policy_bounds_isolation ON metadata_agent_policy_bounds USING (EXISTS (
    SELECT 1 FROM metadata_agent_policy_revisions r
    WHERE r.agent_policy_revision_id = metadata_agent_policy_bounds.agent_policy_revision_id
));
ALTER TABLE metadata_ai_operation_usage ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_ai_operation_usage_isolation ON metadata_ai_operation_usage USING (EXISTS (
    SELECT 1 FROM metadata_operations o JOIN metadata_chats c USING (chat_id)
    WHERE o.operation_id = metadata_ai_operation_usage.operation_id
      AND c.application_id = metadata_current_application()
));

COMMENT ON COLUMN metadata_agent_policy_bounds.operation_timeout_ms IS
    'NULL inherits PostgreSQL timeout policy; the application does not issue a statement_timeout override.';
COMMENT ON COLUMN metadata_policy_versions.agent_policy_revision_id IS
    'NULL identifies a legacy chat policy whose original semantics must not be reinterpreted as agent settings.';
