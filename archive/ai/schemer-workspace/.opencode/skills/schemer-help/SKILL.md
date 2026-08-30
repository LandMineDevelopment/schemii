---
name: schemer-help
description: Use for Schemer dashboard, widget, visualization, detail-report, lineage, and workflow guidance.
---

# Schemer Help

- Schemer builds dashboards from one verified PostgreSQL relation per widget.
- Edit mode manages widget names, sources, structured queries, visualization, sorting, and detail reports.
- Data Lineage shows redacted source identity, generated SQL, and separately bound parameters.
- Persisted widgets remain structured, caller-SQL-free, and bound to one verified relation; joins for reusable widget sources belong in PostgreSQL views.
- Dashboard date slicers bind named half-open ranges to exact widget date/timestamp columns. Aggregate and detail results support their displayed JSON/CSV exports.
- The human Console supports role-permitted SQL in its reviewed transaction modes. Separately confirmed AI Data-mode analytic SQL may join relations, but it does not mutate persisted single-relation widget configuration.
- Record editing and migrations are not Schemer widget features. Proposal execution follows effective approval modes and operation-specific confirmation floors rather than one universal confirmation rule.
