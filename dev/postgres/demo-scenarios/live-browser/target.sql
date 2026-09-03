CREATE VIEW public.project_workload AS
WITH task_rollup AS (
    SELECT
        project_id,
        count(*) AS task_count,
        count(*) FILTER (WHERE status = 'done') AS completed_count,
        sum(estimate_hours) AS estimated_hours,
        max(updated_at) AS last_activity_at
    FROM public.tasks
    GROUP BY project_id
)
SELECT
    projects.id AS project_id,
    teams.name AS team_name,
    projects.name AS project_name,
    users.display_name AS owner_name,
    coalesce(task_rollup.task_count, 0) AS task_count,
    coalesce(task_rollup.completed_count, 0) AS completed_count,
    coalesce(task_rollup.estimated_hours, 0) AS estimated_hours,
    task_rollup.last_activity_at
FROM public.projects
JOIN public.teams ON teams.id = projects.team_id
JOIN public.users ON users.id = projects.owner_id
LEFT JOIN task_rollup ON task_rollup.project_id = projects.id;

CREATE VIEW public.team_delivery_health AS
WITH project_metrics AS (
    SELECT
        projects.team_id,
        projects.id AS project_id,
        projects.budget,
        count(tasks.id) AS task_count,
        count(tasks.id) FILTER (WHERE tasks.status = 'done') AS completed_count,
        count(tasks.id) FILTER (WHERE tasks.status = 'blocked') AS blocked_count,
        sum(tasks.estimate_hours) AS estimated_hours
    FROM public.projects
    LEFT JOIN public.tasks ON tasks.project_id = projects.id
    GROUP BY projects.team_id, projects.id, projects.budget
)
SELECT
    teams.id AS team_id,
    teams.name AS team_name,
    count(project_metrics.project_id) AS project_count,
    sum(project_metrics.task_count) AS task_count,
    sum(project_metrics.completed_count) AS completed_count,
    sum(project_metrics.blocked_count) AS blocked_count,
    sum(project_metrics.estimated_hours) AS estimated_hours,
    sum(project_metrics.budget) AS total_budget,
    round(
        100.0 * sum(project_metrics.completed_count)
        / nullif(sum(project_metrics.task_count), 0),
        1
    ) AS completion_percent
FROM public.teams
LEFT JOIN project_metrics ON project_metrics.team_id = teams.id
GROUP BY teams.id, teams.name;

CREATE MATERIALIZED VIEW public.priority_queue AS
SELECT
    tasks.id AS task_id,
    projects.code AS project_code,
    projects.name AS project_name,
    tasks.title,
    tasks.status,
    tasks.priority,
    users.display_name AS assignee_name,
    tasks.due_at
FROM public.tasks
JOIN public.projects ON projects.id = tasks.project_id
LEFT JOIN public.users ON users.id = tasks.assignee_id
WHERE tasks.status <> 'done'
ORDER BY tasks.priority, tasks.due_at NULLS LAST;

CREATE UNIQUE INDEX priority_queue_task_id_idx
    ON public.priority_queue (task_id);
