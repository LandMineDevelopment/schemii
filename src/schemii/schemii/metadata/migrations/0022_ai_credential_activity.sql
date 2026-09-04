CREATE TABLE metadata.ai_credential_activity (
    owner_id text PRIMARY KEY REFERENCES metadata.users(id) ON DELETE CASCADE,
    last_active_at timestamptz NOT NULL,
    credentials_expired boolean NOT NULL DEFAULT FALSE
);

CREATE INDEX ai_credential_activity_expiry_idx
    ON metadata.ai_credential_activity (last_active_at)
    WHERE NOT credentials_expired;

INSERT INTO metadata.ai_credential_activity (owner_id, last_active_at)
SELECT DISTINCT owner_id, clock_timestamp() FROM metadata.ai_credentials;

COMMENT ON TABLE metadata.ai_credential_activity IS
    'Last interactive application activity; provider refresh and background polling never extend retention.';
