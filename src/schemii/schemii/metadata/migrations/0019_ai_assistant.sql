CREATE TABLE schemii.ai_settings (
    owner_id text PRIMARY KEY REFERENCES metadata.users(id) ON DELETE CASCADE,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    enabled boolean NOT NULL DEFAULT true,
    default_provider_id text,
    default_model_id text,
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(capabilities) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE schemii.ai_chats (
    id text PRIMARY KEY CHECK (id ~ '^chat_[0-9a-f]{32}$'),
    owner_id text NOT NULL,
    workspace_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 80),
    provider_id text NOT NULL CHECK (char_length(provider_id) BETWEEN 1 AND 128),
    model_id text NOT NULL CHECK (char_length(model_id) BETWEEN 1 AND 256),
    external_session_id text,
    capabilities jsonb NOT NULL CHECK (jsonb_typeof(capabilities) = 'object'),
    status text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'working', 'failed', 'deleted')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id) ON DELETE CASCADE
);

CREATE INDEX ai_chats_owner_workspace_updated
ON schemii.ai_chats (owner_id, workspace_id, updated_at DESC)
WHERE status <> 'deleted';

CREATE TABLE schemii.ai_turns (
    id text PRIMARY KEY CHECK (id ~ '^turn_[0-9a-f]{32}$'),
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    result_context_operation_id text,
    result_context_rerun boolean NOT NULL DEFAULT false,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    completed_at timestamptz
);

CREATE UNIQUE INDEX ai_turns_one_active_chat
ON schemii.ai_turns (chat_id)
WHERE status IN ('queued', 'running');

CREATE TABLE schemii.ai_messages (
    id text PRIMARY KEY CHECK (id ~ '^msg_[0-9a-f]{32}$'),
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    turn_id text REFERENCES schemii.ai_turns(id) ON DELETE SET NULL,
    sequence bigint NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text text NOT NULL CHECK (octet_length(text) <= 1048576),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (chat_id, sequence)
);

CREATE INDEX ai_messages_chat_sequence ON schemii.ai_messages (chat_id, sequence);

CREATE TABLE schemii.ai_proposals (
    id text PRIMARY KEY CHECK (id ~ '^prop_[0-9a-f]{32}$'),
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    turn_id text NOT NULL REFERENCES schemii.ai_turns(id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    capability text NOT NULL,
    action_type text NOT NULL,
    summary text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 2048),
    action jsonb NOT NULL CHECK (jsonb_typeof(action) = 'object' AND octet_length(action::text) <= 4194304),
    digest char(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    expected_workspace_revision integer NOT NULL CHECK (expected_workspace_revision > 0),
    expected_design_revision integer NOT NULL CHECK (expected_design_revision >= 0),
    destructive boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'executing', 'succeeded', 'failed', 'dismissed', 'expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

CREATE INDEX ai_proposals_chat_created ON schemii.ai_proposals (chat_id, created_at DESC);

CREATE TABLE schemii.ai_operations (
    id text PRIMARY KEY CHECK (id ~ '^aop_[0-9a-f]{32}$'),
    proposal_id text NOT NULL UNIQUE REFERENCES schemii.ai_proposals(id) ON DELETE CASCADE,
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    kind text NOT NULL CHECK (kind IN ('design_change', 'migration_review', 'data_read', 'console_script', 'navigation')),
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'uncertain')),
    resource_kind text,
    resource_id text,
    result_summary jsonb CHECK (result_summary IS NULL OR (jsonb_typeof(result_summary) = 'object' AND octet_length(result_summary::text) <= 1048576)),
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE schemii.ai_operations IS
    'Authority receipts and references only. Query row values are forbidden from result_summary.';

CREATE TABLE schemii.ai_activity_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id text NOT NULL REFERENCES schemii.ai_chats(id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('status', 'message', 'proposal', 'operation', 'error', 'freshness')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX ai_activity_chat_id ON schemii.ai_activity_events (chat_id, id);

COMMENT ON TABLE schemii.ai_activity_events IS
    'Bounded assistant lifecycle events. SQL result row values are forbidden.';
