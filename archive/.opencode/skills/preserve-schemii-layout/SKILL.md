---
name: preserve-schemii-layout
description: Schemii layout preservation guard. Use for saved schema JSON synchronization, PostgreSQL introspection refreshes, generated schema writes, migration delivery, or restarting the Schemii server after schema changes.
---

# Preserve Schemii Layout

Treat saved canvas layout as user-owned data. Database introspection may replace semantic schema objects, but it must never regenerate, normalize, auto-arrange, or silently replace established positions, colors, or viewport state.

## Locate The Schema Directory

Resolve the schema directory before reading or writing any record:

1. Use the absolute, expanded value of `SCHEMII_SCHEMA_DIR` when it is set.
2. Otherwise use `~/.local/share/schemii/schemas`.
3. If a command supplies another schema directory explicitly, confirm that it is the directory used by the server being tested.

The directory may be outside the repository. Do not substitute a repository-relative `schemas/` path, and do not copy records into the repository merely to edit them.

## Required Sequence

1. For a PostgreSQL-linked record, identify the exact profile, database, namespace, schema ID, and resolved schema file path. For a local-only design, identify the schema ID and file and record explicitly that no live target exists.
2. Stop the Schemii server before generated writes so browser tabs cannot race the synchronization.
3. Read the current record and snapshot its layout-bearing data to `/tmp/opencode/<schema-id>-layout-before.json`. Preserve `record.schema.layout` when present and the table-level `x`, `y`, and `color` values used by older records.
4. Introspect or synchronize semantic fields only.
5. Restore the exact snapshotted layout values without reformatting or deriving them from introspected table order.
6. Compare parsed JSON values to the snapshot and require equality before starting the server.
7. For a PostgreSQL-linked record, preview the saved schema against its matching database and namespace. Require zero unexpected steps and warnings when synchronization is expected. Skip migration preview for a confirmed local-only design.
8. Start exactly one server for lifecycle checks. Recheck every snapshotted table after browser reconnection. New layout entries are allowed only for newly added semantic tables; existing positions and colors must remain equal. Preserve the snapshotted viewport unless the user intentionally changes it after restart.
9. If a stale session receives `layout_conflict`, hard-refresh that tab. Never bypass the guard to make an old tab save.
10. Stop the server for delivery unless the user asks to leave it running. If it remains running, perform one final parsed-layout comparison immediately before reporting completion.

## Prohibited Actions

- Do not build a new layout from introspected table order.
- Do not accept default grid coordinates for tables that already have saved positions.
- Do not use a stale browser tab as the schema synchronization mechanism.
- Do not overwrite the snapshot after detecting drift.
- Do not intentionally alter positions, colors, or viewport without explicit user approval.
- Do not assume an external schema directory is disposable or safe to rewrite wholesale.

## Verification

Compare parsed JSON objects, not formatting. For records with a layout object:

```text
saved_record.schema.layout == snapshotted_record.schema.layout
```

For table-level layout, require every existing table's values to match:

```text
all(
    (saved_table.x, saved_table.y, saved_table.color)
    == (snapshot_table.x, snapshot_table.y, snapshot_table.color)
    for snapshot_table in pre_existing_tables
)
```

Run the server tests covering `layout_conflict`. The server rejects stale clients that attempt wholesale established-layout changes without the current layout token; intentional edits from current clients continue to work.
