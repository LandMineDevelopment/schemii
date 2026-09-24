-- One instance-owned OpenCode Zen key. The application encryption key stays in
-- the launcher's private secret file; only authenticated ciphertext is stored.
CREATE TABLE metadata.ai_instance_provider_credentials (
    provider_id text PRIMARY KEY CHECK (provider_id = 'opencode'),
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    ciphertext bytea,
    nonce bytea,
    key_version smallint,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((ciphertext IS NULL AND nonce IS NULL AND key_version IS NULL)
        OR (ciphertext IS NOT NULL AND octet_length(ciphertext) BETWEEN 17 AND 16400
            AND nonce IS NOT NULL AND octet_length(nonce) = 12
            AND key_version IS NOT NULL AND key_version > 0))
);
INSERT INTO metadata.ai_instance_provider_credentials (provider_id) VALUES ('opencode');

-- NULL/NULL is the explicit detached Schemii workspace scope. It does not
-- match a database connection and grants never imply a wildcard.
CREATE TABLE metadata.ai_instance_provider_grants (
    provider_id text NOT NULL REFERENCES metadata.ai_instance_provider_credentials(provider_id) ON DELETE CASCADE,
    user_id text NOT NULL REFERENCES metadata.auth_accounts(user_id) ON DELETE CASCADE,
    product text NOT NULL CHECK (product IN ('schemii', 'schemoo', 'schemer')),
    connection_owner_id text,
    connection_id text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((connection_owner_id IS NULL AND connection_id IS NULL AND product = 'schemii')
        OR (connection_owner_id IS NOT NULL AND connection_id IS NOT NULL)),
    FOREIGN KEY (connection_owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ai_instance_provider_grants_connected_unique
    ON metadata.ai_instance_provider_grants
    (provider_id, user_id, product, connection_owner_id, connection_id)
    WHERE connection_id IS NOT NULL;
CREATE UNIQUE INDEX ai_instance_provider_grants_detached_unique
    ON metadata.ai_instance_provider_grants (provider_id, user_id, product)
    WHERE connection_id IS NULL;
CREATE INDEX ai_instance_provider_grants_user_lookup
    ON metadata.ai_instance_provider_grants (user_id, product, connection_owner_id, connection_id);

COMMENT ON TABLE metadata.ai_instance_provider_credentials IS
    'Instance-owned encrypted Zen API key; plaintext and user-owned tokens are forbidden.';
COMMENT ON TABLE metadata.ai_instance_provider_grants IS
    'Exact user, application, and database identity permission for the instance AI key.';
