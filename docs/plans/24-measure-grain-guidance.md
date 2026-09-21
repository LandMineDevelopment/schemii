# Optional tools to inspect and manage repeated measure records

Status: implementation plan; no runtime behavior changed.

Issue: https://github.com/LandMineDevelopment/schemii/issues/24

## Problem and existing behavior

If an order worth $100 has three items, summing the order value after joining
items can produce $300. COUNT can similarly repeat records, and AVG can weight
records differently according to how many children they have. Joined-row
analysis can also be intentional. Schemoo authors own the intended meaning of
their measures; repetition alone is not an error to correct.

## Product contract

Keep the existing nonblocking warning and the requested SQL behavior. Provide
on-demand tools to understand repetition and optional modeling tools to change
it when desired. No warning requires acknowledgement, review, a model edit or
a resolution before running, saving, refreshing, drilling or exporting. Do not
introduce a mandatory wizard, automatic DISTINCT, automatic deduplication or
automatic query/model rewrites. Existing structural, type and revision checks
remain in place; multiplicity introduces no new execution or save gate.

Schemoo's compiler already checks multiplicity from each measure's source in
`src/schemii/schemoo/prototype.py`. It currently emits text warnings and executes
the requested aggregation. Schemer identifies the relevant warnings by matching
text in `web/visualizations.js`. Schemoo already provides aggregate sources,
grouping/connection validation, and a cancellable editor in
`web/derived-dialog.js`. Reuse those contracts and validation boundaries.

## Proposed implementation

1. **Return structured, server-owned diagnostics.** Extend query plans with a
   stable diagnostic code, affected output/measure reference, contributing
   source, and the relationship/path that can repeat its records. Distinguish
   potential multiplicity from observed duplicate data. Preserve existing
   warning text for current consumers and do not change SQL merely because a
   diagnostic exists. Cover multiple affected measures and relevant branches;
   keep detection bounded by the existing model limits.

2. **Explain the consequence where the author sees the number.** Use the same
   diagnostic in Schemoo previews and Schemer tile configuration/results. Name
   the measure and relationship in plain language, explain which records may
   repeat, and provide an optional Inspect repetition action. Keep the existing
   warning nonblocking; opening the inspector is never required. Do not infer a numerical error
   from schema cardinality alone. Support keyboard navigation and narrow views.
   Keep intentional joined-row analysis available without silently dismissing
   future diagnostics after model changes.

3. **Offer optional modeling tools in Schemoo.** Only when requested, let the author inspect suitable existing
   aggregate sources or prepare a grouped-summary draft in Schemoo using the
   existing editor. Prefill the source, measure and grouping/connection mapping
   only when the compiler/catalog evidence makes them unambiguous. Show the
   proposed meaning (for example, one summary per order), filters and affected
   output before applying. Preserve unsaved drafts and leave saved models and
   dashboard tiles unchanged on cancel. For ambiguous groupings, explain the
   choices and let the author configure them manually. Unavailable suggestions
   must not label the report broken or interfere with continued use as authored.

4. **Revalidate the entire proposed report.** Creating a summary is not itself
   proof of correctness: later joins can repeat that summary, child dimensions
   can imply allocation choices, and averaging averages changes weighting.
   Recompile with the intended tile dimensions, scopes, filters and measure
   to show its remaining repetition and changed semantics; the author decides
   whether those semantics are desired. Avoid promising a universally safe fix.
   Never replace SUM with SUM(DISTINCT),
   treat equal values as duplicate records, or silently change COUNT semantics.
   Preserve zero/NULL behavior and the existing mixed-grain summary safeguards.

5. **Use existing save and revision workflows only for requested edits.** Apply model changes through
   Schemoo's normal validation and optimistic revision checks. Return the author
   to Schemer to review the updated model and explicitly select the intended
   output. Do not silently rewrite other dashboards or bypass the dashboard
   model-update review. Reuse the same compiler for previews, refreshes, drill
   plans and full exports; explain that drill rows may still repeat contributors
   and that full downloads use a fresh snapshot.

## Verification

- Compiler tests: SUM/COUNT/AVG across one-to-many and independent child branches;
  unique relationships that preserve records; NULLs and repeated equal values;
  row and summary-derived measures; MIN/MAX/COUNT DISTINCT behavior; and stable
  identification of each affected output and relationship.
- Semantic tests against PostgreSQL: a $100 order with three items, two distinct
  orders with equal values, unequal child counts for averages, multiple child
  branches, and required/optional filters. Assert both intended joined-row
  results and reviewed summary results. Include a summary that is multiplied
  again to ensure it is not falsely declared safe.
- Frontend tests: diagnostic rendering without string matching, prefilled
  review, existing-summary selection, unsupported/ambiguous choices, cancellation,
  unsaved model drafts, and revision conflicts.
- Nonblocking workflow tests: a report with a multiplicity warning can be saved,
  run, refreshed, drilled and exported without opening the inspector or accepting
  a remedy. Ignoring the warning or cancelling an optional edit preserves both
  configuration and SQL semantics, including intentionally repeated values.
- Browser checks: author review from a dashboard through Schemoo and back;
  keyboard and mobile layouts; explicit model/tile saving; consistent guidance
  in previews and drill views; and full CSV values.
- Run relevant Python/frontend suites and available PostgreSQL integration
  tests. Before showing implementation changes, rebuild only with `./start.sh`,
  wait for its checks, and verify both `https://localhost:8001` and
  `https://omarchy.taile4f57f.ts.net`. Report any unavailable checks explicitly.

## Scope and delivery

This draft contains only the plan. Implementation will stay in this branch/PR,
with focused compiler, authoring-UI and regression-test commits. General report
calculation, time analysis, ranking, pivoting and retained snapshots belong to
their separate issues. No new dependency is anticipated. Keep the PR draft until
the issue's workflow and verification are complete; use a closing reference only
when it fully resolves #24.

Start with structured detection details and optional access to existing summary
tools. Do not expand this issue into a forced correction workflow or a general
automatic metric-repair engine.
