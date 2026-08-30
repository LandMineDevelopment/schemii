"""Static, parameterized pg_catalog queries used by the read-only gateway."""

METADATA_QUERY = """
    /* schemii_catalog_metadata */
    SELECT pg_catalog.current_database() AS database,
           pg_catalog.current_setting('server_version') AS server_version,
           pg_catalog.current_setting('server_version_num')::integer AS server_version_num,
           pg_catalog.current_setting('TimeZone') AS server_timezone
"""

CONNECTION_TEST_QUERY = """
    /* schemii_connection_test */
    SELECT pg_catalog.current_database() AS database,
           pg_catalog.current_setting('server_version') AS server_version
"""

NAMESPACE_EXISTS_QUERY = """
    /* schemii_namespace_exists */
    SELECT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS n
        WHERE n.nspname = %s
    ) AS namespace_exists
"""

TABLES_QUERY = """
    /* schemii_catalog_tables */
    SELECT c.relname AS table_name,
           c.relkind AS relation_kind,
           c.relispartition AS is_partition,
           CASE WHEN definition.byte_count <= %s
                THEN definition.value
                ELSE NULL
           END AS partition_key,
           definition.byte_count AS partition_key_bytes
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL (
        SELECT CASE WHEN c.relkind = 'p'
                    THEN pg_catalog.pg_get_partkeydef(c.oid)
                    ELSE NULL
               END AS value
    ) AS raw_definition
    CROSS JOIN LATERAL (
        SELECT raw_definition.value,
               pg_catalog.octet_length(raw_definition.value) AS byte_count
    ) AS definition
    WHERE n.nspname = %s
      AND c.relkind IN ('r', 'p')
    ORDER BY c.relname, c.relkind
    LIMIT %s
"""

COLUMNS_QUERY = """
    /* schemii_catalog_columns */
    SELECT c.relname AS relation_name,
           c.relkind AS relation_kind,
           a.attname AS column_name,
           a.attnum AS ordinal,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
           NOT a.attnotnull AS nullable,
           CASE WHEN default_definition.byte_count <= %s
                THEN default_definition.value
                ELSE NULL
           END AS default_expression,
           default_definition.byte_count AS default_expression_bytes,
           a.attidentity AS identity_kind,
           a.attgenerated AS generated_kind,
           cn.nspname AS collation_schema,
           coll.collname AS collation_name
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute AS a
      ON a.attrelid = c.oid
     AND a.attnum > 0
     AND NOT a.attisdropped
    LEFT JOIN pg_catalog.pg_attrdef AS d
      ON d.adrelid = c.oid
     AND d.adnum = a.attnum
    LEFT JOIN LATERAL (
        SELECT raw_default.value,
               pg_catalog.octet_length(raw_default.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_expr(d.adbin, d.adrelid, true) AS value
        ) AS raw_default
    ) AS default_definition ON d.oid IS NOT NULL
    LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = a.attcollation
    LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
    WHERE n.nspname = %s
      AND c.relkind IN ('r', 'p', 'v', 'm')
    ORDER BY c.relkind, c.relname, a.attnum
    LIMIT %s
"""

CONSTRAINTS_QUERY = """
    /* schemii_catalog_constraints */
    SELECT con.conname AS constraint_name,
           src.relname AS table_name,
           con.contype AS constraint_type,
           ARRAY(
               SELECT att.attname
               FROM pg_catalog.unnest(con.conkey) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_catalog.pg_attribute AS att
                 ON att.attrelid = con.conrelid
                AND att.attnum = key.attnum
               ORDER BY key.ord
           ) AS columns,
           target_ns.nspname AS target_namespace,
           target.relname AS target_table,
           ARRAY(
               SELECT att.attname
               FROM pg_catalog.unnest(con.confkey) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_catalog.pg_attribute AS att
                 ON att.attrelid = con.confrelid
                AND att.attnum = key.attnum
               ORDER BY key.ord
           ) AS target_columns,
           con.confupdtype AS update_action,
           con.confdeltype AS delete_action,
           con.confmatchtype AS match_type,
           con.convalidated AS validated,
           con.condeferrable AS deferrable,
           con.condeferred AS initially_deferred,
           CASE WHEN constraint_definition.byte_count <= %s
                THEN constraint_definition.value
                ELSE NULL
           END AS definition,
           constraint_definition.byte_count AS definition_bytes
    FROM pg_catalog.pg_constraint AS con
    JOIN pg_catalog.pg_class AS src ON src.oid = con.conrelid
    JOIN pg_catalog.pg_namespace AS src_ns ON src_ns.oid = src.relnamespace
    LEFT JOIN pg_catalog.pg_class AS target ON target.oid = con.confrelid
    LEFT JOIN pg_catalog.pg_namespace AS target_ns ON target_ns.oid = target.relnamespace
    CROSS JOIN LATERAL (
        SELECT raw_definition.value,
               pg_catalog.octet_length(raw_definition.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_constraintdef(con.oid, true) AS value
        ) AS raw_definition
    ) AS constraint_definition
    WHERE src_ns.nspname = %s
       AND con.contype IN ('p', 'u', 'f', 'c', 'n', 'x')
    ORDER BY src.relname, con.contype, con.conname
    LIMIT %s
"""

