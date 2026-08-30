CREATE TABLE metadata_console_execution_receipts (
    execution_id uuid PRIMARY KEY,
    application_id text NOT NULL REFERENCES metadata_applications(application_id),
    session_binding_hash char(64) NOT NULL CHECK (session_binding_hash ~ '^[0-9a-f]{64}$'),
    server_id text NOT NULL CHECK (length(server_id) BETWEEN 1 AND 256),
    profile_id text NOT NULL CHECK (length(profile_id) BETWEEN 1 AND 256),
    profile_fingerprint char(64) NOT NULL CHECK (profile_fingerprint ~ '^[0-9a-f]{64}$'),
    database_name text NOT NULL CHECK (length(database_name) BETWEEN 1 AND 63),
    namespace_name text NOT NULL CHECK (length(namespace_name) BETWEEN 1 AND 63),
    console_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('managed_read', 'managed', 'explicit', 'autocommit')),
    settings_revision bigint CHECK (settings_revision IS NULL OR settings_revision > 0),
    state text NOT NULL CHECK (state IN ('succeeded', 'failed', 'cancelled', 'uncertain')),
    outcome text NOT NULL CHECK (outcome IN ('rolled_back', 'committed', 'partial_committed', 'transaction_open', 'not_started', 'uncertain')),
    completed_statement_indexes integer[] NOT NULL DEFAULT '{}',
    error_code text,
    postgres_evidence jsonb,
    reconciliation_evidence jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (postgres_evidence IS NULL OR (jsonb_typeof(postgres_evidence) = 'object' AND pg_column_size(postgres_evidence) <= 16384)),
    CHECK (reconciliation_evidence IS NULL OR (jsonb_typeof(reconciliation_evidence) = 'object' AND pg_column_size(reconciliation_evidence) <= 16384))
);

CREATE INDEX metadata_console_receipts_owner
    ON metadata_console_execution_receipts(application_id, session_binding_hash, server_id, profile_id, execution_id);

ALTER TABLE metadata_console_execution_receipts ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_console_execution_receipts_isolation ON metadata_console_execution_receipts
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());

COMMENT ON TABLE metadata_console_execution_receipts IS
    'Terminal operational evidence only. SQL text, result rows, credentials, and connection strings are forbidden.';

CREATE TABLE metadata_console_settings (
    application_id text PRIMARY KEY REFERENCES metadata_applications(application_id),
    revision bigint NOT NULL CHECK (revision > 0),
    write_intent text NOT NULL CHECK (write_intent IN ('disabled', 'enabled')),
    default_mode text NOT NULL CHECK (default_mode IN ('managed_read', 'managed', 'explicit', 'autocommit')),
    statement_limit integer NOT NULL CHECK (statement_limit BETWEEN 1 AND 20),
    row_page_size integer NOT NULL CHECK (row_page_size BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE metadata_console_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY metadata_console_settings_isolation ON metadata_console_settings
    USING (application_id = metadata_current_application())
    WITH CHECK (application_id = metadata_current_application());

COMMENT ON TABLE metadata_console_settings IS
    'Durable user-owned Console intent scoped to one application. Every field is concrete and non-null; settings do not inherit across applications.';
