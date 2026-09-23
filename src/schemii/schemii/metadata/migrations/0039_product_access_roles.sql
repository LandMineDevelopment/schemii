-- Existing author roles retain access to each product. Database credentials
-- remain attached to their original account or explicitly managed role.
UPDATE metadata.auth_roles
SET capabilities = (capabilities - 'author') ||
    '["schemii:access", "schemoo:access", "schemer:access", "schemer:author"]'::jsonb
WHERE capabilities ? 'author';

-- Shared report roles previously had no application capability. Grant the
-- Schemer viewer capability only to roles that actually share dashboards.
UPDATE metadata.auth_roles AS role
SET capabilities = role.capabilities || '["schemer:access"]'::jsonb
WHERE EXISTS (
    SELECT 1 FROM metadata.auth_role_dashboards AS dashboard
    WHERE dashboard.role_id = role.id
) AND NOT role.capabilities ? 'schemer:access';

-- Account provisioning is a reserved role. The old is_admin column is kept
-- as a compatibility projection for existing account APIs.
INSERT INTO metadata.auth_roles (id, name, capabilities)
VALUES ('role_application_provisioners', 'Application provisioners', '["accounts:provision"]'::jsonb);

INSERT INTO metadata.auth_user_roles (user_id, role_id)
SELECT user_id, 'role_application_provisioners'
FROM metadata.auth_accounts
WHERE is_admin;

-- Preserve preexisting administrators' product access without making
-- provisioning authority imply any product or PostgreSQL privileges.
INSERT INTO metadata.auth_roles (id, name, capabilities)
VALUES ('role_legacy_admin_products', 'Existing administrator product access',
        '["schemii:access", "schemoo:access", "schemer:access", "schemer:author"]'::jsonb);

INSERT INTO metadata.auth_user_roles (user_id, role_id)
SELECT user_id, 'role_legacy_admin_products'
FROM metadata.auth_accounts
WHERE is_admin;
