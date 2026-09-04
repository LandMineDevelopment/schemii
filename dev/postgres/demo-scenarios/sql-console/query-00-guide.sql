-- SQL CONSOLE TEST GUIDE
--
-- Seven named tabs were loaded from the server-owned demo fixture. Your edits
-- remain browser-local unless you use Save query.
--
-- 01  Put the cursor in one statement, or highlight one or more statements.
--     Use Run current / Run selection (Ctrl/Cmd+Enter).
-- 02  Use Run all and switch among the result tabs.
-- 03  Pin and rename a result, run another tab, page through rows, and export.
-- 04  Enable Write mode, run all, then confirm Roll back.
-- 05  Enable Write mode, run all, then confirm Commit.
-- 06  Run all to exercise comments, embedded semicolons, dollar quotes, and CTEs.
--
-- Also try: rename this query tab, create/close a tab, Save query, and open the
-- query library to verify saved queries and execution history.

SELECT
    current_database() AS database,
    current_schema() AS namespace,
    current_user AS connected_as,
    now() AS started_at;
