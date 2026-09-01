CREATE TABLE metadata.users (
    id text PRIMARY KEY,
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE metadata.postgres_connections (
    id text PRIMARY KEY CHECK (id ~ '^pg_[0-9a-f]{32}$'),
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    host text NOT NULL CHECK (length(host) BETWEEN 1 AND 255),
    port integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
    database_name text NOT NULL CHECK (
        length(database_name) BETWEEN 1 AND 63
        AND octet_length(database_name) <= 63
    ),
    username text NOT NULL CHECK (
        length(username) BETWEEN 1 AND 63
        AND octet_length(username) <= 63
    ),
    ssl_mode text NOT NULL CHECK (
        ssl_mode IN ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full')
    ),
    connect_timeout integer NOT NULL CHECK (connect_timeout BETWEEN 1 AND 30),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, id)
);

CREATE INDEX postgres_connections_owner_name_idx
ON metadata.postgres_connections (owner_id, lower(name), id);

CREATE TABLE metadata.postgres_connection_credentials (
    connection_id text PRIMARY KEY,
    owner_id text NOT NULL,
    ciphertext bytea NOT NULL CHECK (octet_length(ciphertext) BETWEEN 17 AND 4112),
    nonce bytea NOT NULL CHECK (octet_length(nonce) = 12),
    key_version smallint NOT NULL CHECK (key_version > 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id)
        ON DELETE CASCADE
);

COMMENT ON TABLE metadata.postgres_connections IS
    'Owner-scoped, non-secret PostgreSQL connection profiles.';
COMMENT ON TABLE metadata.postgres_connection_credentials IS
    'Application-encrypted credentials. Plaintext credentials are forbidden.';
