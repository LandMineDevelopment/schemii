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

NAMESPACES_QUERY = r"""
    /* schemii_namespaces */
    SELECT n.nspname AS namespace_name,
           (
               n.nspname = 'information_schema'
               OR pg_catalog.left(n.nspname, 3) = 'pg_'
           ) AS is_system
    FROM pg_catalog.pg_namespace AS n
    WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
      AND pg_catalog.has_schema_privilege(
          CURRENT_USER,
          n.oid,
          'USAGE'
      )
    ORDER BY is_system, n.nspname
    LIMIT %s
"""

TYPES_QUERY = """
    /* schemii_catalog_types */
    SELECT type_definition.type_name,
           type_definition.type_kind,
           CASE WHEN pg_catalog.octet_length(type_definition.definition) <= %s
                THEN type_definition.definition
                ELSE NULL
           END AS definition,
           pg_catalog.octet_length(type_definition.definition) AS definition_bytes
    FROM (
        SELECT t.typname AS type_name,
               CASE t.typtype WHEN 'e' THEN 'enum' ELSE 'domain' END AS type_kind,
               CASE t.typtype
                   WHEN 'e' THEN pg_catalog.format(
                       'CREATE TYPE %%I AS ENUM (%%s)',
                       t.typname,
                       (
                           SELECT pg_catalog.string_agg(
                               pg_catalog.quote_literal(enum.enumlabel),
                               ', ' ORDER BY enum.enumsortorder
                           )
                           FROM pg_catalog.pg_enum AS enum
                           WHERE enum.enumtypid = t.oid
                       )
                   )
                   ELSE pg_catalog.format(
                       'CREATE DOMAIN %%I AS %%s%%s%%s%%s%%s',
                       t.typname,
                       pg_catalog.format_type(t.typbasetype, t.typtypmod),
                       CASE
                           WHEN t.typcollation <> 0
                            AND t.typcollation <> base_type.typcollation
                           THEN pg_catalog.format(
                               ' COLLATE %%I.%%I',
                               collation_namespace.nspname,
                               coll.collname
                           )
                           ELSE ''
                       END,
                       CASE
                           WHEN t.typdefault IS NOT NULL
                           THEN ' DEFAULT ' || t.typdefault
                           ELSE ''
                       END,
                       CASE WHEN t.typnotnull THEN ' NOT NULL' ELSE '' END,
                       COALESCE(
                           (
                               SELECT pg_catalog.string_agg(
                                   pg_catalog.format(
                                       ' CONSTRAINT %%I %%s',
                                       con.conname,
                                       pg_catalog.pg_get_constraintdef(
                                           con.oid,
                                           true
                                       )
                                   ),
                                   '' ORDER BY con.conname
                               )
                               FROM pg_catalog.pg_constraint AS con
                               WHERE con.contypid = t.oid
                           ),
                           ''
                       )
                   )
               END AS definition
        FROM pg_catalog.pg_type AS t
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = t.typnamespace
        LEFT JOIN pg_catalog.pg_type AS base_type
          ON base_type.oid = t.typbasetype
        LEFT JOIN pg_catalog.pg_collation AS coll
          ON coll.oid = t.typcollation
        LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
          ON collation_namespace.oid = coll.collnamespace
        WHERE namespace.nspname = %s
          AND t.typtype IN ('e', 'd')
    ) AS type_definition
    ORDER BY type_definition.type_name, type_definition.type_kind
    LIMIT %s
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
           con.conislocal AS is_local,
           con.coninhcount AS inheritance_count,
           con.connoinherit AS no_inherit,
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


READABLE_COLUMNS_QUERY = """
    /* schemii_report_readable_columns */
    SELECT c.relname AS relation_name, a.attname AS column_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
      AND a.attnum > 0 AND NOT a.attisdropped
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
    ORDER BY c.relname, a.attnum
    LIMIT %s
