-- Current personnel / slate / slot materialized view.
-- Adds the current personnel and job-slot pay-band levels without changing its
-- current-date or certification semantics. Run against organization.public.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

DROP MATERIALIZED VIEW public.current_personnel_slate_status_slot_mv;

CREATE MATERIALIZED VIEW public.current_personnel_slate_status_slot_mv AS
WITH current_slates AS (
    SELECT
        slate_fact.id,
        slate_fact.personnel_id,
        slate_fact.job_slot_id,
        slate_fact.slate_update_date,
        slate_fact.slate_update_seq,
        slate_fact.slate_type,
        slate_fact.start_date,
        slate_fact.end_date,
        slate_fact.slate_id,
        slate_fact.org_id,
        slate_fact.active_range
    FROM public.slate_fact
    WHERE slate_fact.active_range @> CURRENT_DATE
), current_certifications AS (
    SELECT
        current_personnel.personnel_id,
        array_agg(DISTINCT certification_fact.certification_id)
            FILTER (WHERE certification_fact.certification_id IS NOT NULL) AS current_certification_ids,
        string_agg(DISTINCT certification.name::text, ', '::text ORDER BY certification.name::text) AS certifications_list,
        count(DISTINCT certification.name) AS certification_count
    FROM (SELECT DISTINCT personnel_id FROM current_slates) AS current_personnel
    LEFT JOIN public.personnel_certification_fact AS certification_fact
        ON certification_fact.personnel_id = current_personnel.personnel_id
        AND certification_fact.effective_date <= CURRENT_DATE
        AND (certification_fact.expiration_date >= CURRENT_DATE OR certification_fact.expiration_date IS NULL)
    LEFT JOIN public.certification_dim AS certification
        ON certification.id = certification_fact.certification_id
    GROUP BY current_personnel.personnel_id
), current_slot_counts AS (
    SELECT
        job_slot_fact.org_id,
        count(DISTINCT job_slot_fact.job_slot_id) AS total_current_slots
    FROM public.job_slot_fact
    WHERE job_slot_fact.slice_start_date <= CURRENT_DATE
      AND (job_slot_fact.slice_end_date >= CURRENT_DATE OR job_slot_fact.slice_end_date IS NULL)
    GROUP BY job_slot_fact.org_id
), current_filled_slot_counts AS (
    SELECT
        slate.org_id,
        count(DISTINCT slate.job_slot_id) AS filled_current_slots
    FROM current_slates AS slate
    JOIN public.job_slot_fact AS slot
        ON slot.job_slot_id = slate.job_slot_id
        AND slot.org_id = slate.org_id
        AND slot.slice_start_date <= CURRENT_DATE
        AND (slot.slice_end_date >= CURRENT_DATE OR slot.slice_end_date IS NULL)
    GROUP BY slate.org_id
)
SELECT
    slate.id AS slate_fact_id,
    slate.slate_id,
    slate.personnel_id,
    personnel.name AS personnel_name,
    personnel.email AS personnel_email,
    personnel.phone AS personnel_phone,
    personnel.job_field_id AS personnel_job_field_id,
    personnel_job_field.name AS personnel_job_field_name,
    personnel_job_field.description AS personnel_job_field_description,
    personnel.pay_band_class_id AS personnel_pay_band_class_id,
    personnel_pay_band.level AS personnel_pay_band_level,
    personnel_pay_band.min_pay_range AS personnel_pay_band_min_range,
    personnel_pay_band.max_pay_range AS personnel_pay_band_max_range,
    personnel_pay_band.is_manager AS personnel_pay_band_is_manager,
    personnel.certification_list AS personnel_certification_ids,
    slate.job_slot_id,
    slate.org_id,
    organization.name AS organization_name,
    organization.description AS organization_description,
    slate.slate_type,
    slate.slate_update_date,
    slate.slate_update_seq,
    slate.start_date AS slate_start_date,
    slate.end_date AS slate_end_date,
    slate.active_range AS slate_active_range,
    status.id AS slate_status_id,
    status.slate_status,
    status.status_start_date,
    status.status_end_date,
    slot.id AS job_slot_fact_id,
    slot.org_id AS job_slot_org_id,
    slot.manager_role,
    slot.job_field_id AS slot_job_field_id,
    slot_job_field.name AS slot_job_field_name,
    slot_job_field.description AS slot_job_field_description,
    slot.pay_band_class_id AS slot_pay_band_class_id,
    slot_pay_band.level AS job_slot_pay_band_level,
    slot_pay_band.min_pay_range AS job_slot_pay_band_min_range,
    slot_pay_band.max_pay_range AS job_slot_pay_band_max_range,
    slot_pay_band.is_manager AS job_slot_pay_band_is_manager,
    slot.required_certification_1_id,
    required_certification_1.name AS required_certification_1_name,
    required_certification_1.description AS required_certification_1_description,
    required_certification_1.type AS required_certification_1_type,
    slot.required_certification_2_id,
    required_certification_2.name AS required_certification_2_name,
    required_certification_2.description AS required_certification_2_description,
    required_certification_2.type AS required_certification_2_type,
    slot.slice_start_date AS slot_slice_start_date,
    slot.slice_end_date AS slot_slice_end_date,
    ROUND(
        100.0 * COALESCE(filled_slots.filled_current_slots, 0)
        / NULLIF(slot_counts.total_current_slots, 0),
        2
    ) AS organization_slot_fill_rate_percent,
    COALESCE(certifications.current_certification_ids, ARRAY[]::uuid[]) AS current_certification_ids,
    certifications.certifications_list,
    COALESCE(certifications.certification_count, 0::bigint) AS certification_count,
    (
        (slot.required_certification_1_id IS NULL OR slot.required_certification_1_id = ANY(COALESCE(certifications.current_certification_ids, ARRAY[]::uuid[])))
        AND (slot.required_certification_2_id IS NULL OR slot.required_certification_2_id = ANY(COALESCE(certifications.current_certification_ids, ARRAY[]::uuid[])))
    ) AS personnel_has_all_required_slot_certifications,
    personnel_pay_band.level = slot_pay_band.level AS personnel_pay_band_level_matches_slot,
    (
        (slot.required_certification_1_id IS NULL OR slot.required_certification_1_id = ANY(COALESCE(certifications.current_certification_ids, ARRAY[]::uuid[])))
        AND (slot.required_certification_2_id IS NULL OR slot.required_certification_2_id = ANY(COALESCE(certifications.current_certification_ids, ARRAY[]::uuid[])))
        AND personnel_pay_band.level = slot_pay_band.level
    ) AS personnel_matches_slot_requirements
