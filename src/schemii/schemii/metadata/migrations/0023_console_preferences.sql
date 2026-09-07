CREATE TABLE schemii.console_preferences (
    owner_id text PRIMARY KEY,
    revision integer NOT NULL CHECK (revision > 1),
    row_page_size integer NOT NULL CHECK (row_page_size BETWEEN 1 AND 1000)
);

COMMENT ON TABLE schemii.console_preferences IS
    'One bounded presentation preference per owner. No SQL, rows, write authority or history.';
