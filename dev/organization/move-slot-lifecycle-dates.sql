-- Organization HR: move slot-level lifecycle dates from temporal facts to slots.
-- Run against organization.public. Fails and rolls back on conflicting/missing data.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';
LOCK TABLE public.job_slot_dim, public.job_slot_fact IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
  IF current_database() <> 'organization' THEN
    RAISE EXCEPTION 'This migration only targets the organization database';
  END IF;
  IF EXISTS (
    SELECT job_slot_id FROM public.job_slot_fact GROUP BY job_slot_id
    HAVING min(slot_initialization_date) <> max(slot_initialization_date)
       OR min(slot_termination_date) <> max(slot_termination_date)
       OR count(slot_initialization_date) <> count(*)
       OR count(slot_termination_date) <> count(*)
  ) THEN
    RAISE EXCEPTION 'A slot has conflicting or missing lifecycle dates';
  END IF;
END $$;

ALTER TABLE public.job_slot_dim
  ADD COLUMN slot_initialization_date date,
  ADD COLUMN slot_termination_date date;

UPDATE public.job_slot_dim AS d
SET slot_initialization_date = f.initialized,
    slot_termination_date = f.terminated
FROM (
  SELECT job_slot_id, min(slot_initialization_date) AS initialized,
         min(slot_termination_date) AS terminated
  FROM public.job_slot_fact GROUP BY job_slot_id
) AS f
WHERE d.id = f.job_slot_id;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.job_slot_fact f
    LEFT JOIN public.job_slot_dim d ON d.id = f.job_slot_id
    WHERE d.id IS NULL
       OR d.slot_initialization_date IS DISTINCT FROM f.slot_initialization_date
       OR d.slot_termination_date IS DISTINCT FROM f.slot_termination_date
  ) THEN
    RAISE EXCEPTION 'Copied lifecycle dates do not match the original fact rows';
  END IF;
END $$;

ALTER TABLE public.job_slot_dim
  ALTER COLUMN slot_initialization_date SET NOT NULL,
  ALTER COLUMN slot_termination_date SET NOT NULL;
-- RESTRICT is intentional: do not silently remove dependent database objects.
ALTER TABLE public.job_slot_fact
  DROP COLUMN slot_initialization_date RESTRICT,
  DROP COLUMN slot_termination_date RESTRICT;
COMMIT;
