CREATE SCHEMA IF NOT EXISTS schemii;

CREATE TABLE schemii.workspaces (
    id text PRIMARY KEY CHECK (id ~ '^ws_[0-9a-f]{32}$'),
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, id)
);

CREATE INDEX workspaces_owner_created_idx
ON schemii.workspaces (owner_id, created_at, id);

CREATE TABLE schemii.workspace_targets (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL,
    connection_id text NOT NULL,
    database_name text NOT NULL CHECK (
        length(database_name) BETWEEN 1 AND 63
        AND octet_length(database_name) <= 63
    ),
    namespace text NOT NULL CHECK (
        length(namespace) BETWEEN 1 AND 63
        AND octet_length(namespace) <= 63
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX workspace_targets_owner_connection_idx
ON schemii.workspace_targets (owner_id, connection_id, workspace_id);

CREATE TABLE schemii.workspace_table_positions (
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    table_name text NOT NULL CHECK (
        length(table_name) BETWEEN 1 AND 63
        AND octet_length(table_name) <= 63
    ),
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 9999),
    x double precision NOT NULL CHECK (x BETWEEN -1000000 AND 1000000),
    y double precision NOT NULL CHECK (y BETWEEN -1000000 AND 1000000),
    PRIMARY KEY (workspace_id, table_name),
    UNIQUE (workspace_id, ordinal),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_table_positions_owner_idx
ON schemii.workspace_table_positions (owner_id, workspace_id, ordinal);

COMMENT ON TABLE schemii.workspaces IS
    'Durable owner-scoped workspaces; target rows are optional for detached designs.';
COMMENT ON TABLE schemii.workspace_targets IS
    'Optional exact PostgreSQL connection, database, and namespace bindings.';
COMMENT ON TABLE schemii.workspace_table_positions IS
    'Ordered canvas positions only; live PostgreSQL catalog objects are not copied.';
