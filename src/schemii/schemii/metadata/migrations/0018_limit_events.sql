CREATE TABLE metadata.limit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    owner_id text REFERENCES metadata.users(id) ON DELETE SET NULL,
    request_id text CHECK (request_id IS NULL OR length(request_id) BETWEEN 1 AND 128),
    request_method varchar(16),
    request_path text CHECK (request_path IS NULL OR length(request_path) <= 1024),
    workspace_id text,
    connection_id text,
    error_code text NOT NULL CHECK (length(error_code) BETWEEN 1 AND 128),
    resource text NOT NULL CHECK (length(resource) BETWEEN 1 AND 128),
    limit_name text NOT NULL CHECK (length(limit_name) BETWEEN 1 AND 128),
    configured_limit numeric NOT NULL CHECK (configured_limit >= 0),
    observed_value numeric CHECK (observed_value IS NULL OR observed_value >= 0),
    outcome text NOT NULL CHECK (outcome IN ('rejected', 'evicted'))
);

CREATE INDEX limit_events_occurred_at_idx
ON metadata.limit_events (occurred_at DESC, id DESC);

CREATE INDEX limit_events_owner_occurred_at_idx
ON metadata.limit_events (owner_id, occurred_at DESC, id DESC);

COMMENT ON TABLE metadata.limit_events IS
    'Privacy-safe operational events for configured limits; SQL and row data are forbidden.';
