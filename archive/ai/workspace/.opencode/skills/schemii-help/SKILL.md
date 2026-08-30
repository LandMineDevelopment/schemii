---
name: schemii-help
description: Use for help with Schemii concepts, workflow, saved schemas, introspection, design, query preview, migration preview, and confirmations.
---

# Schemii Help

Schemii designs saved PostgreSQL schemas, introspects live catalogs, previews read-only data queries, and previews or applies migrations. Explain workflows using these boundaries:

- PostgreSQL is authoritative for current live database state.
- The saved schema selected for the exact profile, database, and namespace is authoritative for intended state.
- Introspection may refresh semantic content but must preserve user-owned canvas layout.
- A tool call emits an inert action proposal for the application. The application validates target state and asks for any required approval or confirmation.
- Project create/open and saved-connection open operations use logical IDs and confirmed Schemii UI actions; they never grant filesystem or credential access.
- Creating a table in the saved design is supported through `schema_add_table` or `schema_populate` after Schemii confirms the proposal.
- Creating the designed table in PostgreSQL requires a separate exact-target migration preview and a server-issued apply proposal.
- Structured row insertion is supported through `schema_insert_rows_preview`; only Schemii may issue its separate apply proposal. Updates and deletes remain in SQL Console write mode.
- New ordinary-view creation is supported through `schema_create_view_preview`; only Schemii may issue its separate apply proposal. Replacement, deletion, and materialized views remain in the Views workspace.
- With **Data read** enabled, read-only data access may be proposed with `schema_read_query` against the exact target. Never use it for a write. Schema changes and Data write can be enabled independently or together in the same chat.
- Chat responses and tool output do not prove that an action completed.
- The no-argument launcher uses `ai-docker-db` and includes OpenCode. Explicit `ui`, `local-db`, and `docker-db` modes omit the sidecar.

When help turns into an action request, load the relevant safety skill and use the narrow proposal tool.
