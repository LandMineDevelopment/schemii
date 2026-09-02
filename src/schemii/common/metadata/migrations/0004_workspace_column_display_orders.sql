CREATE TABLE schemii.workspace_table_column_orders (
    workspace_id text NOT NULL,
    owner_id text NOT NULL,
    table_name text NOT NULL CHECK (
        length(table_name) BETWEEN 1 AND 63
        AND octet_length(table_name) <= 63
    ),
    column_name text NOT NULL CHECK (
        length(column_name) BETWEEN 1 AND 63
        AND octet_length(column_name) <= 63
    ),
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 1599),
    PRIMARY KEY (workspace_id, table_name, column_name),
    UNIQUE (workspace_id, table_name, ordinal),
    FOREIGN KEY (owner_id, workspace_id)
        REFERENCES schemii.workspaces(owner_id, id)
        ON DELETE CASCADE
);

CREATE INDEX workspace_table_column_orders_owner_idx
ON schemii.workspace_table_column_orders (
    owner_id, workspace_id, table_name, ordinal
);

COMMENT ON TABLE schemii.workspace_table_column_orders IS
    'Presentation-only live column order; PostgreSQL attnum remains authoritative physical order.';
