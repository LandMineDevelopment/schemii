ALTER TABLE metadata_proposals
    ADD COLUMN binding jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(binding) = 'object' AND pg_column_size(binding) <= 1048576);
ALTER TABLE metadata_proposals ALTER COLUMN binding DROP DEFAULT;

ALTER TABLE metadata_operation_attempts
    ADD COLUMN lease_expires_at timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '60 seconds');
ALTER TABLE metadata_operation_attempts
    ADD CONSTRAINT metadata_attempt_lease_after_claim CHECK (lease_expires_at >= claimed_at);

ALTER TABLE metadata_migration_executions
    ADD COLUMN reconciliation_status text NOT NULL DEFAULT 'not_required'
        CHECK (reconciliation_status IN ('not_required', 'required', 'reconciling', 'reconciled', 'failed')),
    ADD COLUMN reconciliation_evidence jsonb
        CHECK (reconciliation_evidence IS NULL OR
               (jsonb_typeof(reconciliation_evidence) = 'object' AND pg_column_size(reconciliation_evidence) <= 1048576));

CREATE INDEX metadata_chats_state_updated ON metadata_chats(state, updated_at);
CREATE INDEX metadata_operations_state_updated ON metadata_operations(state, updated_at);
CREATE INDEX metadata_attempts_lease ON metadata_operation_attempts(state, lease_expires_at);
CREATE INDEX metadata_deliveries_state_reserved ON metadata_query_result_deliveries(state, reserved_at);
CREATE INDEX metadata_migration_executions_state_updated ON metadata_migration_executions(state, updated_at);

CREATE FUNCTION metadata_proposal_snapshot_immutable() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (NEW.chat_id, NEW.capability, NEW.policy_revision, NEW.binding, NEW.action, NEW.created_at, NEW.expires_at)
       IS DISTINCT FROM
       (OLD.chat_id, OLD.capability, OLD.policy_revision, OLD.binding, OLD.action, OLD.created_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'proposal authorization snapshot is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER metadata_proposal_snapshot_guard
    BEFORE UPDATE ON metadata_proposals
    FOR EACH ROW EXECUTE FUNCTION metadata_proposal_snapshot_immutable();

CREATE FUNCTION metadata_migration_plan_snapshot_immutable() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) AND NEW.state = OLD.state THEN
        RAISE EXCEPTION 'migration plan review snapshot is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND
       (NEW.plan_id, NEW.application_id, NEW.resource_kind, NEW.resource_id, NEW.resource_revision,
        NEW.layout_token, NEW.profile_id, NEW.database_name, NEW.namespace_name, NEW.profile_fingerprint,
        NEW.connected_target_fingerprint, NEW.live_fingerprint, NEW.desired_fingerprint, NEW.private_payload,
        NEW.review_payload, NEW.review_digest, NEW.destructive, NEW.created_at, NEW.expires_at)
       IS DISTINCT FROM
       (OLD.plan_id, OLD.application_id, OLD.resource_kind, OLD.resource_id, OLD.resource_revision,
        OLD.layout_token, OLD.profile_id, OLD.database_name, OLD.namespace_name, OLD.profile_fingerprint,
        OLD.connected_target_fingerprint, OLD.live_fingerprint, OLD.desired_fingerprint, OLD.private_payload,
        OLD.review_payload, OLD.review_digest, OLD.destructive, OLD.created_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'migration plan review snapshot is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER metadata_migration_plan_snapshot_guard
    BEFORE UPDATE ON metadata_migration_plans
    FOR EACH ROW EXECUTE FUNCTION metadata_migration_plan_snapshot_immutable();
