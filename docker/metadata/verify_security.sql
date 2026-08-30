WITH expected_version(value) AS (
    VALUES ((:'expected_metadata_version')::integer)
), expected_migration_history(version, name, checksum) AS (
    SELECT version, name, checksum
    FROM expected_metadata_migrations
    WHERE version <= (SELECT value FROM expected_version)
), actual_migration_history(version, name, checksum) AS (
    SELECT version, name, checksum::text
    FROM metadata_schema_migrations
), migration_history_differences AS (
    (SELECT * FROM expected_migration_history EXCEPT SELECT * FROM actual_migration_history)
    UNION ALL
    (SELECT * FROM actual_migration_history EXCEPT SELECT * FROM expected_migration_history)
), expected_roles(name, login, inherit, superuser, create_role, create_database, replication, bypass_rls, connection_limit, valid_until_is_null, role_config) AS (
    VALUES
        ('schemii_metadata_bootstrap', false, true, true, true, true, true, true, -1, true, NULL::text[]),
        ('schemii_metadata_owner', false, true, false, false, false, false, false, -1, true, NULL::text[]),
        ('schemii_metadata_migration', true, true, false, false, false, false, false, -1, true, NULL::text[]),
        ('schemii_metadata_schemii', true, true, false, false, false, false, false, -1, true, NULL::text[]),
        ('schemii_metadata_schemer', true, true, false, false, false, false, false, -1, true, NULL::text[])
), actual_roles AS (
    SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
           rolconnlimit, rolvaliduntil IS NULL, rolconfig
    FROM pg_catalog.pg_roles
    WHERE rolname LIKE 'schemii\_metadata\_%' ESCAPE '\'
), expected_memberships(role_name, member_name, admin_option, inherit_option, set_option) AS (
    VALUES ('schemii_metadata_owner', 'schemii_metadata_migration', false, true, true)
), actual_memberships AS (
    SELECT role.rolname, member.rolname, membership.admin_option,
           membership.inherit_option, membership.set_option
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS role ON role.oid = membership.roleid
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE role.rolname IN (SELECT name FROM expected_roles)
       OR member.rolname IN (SELECT name FROM expected_roles)
), expected_database_acl(grantee, privilege_type, is_grantable) AS (
    VALUES
        ('schemii_metadata_owner', 'CONNECT', false),
        ('schemii_metadata_owner', 'CREATE', false),
        ('schemii_metadata_owner', 'TEMPORARY', false),
        ('schemii_metadata_migration', 'CONNECT', false),
        ('schemii_metadata_schemii', 'CONNECT', false),
        ('schemii_metadata_schemer', 'CONNECT', false)
), actual_database_acl AS (
    SELECT COALESCE(grantee.rolname, 'PUBLIC'), privilege.privilege_type, privilege.is_grantable
    FROM pg_catalog.pg_database AS database
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(database.datacl, pg_catalog.acldefault('d', database.datdba))
    ) AS privilege
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = privilege.grantee
    WHERE database.datname = current_database()
), expected_schema_acl(schema_name, grantee, privilege_type, is_grantable) AS (
    VALUES
        ('public', 'schemii_metadata_owner', 'CREATE', false),
        ('public', 'schemii_metadata_owner', 'USAGE', false),
        ('public', 'schemii_metadata_schemii', 'USAGE', false),
        ('public', 'schemii_metadata_schemer', 'USAGE', false),
        ('schemii_admin', 'schemii_metadata_bootstrap', 'CREATE', false),
        ('schemii_admin', 'schemii_metadata_bootstrap', 'USAGE', false),
        ('schemii_admin', 'schemii_metadata_migration', 'USAGE', false)
), actual_schema_acl AS (
    SELECT namespace.nspname, COALESCE(grantee.rolname, 'PUBLIC'), privilege.privilege_type, privilege.is_grantable
    FROM pg_catalog.pg_namespace AS namespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(namespace.nspacl, pg_catalog.acldefault('n', namespace.nspowner))
    ) AS privilege
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = privilege.grantee
    WHERE namespace.nspname IN ('public', 'schemii_admin')
), expected_default_acl(owner_name, schema_name, object_type, grantee, privilege_type, is_grantable) AS (
    VALUES
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemii', 'DELETE', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemii', 'INSERT', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemii', 'SELECT', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemii', 'UPDATE', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemer', 'DELETE', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemer', 'INSERT', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemer', 'SELECT', false),
        ('schemii_metadata_owner', 'public', 'r', 'schemii_metadata_schemer', 'UPDATE', false),
        ('schemii_metadata_owner', 'public', 'S', 'schemii_metadata_schemii', 'SELECT', false),
        ('schemii_metadata_owner', 'public', 'S', 'schemii_metadata_schemii', 'USAGE', false),
        ('schemii_metadata_owner', 'public', 'S', 'schemii_metadata_schemer', 'SELECT', false),
        ('schemii_metadata_owner', 'public', 'S', 'schemii_metadata_schemer', 'USAGE', false)
), actual_default_acl AS (
    SELECT owner.rolname, COALESCE(namespace.nspname, ''), defaults.defaclobjtype::text,
           COALESCE(grantee.rolname, 'PUBLIC'), privilege.privilege_type, privilege.is_grantable
    FROM pg_catalog.pg_default_acl AS defaults
    JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
    LEFT JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = defaults.defaclnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS privilege
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = privilege.grantee
    WHERE owner.rolname LIKE 'schemii\_metadata\_%' ESCAPE '\'
      AND privilege.grantee <> defaults.defaclrole
), expected_default_acl_records(owner_name, schema_name, object_type) AS (
    SELECT * FROM (VALUES
        ('schemii_metadata_owner', 'public', 'r'),
        ('schemii_metadata_owner', 'public', 'S')
    ) AS historical(owner_name, schema_name, object_type)
    UNION ALL
    SELECT 'schemii_metadata_owner', '', 'f'
    WHERE (SELECT value FROM expected_version) >= 12
), actual_default_acl_records AS (
    SELECT owner.rolname, COALESCE(namespace.nspname, ''), defaults.defaclobjtype::text
    FROM pg_catalog.pg_default_acl AS defaults
    JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
    LEFT JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = defaults.defaclnamespace
    WHERE owner.rolname LIKE 'schemii\_metadata\_%' ESCAPE '\'
), expected_relations(relation_name, relation_kind) AS (
    VALUES
        ('metadata_schema_migrations', 'r'),
        ('metadata_applications', 'r'),
        ('metadata_chats', 'r'),
        ('metadata_targets', 'r'),
        ('metadata_policy_versions', 'r'),
        ('metadata_capabilities', 'r'),
        ('metadata_grants', 'r'),
        ('metadata_proposals', 'r'),
        ('metadata_operations', 'r'),
        ('metadata_operation_approvals', 'r'),
        ('metadata_operation_attempts', 'r'),
        ('metadata_operation_outcomes', 'r'),
        ('metadata_query_result_references', 'r'),
        ('metadata_query_result_payloads', 'r'),
        ('metadata_query_result_deliveries', 'r'),
        ('metadata_authority_transitions', 'r'),
        ('metadata_authority_transitions_transition_id_seq', 'S'),
        ('metadata_migration_plans', 'r'),
        ('metadata_migration_executions', 'r'),
        ('metadata_migration_syncs', 'r'),
        ('metadata_migration_transitions', 'r'),
        ('metadata_migration_transitions_transition_id_seq', 'S'),
        ('metadata_console_execution_receipts', 'r'),
        ('metadata_console_settings', 'r'),
        ('metadata_agent_settings', 'r'),
        ('metadata_agent_policy_revisions', 'r'),
        ('metadata_agent_policy_capabilities', 'r'),
        ('metadata_agent_policy_bounds', 'r'),
        ('metadata_ai_operation_usage', 'r')
), actual_relations AS (
    SELECT relation.relname, relation.relkind::text
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
), expected_relation_acl(relation_oid, grantee, privilege_type, is_grantable) AS (
    SELECT relation.oid, runtime_role.name, privilege.name, false
    FROM expected_relations AS expected
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.nspname = 'public'
    JOIN pg_catalog.pg_class AS relation
      ON relation.relnamespace = namespace.oid AND relation.relname = expected.relation_name
    CROSS JOIN (VALUES ('schemii_metadata_schemii'), ('schemii_metadata_schemer')) AS runtime_role(name)
    CROSS JOIN LATERAL (
        SELECT name
        FROM unnest(
            CASE
                WHEN expected.relation_kind = 'S' THEN ARRAY['SELECT', 'USAGE']
                WHEN expected.relation_name = 'metadata_schema_migrations' THEN ARRAY['SELECT']
                ELSE ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']
            END
        ) AS name
    ) AS privilege
), actual_relation_acl AS (
    SELECT relation.oid, COALESCE(grantee.rolname, 'PUBLIC'), privilege.privilege_type, privilege.is_grantable
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
            relation.relacl,
            pg_catalog.acldefault(CASE WHEN relation.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END, relation.relowner)
        )
    ) AS privilege
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = privilege.grantee
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
      AND privilege.grantee <> relation.relowner
), expected_function_inventory(schema_name, function_name, identity_arguments, result_type) AS (
    VALUES
        ('public', 'metadata_current_application', '', 'text'),
        ('public', 'metadata_proposal_snapshot_immutable', '', 'trigger'),
        ('public', 'metadata_migration_plan_snapshot_immutable', '', 'trigger'),
        ('public', 'metadata_chat_agent_policy_link_valid', '', 'trigger'),
        ('public', 'metadata_agent_policy_immutable', '', 'trigger'),
        ('schemii_admin', 'rotate_metadata_passwords', 'migration_password text, schemii_password text, schemer_password text', 'void')
), actual_function_inventory AS (
    SELECT namespace.nspname, procedure.proname,
           pg_catalog.pg_get_function_identity_arguments(procedure.oid),
           pg_catalog.pg_get_function_result(procedure.oid)
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname IN ('public', 'schemii_admin')
), expected_function_acl(function_oid, grantee, privilege_type, is_grantable) AS (
    SELECT procedure.oid, runtime_role.name, 'EXECUTE', false
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    CROSS JOIN (VALUES ('schemii_metadata_schemii'), ('schemii_metadata_schemer')) AS runtime_role(name)
    WHERE namespace.nspname = 'public'
      AND procedure.proname = 'metadata_current_application'
    UNION ALL
    SELECT procedure.oid, 'PUBLIC', 'EXECUTE', false
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'public'
      AND procedure.proname <> 'metadata_current_application'
      AND (SELECT value FROM expected_version) < 12
    UNION ALL
    SELECT procedure.oid, 'schemii_metadata_migration', 'EXECUTE', false
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'schemii_admin'
      AND procedure.proname = 'rotate_metadata_passwords'
), actual_function_acl AS (
    SELECT procedure.oid, COALESCE(grantee.rolname, 'PUBLIC'), privilege.privilege_type, privilege.is_grantable
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))
    ) AS privilege
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = privilege.grantee
    WHERE namespace.nspname IN ('public', 'schemii_admin')
      AND privilege.grantee <> procedure.proowner
), expected_isolation_policies(table_name, policy_name, role_names, command, permissive, qualification, check_expression) AS (
    VALUES
        ('metadata_applications', 'metadata_applications_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_chats', 'metadata_chats_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_targets', 'metadata_targets_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_targets.chat_id)))', NULL),
        ('metadata_policy_versions', 'metadata_policy_versions_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_policy_versions.chat_id)))', NULL),
        ('metadata_capabilities', 'metadata_capabilities_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_policy_versionsvWHERE(v.policy_version_id=metadata_capabilities.policy_version_id)))', NULL),
        ('metadata_grants', 'metadata_grants_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_grants.chat_id)))', NULL),
        ('metadata_proposals', 'metadata_proposals_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_proposals.chat_id)))', NULL),
        ('metadata_operations', 'metadata_operations_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_operations.chat_id)))', NULL),
        ('metadata_operation_approvals', 'metadata_operation_approvals_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_operationsoWHERE(o.operation_id=metadata_operation_approvals.operation_id)))', NULL),
        ('metadata_operation_attempts', 'metadata_operation_attempts_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_operationsoWHERE(o.operation_id=metadata_operation_attempts.operation_id)))', NULL),
        ('metadata_operation_outcomes', 'metadata_operation_outcomes_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_operationsoWHERE(o.operation_id=metadata_operation_outcomes.operation_id)))', NULL),
        ('metadata_query_result_references', 'metadata_query_result_references_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_chatscWHERE(c.chat_id=metadata_query_result_references.chat_id)))', NULL),
        ('metadata_query_result_payloads', 'metadata_query_result_payloads_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_query_result_referencesrWHERE(r.result_ref_id=metadata_query_result_payloads.result_ref_id)))', NULL),
        ('metadata_query_result_deliveries', 'metadata_query_result_deliveries_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_query_result_referencesrWHERE(r.result_ref_id=metadata_query_result_deliveries.result_ref_id)))', NULL),
        ('metadata_authority_transitions', 'metadata_authority_transitions_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_migration_plans', 'metadata_migration_plans_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_migration_executions', 'metadata_migration_executions_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_migration_planspWHERE(p.plan_id=metadata_migration_executions.plan_id)))', NULL),
        ('metadata_migration_syncs', 'metadata_migration_syncs_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_migration_executionseWHERE(e.execution_id=metadata_migration_syncs.execution_id)))', NULL),
        ('metadata_migration_transitions', 'metadata_migration_transitions_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_migration_executionseWHERE(e.execution_id=metadata_migration_transitions.execution_id)))', NULL),
        ('metadata_console_execution_receipts', 'metadata_console_execution_receipts_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_console_settings', 'metadata_console_settings_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_agent_settings', 'metadata_agent_settings_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_agent_policy_revisions', 'metadata_agent_policy_revisions_isolation', ARRAY['public'], 'ALL', true, '(application_id=metadata_current_application())', '(application_id=metadata_current_application())'),
        ('metadata_agent_policy_capabilities', 'metadata_agent_policy_capabilities_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_agent_policy_revisionsrWHERE(r.agent_policy_revision_id=metadata_agent_policy_capabilities.agent_policy_revision_id)))', NULL),
        ('metadata_agent_policy_bounds', 'metadata_agent_policy_bounds_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROMmetadata_agent_policy_revisionsrWHERE(r.agent_policy_revision_id=metadata_agent_policy_bounds.agent_policy_revision_id)))', NULL),
        ('metadata_ai_operation_usage', 'metadata_ai_operation_usage_isolation', ARRAY['public'], 'ALL', true, '(EXISTS(SELECT1FROM(metadata_operationsoJOINmetadata_chatscUSING(chat_id))WHERE((o.operation_id=metadata_ai_operation_usage.operation_id)AND(c.application_id=metadata_current_application()))))', NULL)
), expected_policies AS (
    SELECT * FROM expected_isolation_policies
    UNION ALL
    SELECT relation_name, 'metadata_owner_maintenance', ARRAY['schemii_metadata_owner'],
           'ALL', true, 'true', 'true'
    FROM expected_relations
    WHERE relation_name <> 'metadata_schema_migrations'
      AND relation_kind IN ('r', 'p')
      AND (SELECT value FROM expected_version) >= 12
), actual_policies AS (
    SELECT relation.relname, policy.polname,
           ARRAY(
               SELECT COALESCE(role.rolname, 'public')
               FROM unnest(policy.polroles) AS listed(role_oid)
               LEFT JOIN pg_catalog.pg_roles AS role ON role.oid = listed.role_oid
               ORDER BY COALESCE(role.rolname, 'public')
           ),
           CASE policy.polcmd WHEN '*' THEN 'ALL' WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT' WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' END,
           policy.polpermissive,
           regexp_replace(pg_catalog.pg_get_expr(policy.polqual, policy.polrelid), '[[:space:]]+', '', 'g'),
           regexp_replace(pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid), '[[:space:]]+', '', 'g')
    FROM pg_catalog.pg_policy AS policy
    JOIN pg_catalog.pg_class AS relation ON relation.oid = policy.polrelid
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
), expected_authority_foreign_keys(child_table, parent_table, delete_action) AS (
    VALUES
        ('metadata_chats', 'metadata_applications', 'a'),
        ('metadata_targets', 'metadata_chats', 'c'),
        ('metadata_policy_versions', 'metadata_chats', 'c'),
        ('metadata_policy_versions', 'metadata_agent_policy_revisions', 'r'),
        ('metadata_capabilities', 'metadata_policy_versions', 'c'),
        ('metadata_grants', 'metadata_chats', 'c'),
        ('metadata_proposals', 'metadata_chats', 'c'),
        ('metadata_operations', 'metadata_proposals', 'a'),
        ('metadata_operations', 'metadata_chats', 'c'),
        ('metadata_operation_approvals', 'metadata_operations', 'c'),
        ('metadata_operation_attempts', 'metadata_operations', 'c'),
        ('metadata_operation_outcomes', 'metadata_operations', 'c'),
        ('metadata_ai_operation_usage', 'metadata_operations',
            CASE WHEN (SELECT value FROM expected_version) >= 13 THEN 'c' ELSE 'r' END),
        ('metadata_query_result_references', 'metadata_chats', 'c'),
        ('metadata_query_result_payloads', 'metadata_query_result_references', 'c'),
        ('metadata_query_result_deliveries', 'metadata_query_result_references', 'c'),
        ('metadata_authority_transitions', 'metadata_applications', 'a')
), actual_authority_foreign_keys(child_table, parent_table, delete_action) AS (
    SELECT child.relname, parent.relname, constraint_record.confdeltype::text
    FROM pg_catalog.pg_constraint AS constraint_record
    JOIN pg_catalog.pg_class AS child ON child.oid = constraint_record.conrelid
    JOIN pg_catalog.pg_namespace AS child_namespace ON child_namespace.oid = child.relnamespace
    JOIN pg_catalog.pg_class AS parent ON parent.oid = constraint_record.confrelid
    JOIN pg_catalog.pg_namespace AS parent_namespace ON parent_namespace.oid = parent.relnamespace
    WHERE constraint_record.contype = 'f'
      AND child_namespace.nspname = 'public'
      AND parent_namespace.nspname = 'public'
      AND child.relname IN (SELECT child_table FROM expected_authority_foreign_keys)
), role_differences AS (
    (SELECT * FROM expected_roles EXCEPT SELECT * FROM actual_roles)
    UNION ALL
    (SELECT * FROM actual_roles EXCEPT SELECT * FROM expected_roles)
), membership_differences AS (
    (SELECT * FROM expected_memberships EXCEPT SELECT * FROM actual_memberships)
    UNION ALL
    (SELECT * FROM actual_memberships EXCEPT SELECT * FROM expected_memberships)
), database_acl_differences AS (
    (SELECT * FROM expected_database_acl EXCEPT SELECT * FROM actual_database_acl)
    UNION ALL
    (SELECT * FROM actual_database_acl EXCEPT SELECT * FROM expected_database_acl)
), schema_acl_differences AS (
    (SELECT * FROM expected_schema_acl EXCEPT SELECT * FROM actual_schema_acl)
    UNION ALL
    (SELECT * FROM actual_schema_acl EXCEPT SELECT * FROM expected_schema_acl)
), default_acl_differences AS (
    (SELECT * FROM expected_default_acl EXCEPT SELECT * FROM actual_default_acl)
    UNION ALL
    (SELECT * FROM actual_default_acl EXCEPT SELECT * FROM expected_default_acl)
), default_acl_record_differences AS (
    (SELECT * FROM expected_default_acl_records EXCEPT SELECT * FROM actual_default_acl_records)
    UNION ALL
    (SELECT * FROM actual_default_acl_records EXCEPT SELECT * FROM expected_default_acl_records)
), relation_inventory_differences AS (
    (SELECT * FROM expected_relations EXCEPT SELECT * FROM actual_relations)
    UNION ALL
    (SELECT * FROM actual_relations EXCEPT SELECT * FROM expected_relations)
), relation_acl_differences AS (
    (SELECT * FROM expected_relation_acl EXCEPT SELECT * FROM actual_relation_acl)
    UNION ALL
    (SELECT * FROM actual_relation_acl EXCEPT SELECT * FROM expected_relation_acl)
), function_inventory_differences AS (
    (SELECT * FROM expected_function_inventory EXCEPT SELECT * FROM actual_function_inventory)
    UNION ALL
    (SELECT * FROM actual_function_inventory EXCEPT SELECT * FROM expected_function_inventory)
), function_acl_differences AS (
    (SELECT * FROM expected_function_acl EXCEPT SELECT * FROM actual_function_acl)
    UNION ALL
    (SELECT * FROM actual_function_acl EXCEPT SELECT * FROM expected_function_acl)
), policy_differences AS (
    (SELECT * FROM expected_policies EXCEPT SELECT * FROM actual_policies)
    UNION ALL
    (SELECT * FROM actual_policies EXCEPT SELECT * FROM expected_policies)
), authority_foreign_key_differences AS (
    (SELECT * FROM expected_authority_foreign_keys EXCEPT SELECT * FROM actual_authority_foreign_keys)
    UNION ALL
    (SELECT * FROM actual_authority_foreign_keys EXCEPT SELECT * FROM expected_authority_foreign_keys)
), invalid_owners AS (
    SELECT 1
    FROM pg_catalog.pg_database AS database
    WHERE database.datname = current_database()
      AND pg_catalog.pg_get_userbyid(database.datdba) <> 'schemii_metadata_owner'
    UNION ALL
    SELECT 1
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
      AND pg_catalog.pg_get_userbyid(relation.relowner) <> 'schemii_metadata_owner'
    UNION ALL
    SELECT 1
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'public'
      AND pg_catalog.pg_get_userbyid(procedure.proowner) <> 'schemii_metadata_owner'
), invalid_schema_owners AS (
    SELECT 1
    FROM (VALUES
        ('public', 'schemii_metadata_owner'),
        ('schemii_admin', 'schemii_metadata_bootstrap')
    ) AS expected(schema_name, owner_name)
    LEFT JOIN pg_catalog.pg_namespace AS namespace ON namespace.nspname = expected.schema_name
    WHERE namespace.oid IS NULL
       OR pg_catalog.pg_get_userbyid(namespace.nspowner) <> expected.owner_name
), expected_row_security(relation_oid, row_security, force_row_security) AS (
    SELECT relation.oid,
           expected.relation_name <> 'metadata_schema_migrations',
           expected.relation_name <> 'metadata_schema_migrations'
             AND (SELECT value FROM expected_version) >= 12
    FROM expected_relations AS expected
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.nspname = 'public'
    JOIN pg_catalog.pg_class AS relation
      ON relation.relnamespace = namespace.oid AND relation.relname = expected.relation_name
    WHERE expected.relation_kind IN ('r', 'p')
), actual_row_security AS (
    SELECT relation.oid, relation.relrowsecurity, relation.relforcerowsecurity
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'p')
      AND relation.relname LIKE 'metadata\_%' ESCAPE '\'
), row_security_differences AS (
    (SELECT * FROM expected_row_security EXCEPT SELECT * FROM actual_row_security)
    UNION ALL
    (SELECT * FROM actual_row_security EXCEPT SELECT * FROM expected_row_security)
), invalid_rotation AS (
    SELECT 1
    WHERE (
        SELECT count(*)
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner
        WHERE namespace.nspname = 'schemii_admin'
          AND procedure.proname = 'rotate_metadata_passwords'
          AND owner.rolname = 'schemii_metadata_bootstrap'
          AND procedure.prosecdef
          AND procedure.proconfig = ARRAY['search_path=pg_catalog']
    ) <> 1
), invalid_application_function AS (
    SELECT 1
    WHERE (
        SELECT count(*)
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner
        JOIN pg_catalog.pg_language AS language ON language.oid = procedure.prolang
        WHERE namespace.nspname = 'public'
          AND procedure.proname = 'metadata_current_application'
          AND owner.rolname = 'schemii_metadata_owner'
          AND language.lanname = 'sql'
          AND NOT procedure.prosecdef
          AND procedure.provolatile = 's'
          AND procedure.proparallel = 's'
          AND procedure.proconfig IS NULL
    ) <> 1
)
SELECT CASE WHEN
    (SELECT value FROM expected_version) BETWEEN 10 AND 13
    AND NOT EXISTS (SELECT 1 FROM migration_history_differences)
    AND NOT EXISTS (SELECT 1 FROM role_differences)
    AND NOT EXISTS (SELECT 1 FROM membership_differences)
    AND NOT EXISTS (SELECT 1 FROM database_acl_differences)
    AND NOT EXISTS (SELECT 1 FROM schema_acl_differences)
    AND NOT EXISTS (SELECT 1 FROM default_acl_differences)
    AND NOT EXISTS (SELECT 1 FROM default_acl_record_differences)
    AND NOT EXISTS (SELECT 1 FROM relation_inventory_differences)
    AND NOT EXISTS (SELECT 1 FROM relation_acl_differences)
    AND NOT EXISTS (SELECT 1 FROM function_inventory_differences)
    AND NOT EXISTS (SELECT 1 FROM function_acl_differences)
    AND NOT EXISTS (SELECT 1 FROM policy_differences)
    AND NOT EXISTS (SELECT 1 FROM authority_foreign_key_differences)
    AND NOT EXISTS (SELECT 1 FROM invalid_owners)
    AND NOT EXISTS (SELECT 1 FROM invalid_schema_owners)
    AND NOT EXISTS (SELECT 1 FROM row_security_differences)
    AND NOT EXISTS (SELECT 1 FROM invalid_rotation)
    AND NOT EXISTS (SELECT 1 FROM invalid_application_function)
