CREATE TABLE schemii.workspace_designs (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    content jsonb NOT NULL DEFAULT '{"tables":[],"relationships":[],"functions":[],"views":[]}'::jsonb,
    fingerprint char(64) NOT NULL DEFAULT '50171e44211a343125cfe9c03b31ec4e989807ab3d42a73f6f1631e090b21198'
        CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(content) = 'object'),
    CHECK (octet_length(content::text) <= 16777216),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_designs_owner_idx
ON schemii.workspace_designs (owner_id, workspace_id);

CREATE TABLE schemii.workspace_design_layouts (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    design_revision integer NOT NULL DEFAULT 0 CHECK (design_revision >= 0),
    objects jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(objects) = 'array'),
    CHECK (octet_length(objects::text) <= 8388608),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_design_layouts_owner_idx
ON schemii.workspace_design_layouts (owner_id, workspace_id);

INSERT INTO schemii.workspace_designs (workspace_id, owner_id)
SELECT id, owner_id
FROM schemii.workspaces
ON CONFLICT (workspace_id) DO NOTHING;

INSERT INTO schemii.workspace_design_layouts (workspace_id, owner_id)
SELECT id, owner_id
FROM schemii.workspaces
ON CONFLICT (workspace_id) DO NOTHING;

COMMENT ON TABLE schemii.workspace_designs IS
    'Canonical database-independent desired schema aggregates with semantic revisions.';
COMMENT ON TABLE schemii.workspace_design_layouts IS
    'Stable desired-object positions only; camera and inspector state remain browser-owned.';