"""

# Versioned safety evidence is separate from lossless editable import. All rows
# are bounded by the same gateway catalog limits and included in the full digest.
SAFETY_OBJECTS_QUERY = """
    /* schemii_catalog_safety_objects */
    WITH settings AS (SELECT %s::int AS maximum, %s::text AS namespace)
    SELECT CASE WHEN octet_length(descriptor.value) <= settings.maximum THEN descriptor.value END AS descriptor,
           octet_length(descriptor.value) AS descriptor_bytes
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=c.reltablespace
    LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
    CROSS JOIN settings
    CROSS JOIN LATERAL (SELECT jsonb_build_object(
      'identity', jsonb_build_object('kind','relation','namespace',n.nspname,'name',c.relname),
      'relation_kind',c.relkind::text, 'owner',pg_catalog.pg_get_userbyid(c.relowner),'actor',current_user,
      'acl',COALESCE((SELECT jsonb_agg(jsonb_build_object('grantor',pg_get_userbyid(a.grantor),
         'grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
         'privilege',a.privilege_type,'grantable',a.is_grantable) ORDER BY a.grantee,a.privilege_type,a.grantor)
         FROM aclexplode(COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) a),'[]'::jsonb),
      'column_acl',COALESCE((SELECT jsonb_agg(jsonb_build_object('column',att.attname,'ordinal',att.attnum,
         'grantor',pg_get_userbyid(a.grantor),'grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
         'privilege',a.privilege_type,'grantable',a.is_grantable) ORDER BY att.attnum,a.grantee,a.privilege_type,a.grantor)
         FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) a
         WHERE att.attrelid=c.oid AND att.attnum>0 AND NOT att.attisdropped),'[]'::jsonb),
      'identity_sequence_owner',(SELECT jsonb_build_object('table',jsonb_build_object('kind','relation','namespace',owner_ns.nspname,'name',owner_table.relname),
         'column',owner_column.attname,'ordinal',owner_column.attnum,'table_owner',pg_get_userbyid(owner_table.relowner),'dependency_kind',d.deptype::text)
         FROM pg_depend d JOIN pg_class owner_table ON owner_table.oid=d.refobjid
         JOIN pg_namespace owner_ns ON owner_ns.oid=owner_table.relnamespace
         JOIN pg_attribute owner_column ON owner_column.attrelid=owner_table.oid AND owner_column.attnum=d.refobjsubid
         WHERE c.relkind='S' AND d.classid='pg_class'::regclass AND d.objid=c.oid AND d.objsubid=0
           AND d.refclassid='pg_class'::regclass AND d.deptype='i' AND owner_column.attidentity IN ('a','d')
           AND NOT owner_column.attisdropped AND owner_table.relkind IN ('r','p') AND owner_table.relowner=c.relowner
           AND (SELECT count(*) FROM pg_depend internal WHERE internal.classid='pg_class'::regclass AND internal.objid=c.oid AND internal.deptype='i')=1),
      'reloptions',to_jsonb(COALESCE(c.reloptions,ARRAY[]::text[])),
      'tablespace',ts.spcname,'access_method',am.amname,
      'partition_bound',pg_get_expr(c.relpartbound,c.oid,true),
      'partition_parents',COALESCE((SELECT jsonb_agg(jsonb_build_object('kind','relation','namespace',pn.nspname,'name',p.relname) ORDER BY pn.nspname,p.relname)
         FROM pg_inherits i JOIN pg_class p ON p.oid=i.inhparent JOIN pg_namespace pn ON pn.oid=p.relnamespace WHERE i.inhrelid=c.oid),'[]'::jsonb),
      'indexes',COALESCE((SELECT jsonb_agg(jsonb_build_object('name',ic.relname,'table',c.relname,
         'definition',pg_get_indexdef(i.indexrelid),'method',iam.amname,'unique',i.indisunique,
         'valid',i.indisvalid,'predicate',pg_get_expr(i.indpred,i.indrelid,true)) ORDER BY ic.relname)
         FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am iam ON iam.oid=ic.relam WHERE i.indrelid=c.oid),'[]'::jsonb),
      'default_grantees',COALESCE((SELECT jsonb_agg(DISTINCT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END ORDER BY CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
         FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
         WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user)
           AND d.defaclobjtype='r' AND d.defaclnamespace IN (0,n.oid)),'[]'::jsonb),
      'properties',jsonb_build_object('persistence',c.relpersistence,'row_security',c.relrowsecurity,
          'force_row_security',c.relforcerowsecurity,'replica_identity',c.relreplident,
          'policies',COALESCE((SELECT jsonb_agg(jsonb_build_object('name',pol.polname,'permissive',pol.polpermissive,
             'command',pol.polcmd,'roles',ARRAY(SELECT CASE WHEN role=0 THEN 'PUBLIC' ELSE pg_get_userbyid(role) END
                FROM unnest(pol.polroles) role ORDER BY role),
             'using',pg_get_expr(pol.polqual,pol.polrelid,true),'check',pg_get_expr(pol.polwithcheck,pol.polrelid,true)) ORDER BY pol.polname)
             FROM pg_policy pol WHERE pol.polrelid=c.oid),'[]'::jsonb))::text
    )::text AS value) descriptor
    WHERE n.nspname=settings.namespace AND c.relkind IN ('r','p','v','m','f','S','c')
    ORDER BY c.relname LIMIT %s