THEN 'verified' ELSE format(
    'invalid migrations=%s roles=%s memberships=%s database_acl=%s schema_acl=%s default_acl=%s default_records=%s relation_inventory=%s relation_acl=%s function_inventory=%s function_acl=%s policies=%s authority_foreign_keys=%s owners=%s schema_owners=%s row_security=%s rotation=%s application_function=%s',
    (SELECT count(*) FROM migration_history_differences),
    (SELECT count(*) FROM role_differences),
    (SELECT count(*) FROM membership_differences),
    (SELECT count(*) FROM database_acl_differences),
    (SELECT count(*) FROM schema_acl_differences),
    (SELECT count(*) FROM default_acl_differences),
    (SELECT count(*) FROM default_acl_record_differences),
    (SELECT count(*) FROM relation_inventory_differences),
    (SELECT count(*) FROM relation_acl_differences),
    (SELECT count(*) FROM function_inventory_differences),
    (SELECT count(*) FROM function_acl_differences),
    (SELECT count(*) FROM policy_differences),
    (SELECT count(*) FROM authority_foreign_key_differences),
    (SELECT count(*) FROM invalid_owners),
    (SELECT count(*) FROM invalid_schema_owners),
    (SELECT count(*) FROM row_security_differences),
    (SELECT count(*) FROM invalid_rotation),
    (SELECT count(*) FROM invalid_application_function)
) END;
