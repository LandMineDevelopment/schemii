-- A Schemii-managed PostgreSQL login is owned by the application, never by
-- the administrator who created it or a person who has been granted its use.
INSERT INTO metadata.users (id, display_name)
VALUES ('user_schemii_connection_pool', 'Schemii-managed connections')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE metadata.postgres_connections
    ADD COLUMN ownership text NOT NULL DEFAULT 'user';

ALTER TABLE metadata.postgres_connections
    ADD CONSTRAINT postgres_connections_ownership_matches_owner
    CHECK (
        (ownership = 'schemii' AND owner_id = 'user_schemii_connection_pool')
        OR (ownership = 'user' AND owner_id <> 'user_schemii_connection_pool')
    );

COMMENT ON COLUMN metadata.postgres_connections.ownership IS
    'Explicit credential ownership boundary: personal user or Schemii-managed.';
