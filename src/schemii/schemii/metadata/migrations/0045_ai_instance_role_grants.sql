-- AI permissions are scoped to an existing role, application, and exact profile.
-- Multiple Codex model/effort pairs may be assigned within one scope.
DROP INDEX metadata.ai_instance_provider_grants_connected_unique;
DROP INDEX metadata.ai_instance_provider_grants_detached_unique;
CREATE UNIQUE INDEX ai_instance_provider_grants_connected_unique
    ON metadata.ai_instance_provider_grants
    (provider_id, user_id, product, connection_owner_id, connection_id)
    WHERE connection_id IS NOT NULL AND provider_id = 'opencode';
CREATE UNIQUE INDEX ai_instance_provider_grants_detached_unique
    ON metadata.ai_instance_provider_grants (provider_id, user_id, product)
    WHERE connection_id IS NULL AND provider_id = 'opencode';
CREATE UNIQUE INDEX ai_instance_provider_grants_codex_connected_unique
    ON metadata.ai_instance_provider_grants
    (provider_id, user_id, product, connection_owner_id, connection_id, model_id, reasoning_effort)
    WHERE connection_id IS NOT NULL AND provider_id = 'openai-codex';
CREATE UNIQUE INDEX ai_instance_provider_grants_codex_detached_unique
    ON metadata.ai_instance_provider_grants
    (provider_id, user_id, product, model_id, reasoning_effort)
    WHERE connection_id IS NULL AND provider_id = 'openai-codex';

CREATE TABLE metadata.ai_instance_provider_role_grants (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id text NOT NULL REFERENCES metadata.ai_instance_provider_credentials(provider_id) ON DELETE CASCADE,
    role_id text NOT NULL REFERENCES metadata.auth_roles(id) ON DELETE CASCADE,
    product text NOT NULL CHECK (product IN ('schemii', 'schemoo', 'schemer')),
    connection_owner_id text,
    connection_id text,
    model_id text,
    reasoning_effort text,
    revision bigint NOT NULL DEFAULT nextval('metadata.ai_instance_provider_grant_revision_seq'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((connection_owner_id IS NULL AND connection_id IS NULL AND product = 'schemii')
        OR (connection_owner_id IS NOT NULL AND connection_id IS NOT NULL)),
    CHECK ((provider_id = 'opencode' AND model_id IS NULL AND reasoning_effort IS NULL)
        OR (provider_id = 'openai-codex' AND model_id IS NOT NULL
            AND length(model_id) BETWEEN 1 AND 128 AND reasoning_effort IS NOT NULL
            AND length(reasoning_effort) BETWEEN 1 AND 32)),
    FOREIGN KEY (connection_owner_id, connection_id)
        REFERENCES metadata.postgres_connections(owner_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ai_instance_role_grants_connected_unique
    ON metadata.ai_instance_provider_role_grants
    (provider_id, role_id, product, connection_owner_id, connection_id, model_id, reasoning_effort)
    WHERE connection_id IS NOT NULL AND provider_id = 'openai-codex';
CREATE UNIQUE INDEX ai_instance_role_grants_connected_zen_unique
    ON metadata.ai_instance_provider_role_grants
    (provider_id, role_id, product, connection_owner_id, connection_id)
    WHERE connection_id IS NOT NULL AND provider_id = 'opencode';
CREATE UNIQUE INDEX ai_instance_role_grants_detached_unique
    ON metadata.ai_instance_provider_role_grants
    (provider_id, role_id, product, model_id, reasoning_effort)
    WHERE connection_id IS NULL AND provider_id = 'openai-codex';
CREATE UNIQUE INDEX ai_instance_role_grants_detached_zen_unique
    ON metadata.ai_instance_provider_role_grants
    (provider_id, role_id, product)
    WHERE connection_id IS NULL AND provider_id = 'opencode';
CREATE INDEX ai_instance_role_grants_scope_lookup
    ON metadata.ai_instance_provider_role_grants
    (provider_id, product, connection_owner_id, connection_id, role_id);
