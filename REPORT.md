# Findings and coverage

The most disruptive confirmed defect is the mobile diagnostics chooser: search and Close are above the screen. Most tested core data workflows succeeded; the other findings concern readability, misleading controls, validation recovery and unnecessary unsaved-change warnings.

## Issues

Eight new issues filed; existing #17 updated. Every finding includes screenshots and concrete fix/acceptance criteria.

| Priority | Finding | GitHub issue |
|---|---|---|
| High | Diagnostics: keep the mobile entry picker and its Close/search controls on screen | [#48](https://github.com/LandMineDevelopment/schemii/issues/48) |
| Medium | Schemii: honor hidden state for conditional checkbox rows | [#49](https://github.com/LandMineDevelopment/schemii/issues/49) |
| Medium | UI: use a consistent readable type scale across schema forms, reports and diagnostics | [#50](https://github.com/LandMineDevelopment/schemii/issues/50) |
| Medium | Accounts: prevent mobile navigation from overlapping the Schemii wordmark | [#51](https://github.com/LandMineDevelopment/schemii/issues/51) |
| Medium | Schemer: keep searchable-select arrows attached to their input fields | [#52](https://github.com/LandMineDevelopment/schemii/issues/52) |
| Medium | Schemoo: focus and reveal invalid fields when model-editor validation fails | [#53](https://github.com/LandMineDevelopment/schemii/issues/53) |
| Medium | Schemoo: opening a model without saved positions should not create unsaved changes | [#54](https://github.com/LandMineDevelopment/schemii/issues/54) |
| Medium | Authentication: show actionable feedback for rejected sign-in credentials | [#55](https://github.com/LandMineDevelopment/schemii/issues/55) |
| Medium, existing | Tall-model canvas becomes unreadable when fitted | [#17, fresh screenshot evidence](https://github.com/LandMineDevelopment/schemii/issues/17#issuecomment-5805147384) |

## Completed coverage

| Area | Desktop and mobile examination | Executed workflows |
|---|---|---|
| Authentication | Sign-in, incorrect credentials, narrow screen | Valid sign-in and return to requested Schemer destination; bad credentials safely rejected |
| Connections/workspaces | Managers, create/edit forms, PostgreSQL namespace choice, rename dialog, empty/populated designs | Created and deleted two disposable local workspaces; preserved saved connections |
| Schema design | Tables, columns, value behavior, key/check/index/relationship editors, type/routine/trigger forms, Views and source analysis | Created table, undo/redo, created enum and view on both layouts; read-only migration review |
| Database browsing | Table data, full row preview, types, routines, object catalog, view relation/query story | Read-only seeded bookstore catalog and table rows |
| SQL | Draft/results/error, history/save dialog, estimated/measured plan, result preferences, raw mode, transaction inspector, COPY dialog | SELECT, deliberate invalid-column error, Explain, Analyze, raw SELECT and successful CSV download events on desktop/mobile |
| AI | Schemii and Schemoo assistant panels; model/reasoning/permission settings and history; admin provider/policy views | UI inspection only; no paid model completion, provider changes or execution of proposed actions |
| Schemoo | Library, source/exposure, aliases, logical-relationship entry, calculated sources, reusable/report filters, preview fields, saved preview, help | Duplicated/saved six disposable models, read results, Explain/Analyze; validation rejected incomplete forms |
| Schemer | Library/create/empty, rename/duplicate, filters, six tile editors, time options, all expanded views, SQL/drill | Created/executed detail, aggregate, bar, line, donut and KPI; correct demo drill rows; cached/full CSV downloads; weekly comparison and running totals |
| Account/admin | Account password form; Users/Databases/Roles/AI Connections/Diagnostics; user Account/Access/AI; role Permissions/Members/AI; staged AI policy editor | Read-only; no password/access/provider mutations |
| Diagnostics | System/API/database/internal lenses, entry chooser, expansion, explanation, source inspector; OpenAPI docs | Navigation and source inspection |

## Interpretation and limitations

This is a broad functional-surface and workflow audit, not exhaustive validation of every configuration or backend operation. The gallery documents inspected states rather than claiming every possible state was exercised.

- No actual schema migration, source-data write, COPY upload, commit/rollback with pending writes, password reset, user/role mutation, provider credential change or paid AI execution was performed. Those operations affect persistent data, access or cost.
- No installed administration-owned database profiles were available; populated-profile assignment/edit/testing was not exercised. Existing personal connection forms and namespace selection were inspected.
- No first-administrator bootstrap, every role permutation, network-failure injection, quota/cap load test, all temporal combinations, or full export-content reconciliation was performed.
- Browser mobile emulation cannot establish real handset keyboard, Safari, screen-reader or physical gesture behavior. A complete accessibility/conformance audit remains separate.
- Upload SQL, Restore examples and Shut down are explicitly unavailable capabilities. Local-design migration is intentionally disabled; these were not misreported as regressions.
- Some initial automation selectors/timings failed. Those were corrected or recorded as coverage gaps, not counted as product bugs. In particular, login destination preservation works; key instructions remain visible after using the actual Fit navigation; assistant dialogs work after their asynchronous load. These are not findings.
- The existing #17 canvas issue was corroborated; other earlier feature requests were not duplicated.
- No test-suite result is claimed. Verification here is direct browser interaction, screenshots, DOM measurements, read-only query/download results, asset fingerprints and successful cleanup responses.

## Suggested order

1. Fix offscreen mobile chooser and hidden-control CSS semantics.
2. Standardize readable form typography and connected dropdown geometry; repair mobile account navigation.
3. Improve model validation recovery and initial dirty-state handling; make invalid sign-in feedback actionable.
4. Address the existing large-model canvas presentation issue.

## Delivery

315 screenshots retained in the published gallery on `audit/ui-2026-09-23`. Original screenshot evidence is pinned to commit `f717a878dcc4a42c562a1965b8753381132aa958`. No application PR was created: this is an audit/evidence branch. GitHub issues are referenced by their canonical URLs; the installed T3 tools do not provide native issue-to-chat linking, so these are references, not verified native issue badges.
