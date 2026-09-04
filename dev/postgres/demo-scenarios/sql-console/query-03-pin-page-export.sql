-- LARGE RESULT: put the cursor in this first statement and run it. Pin and
-- rename its result, then put the cursor in the small second statement and run
-- again. The pinned large result should remain beside the new result. Scroll
-- through it to load every page automatically, then use Export JSON.
SELECT
    number AS row_number,
    md5(number::text) AS deterministic_value,
    number % 7 AS group_number,
    clock_timestamp() AS observed_at
FROM generate_series(1, 250) AS generated(number)
ORDER BY number;

SELECT count(*) AS project_count
FROM public.projects;
