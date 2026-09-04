ALTER TABLE schemii.console_saved_queries
ADD COLUMN starter boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN schemii.console_saved_queries.starter IS
    'When true, initialize this saved query as a browser-local tab on first use of the workspace.';
