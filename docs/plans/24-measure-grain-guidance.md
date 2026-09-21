# Optional tools to inspect and manage repeated measure records

Issue: https://github.com/LandMineDevelopment/schemii/issues/24

## Product contract

Repeated records can be intentional. Schemoo authors decide what their measures
mean. The existing warning remains nonblocking: saving, running, refreshing,
drilling and exporting require no acknowledgement, inspection or remedy.
Requested SQL is unchanged. No automatic DISTINCT, deduplication or model/query
rewrite is introduced. Existing structural, type and revision checks still apply.

## Implementation

The Schemoo compiler returns `repetitionDiagnostics` alongside existing warning
text. Each diagnostic identifies the output, measure and relationship paths
that may repeat its contributing records. Detection follows the existing
multiplicity rules, including unique keys, aliases and derived sources. This
is schema-level evidence of potential repetition, not a measurement of actual
duplicate data. All relevant multiplying branches are included in stable order.

Schemoo and Schemer share an optional **Inspect repetition** dialog. It explains
the affected measures and joins, including weighting for averages. Schemer opens
the corresponding model in a separate tab, preserving the report. Schemoo offers
existing related summary editors and **Create grouped summary** for physical
measures. Creation prefills the source, column and aggregation; authors choose
grouping and connection themselves. Derived measures are not automatically
translated into new calculations.

The summary action opens the existing cancellable draft editor. Applying a
summary does not replace selected report outputs. Saving and updating dashboards
use existing revision workflows. Authors select the desired outputs and preview
them to inspect any remaining repetition. Later joins can repeat summaries, and
averaging averages changes weighting; the tool makes no universal correctness
claim. Cancelling inspection or summary editing leaves the model unchanged.

## Verification

- Compiler tests cover SUM/COUNT/AVG, independent child branches, stable output
  references, alias labels, unique lookups, derived measures, NULLs and distinct
  records with equal values. SQLite and real PostgreSQL checks verify that
  diagnostics preserve the requested repeated values and weighting.
- Frontend tests cover structured-code selection, independent summary drafts,
  role-specific summary choices and unsupported derived inputs.
- Dashboard query tests retain diagnostics through ordering and allow drill.
- A live browser test creates disposable models and dashboards against the real
  API/database, exercises preview, optional inspection, summary cancellation,
  explicit creation/save/edit, dashboard refresh, drill and CSV download on
  desktop and Android-sized Chromium. It deletes only its own metadata fixtures.
- Existing dashboard and summary-authoring browser regressions remain relevant.
- Deployment uses only `./start.sh` and verifies the canonical local and Tailscale
  HTTPS origins. PR verification records report actual results and limitations.

## Boundaries

No new dependency, storage migration or execution gate. General report
calculation, time analysis, ranking, pivoting and retained snapshots remain
separate issues. This capability helps authors inspect and choose their models;
it does not automatically repair metrics.
