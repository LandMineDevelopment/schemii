-- ROLLBACK: enable Write mode using the lock in the toolbar, then Run all.
-- Confirm Roll back in the transaction bar. Return here and run only the final
-- SELECT in Safe read; change_count should be unchanged.
UPDATE public.console_demo_notes
SET note = 'This edit should be rolled back',
    change_count = change_count + 100,
    updated_at = clock_timestamp()
WHERE scenario_key = 'transaction-demo'
RETURNING scenario_key, note, change_count, updated_at;

SELECT scenario_key, note, change_count, updated_at
FROM public.console_demo_notes
WHERE scenario_key = 'transaction-demo';
