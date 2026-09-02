CREATE TABLE schemii.workspace_schema_baselines (
    id text PRIMARY KEY CHECK (id ~ '^mbl_[0-9a-f]{32}$'),
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    predecessor_id text REFERENCES schemii.workspace_schema_baselines(id),
    connection_id text NOT NULL,
    connection_revision integer NOT NULL CHECK (connection_revision > 0),
    database_name text NOT NULL CHECK (
        length(database_name) BETWEEN 1 AND 63
        AND octet_length(database_name) <= 63
    ),
    namespace text NOT NULL CHECK (
        length(namespace) BETWEEN 1 AND 63
        AND octet_length(namespace) <= 63
    ),
    design_revision integer NOT NULL CHECK (design_revision >= 0),
    design_fingerprint char(64) NOT NULL CHECK (
        design_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    design_content jsonb NOT NULL CHECK (
        jsonb_typeof(design_content) = 'object'
        AND octet_length(design_content::text) <= 16777216
    ),
    catalog_fingerprint char(64) NOT NULL CHECK (
        catalog_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    catalog jsonb NOT NULL CHECK (
        jsonb_typeof(catalog) = 'object'
        AND octet_length(catalog::text) <= 67108864
    ),
    complete boolean NOT NULL,
    issues jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (
        jsonb_typeof(issues) = 'array'
        AND octet_length(issues::text) <= 4194304
    ),
    source text NOT NULL CHECK (
        source IN ('import', 'target_attach', 'drift_reconciliation', 'migration')
    ),
    source_execution_id text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, workspace_id, revision),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE schemii.workspace_schema_baseline_heads (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    baseline_id text NOT NULL UNIQUE
        REFERENCES schemii.workspace_schema_baselines(id) ON DELETE RESTRICT,
    baseline_revision integer NOT NULL CHECK (baseline_revision > 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE TABLE schemii.migration_plans (
    id text PRIMARY KEY CHECK (id ~ '^mpl_[0-9a-f]{32}$'),
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    baseline_id text NOT NULL
        REFERENCES schemii.workspace_schema_baselines(id) ON DELETE RESTRICT,
    baseline_revision integer NOT NULL CHECK (baseline_revision > 0),
    workspace_revision integer NOT NULL CHECK (workspace_revision > 0),
    design_revision integer NOT NULL CHECK (design_revision >= 0),
    design_fingerprint char(64) NOT NULL CHECK (
        design_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    merged_design_fingerprint char(64) NOT NULL CHECK (
        merged_design_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    connection_id text NOT NULL,
    connection_revision integer NOT NULL CHECK (connection_revision > 0),
    database_name text NOT NULL,
    namespace text NOT NULL,
    catalog_fingerprint char(64) NOT NULL CHECK (
        catalog_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    review_document jsonb NOT NULL CHECK (
        jsonb_typeof(review_document) = 'object'
        AND octet_length(review_document::text) <= 33554432
    ),
    authority_document jsonb NOT NULL CHECK (
        jsonb_typeof(authority_document) = 'object'
        AND octet_length(authority_document::text) <= 67108864
    ),
    review_digest char(64) NOT NULL CHECK (review_digest ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (
        status IN ('reviewable', 'blocked', 'expired', 'claimed', 'resolved')
    ),
    complete boolean NOT NULL,
    apply_capable boolean NOT NULL,
    destructive boolean NOT NULL,
    drift_status text NOT NULL CHECK (
        drift_status IN ('none', 'compatible', 'conflicting')
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX migration_plans_workspace_created_idx
ON schemii.migration_plans (owner_id, workspace_id, created_at DESC);

CREATE TABLE schemii.migration_executions (
    id text PRIMARY KEY CHECK (id ~ '^mex_[0-9a-f]{32}$'),
    plan_id text NOT NULL UNIQUE
        REFERENCES schemii.migration_plans(id) ON DELETE RESTRICT,
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    status text NOT NULL CHECK (
        status IN (
            'reserved', 'applying', 'succeeded', 'failed',
            'uncertain', 'reconciliation_required'
        )
    ),
    confirmed_review_digest char(64) NOT NULL CHECK (
        confirmed_review_digest ~ '^[0-9a-f]{64}$'
    ),
    destructive_confirmed boolean NOT NULL,
    external_changes_confirmed boolean NOT NULL,
    completed_step_count integer NOT NULL DEFAULT 0 CHECK (completed_step_count >= 0),
    target_xid text,
    target_identity jsonb CHECK (
        target_identity IS NULL OR jsonb_typeof(target_identity) = 'object'
    ),
    intended_result jsonb CHECK (
        intended_result IS NULL OR jsonb_typeof(intended_result) = 'object'
    ),
    commit_outcome text CHECK (
        commit_outcome IN ('committed', 'rolled_back', 'uncertain')
    ),
    error_code text,
    error_detail jsonb CHECK (
        error_detail IS NULL OR jsonb_typeof(error_detail) = 'object'
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE UNIQUE INDEX migration_executions_one_active_workspace
ON schemii.migration_executions (owner_id, workspace_id)
WHERE status IN ('reserved', 'applying', 'uncertain', 'reconciliation_required');

CREATE TABLE schemii.migration_execution_transitions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id text NOT NULL
        REFERENCES schemii.migration_executions(id) ON DELETE CASCADE,
    from_status text,
    to_status text NOT NULL,
    evidence jsonb CHECK (evidence IS NULL OR jsonb_typeof(evidence) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE schemii.migration_syncs (
    execution_id text PRIMARY KEY
        REFERENCES schemii.migration_executions(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('pending', 'succeeded', 'conflict', 'failed')),
    baseline_id text REFERENCES schemii.workspace_schema_baselines(id),
    receipt jsonb CHECK (receipt IS NULL OR jsonb_typeof(receipt) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE schemii.drift_reconciliations (
    id text PRIMARY KEY CHECK (id ~ '^mdr_[0-9a-f]{32}$'),
    plan_id text NOT NULL UNIQUE
        REFERENCES schemii.migration_plans(id) ON DELETE RESTRICT,
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    review_digest char(64) NOT NULL CHECK (review_digest ~ '^[0-9a-f]{64}$'),
    previous_design_revision integer NOT NULL CHECK (previous_design_revision >= 0),
    design_revision integer NOT NULL CHECK (design_revision > 0),
    previous_baseline_revision integer NOT NULL CHECK (previous_baseline_revision > 0),
    baseline_revision integer NOT NULL CHECK (baseline_revision > 0),
    catalog_fingerprint char(64) NOT NULL CHECK (
        catalog_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    resolutions jsonb NOT NULL CHECK (jsonb_typeof(resolutions) = 'array'),
    external_changes jsonb NOT NULL CHECK (jsonb_typeof(external_changes) = 'array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION schemii.migration_plan_snapshot_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'migration plan review snapshot is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER migration_plan_snapshot_guard
BEFORE UPDATE ON schemii.migration_plans
FOR EACH ROW EXECUTE FUNCTION schemii.migration_plan_snapshot_immutable();

COMMENT ON TABLE schemii.workspace_schema_baselines IS
    'Immutable server-derived PostgreSQL synchronization points for three-way reconciliation.';
COMMENT ON TABLE schemii.migration_plans IS
    'Immutable reviewed plans; the displayed SQL is the exact server-owned SQL executed.';
COMMENT ON TABLE schemii.drift_reconciliations IS
    'Audited server-side incorporation or acknowledgement of external PostgreSQL changes.';
