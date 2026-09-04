-- SQL CONSOLE DEMO
--
-- 1. Put the cursor anywhere in this statement and press Ctrl/Cmd+Enter.
SELECT
    current_database() AS database,
    current_schema() AS namespace,
    now() AS checked_at;

-- 2. Highlight both statements below, then choose Run selection.
SELECT title, price, format
FROM books
ORDER BY price DESC
LIMIT 5;

SELECT status, count(*) AS orders
FROM orders
GROUP BY status
ORDER BY orders DESC;

-- 3. Put the cursor in this report to run only this complete WITH query.
WITH author_sales AS (
    SELECT
        authors.id,
        authors.name,
        count(DISTINCT order_items.order_id) AS order_count,
        sum(order_items.line_total) AS revenue
    FROM authors
    JOIN book_authors ON book_authors.author_id = authors.id
    JOIN order_items ON order_items.book_id = book_authors.book_id
    GROUP BY authors.id, authors.name
)
SELECT name, order_count, revenue
FROM author_sales
ORDER BY revenue DESC
LIMIT 10;

-- 4. Switch to Write transaction, highlight this final block, and run it.
-- The temporary table demonstrates writes without changing the seeded database.
-- The final ROLLBACK uses the same server action as the Roll back button.
CREATE TEMP TABLE console_demo_notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    note text NOT NULL
) ON COMMIT DROP;

INSERT INTO console_demo_notes (note)
VALUES ('This row exists only inside the open Console transaction.');

SELECT * FROM console_demo_notes;

ROLLBACK;
