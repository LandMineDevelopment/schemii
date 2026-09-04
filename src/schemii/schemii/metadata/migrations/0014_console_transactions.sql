CREATE TABLE schemii.console_transactions (
    id text PRIMARY KEY CHECK (id ~ '^ctx_[0-9a-f]{32}$'),
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    console_id text NOT NULL CHECK (console_id ~ '^con_[0-9a-f]{32}$'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    workspace_revision integer NOT NULL CHECK (workspace_revision > 0),
    connection_id text NOT NULL,
    connection_revision integer NOT NULL CHECK (connection_revision > 0),
    database_name text NOT NULL,
    namespace text NOT NULL,
    backend_pid integer NOT NULL CHECK (backend_pid > 0),
    status text NOT NULL CHECK (
        status IN ('open', 'committed', 'rolled_back', 'expired', 'failed', 'uncertain')
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    maximum_expires_at timestamptz NOT NULL,
    CHECK (expires_at <= maximum_expires_at),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE,
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX console_transactions_one_active_workspace
ON schemii.console_transactions (owner_id, workspace_id)
WHERE status IN ('open', 'failed');

CREATE INDEX console_transactions_expiry_idx
ON schemii.console_transactions (expires_at)
WHERE status IN ('open', 'failed');

ALTER TABLE schemii.console_executions
ADD COLUMN transaction_id text
    REFERENCES schemii.console_transactions(id) ON DELETE RESTRICT;

CREATE INDEX console_executions_transaction_idx
ON schemii.console_executions (transaction_id, created_at)
WHERE transaction_id IS NOT NULL;

COMMENT ON TABLE schemii.console_transactions IS
    'Durable receipts for process-bound human SQL transactions; open receipts expire after process interruption.';
COMMENT ON COLUMN schemii.console_executions.transaction_id IS
    'Owning explicit transaction when this execution ran on a retained PostgreSQL connection.';
