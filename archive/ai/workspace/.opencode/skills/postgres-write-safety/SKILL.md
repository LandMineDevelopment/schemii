---
name: postgres-write-safety
description: Use when the user asks the AI to insert PostgreSQL rows or create a new ordinary view through Schemii's proposal-gated preview and apply workflow.
---

# PostgreSQL Write Safety

- Select the exact listed profile and namespace. Never infer or invent a live target.
- These proposal tools are available only when **Data write** is enabled. If unavailable, tell the user to enable Data write and ask again; do not claim the feature is unsupported. Schema changes and Data read may be enabled at the same time in the same chat.
- Use `schema_insert_rows_preview` only for structured inserts into one table. Never generate raw INSERT SQL. Every row must have the same columns; omit defaulted columns from every row rather than sending null.
- Use `schema_create_view_preview` only for a new expected-absent ordinary view with one exact `CREATE VIEW` statement. Do not use `OR REPLACE`, materialized views, temporary views, or multiple statements.
- Preview proposals are read-only and require Schemii confirmation. Preview confirmation does not authorize the write.
- Only Schemii may issue the separate apply proposal for the reviewed durable plan. Never invent `postgres_write_apply`, a plan ID, or an apply result.
- Explain the exact target, row count or view identity, constraints, triggers, permissions, locks, and transaction risks before proposing the preview.
- If an apply outcome is uncertain, require reconciliation. Never propose retrying the same insert because it could duplicate rows.
