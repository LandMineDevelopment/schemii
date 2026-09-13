ALTER TABLE schemii.ai_chats ADD COLUMN reasoning_effort text NOT NULL DEFAULT 'default'
    CHECK (reasoning_effort IN ('default', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'));
ALTER TABLE schemii.ai_settings ADD COLUMN default_reasoning_effort text NOT NULL DEFAULT 'default'
    CHECK (default_reasoning_effort IN ('default', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'));
