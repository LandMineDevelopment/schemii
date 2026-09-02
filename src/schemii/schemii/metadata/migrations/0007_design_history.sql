CREATE TABLE schemii.workspace_design_history_entries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    parent_id bigint REFERENCES schemii.workspace_design_history_entries(id) ON DELETE RESTRICT,
    source_design_revision integer NOT NULL CHECK (source_design_revision >= 0),
    operation_kind text NOT NULL CHECK (
        operation_kind IN ('initial', 'edit', 'baseline_reset', 'checkpoint')
    ),
    operation_group_id text CHECK (
        operation_group_id IS NULL OR operation_group_id ~ '^dgrp_[0-9a-f]{32}$'
    ),
    content jsonb NOT NULL CHECK (
        jsonb_typeof(content) = 'object'
        AND octet_length(content::text) <= 16777216
    ),
    fingerprint char(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, workspace_id, source_design_revision),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_design_history_parent_idx
ON schemii.workspace_design_history_entries (owner_id, workspace_id, parent_id);

CREATE TABLE schemii.workspace_design_history_state (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    cursor_id bigint NOT NULL
        REFERENCES schemii.workspace_design_history_entries(id) ON DELETE CASCADE,
    active_tip_id bigint NOT NULL
        REFERENCES schemii.workspace_design_history_entries(id) ON DELETE CASCADE,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE TABLE schemii.workspace_design_history_transitions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('undo', 'redo')),
    from_entry_id bigint NOT NULL
        REFERENCES schemii.workspace_design_history_entries(id) ON DELETE CASCADE,
    to_entry_id bigint NOT NULL
        REFERENCES schemii.workspace_design_history_entries(id) ON DELETE CASCADE,
    design_revision integer NOT NULL CHECK (design_revision > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE TABLE schemii.workspace_design_position_memory (
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    object_id text NOT NULL CHECK (object_id ~ '^[a-z]+_[0-9a-f]{32}$'),
    layer text NOT NULL CHECK (layer IN ('tables', 'views')),
    x double precision NOT NULL CHECK (x BETWEEN -1000000 AND 1000000),
    y double precision NOT NULL CHECK (y BETWEEN -1000000 AND 1000000),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id, workspace_id, object_id),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

WITH roots AS (
    INSERT INTO schemii.workspace_design_history_entries (
        workspace_id, owner_id, parent_id, source_design_revision,
        operation_kind, content, fingerprint
    )
    SELECT workspace_id, owner_id, NULL, revision, 'initial', content, fingerprint
    FROM schemii.workspace_designs
    RETURNING id, workspace_id, owner_id
)
INSERT INTO schemii.workspace_design_history_state (
    workspace_id, owner_id, cursor_id, active_tip_id
)
SELECT workspace_id, owner_id, id, id
FROM roots;

INSERT INTO schemii.workspace_design_position_memory (
    workspace_id, owner_id, object_id, layer, x, y
)
SELECT layout.workspace_id,
       layout.owner_id,
       COALESCE(position->>'objectId', position->>'object_id'),
       position->>'layer',
       (position->>'x')::double precision,
       (position->>'y')::double precision
FROM schemii.workspace_design_layouts AS layout
CROSS JOIN LATERAL jsonb_array_elements(layout.objects) AS position;

COMMENT ON TABLE schemii.workspace_design_history_entries IS
    'Immutable complete desired-design snapshots; active cursor movement never rewrites history.';
COMMENT ON TABLE schemii.workspace_design_history_state IS
    'One durable undo/redo cursor and active branch tip per workspace.';
COMMENT ON TABLE schemii.workspace_design_position_memory IS
    'Last known positions retained across semantic deletion and restoration.';
