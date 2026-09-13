CREATE TABLE schemii.bulk_jobs (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    document jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id) REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE
);
CREATE INDEX bulk_jobs_owner_workspace ON schemii.bulk_jobs(owner_id, workspace_id);
