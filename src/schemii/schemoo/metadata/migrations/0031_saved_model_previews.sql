CREATE UNIQUE INDEX schemoo_models_owner_id ON schemoo.models(owner_id, id);

CREATE TABLE schemoo.model_previews (
    id text PRIMARY KEY CHECK (id ~ '^preview_[0-9a-f]{32}$'),
    owner_id text NOT NULL,
    model_id text NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 128),
    explore jsonb NOT NULL CHECK (jsonb_typeof(explore) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, model_id) REFERENCES schemoo.models(owner_id, id) ON DELETE CASCADE
);
CREATE INDEX schemoo_model_previews_owner_model ON schemoo.model_previews(owner_id, model_id, created_at, id);
COMMENT ON TABLE schemoo.model_previews IS
    'Owner-private named test query inputs referencing the current semantic model. Never result rows, model copies, catalogs or credentials.';
