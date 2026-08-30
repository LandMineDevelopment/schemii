ALTER TABLE metadata_console_execution_receipts
    DROP CONSTRAINT metadata_console_execution_receipts_state_check;

ALTER TABLE metadata_console_execution_receipts
    ADD CONSTRAINT metadata_console_execution_receipts_state_check
    CHECK (state IN ('reserved', 'running', 'succeeded', 'failed', 'cancelled', 'uncertain'));

COMMENT ON TABLE metadata_console_execution_receipts IS
    'Durable pre-dispatch reservations and operational evidence. SQL text, result rows, credentials, and connection strings are forbidden.';
