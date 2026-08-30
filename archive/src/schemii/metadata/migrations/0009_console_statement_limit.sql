ALTER TABLE metadata_console_settings
    DROP CONSTRAINT metadata_console_settings_statement_limit_check;

ALTER TABLE metadata_console_settings
    ADD CONSTRAINT metadata_console_settings_statement_limit_check
        CHECK (statement_limit BETWEEN 1 AND 100);
