CREATE TABLE metadata.ai_credentials (
    owner_id text NOT NULL REFERENCES metadata.users(id) ON DELETE CASCADE,
    credential_id text NOT NULL CHECK (length(credential_id) BETWEEN 1 AND 128),
    provider_id text NOT NULL CHECK (provider_id IN ('openai', 'openai-codex')),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    ciphertext bytea,
    nonce bytea,
    key_version smallint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id, credential_id),
    CHECK (
        (ciphertext IS NULL AND nonce IS NULL AND key_version IS NULL)
        OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND key_version IS NOT NULL
            AND octet_length(ciphertext) BETWEEN 17 AND 65552
            AND octet_length(nonce) = 12 AND key_version > 0)
    )
);

COMMENT ON TABLE metadata.ai_credentials IS
    'Owner-scoped application-encrypted AI credentials; disconnected tombstones fence late login completions. No chat content.';
