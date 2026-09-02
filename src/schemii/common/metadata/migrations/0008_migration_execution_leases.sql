ALTER TABLE schemii.migration_executions
ADD COLUMN lease_owner text,
ADD COLUMN lease_expires_at timestamptz;

UPDATE schemii.migration_executions
SET lease_owner = 'mls_' || md5(id || updated_at::text),
    lease_expires_at = updated_at
WHERE status IN ('reserved', 'applying');

ALTER TABLE schemii.migration_executions
ADD CONSTRAINT migration_execution_lease_pair_check CHECK (
    (status IN ('reserved', 'applying')) =
    (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
),
ADD CONSTRAINT migration_execution_lease_owner_check CHECK (
    lease_owner IS NULL OR lease_owner ~ '^mls_[0-9a-f]{32}$'
);

CREATE INDEX migration_executions_recovery_idx
ON schemii.migration_executions (lease_expires_at, id)
WHERE status IN ('reserved', 'applying');

COMMENT ON COLUMN schemii.migration_executions.lease_owner IS
    'Private worker-attempt token required for active execution transitions.';
COMMENT ON COLUMN schemii.migration_executions.lease_expires_at IS
    'Earliest time an abandoned reserved or applying execution may be reconciled.';
