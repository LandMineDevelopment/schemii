CREATE TABLE schemii.console_executions (
    id text PRIMARY KEY CHECK (id ~ '^cex_[0-9a-f]{32}$'),
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    console_id text NOT NULL CHECK (console_id ~ '^con_[0-9a-f]{32}$'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    workspace_revision integer NOT NULL CHECK (workspace_revision > 0),
    connection_id text NOT NULL,
    connection_revision integer NOT NULL CHECK (connection_revision > 0),
    database_name text NOT NULL,
    namespace text NOT NULL,
    status text NOT NULL CHECK (
        status IN ('reserved', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    statements jsonb NOT NULL CHECK (
        jsonb_typeof(statements) = 'array'
        AND octet_length(statements::text) <= 4194304
    ),
    page_size integer NOT NULL CHECK (page_size BETWEEN 1 AND 1000),
    completed_statement_indexes integer[] NOT NULL DEFAULT '{}',
    backend_pid integer,
    cancel_requested boolean NOT NULL DEFAULT false,
    error_code text,
    error_message text,
    error_statement_index integer CHECK (error_statement_index >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE,
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX console_executions_one_active_workspace
ON schemii.console_executions (owner_id, workspace_id)
WHERE status IN ('reserved', 'running');

CREATE INDEX console_executions_retention_idx
ON schemii.console_executions (updated_at)
WHERE status NOT IN ('reserved', 'running');

CREATE TABLE schemii.console_results (
    id text PRIMARY KEY CHECK (id ~ '^res_[0-9a-f]{32}$'),
    execution_id text NOT NULL
        REFERENCES schemii.console_executions(id) ON DELETE CASCADE,
    statement_index integer NOT NULL CHECK (statement_index >= 0),
    command text NOT NULL,
    columns_document jsonb NOT NULL CHECK (jsonb_typeof(columns_document) = 'array'),
    rows_document jsonb NOT NULL CHECK (
        jsonb_typeof(rows_document) = 'array'
        AND octet_length(rows_document::text) <= 4194304
    ),
    truncated boolean NOT NULL,
    expires_at timestamptz NOT NULL,
    closed_at timestamptz,
    UNIQUE (execution_id, statement_index)
);

CREATE TABLE schemii.console_result_cursors (
    token text PRIMARY KEY CHECK (token ~ '^crc_[0-9a-f]{32}$'),
    result_id text NOT NULL REFERENCES schemii.console_results(id) ON DELETE CASCADE,
    row_offset integer NOT NULL CHECK (row_offset > 0),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);

COMMENT ON TABLE schemii.console_executions IS
    'Durable owner/workspace receipts for bounded human read-only SQL execution.';
COMMENT ON TABLE schemii.console_results IS
    'Bounded retained result snapshots; pages never rerun the original query.';
COMMENT ON TABLE schemii.console_result_cursors IS
    'Single-use opaque continuation authority for retained Console result pages.';
