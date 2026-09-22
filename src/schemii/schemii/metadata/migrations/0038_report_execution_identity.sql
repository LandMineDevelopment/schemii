-- A shared report's actor owns the execution; its role owns access to a
-- separately owned credential profile. Existing private executions retain both.
ALTER TABLE schemii.console_executions ADD COLUMN connection_owner_id text;
UPDATE schemii.console_executions SET connection_owner_id = owner_id;
ALTER TABLE schemii.console_executions ALTER COLUMN connection_owner_id SET NOT NULL;
ALTER TABLE schemii.console_executions
    DROP CONSTRAINT console_executions_owner_id_connection_id_fkey;
ALTER TABLE schemii.console_executions
    ADD CONSTRAINT console_executions_connection_identity_fkey
    FOREIGN KEY (connection_owner_id, connection_id)
    REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT;
ALTER TABLE schemii.console_executions
    ADD CONSTRAINT console_executions_actor_fkey
    FOREIGN KEY (owner_id) REFERENCES metadata.users(id) ON DELETE CASCADE;
ALTER TABLE schemii.console_executions
    ADD CONSTRAINT console_executions_workspace_private_identity
    CHECK (workspace_id IS NULL OR connection_owner_id = owner_id);
