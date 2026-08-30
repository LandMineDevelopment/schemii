ALTER TABLE metadata_chats
    ADD COLUMN display_title text NOT NULL DEFAULT 'Untitled chat'
        CHECK (length(display_title) BETWEEN 1 AND 256);
ALTER TABLE metadata_chats ALTER COLUMN display_title DROP DEFAULT;

CREATE INDEX metadata_chats_external_session
    ON metadata_chats(application_id, external_session_id)
    WHERE external_session_id IS NOT NULL;
