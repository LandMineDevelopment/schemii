---
name: schemer-query-safety
description: Use for questions about data sources, queries, filters, visualizations, detail reports, or PostgreSQL execution safety.
---

# Query Safety

- Schemer accepts one exact verified table, view, or materialized view per widget.
- Queries are structured dimensions, measures, filter groups, sorts, and bounded limits.
- Raw SQL is permitted only through `schemer_read_query` in explicit data mode and must use the exact supplied profile, database, and namespace.
- Analytic SQL may join relations when needed, but joins are never valid in a widget's structured single-relation configuration.
- Permit one SELECT, WITH, VALUES, or TABLE statement. Never use EXPLAIN, transaction control, writes, schema changes, or potentially side-effecting functions.
- Query proposals are inert and require a separate browser confirmation before the bounded read-only route executes them.
- Rows may be considered only when supplied as the validated `queryResult` in the current data-mode context. Never repeat sensitive row values unnecessarily or use them outside data mode.
- PostgreSQL remains authoritative for source identity and columns.
- Source fingerprints and structured query validation must succeed before execution.
- Structured widget query execution remains parameterized by Schemer; caller-authored read-query proposals are separately validated and executed in a read-only transaction.
