-- Console result values are transient data-plane state. They must never be
-- copied into Schemii's durable metadata database.
DROP TABLE IF EXISTS schemii.console_result_cursors;

ALTER TABLE schemii.console_results
    DROP COLUMN IF EXISTS rows_document,
    ADD COLUMN IF NOT EXISTS row_count bigint CHECK (row_count >= 0),
    ADD COLUMN IF NOT EXISTS replayable boolean NOT NULL DEFAULT false;

CREATE TABLE schemii.console_query_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    sql text NOT NULL CHECK (
        char_length(sql) > 0
        AND octet_length(sql) <= 20971520
    ),
    ran_at timestamptz NOT NULL,
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE
);

CREATE INDEX console_query_history_owner_workspace_ran
ON schemii.console_query_history (owner_id, workspace_id, ran_at DESC, id DESC);

COMMENT ON TABLE schemii.console_results IS
    'Result shape and lifecycle metadata only; raw PostgreSQL row values stay outside metadata.';
COMMENT ON TABLE schemii.console_query_history IS
    'Bounded per-user replay history containing only SQL text and the time it ran.';
