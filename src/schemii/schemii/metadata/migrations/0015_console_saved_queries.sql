CREATE TABLE schemii.console_saved_queries (
    id text PRIMARY KEY CHECK (id ~ '^sq_[0-9a-f]{32}$'),
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    sql text NOT NULL CHECK (
        char_length(sql) > 0
        AND octet_length(sql) <= 1048576
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX console_saved_queries_owner_workspace_name
ON schemii.console_saved_queries (owner_id, workspace_id, lower(name));

CREATE INDEX console_saved_queries_workspace_updated
ON schemii.console_saved_queries (owner_id, workspace_id, updated_at DESC);

COMMENT ON TABLE schemii.console_saved_queries IS
    'Per-user named SQL kept in Schemii metadata, never written into a connected target database.';
