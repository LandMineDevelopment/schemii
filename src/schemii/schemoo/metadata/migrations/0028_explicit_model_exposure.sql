-- Freeze the old canvas selection as exposure; keep preview fields, measures,
-- ordering and all other model documents untouched. Explicit exposure already
-- authored by a user (including an empty list) must not be overwritten.
UPDATE schemoo.models AS model
SET definition = jsonb_set(model.definition, '{exposedFields}', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
        'table', selected.source_id, 'column', selected.column_name, 'aggregate', 'none'
    ) ORDER BY selected.position)
    FROM (
        SELECT field->>'table' AS source_id, field->>'column' AS column_name,
               min(ordinality) AS position
        FROM jsonb_array_elements(COALESCE(model.explore->'fields', '[]'::jsonb))
             WITH ORDINALITY AS fields(field, ordinality)
        GROUP BY field->>'table', field->>'column'
    ) AS selected
), '[]'::jsonb)),
revision = revision + 1,
updated_at = clock_timestamp()
WHERE definition->'exposedFields' IS NULL
   OR definition->'exposedFields' = 'null'::jsonb;
