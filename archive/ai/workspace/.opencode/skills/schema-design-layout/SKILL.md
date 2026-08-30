---
name: schema-design-layout
description: Use when the user asks to create, add, design, populate, rename, update, relate, or delete saved-schema tables, columns, constraints, or relationships while preserving layout.
---

# Schema Design And Layout

- Use stable element IDs where a proposal schema requests them; names alone are not identity.
- Preserve all table positions, colors, and viewport state. Never propose regenerating, normalizing, or rearranging layout as part of a semantic change.
- Prefer PostgreSQL constraints for primary keys, foreign keys, uniqueness, checks, and nullability.
- Before risky type, nullability, default, or relationship changes, identify existing-row compatibility, table rewrite, lock, and validation concerns.
- Deletion is destructive. State what can be lost before emitting a delete proposal.
- Every write proposal requires confirmation in Schemii. Chat text is never confirmation.
- Use `schema_add_table` for one complete table in the active saved design.
- Use `schema_populate` for several complete tables and relationships in the active saved design. Populate means adding schema definitions, never inserting table rows.
- Saved-design tools execute only after Schemii confirms their durable proposal. They do not directly run DDL against PostgreSQL.
- To create designed tables in PostgreSQL, use the separate exact-target migration preview flow. Only Schemii may issue the apply proposal after preview.
- Row insertion and new ordinary-view creation use the separate `postgres-write-safety` skill and target-bound preview tools. Never treat a saved-design proposal as authorization for a PostgreSQL write.