"""

SAFETY_DEPENDENCIES_QUERY = """
    /* schemii_catalog_safety_dependencies */
    WITH settings AS (SELECT %s::int AS maximum, %s::text AS namespace),
    nodes AS (
      SELECT 'pg_class'::regclass::oid AS classid,c.oid AS objid,'relation'::text AS kind,n.nspname AS namespace,c.relname AS name,''::text AS signature
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'pg_type'::regclass::oid,t.oid,CASE WHEN c.oid IS NULL AND ec.oid IS NULL THEN 'type' ELSE 'relation' END,n.nspname,COALESCE(c.relname,ec.relname,t.typname),''
      FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace LEFT JOIN pg_class c ON c.oid=t.typrelid
      LEFT JOIN pg_type element ON element.oid=t.typelem LEFT JOIN pg_class ec ON ec.oid=element.typrelid
      UNION ALL
      SELECT 'pg_proc'::regclass::oid,p.oid,'routine',n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    ), edges AS (
      SELECT CASE WHEN COALESCE(r.ev_class,con.conrelid,tr.tgrelid,ad.adrelid,pol.polrelid) IS NOT NULL THEN 'pg_class'::regclass ELSE d.classid END AS source_class,
             COALESCE(r.ev_class,con.conrelid,tr.tgrelid,ad.adrelid,pol.polrelid,d.objid) AS source,
             d.refclassid AS target_class,d.refobjid AS target,d.deptype::text AS kind
      FROM pg_depend d
      LEFT JOIN pg_rewrite r ON d.classid='pg_rewrite'::regclass AND r.oid=d.objid
      LEFT JOIN pg_constraint con ON d.classid='pg_constraint'::regclass AND con.oid=d.objid AND con.conrelid<>0
      LEFT JOIN pg_trigger tr ON d.classid='pg_trigger'::regclass AND tr.oid=d.objid
      LEFT JOIN pg_attrdef ad ON d.classid='pg_attrdef'::regclass AND ad.oid=d.objid
      LEFT JOIN pg_policy pol ON d.classid='pg_policy'::regclass AND pol.oid=d.objid
      WHERE d.refclassid IN ('pg_class'::regclass,'pg_type'::regclass,'pg_proc'::regclass)
      UNION ALL SELECT 'pg_class'::regclass,inhrelid,'pg_class'::regclass,inhparent,'partition' FROM pg_inherits
    )
    SELECT CASE WHEN octet_length(descriptor.value)<=settings.maximum THEN descriptor.value END AS descriptor,
           octet_length(descriptor.value) AS descriptor_bytes
    FROM (SELECT DISTINCT source_class,source,target_class,target,kind FROM edges) e
    JOIN nodes sn ON sn.classid=e.source_class AND sn.objid=e.source
    JOIN nodes tn ON tn.classid=e.target_class AND tn.objid=e.target
    CROSS JOIN settings
    CROSS JOIN LATERAL (SELECT jsonb_build_object(
      'source',jsonb_build_object('kind',sn.kind,'namespace',sn.namespace,'name',sn.name,'signature',sn.signature),
      'target',jsonb_build_object('kind',tn.kind,'namespace',tn.namespace,'name',tn.name,'signature',tn.signature),'kind',e.kind)::text AS value) descriptor
    WHERE sn.namespace !~ '^pg_' AND sn.namespace<>'information_schema'
      AND tn.namespace !~ '^pg_' AND tn.namespace<>'information_schema'
      AND (sn.kind,sn.namespace,sn.name,sn.signature)<>(tn.kind,tn.namespace,tn.name,tn.signature)
    ORDER BY sn.namespace,sn.name,sn.signature,tn.namespace,tn.name,tn.signature,e.kind LIMIT %s
"""

# Policy self-references belong to their preserved table properties. References
# to other objects remain unknown: an external policy owner may be outside the
# schema-local preservation inventory, even though its graph edge is collected.
SAFETY_UNKNOWNS_QUERY = """
    /* schemii_catalog_safety_unknowns */
    SELECT
      EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema') AS routine_bodies,
      EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('f','c'))
      OR EXISTS(SELECT 1 FROM pg_depend d
        LEFT JOIN pg_policy pol ON d.classid='pg_policy'::regclass AND pol.oid=d.objid
        LEFT JOIN pg_class c ON d.refclassid='pg_class'::regclass AND c.oid=d.refobjid
        LEFT JOIN pg_type t ON d.refclassid='pg_type'::regclass AND t.oid=d.refobjid
        LEFT JOIN pg_proc p ON d.refclassid='pg_proc'::regclass AND p.oid=d.refobjid
        JOIN pg_namespace n ON n.oid=COALESCE(c.relnamespace,t.typnamespace,p.pronamespace)
        WHERE d.classid NOT IN ('pg_class'::regclass,'pg_type'::regclass,'pg_proc'::regclass,
          'pg_rewrite'::regclass,'pg_constraint'::regclass,'pg_trigger'::regclass,'pg_attrdef'::regclass)
          AND NOT (d.classid='pg_policy'::regclass AND pol.oid IS NOT NULL
            AND d.refclassid='pg_class'::regclass AND d.refobjid=pol.polrelid)
          AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema') AS opaque_dependencies,
      NOT has_schema_privilege(%s,'USAGE') AS inaccessible
"""
