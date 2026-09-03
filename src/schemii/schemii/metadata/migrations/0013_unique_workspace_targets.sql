-- One owner has one saved design for an exact saved PostgreSQL account and schema.
-- Prototype duplicates are intentionally discarded; all dependent design/history
-- rows cascade with the removed workspace.
WITH ranked_targets AS (
    SELECT target.workspace_id,
           row_number() OVER (
               PARTITION BY target.owner_id,
                            target.connection_id,
                            target.database_name,
                            target.namespace
               ORDER BY workspace.updated_at DESC,
                        workspace.created_at DESC,
                        workspace.id DESC
           ) AS duplicate_rank
    FROM schemii.workspace_targets AS target
    JOIN schemii.workspaces AS workspace
      ON workspace.owner_id = target.owner_id
     AND workspace.id = target.workspace_id
), duplicate_workspaces AS (
    SELECT workspace_id
    FROM ranked_targets
    WHERE duplicate_rank > 1
)
DELETE FROM schemii.workspaces AS workspace
USING duplicate_workspaces AS duplicate
WHERE workspace.id = duplicate.workspace_id;

ALTER TABLE schemii.workspace_targets
ADD CONSTRAINT workspace_targets_owner_connection_database_namespace_key
UNIQUE (owner_id, connection_id, database_name, namespace);

COMMENT ON CONSTRAINT workspace_targets_owner_connection_database_namespace_key
ON schemii.workspace_targets IS
    'Prevents duplicate personal designs for one exact saved PostgreSQL account and schema.';
