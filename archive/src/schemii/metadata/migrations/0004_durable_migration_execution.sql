-- Phase 2 plan rows had no installed execution adapter. Preserve them for audit
-- but never activate them under Phase 3 adapter defaults.
UPDATE metadata_migration_plans SET state = 'expired' WHERE state = 'ready';

ALTER TABLE metadata_migration_plans
    ADD COLUMN adapter_kind text NOT NULL DEFAULT 'full_schema'
        CHECK (adapter_kind IN ('full_schema', 'view_mutation', 'insert_rows')),
    ADD COLUMN source_kind text NOT NULL DEFAULT 'normal'
        CHECK (source_kind IN ('normal', 'ai')),
    ADD COLUMN retain_until timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '30 days'),
    ADD COLUMN private_payload_redacted_at timestamptz;

ALTER TABLE metadata_migration_plans ALTER COLUMN adapter_kind DROP DEFAULT;
ALTER TABLE metadata_migration_plans ALTER COLUMN source_kind DROP DEFAULT;
ALTER TABLE metadata_migration_plans ALTER COLUMN retain_until DROP DEFAULT;

ALTER TABLE metadata_migration_plans
    ADD CONSTRAINT metadata_migration_retention_after_creation CHECK (retain_until > created_at);

CREATE INDEX metadata_migration_plans_retention
    ON metadata_migration_plans(retain_until)
    WHERE private_payload_redacted_at IS NULL;

CREATE OR REPLACE FUNCTION metadata_migration_plan_snapshot_immutable() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - 'state' - 'private_payload' - 'private_payload_redacted_at')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'state' - 'private_payload' - 'private_payload_redacted_at') THEN
        RAISE EXCEPTION 'migration plan review snapshot is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.private_payload IS DISTINCT FROM OLD.private_payload AND
       NOT (OLD.private_payload_redacted_at IS NULL AND NEW.private_payload = '{}'::jsonb AND
            NEW.private_payload_redacted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'migration plan private payload may only be redacted' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

COMMENT ON COLUMN metadata_migration_plans.private_payload IS
    'Strictly validated execution payload; omitted from public APIs and reset to {} after terminal retention.';
COMMENT ON COLUMN metadata_migration_plans.retain_until IS
    'Earliest time at which a terminal plan private payload may be irreversibly redacted.';
