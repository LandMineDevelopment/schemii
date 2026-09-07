ALTER TABLE schemii.ai_chats DROP CONSTRAINT ai_chats_status_check;
ALTER TABLE schemii.ai_chats ADD CHECK (status IN ('idle','working','waiting_approval','failed','deleted'));
ALTER TABLE schemii.ai_turns DROP CONSTRAINT ai_turns_status_check;
ALTER TABLE schemii.ai_turns ADD CHECK (status IN ('queued','running','waiting_approval','succeeded','failed','cancelled'));
DROP INDEX schemii.ai_turns_one_active_chat;
CREATE UNIQUE INDEX ai_turns_one_active_chat ON schemii.ai_turns(chat_id)
WHERE status IN ('queued','running','waiting_approval');

CREATE TABLE schemii.ai_turn_continuations (
    turn_id text PRIMARY KEY REFERENCES schemii.ai_turns(id) ON DELETE CASCADE,
    state jsonb NOT NULL CHECK (jsonb_typeof(state)='object' AND octet_length(state::text)<=4194304),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE schemii.ai_turn_continuations IS
    'Tool identities and query references for continuation. Query rows and derived result content are forbidden.';

CREATE TABLE schemii.ai_read_runs (
    id text PRIMARY KEY CHECK (id ~ '^arr_[0-9a-f]{32}$'),
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    turn_id text NOT NULL REFERENCES schemii.ai_turns(id) ON DELETE CASCADE,
    operation_id text NOT NULL REFERENCES schemii.ai_operations(id) ON DELETE CASCADE,
    sql text NOT NULL,
    label text NOT NULL,
    execution_id text NOT NULL,
    result_id text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count>=0),
    has_more boolean NOT NULL,
    executed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    rerun_of text REFERENCES schemii.ai_read_runs(id) ON DELETE SET NULL
);
CREATE INDEX ai_read_runs_chat_executed ON schemii.ai_read_runs(chat_id,executed_at DESC);
COMMENT ON TABLE schemii.ai_read_runs IS
    'SQL, execution references and freshness only; result row values are never retained.';
