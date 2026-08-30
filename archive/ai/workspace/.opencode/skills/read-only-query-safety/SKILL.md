---
name: read-only-query-safety
description: Use for PostgreSQL SQL, data inspection, catalog queries, or schema_read_query proposals requiring read-only query safety.
---

# Read-only Query Safety

- Propose one bounded, read-only statement. Do not propose DDL, DML, transaction control, `COPY ... PROGRAM`, locking clauses, or multi-statement SQL.
- Validate PostgreSQL-specific syntax before proposing the query. `DISTINCT ON` expressions must match the leading `ORDER BY` expressions; use aggregation or a subquery when distinct rows need a different final ordering.
- Prefer PostgreSQL catalog and `information_schema` queries with explicit columns and useful limits.
- Explain what data the query reads and why it is needed.
- Raw SQL requires approval in Schemii even when marked read-only.
- PostgreSQL read-only transactions prevent ordinary database writes, but functions can still produce external side effects. Warn the user before any function call whose behavior is not known to be side-effect free.
- Never claim the query ran or that displayed chat text approved it.
