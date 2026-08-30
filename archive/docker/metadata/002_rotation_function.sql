BEGIN;

DO $guard$
BEGIN
    IF current_user <> 'schemii_metadata_bootstrap' THEN
        RAISE EXCEPTION 'rotation function must be installed by schemii_metadata_bootstrap';
    END IF;
END
$guard$;

CREATE SCHEMA schemii_admin AUTHORIZATION schemii_metadata_bootstrap;
REVOKE ALL ON SCHEMA schemii_admin FROM PUBLIC;
GRANT USAGE ON SCHEMA schemii_admin TO schemii_metadata_migration;

CREATE FUNCTION schemii_admin.rotate_metadata_passwords(
    migration_password text,
    schemii_password text,
    schemer_password text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF migration_password IS NULL OR octet_length(migration_password) NOT BETWEEN 16 AND 256 OR migration_password !~ '^[A-Za-z0-9_-]+$'
       OR schemii_password IS NULL OR octet_length(schemii_password) NOT BETWEEN 16 AND 256 OR schemii_password !~ '^[A-Za-z0-9_-]+$'
       OR schemer_password IS NULL OR octet_length(schemer_password) NOT BETWEEN 16 AND 256 OR schemer_password !~ '^[A-Za-z0-9_-]+$' THEN
        RAISE EXCEPTION 'metadata passwords must contain 16-256 characters from [A-Za-z0-9_-]'
            USING ERRCODE = '22023';
    END IF;

    EXECUTE format('ALTER ROLE schemii_metadata_migration PASSWORD %L', migration_password);
    EXECUTE format('ALTER ROLE schemii_metadata_schemii PASSWORD %L', schemii_password);
    EXECUTE format('ALTER ROLE schemii_metadata_schemer PASSWORD %L', schemer_password);
END
$function$;

ALTER FUNCTION schemii_admin.rotate_metadata_passwords(text, text, text)
    OWNER TO schemii_metadata_bootstrap;
REVOKE ALL ON FUNCTION schemii_admin.rotate_metadata_passwords(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION schemii_admin.rotate_metadata_passwords(text, text, text)
    TO schemii_metadata_migration;

ALTER ROLE schemii_metadata_bootstrap NOLOGIN;

COMMIT;