FROM current_slates AS slate
JOIN public.personnel_dim AS personnel
    ON personnel.id = slate.personnel_id
JOIN public.job_field_dim AS personnel_job_field
    ON personnel_job_field.id = personnel.job_field_id
JOIN public.pay_band_class_dim AS personnel_pay_band
    ON personnel_pay_band.id = personnel.pay_band_class_id
JOIN public.org_dim AS organization
    ON organization.id = slate.org_id
JOIN public.slate_status AS status
    ON status.slate_id = slate.id
    AND status.status_start_date <= CURRENT_DATE
    AND (status.status_end_date >= CURRENT_DATE OR status.status_end_date IS NULL)
JOIN public.job_slot_fact AS slot
    ON slot.job_slot_id = slate.job_slot_id
    AND slot.org_id = slate.org_id
    AND slot.slice_start_date <= CURRENT_DATE
    AND (slot.slice_end_date >= CURRENT_DATE OR slot.slice_end_date IS NULL)
JOIN public.job_field_dim AS slot_job_field
    ON slot_job_field.id = slot.job_field_id
JOIN public.pay_band_class_dim AS slot_pay_band
    ON slot_pay_band.id = slot.pay_band_class_id
LEFT JOIN current_slot_counts AS slot_counts
    ON slot_counts.org_id = slate.org_id
LEFT JOIN current_filled_slot_counts AS filled_slots
    ON filled_slots.org_id = slate.org_id
LEFT JOIN public.certification_dim AS required_certification_1
    ON required_certification_1.id = slot.required_certification_1_id
LEFT JOIN public.certification_dim AS required_certification_2
    ON required_certification_2.id = slot.required_certification_2_id
LEFT JOIN current_certifications AS certifications
    ON certifications.personnel_id = slate.personnel_id;

CREATE UNIQUE INDEX current_personnel_slate_status_slot_mv_uq
    ON public.current_personnel_slate_status_slot_mv (slate_fact_id, slate_status_id, job_slot_fact_id);
CREATE INDEX current_personnel_slate_status_slot_mv_org_personnel_idx
    ON public.current_personnel_slate_status_slot_mv (org_id, personnel_id);

COMMIT;