INDEXES_QUERY = """
    /* schemii_catalog_indexes */
    SELECT tab.relname AS table_name,
           idx.relname AS index_name,
           CASE WHEN index_definition.byte_count <= %s
                THEN index_definition.value
                ELSE NULL
           END AS definition,
           index_definition.byte_count AS definition_bytes,
           am.amname AS method,
           i.indisunique AS is_unique,
           i.indisvalid AS is_valid,
           CASE WHEN predicate_definition.byte_count <= %s
                THEN predicate_definition.value
                ELSE NULL
           END AS predicate,
           predicate_definition.byte_count AS predicate_bytes
    FROM pg_catalog.pg_index AS i
    JOIN pg_catalog.pg_class AS idx ON idx.oid = i.indexrelid
    JOIN pg_catalog.pg_class AS tab ON tab.oid = i.indrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = tab.relnamespace
    JOIN pg_catalog.pg_am AS am ON am.oid = idx.relam
    LEFT JOIN pg_catalog.pg_constraint AS con ON con.conindid = i.indexrelid
    CROSS JOIN LATERAL (
        SELECT raw_definition.value,
               pg_catalog.octet_length(raw_definition.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_indexdef(i.indexrelid) AS value
        ) AS raw_definition
    ) AS index_definition
    CROSS JOIN LATERAL (
        SELECT raw_predicate.value,
               pg_catalog.octet_length(raw_predicate.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_expr(i.indpred, i.indrelid, true) AS value
        ) AS raw_predicate
    ) AS predicate_definition
    WHERE n.nspname = %s
      AND tab.relkind IN ('r', 'p')
      AND con.oid IS NULL
    ORDER BY tab.relname, idx.relname
    LIMIT %s
"""

TRIGGERS_QUERY = """
    /* schemii_catalog_triggers */
    SELECT c.relname AS table_name,
           t.tgname AS trigger_name,
           CASE WHEN trigger_definition.byte_count <= %s
                THEN trigger_definition.value
                ELSE NULL
           END AS definition,
           trigger_definition.byte_count AS definition_bytes,
           t.tgenabled AS enabled
    FROM pg_catalog.pg_trigger AS t
    JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL (
        SELECT raw_definition.value,
               pg_catalog.octet_length(raw_definition.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_triggerdef(t.oid, true) AS value
        ) AS raw_definition
    ) AS trigger_definition
    WHERE n.nspname = %s
      AND c.relkind IN ('r', 'p')
      AND NOT t.tgisinternal
    ORDER BY c.relname, t.tgname
    LIMIT %s
"""

FUNCTIONS_QUERY = """
    /* schemii_catalog_functions */
    SELECT p.proname AS function_name,
           p.prokind AS function_kind,
           CASE WHEN function_definition.identity_arguments_bytes <= %s
                THEN function_definition.identity_arguments
                ELSE NULL
           END AS identity_arguments,
           function_definition.identity_arguments_bytes,
           CASE WHEN function_definition.arguments_bytes <= %s
                THEN function_definition.arguments
                ELSE NULL
           END AS arguments,
           function_definition.arguments_bytes,
           CASE WHEN function_definition.return_type_bytes <= %s
                THEN function_definition.return_type
                ELSE NULL
           END AS return_type,
           function_definition.return_type_bytes,
           lang.lanname AS language,
           CASE WHEN function_definition.definition_bytes <= %s
                THEN function_definition.definition
                ELSE NULL
           END AS definition,
           function_definition.definition_bytes
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
    JOIN pg_catalog.pg_language AS lang ON lang.oid = p.prolang
    CROSS JOIN LATERAL (
        SELECT raw_definition.*,
               pg_catalog.octet_length(raw_definition.identity_arguments)
                   AS identity_arguments_bytes,
               pg_catalog.octet_length(raw_definition.arguments) AS arguments_bytes,
               pg_catalog.octet_length(raw_definition.return_type) AS return_type_bytes,
               pg_catalog.octet_length(raw_definition.definition) AS definition_bytes
        FROM (
            SELECT pg_catalog.pg_get_function_identity_arguments(p.oid)
                       AS identity_arguments,
                   pg_catalog.pg_get_function_arguments(p.oid) AS arguments,
                   pg_catalog.pg_get_function_result(p.oid) AS return_type,
                   pg_catalog.pg_get_functiondef(p.oid) AS definition
        ) AS raw_definition
    ) AS function_definition
    WHERE n.nspname = %s
      AND p.prokind IN ('f', 'p')
    ORDER BY p.proname, function_definition.identity_arguments, p.prokind
    LIMIT %s
"""

VIEWS_QUERY = """
    /* schemii_catalog_views */
    SELECT c.relname AS view_name,
           c.relkind AS relation_kind,
           CASE WHEN view_definition.byte_count <= %s
                THEN view_definition.value
                ELSE NULL
           END AS query_definition,
           view_definition.byte_count AS query_definition_bytes,
           c.relispopulated AS populated
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL (
        SELECT raw_definition.value,
               pg_catalog.octet_length(raw_definition.value) AS byte_count
        FROM (
            SELECT pg_catalog.pg_get_viewdef(c.oid, true) AS value
        ) AS raw_definition
    ) AS view_definition
    WHERE n.nspname = %s
      AND c.relkind IN ('v', 'm')
    ORDER BY c.relkind, c.relname
    LIMIT %s
"""
