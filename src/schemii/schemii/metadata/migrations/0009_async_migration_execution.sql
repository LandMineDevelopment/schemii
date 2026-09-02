ALTER TABLE schemii.migration_executions
DROP CONSTRAINT migration_execution_lease_pair_check;

ALTER TABLE schemii.migration_executions
ADD CONSTRAINT migration_execution_lease_pair_check CHECK (
    (lease_owner IS NULL) = (lease_expires_at IS NULL)
    AND (status <> 'applying' OR lease_owner IS NOT NULL)
    AND (
        status IN ('reserved', 'applying', 'succeeded')
        OR lease_owner IS NULL
    )
);

DROP INDEX schemii.migration_executions_recovery_idx;

CREATE INDEX migration_executions_work_idx
ON schemii.migration_executions (
    status,
    lease_expires_at,
    created_at,
    id
)
WHERE status IN ('reserved', 'applying', 'succeeded');

COMMENT ON COLUMN schemii.migration_executions.lease_owner IS
    'Private worker token. NULL reserved rows are durable queued work; applying rows are always leased.';
COMMENT ON COLUMN schemii.migration_executions.lease_expires_at IS
    'Lease recovery boundary for claimed execution, reconciliation, or post-commit synchronization work.';
