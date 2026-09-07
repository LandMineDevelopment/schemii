CREATE SCHEMA IF NOT EXISTS schemoo;

CREATE TABLE schemoo.models (
    id text PRIMARY KEY CHECK (id ~ '^model_[0-9a-f]{32}$'),
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    connection_id text NOT NULL,
    database text NOT NULL CHECK (length(database) BETWEEN 1 AND 63),
    namespace text NOT NULL CHECK (length(namespace) BETWEEN 1 AND 63),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    layout_revision integer NOT NULL DEFAULT 1 CHECK (layout_revision > 0),
    explore_revision integer NOT NULL DEFAULT 1 CHECK (explore_revision > 0),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    layout jsonb NOT NULL CHECK (jsonb_typeof(layout) = 'object'),
    explore jsonb NOT NULL CHECK (jsonb_typeof(explore) = 'object'),
    catalog_fingerprint text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT
);

CREATE INDEX schemoo_models_owner_target ON schemoo.models(owner_id, connection_id, database, namespace);
COMMENT ON TABLE schemoo.models IS
    'Current owner-private semantic definitions, layout and Explore inputs. Never query result rows or credentials.';

-- Shared bounded reads need not be owned by a Schemii design workspace.
ALTER TABLE schemii.console_executions ALTER COLUMN workspace_id DROP NOT NULL;
ALTER TABLE schemii.console_executions ALTER COLUMN workspace_revision DROP NOT NULL;
CREATE UNIQUE INDEX console_executions_one_active_console
ON schemii.console_executions(owner_id, console_id)
WHERE workspace_id IS NULL AND status IN ('reserved', 'running');
