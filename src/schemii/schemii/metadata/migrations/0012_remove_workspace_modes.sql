DELETE FROM schemii.workspaces
WHERE mode = 'live';

ALTER TABLE schemii.workspaces
DROP COLUMN mode;

COMMENT ON TABLE schemii.workspaces IS
    'Editable schema workspaces; an optional immutable target distinguishes local and PostgreSQL-backed designs.';
