ALTER TABLE schemii.workspaces
ADD COLUMN mode text NOT NULL DEFAULT 'design'
CHECK (mode IN ('design', 'live'));

UPDATE schemii.workspaces AS workspace
SET mode = 'live'
WHERE EXISTS (
    SELECT 1
    FROM schemii.workspace_targets AS target
    WHERE target.workspace_id = workspace.id
);

CREATE TABLE schemii.workspace_design_imports (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    catalog_fingerprint char(64) NOT NULL
        CHECK (catalog_fingerprint ~ '^[0-9a-f]{64}$'),
    catalog_captured_at timestamptz NOT NULL,
    complete boolean NOT NULL,
    imported_objects jsonb NOT NULL,
    issues jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(imported_objects) = 'object'),
    CHECK (jsonb_typeof(issues) = 'array'),
    CHECK (octet_length(imported_objects::text) <= 65536),
    CHECK (octet_length(issues::text) <= 4194304),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_design_imports_owner_idx
ON schemii.workspace_design_imports (owner_id, workspace_id);

COMMENT ON COLUMN schemii.workspaces.mode IS
    'Design workspaces are editable and may be detached or targeted; live workspaces inspect PostgreSQL directly.';
COMMENT ON TABLE schemii.workspace_design_imports IS
    'Immutable source snapshot provenance and lossiness report for designs created by PostgreSQL import.';
