CREATE SCHEMA IF NOT EXISTS schemer;
CREATE TABLE schemer.dashboards (
    id text PRIMARY KEY CHECK (id ~ '^dashboard_[0-9a-f]{32}$'),
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    model_id text NOT NULL CHECK (model_id ~ '^model_[0-9a-f]{32}$'),
    model_revision integer NOT NULL CHECK (model_revision > 0),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    selections jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(selections) = 'object'),
    tiles jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(tiles) = 'array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX schemer_dashboards_owner ON schemer.dashboards(owner_id, created_at, id);
COMMENT ON TABLE schemer.dashboards IS 'Owner-private dashboard configuration; never query rows. Retained if its model is deleted so authored configuration is not lost.';
