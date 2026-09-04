-- Assistant conversations are now physically deleted by the application. Remove
-- prototype-era soft deletes so their messages, proposals, and operations cascade.
DELETE FROM schemii.ai_chats WHERE status = 'deleted';

CREATE INDEX ai_chats_retention
ON schemii.ai_chats (updated_at)
WHERE status <> 'working';

CREATE INDEX ai_turns_chat_created
ON schemii.ai_turns (chat_id, created_at DESC);

CREATE INDEX ai_operations_owner_chat_created
ON schemii.ai_operations (owner_id, chat_id, created_at DESC);

COMMENT ON TABLE schemii.ai_messages IS
    'Bounded conversation text. Answers derived from query rows use an in-memory transient response and store only a privacy notice here.';
