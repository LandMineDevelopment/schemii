\set ON_ERROR_STOP on

-- Synthetic HR browser fixture. No production records are copied.
-- Install only into an empty public schema; subsequent launches preserve edits.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:test-seed:organization')::bigint);
SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')) AS install_organization \gset
\if :install_organization

CREATE TABLE public."certification_dim" (
    "id" uuid NOT NULL,
    "description" character varying(255) NOT NULL,
    "name" character varying(255) NOT NULL,
    "type" character varying(255) NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."job_field_dim" (
    "id" uuid NOT NULL,
    "description" character varying(255) NOT NULL,
    "name" character varying(255) NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."job_slot_dim" (
    "id" uuid NOT NULL,
    "slot_initialization_date" date NOT NULL,
    "slot_termination_date" date NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."job_slot_fact" (
    "id" uuid NOT NULL,
    "org_id" uuid NOT NULL,
    "manager_role" boolean NOT NULL,
    "job_field_id" uuid NOT NULL,
    "pay_band_class_id" uuid NOT NULL,
    "required_certification_1_id" uuid,
    "required_certification_2_id" uuid,
    "slice_start_date" date NOT NULL,
    "slice_end_date" date NOT NULL,
    "job_slot_id" uuid NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."org_dim" (
    "id" uuid NOT NULL,
    "name" character varying(255) NOT NULL,
    "description" character varying(255) NOT NULL,
    "parent_id" uuid NOT NULL,
    "hierarchy_level" integer NOT NULL,
    "slice_start_date" date NOT NULL,
    "slice_end_date" date NOT NULL,
    "org_initialization_date" date NOT NULL,
    "org_termination_date" date NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."org_hier" (
    "id" uuid NOT NULL,
    "parent_id" uuid NOT NULL,
    "child_id" uuid NOT NULL,
    "parent_level" integer NOT NULL,
    "child_level" integer,
    PRIMARY KEY ("id")
);

CREATE TABLE public."pay_band_class_dim" (
    "id" uuid NOT NULL,
    "is_manager" boolean NOT NULL,
    "level" integer NOT NULL,
    "max_pay_range" character varying(255) NOT NULL,
    "min_pay_range" integer NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."personnel_certification_fact" (
    "id" uuid NOT NULL,
    "personnel_id" uuid NOT NULL,
    "certification_id" uuid NOT NULL,
    "effective_date" date NOT NULL,
    "expiration_date" date,
    PRIMARY KEY ("id")
);

CREATE TABLE public."personnel_dim" (
    "id" uuid NOT NULL,
    "certification_list" uuid[],
    "name" character varying(255) NOT NULL,
    "job_field_id" uuid NOT NULL,
    "pay_band_class_id" uuid NOT NULL,
    "email" character varying(255),
    "phone" character varying(255),
    PRIMARY KEY ("id")
);

CREATE TABLE public."personnel_pay_band_class_fact" (
    "id" uuid NOT NULL,
    "pay_band_class_id" uuid NOT NULL,
    "start_date" character varying(255) NOT NULL,
    "end_date" character varying(255),
    "personnel_id" uuid NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE public."slate_fact" (
    "id" uuid NOT NULL,
    "personnel_id" uuid NOT NULL,
    "job_slot_id" uuid NOT NULL,
    "slate_update_date" date NOT NULL,
    "slate_update_seq" character varying(255) NOT NULL,
    "slate_type" character varying(255) NOT NULL,
    "start_date" date NOT NULL,
    "end_date" date NOT NULL,
    "slate_id" uuid NOT NULL,
    "org_id" uuid NOT NULL,
    "active_range" daterange,
    PRIMARY KEY ("id")
);

CREATE TABLE public."slate_status" (
    "id" uuid NOT NULL,
    "slate_id" uuid NOT NULL,
    "slate_status" character varying(255) NOT NULL,
    "status_start_date" date NOT NULL,
    "status_end_date" date,
    PRIMARY KEY ("id")
);

ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("org_id") REFERENCES public."org_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("job_field_id") REFERENCES public."job_field_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("job_slot_id") REFERENCES public."job_slot_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("pay_band_class_id") REFERENCES public."pay_band_class_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("required_certification_1_id") REFERENCES public."certification_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."job_slot_fact" ADD FOREIGN KEY ("required_certification_2_id") REFERENCES public."certification_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."org_dim" ADD FOREIGN KEY ("parent_id") REFERENCES public."org_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."org_hier" ADD FOREIGN KEY ("child_id") REFERENCES public."org_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."org_hier" ADD FOREIGN KEY ("parent_id") REFERENCES public."org_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_certification_fact" ADD FOREIGN KEY ("personnel_id") REFERENCES public."personnel_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_certification_fact" ADD FOREIGN KEY ("certification_id") REFERENCES public."certification_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_dim" ADD FOREIGN KEY ("job_field_id") REFERENCES public."job_field_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_dim" ADD FOREIGN KEY ("pay_band_class_id") REFERENCES public."pay_band_class_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_pay_band_class_fact" ADD FOREIGN KEY ("pay_band_class_id") REFERENCES public."pay_band_class_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."personnel_pay_band_class_fact" ADD FOREIGN KEY ("personnel_id") REFERENCES public."personnel_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."slate_fact" ADD FOREIGN KEY ("job_slot_id") REFERENCES public."job_slot_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."slate_fact" ADD FOREIGN KEY ("personnel_id") REFERENCES public."personnel_dim" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public."slate_status" ADD FOREIGN KEY ("slate_id") REFERENCES public."slate_fact" ("id") DEFERRABLE INITIALLY DEFERRED;

-- Three connected fictional employees exercise domains, joins and membership filters.
-- Certifications include expired, current and open-ended validity windows.

INSERT INTO public."certification_dim" ("id", "description", "name", "type") VALUES
    ('00000001-0000-0000-0000-000000000001', 'Certification 1', 'Certification 1', 'Certification 1'),
    ('00000001-0000-0000-0000-000000000002', 'Certification 2', 'Certification 2', 'Certification 2'),
    ('00000001-0000-0000-0000-000000000003', 'Certification 3', 'Certification 3', 'Certification 3');

INSERT INTO public."job_field_dim" ("id", "description", "name") VALUES
    ('00000002-0000-0000-0000-000000000001', 'Job Field 1', 'Job Field 1'),
    ('00000002-0000-0000-0000-000000000002', 'Job Field 2', 'Job Field 2'),
    ('00000002-0000-0000-0000-000000000003', 'Job Field 3', 'Job Field 3');

INSERT INTO public."job_slot_dim" ("id", "slot_initialization_date", "slot_termination_date") VALUES
    ('00000003-0000-0000-0000-000000000001', '2020-01-01', '2099-12-31'),
    ('00000003-0000-0000-0000-000000000002', '2020-01-01', '2099-12-31'),
    ('00000003-0000-0000-0000-000000000003', '2020-01-01', '2099-12-31');

INSERT INTO public."job_slot_fact" ("id", "org_id", "manager_role", "job_field_id", "pay_band_class_id", "required_certification_1_id", "required_certification_2_id", "slice_start_date", "slice_end_date", "job_slot_id") VALUES
    ('00000004-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000001', false, '00000002-0000-0000-0000-000000000001', '00000007-0000-0000-0000-000000000001', '00000001-0000-0000-0000-000000000001', '00000001-0000-0000-0000-000000000001', '2020-01-01', '2099-12-31', '00000003-0000-0000-0000-000000000001'),
    ('00000004-0000-0000-0000-000000000002', '00000005-0000-0000-0000-000000000002', false, '00000002-0000-0000-0000-000000000002', '00000007-0000-0000-0000-000000000002', '00000001-0000-0000-0000-000000000002', '00000001-0000-0000-0000-000000000002', '2020-01-01', '2099-12-31', '00000003-0000-0000-0000-000000000002'),
    ('00000004-0000-0000-0000-000000000003', '00000005-0000-0000-0000-000000000003', true, '00000002-0000-0000-0000-000000000003', '00000007-0000-0000-0000-000000000003', '00000001-0000-0000-0000-000000000003', '00000001-0000-0000-0000-000000000003', '2020-01-01', '2099-12-31', '00000003-0000-0000-0000-000000000003');

INSERT INTO public."org_dim" ("id", "name", "description", "parent_id", "hierarchy_level", "slice_start_date", "slice_end_date", "org_initialization_date", "org_termination_date") VALUES
    ('00000005-0000-0000-0000-000000000001', 'Org 1', 'Org 1', '00000005-0000-0000-0000-000000000001', 1, '2020-01-01', '2099-12-31', '2020-01-01', '2099-12-31'),
    ('00000005-0000-0000-0000-000000000002', 'Org 2', 'Org 2', '00000005-0000-0000-0000-000000000001', 2, '2020-01-01', '2099-12-31', '2020-01-01', '2099-12-31'),
    ('00000005-0000-0000-0000-000000000003', 'Org 3', 'Org 3', '00000005-0000-0000-0000-000000000001', 3, '2020-01-01', '2099-12-31', '2020-01-01', '2099-12-31');

INSERT INTO public."org_hier" ("id", "parent_id", "child_id", "parent_level", "child_level") VALUES
    ('00000006-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000001', 1, 1),
    ('00000006-0000-0000-0000-000000000002', '00000005-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000002', 1, 2),
    ('00000006-0000-0000-0000-000000000003', '00000005-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000003', 1, 3);

INSERT INTO public."pay_band_class_dim" ("id", "is_manager", "level", "max_pay_range", "min_pay_range") VALUES
    ('00000007-0000-0000-0000-000000000001', false, 4, '70000', 50000),
    ('00000007-0000-0000-0000-000000000002', false, 5, '80000', 60000),
    ('00000007-0000-0000-0000-000000000003', true, 6, '90000', 70000);

INSERT INTO public."personnel_certification_fact" ("id", "personnel_id", "certification_id", "effective_date", "expiration_date") VALUES
    ('00000008-0000-0000-0000-000000000001', '00000009-0000-0000-0000-000000000001', '00000001-0000-0000-0000-000000000001', '1999-01-01', '2000-01-01'),
    ('00000008-0000-0000-0000-000000000002', '00000009-0000-0000-0000-000000000002', '00000001-0000-0000-0000-000000000002', '2020-01-01', '2099-12-31'),
    ('00000008-0000-0000-0000-000000000003', '00000009-0000-0000-0000-000000000003', '00000001-0000-0000-0000-000000000003', '2020-01-01', NULL);

INSERT INTO public."personnel_dim" ("id", "certification_list", "name", "job_field_id", "pay_band_class_id", "email", "phone") VALUES
    ('00000009-0000-0000-0000-000000000001', '{}'::uuid[], 'Avery Example', '00000002-0000-0000-0000-000000000001', '00000007-0000-0000-0000-000000000001', 'employee1@example.test', '+1-202-555-0101'),
    ('00000009-0000-0000-0000-000000000002', '{}'::uuid[], 'Blake Sample', '00000002-0000-0000-0000-000000000002', '00000007-0000-0000-0000-000000000002', 'employee2@example.test', '+1-202-555-0102'),
    ('00000009-0000-0000-0000-000000000003', '{}'::uuid[], 'Casey Demo', '00000002-0000-0000-0000-000000000003', '00000007-0000-0000-0000-000000000003', 'employee3@example.test', '+1-202-555-0103');

INSERT INTO public."personnel_pay_band_class_fact" ("id", "pay_band_class_id", "start_date", "end_date", "personnel_id") VALUES
    ('0000000a-0000-0000-0000-000000000001', '00000007-0000-0000-0000-000000000001', '2020-01-01', '2099-12-31', '00000009-0000-0000-0000-000000000001'),
    ('0000000a-0000-0000-0000-000000000002', '00000007-0000-0000-0000-000000000002', '2020-01-01', '2099-12-31', '00000009-0000-0000-0000-000000000002'),
    ('0000000a-0000-0000-0000-000000000003', '00000007-0000-0000-0000-000000000003', '2020-01-01', '2099-12-31', '00000009-0000-0000-0000-000000000003');

INSERT INTO public."slate_fact" ("id", "personnel_id", "job_slot_id", "slate_update_date", "slate_update_seq", "slate_type", "start_date", "end_date", "slate_id", "org_id", "active_range") VALUES
    ('0000000b-0000-0000-0000-000000000001', '00000009-0000-0000-0000-000000000001', '00000003-0000-0000-0000-000000000001', '2020-01-01', 'Slate Fact 1', 'Permanent', '2020-01-01', '2099-12-31', '0000000b-0000-0000-0000-000000000001', '00000005-0000-0000-0000-000000000001', daterange('2020-01-01', '2099-12-31', '[]')),
    ('0000000b-0000-0000-0000-000000000002', '00000009-0000-0000-0000-000000000002', '00000003-0000-0000-0000-000000000002', '2020-01-01', 'Slate Fact 2', 'Temporary', '2020-01-01', '2099-12-31', '0000000b-0000-0000-0000-000000000002', '00000005-0000-0000-0000-000000000002', daterange('2020-01-01', '2099-12-31', '[]')),
    ('0000000b-0000-0000-0000-000000000003', '00000009-0000-0000-0000-000000000003', '00000003-0000-0000-0000-000000000003', '2020-01-01', 'Slate Fact 3', 'Acting', '2020-01-01', '2099-12-31', '0000000b-0000-0000-0000-000000000003', '00000005-0000-0000-0000-000000000003', daterange('2020-01-01', '2099-12-31', '[]'));

INSERT INTO public."slate_status" ("id", "slate_id", "slate_status", "status_start_date", "status_end_date") VALUES
    ('0000000c-0000-0000-0000-000000000001', '0000000b-0000-0000-0000-000000000001', 'Active', '2020-01-01', '2099-12-31'),
    ('0000000c-0000-0000-0000-000000000002', '0000000b-0000-0000-0000-000000000002', 'Active', '2020-01-01', '2099-12-31'),
    ('0000000c-0000-0000-0000-000000000003', '0000000b-0000-0000-0000-000000000003', 'Active', '2020-01-01', '2099-12-31');

\endif
COMMIT;
