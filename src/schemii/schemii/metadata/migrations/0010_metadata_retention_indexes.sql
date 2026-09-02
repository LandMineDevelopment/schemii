CREATE INDEX migration_plans_retention_idx
ON schemii.migration_plans (owner_id, expires_at)
WHERE status IN ('reviewable', 'blocked', 'expired');

CREATE INDEX workspace_design_history_transitions_retention_idx
ON schemii.workspace_design_history_transitions (
    owner_id,
    workspace_id,
    id DESC
);

COMMENT ON INDEX schemii.migration_plans_retention_idx IS
    'Supports opportunistic cleanup of expired, unclaimed migration review snapshots.';
COMMENT ON INDEX schemii.workspace_design_history_transitions_retention_idx IS
    'Supports bounded per-workspace undo and redo transition retention.';
