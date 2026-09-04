-- COMMIT: enable Write mode, Run all, then confirm Commit in the transaction
-- bar. Run only the final SELECT afterward in Safe read to verify durability.
-- To test typed transaction control instead, append COMMIT; and Run all.
INSERT INTO public.console_demo_notes (
    scenario_key,
    note,
    change_count,
    updated_at
)
VALUES (
    'transaction-demo',
    'Committed from the SQL Console demo',
    1,
    clock_timestamp()
)
ON CONFLICT (scenario_key) DO UPDATE
SET note = EXCLUDED.note,
    change_count = console_demo_notes.change_count + 1,
    updated_at = EXCLUDED.updated_at
RETURNING scenario_key, note, change_count, updated_at;

SELECT scenario_key, note, change_count, updated_at
FROM public.console_demo_notes
WHERE scenario_key = 'transaction-demo';
