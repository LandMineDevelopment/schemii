-- Semantic-model ownership stays with the author; its source credential may
-- belong to an organization administrator and be granted through a role.
ALTER TABLE schemoo.models ADD COLUMN connection_owner_id text;
UPDATE schemoo.models SET connection_owner_id = owner_id;
ALTER TABLE schemoo.models ALTER COLUMN connection_owner_id SET NOT NULL;
ALTER TABLE schemoo.models DROP CONSTRAINT models_owner_id_connection_id_fkey;
ALTER TABLE schemoo.models ADD CONSTRAINT models_connection_identity_fkey
    FOREIGN KEY (connection_owner_id, connection_id)
    REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE RESTRICT;
CREATE INDEX schemoo_models_connection_identity_idx
    ON schemoo.models(connection_owner_id, connection_id);
