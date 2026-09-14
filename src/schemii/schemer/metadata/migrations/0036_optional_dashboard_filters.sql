ALTER TABLE schemer.dashboards
    ADD COLUMN optional_filters jsonb NOT NULL DEFAULT '[]'
    CHECK (jsonb_typeof(optional_filters) = 'array');

COMMENT ON COLUMN schemer.dashboards.optional_filters IS
    'Model-authored optional scope IDs that this dashboard exposes for viewer activation.';
