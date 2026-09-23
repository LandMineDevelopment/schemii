-- Add an installation-owned ChatGPT Codex OAuth credential alongside Zen.
-- Both providers retain independent ciphertext, generations, and exact grants.
DO $$
DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'metadata.ai_instance_provider_credentials'::regclass
          AND contype = 'c'
          AND (conname = 'ai_instance_provider_credentials_provider_id_check'
               OR pg_get_constraintdef(oid) LIKE '%octet_length(ciphertext)%')
    LOOP
        EXECUTE format('ALTER TABLE metadata.ai_instance_provider_credentials DROP CONSTRAINT %I',
                       constraint_name);
    END LOOP;
END $$;

ALTER TABLE metadata.ai_instance_provider_credentials
    ADD CONSTRAINT ai_instance_provider_credentials_provider_check
        CHECK (provider_id IN ('opencode', 'openai-codex')),
    ADD CONSTRAINT ai_instance_provider_credentials_envelope_check
        CHECK ((ciphertext IS NULL AND nonce IS NULL AND key_version IS NULL)
            OR (ciphertext IS NOT NULL
                AND octet_length(ciphertext) BETWEEN 17 AND
                    CASE WHEN provider_id = 'opencode' THEN 16400 ELSE 65552 END
                AND nonce IS NOT NULL AND octet_length(nonce) = 12
                AND key_version IS NOT NULL AND key_version > 0));

INSERT INTO metadata.ai_instance_provider_credentials (provider_id)
VALUES ('openai-codex');

CREATE SEQUENCE metadata.ai_instance_provider_grant_revision_seq;
ALTER TABLE metadata.ai_instance_provider_grants
    ADD COLUMN model_id text,
    ADD COLUMN reasoning_effort text,
    ADD COLUMN revision bigint NOT NULL
        DEFAULT nextval('metadata.ai_instance_provider_grant_revision_seq'),
    ADD CONSTRAINT ai_instance_provider_grants_policy_check
        CHECK ((provider_id = 'opencode' AND model_id IS NULL AND reasoning_effort IS NULL)
            OR (provider_id = 'openai-codex'
                AND model_id IS NOT NULL AND length(model_id) BETWEEN 1 AND 128
                AND reasoning_effort IS NOT NULL
                AND length(reasoning_effort) BETWEEN 1 AND 32));

COMMENT ON TABLE metadata.ai_instance_provider_credentials IS
    'Installation-owned encrypted AI provider credentials; no plaintext or user-token references.';
COMMENT ON TABLE metadata.ai_instance_provider_grants IS
    'Exact per-user, product, database AI grants with provider-specific model policy and revision fencing.';
