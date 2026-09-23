-- Workspace ownership and PostgreSQL credential ownership are independent.
ALTER TABLE schemii.workspace_targets ADD COLUMN connection_owner_id text;
UPDATE schemii.workspace_targets SET connection_owner_id = owner_id;
ALTER TABLE schemii.workspace_targets ALTER COLUMN connection_owner_id SET NOT NULL;
ALTER TABLE schemii.workspace_targets
    DROP CONSTRAINT workspace_targets_owner_id_connection_id_fkey;
ALTER TABLE schemii.workspace_targets
    ADD CONSTRAINT workspace_targets_connection_identity_fkey
    FOREIGN KEY (connection_owner_id, connection_id)
    REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT;
CREATE INDEX workspace_targets_connection_identity_idx
    ON schemii.workspace_targets (connection_owner_id, connection_id, workspace_id);

ALTER TABLE schemii.workspace_schema_baselines ADD COLUMN connection_owner_id text;
UPDATE schemii.workspace_schema_baselines SET connection_owner_id = owner_id;
ALTER TABLE schemii.workspace_schema_baselines ALTER COLUMN connection_owner_id SET NOT NULL;
ALTER TABLE schemii.workspace_schema_baselines
    DROP CONSTRAINT workspace_schema_baselines_owner_id_connection_id_fkey;
ALTER TABLE schemii.workspace_schema_baselines
    ADD CONSTRAINT workspace_schema_baselines_connection_identity_fkey
    FOREIGN KEY (connection_owner_id, connection_id)
    REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT;

ALTER TABLE schemii.migration_plans ADD COLUMN connection_owner_id text;
-- The historical plan snapshot trigger treats every column change as an
-- immutable review edit. Suspend it only for this transactional backfill.
DROP TRIGGER migration_plan_snapshot_guard ON schemii.migration_plans;
UPDATE schemii.migration_plans SET connection_owner_id = owner_id;
CREATE TRIGGER migration_plan_snapshot_guard
BEFORE UPDATE ON schemii.migration_plans
FOR EACH ROW EXECUTE FUNCTION schemii.migration_plan_snapshot_immutable();
ALTER TABLE schemii.migration_plans ALTER COLUMN connection_owner_id SET NOT NULL;

ALTER TABLE schemii.console_transactions ADD COLUMN connection_owner_id text;
UPDATE schemii.console_transactions SET connection_owner_id = owner_id;
ALTER TABLE schemii.console_transactions ALTER COLUMN connection_owner_id SET NOT NULL;
ALTER TABLE schemii.console_transactions
    DROP CONSTRAINT console_transactions_owner_id_connection_id_fkey;
ALTER TABLE schemii.console_transactions
    ADD CONSTRAINT console_transactions_connection_identity_fkey
    FOREIGN KEY (connection_owner_id, connection_id)
    REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT;

ALTER TABLE schemii.console_executions
    DROP CONSTRAINT console_executions_workspace_private_identity;
