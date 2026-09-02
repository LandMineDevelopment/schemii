-- PostgreSQL independently adds the same logical column with a different type,
-- producing a server-derived three-way reconciliation conflict.
ALTER TABLE public.projects
ADD COLUMN migration_note character varying(80);
