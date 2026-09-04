-- RUN ALL: use the double-play toolbar action. Each statement should create its
-- own result tab; switching result tabs must not change this query tab.
SELECT 101 AS result_marker, 'first result'::text AS meaning;

SELECT
    teams.name AS team,
    count(projects.id) AS projects,
    coalesce(sum(projects.budget), 0) AS total_budget
FROM public.teams
LEFT JOIN public.projects ON projects.team_id = teams.id
GROUP BY teams.id, teams.name
ORDER BY teams.name;

WITH workload AS (
    SELECT
        projects.code,
        count(tasks.id) AS tasks,
        count(tasks.id) FILTER (WHERE tasks.status = 'done') AS completed
    FROM public.projects
    LEFT JOIN public.tasks ON tasks.project_id = projects.id
    GROUP BY projects.id, projects.code
)
SELECT code, tasks, completed
FROM workload
ORDER BY code;
