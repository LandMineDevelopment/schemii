-- CURRENT STATEMENT: place the cursor anywhere inside one statement and use
-- Run current (Ctrl/Cmd+Enter). Only that complete statement should run.
SELECT 11 AS current_statement_marker;

SELECT
    projects.code,
    projects.name,
    projects.budget
FROM public.projects
ORDER BY projects.code;

-- SELECTION: highlight both statements below and use Run selection. You should
-- receive two result tabs, without running either statement above.
SELECT status, count(*) AS task_count
FROM public.tasks
GROUP BY status
ORDER BY status;

SELECT priority, count(*) AS task_count
FROM public.tasks
GROUP BY priority
ORDER BY priority;
