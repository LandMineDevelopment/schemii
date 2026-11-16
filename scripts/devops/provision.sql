-- ADMIN ONLY, on a separately approved empty cluster/database. Not a migration.
-- psql -X -v ON_ERROR_STOP=1 -v db_name=devops_analytics_NAME \
--   -v database_id=UUID -f scripts/devops/provision.sql postgres
-- Never apply against an existing database. CREATE DATABASE has no IF NOT EXISTS.
\set ON_ERROR_STOP on
-- Check all reserved names and inputs before the first persistent write.
SELECT (:'db_name' ~ '^devops_analytics_[a-z0-9_]{1,48}$'
    AND :'database_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND NOT EXISTS (SELECT FROM pg_database WHERE datname=:'db_name')
    AND NOT EXISTS (SELECT FROM pg_roles WHERE rolname IN
        ('devops_owner','devops_migrator','devops_collector','devops_reader','devops_maintenance'))
) AS safe_provision \gset
\if :safe_provision
\else
    DO $$ BEGIN RAISE EXCEPTION 'Refused: use fresh analytics database and reserved role names; preserve existing objects.'; END $$;
\endif
BEGIN;
CREATE ROLE devops_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE devops_migrator NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE devops_collector NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE devops_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE devops_maintenance NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT devops_owner TO devops_migrator;
COMMIT;
CREATE DATABASE :"db_name" OWNER devops_maintenance TEMPLATE template0;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO devops_migrator, devops_collector, devops_reader;
GRANT CREATE ON DATABASE :"db_name" TO devops_owner;
SELECT format('COMMENT ON DATABASE %I IS %L', :'db_name', 'schemii-devops-v1:' || :'database_id') \gexec
\connect :db_name
REVOKE ALL ON SCHEMA public FROM PUBLIC;
