-- Disposable launcher demo ONLY. These intentionally public passwords are not
-- deployment credentials: report_east / schemii-demo-east-only and
-- report_west / schemii-demo-west-only. Never install this fixture on a real DB.
-- Run as the demo bootstrap administrator, never the database's reporting login.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:accounts-demo')::bigint);
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'report_east') THEN
        CREATE ROLE report_east LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'report_west') THEN
        CREATE ROLE report_west LOGIN;
    END IF;
END
$$;
ALTER ROLE report_east LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
    NOREPLICATION NOBYPASSRLS PASSWORD 'schemii-demo-east-only';
ALTER ROLE report_west LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
    NOREPLICATION NOBYPASSRLS PASSWORD 'schemii-demo-west-only';
CREATE SCHEMA IF NOT EXISTS accounts_demo;
REVOKE ALL ON SCHEMA accounts_demo FROM PUBLIC;
GRANT USAGE ON SCHEMA accounts_demo TO report_east, report_west;
CREATE TABLE IF NOT EXISTS accounts_demo.sales (
    id integer PRIMARY KEY,
    region text NOT NULL,
    amount integer NOT NULL,
    secret text NOT NULL
);
INSERT INTO accounts_demo.sales (id, region, amount, secret) VALUES
    (1, 'east', 100, 'east-private'),
    (2, 'east', 250, 'east-private-two'),
    (3, 'west', 800, 'west-private')
ON CONFLICT (id) DO NOTHING;
ALTER TABLE accounts_demo.sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_demo.sales FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS report_region ON accounts_demo.sales;
CREATE POLICY report_region ON accounts_demo.sales FOR SELECT
    TO report_east, report_west
    USING (('report_' || region) = current_user);
REVOKE ALL ON accounts_demo.sales FROM PUBLIC, report_east, report_west;
REVOKE ALL (id, region, amount, secret) ON accounts_demo.sales FROM report_east, report_west;
GRANT SELECT (id, region, amount) ON accounts_demo.sales TO report_east, report_west;
COMMENT ON SCHEMA accounts_demo IS 'Disposable Schemii account security fixture; public local-demo credentials only.';
COMMIT;
