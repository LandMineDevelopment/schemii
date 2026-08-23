ALTER TABLE metadata_chats
    ADD COLUMN conversation_title text
        CHECK (
            conversation_title IS NULL
            OR (
                length(conversation_title) BETWEEN 1 AND 80
                AND octet_length(conversation_title) <= 80
                AND conversation_title = btrim(conversation_title)
                AND conversation_title !~ '[[:cntrl:]]'
            )
        );
