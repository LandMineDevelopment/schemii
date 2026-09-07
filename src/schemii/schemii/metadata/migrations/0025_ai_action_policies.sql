ALTER TABLE schemii.ai_operations DROP CONSTRAINT ai_operations_kind_check;
ALTER TABLE schemii.ai_operations ADD CONSTRAINT ai_operations_kind_check CHECK
    (kind IN ('design_change','migration_review','migration_apply','design_history','sql_write','data_read','console_script','navigation'));
-- Per-user and per-chat approval policies live in the existing bounded
-- capabilities JSON. New fields default to disabled access / required approval.
