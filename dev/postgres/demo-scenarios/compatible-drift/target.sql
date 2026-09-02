-- External additive drift on a different column should warn without colliding
-- with the desired projects.name type change.
ALTER TABLE public.projects
ADD COLUMN external_reference character varying(64);
