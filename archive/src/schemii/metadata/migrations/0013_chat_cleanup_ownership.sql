-- Usage is operation-owned accounting evidence. Retain it for the complete
-- operation lifetime, then remove it in the same transaction as its operation.
ALTER TABLE metadata_ai_operation_usage
    DROP CONSTRAINT metadata_ai_operation_usage_operation_id_fkey,
    ADD CONSTRAINT metadata_ai_operation_usage_operation_id_fkey
        FOREIGN KEY (operation_id)
        REFERENCES metadata_operations(operation_id)
        ON DELETE CASCADE
        NOT VALID;

ALTER TABLE metadata_ai_operation_usage
    VALIDATE CONSTRAINT metadata_ai_operation_usage_operation_id_fkey;

COMMENT ON CONSTRAINT metadata_ai_operation_usage_operation_id_fkey
    ON metadata_ai_operation_usage IS
    'Usage evidence is retained with its operation and atomically removed only when that operation is deleted.';

COMMENT ON TABLE metadata_authority_transitions IS
    'Durable application-scoped audit evidence. Aggregate IDs intentionally have no foreign key so transitions outlive aggregate cleanup.';
