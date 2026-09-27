# Schemii manual QA issue screenshots

These eight selected screenshots document independently reproduced Schemii UI
findings from the September 26, 2026 manual QA runs. They contain synthetic QA
fixture names and no credentials. The full run reports, session handles and
credential files remain private under ignored `artifacts/` and `.schemii/`.

| Screenshot | Source run | Finding |
| --- | --- | --- |
| `migration-desktop.png`, `migration-mobile.png` | `qa-muj6mdet-5efdb7c3`, lane 6 | Reviewed migration rolls back with `migration_result_mismatch` |
| `local-console-toast.png` | `qa-muj4pi91-1c39e83b`, lane 1 | Detached local design shows unrelated SQL Console error |
| `view-browser-zero-columns.png`, `view-inspector-five-columns.png` | `qa-muj4pi91-1c39e83b`, lane 1 | Desired browser and view inspector disagree about output columns |
| `disabled-apply-styling.png` | `qa-muj4pi91-1c39e83b`, lane 1 | Disabled zero-change Apply action retains primary styling |
| `mobile-migration-sql.png` | `qa-muj4w9rl-2ae5955b`, lane 1 | Expanded SQL is difficult to read on mobile |
| `mobile-ai-header.png` | `qa-muj4pi91-1c39e83b`, lane 1 | Mobile AI settings truncate selected values |

The issue bodies include the reproduction sequence and expected/actual behavior.
Screenshots show observed UI state; they do not establish the cause of a defect.
