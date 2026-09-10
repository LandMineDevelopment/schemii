-- Shared chat storage introduced by Schemoo in the unified migration history.
CREATE TABLE metadata.ai_conversations (
    id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    product text NOT NULL,
    subject_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object')
);
CREATE INDEX ai_conversations_owner_subject ON metadata.ai_conversations(owner_id, product, subject_id);
CREATE INDEX ai_conversations_retention ON metadata.ai_conversations(updated_at);
COMMENT ON TABLE metadata.ai_conversations IS 'Bounded product chat text, permissions, structured proposals and operation receipts. Never tool result rows or native provider contexts.';
CREATE TABLE metadata.ai_preferences (
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    product text NOT NULL,
    document jsonb NOT NULL,
    PRIMARY KEY(owner_id, product)
);
