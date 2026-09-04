-- PARSER EDGES: Run all. Embedded semicolons and comment text must not split a
-- statement. The execution should appear as one entry in server history.
SELECT 'a semicolon ; inside a string'::text AS quoted_text;

SELECT $message$another ; embedded semicolon$message$::text AS dollar_quoted_text;

/* This comment contains SELECT 999; and must not become a statement. */
WITH recursive countdown(value) AS (
    SELECT 3
    UNION ALL
    SELECT value - 1 FROM countdown WHERE value > 1
)
SELECT value
FROM countdown
ORDER BY value DESC;

SHOW server_version;
