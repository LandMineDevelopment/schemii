DO $migration$
DECLARE
    table_name text;
BEGIN
    FOR table_name IN
        SELECT c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND c.relname LIKE 'metadata\_%' ESCAPE '\'
          AND c.relname <> 'metadata_schema_migrations'
        ORDER BY c.relname
    LOOP
        EXECUTE format(
            'CREATE POLICY metadata_owner_maintenance ON public.%I TO schemii_metadata_owner USING (true) WITH CHECK (true)',
            table_name
        );
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
END
$migration$;

ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
