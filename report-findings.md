# Schemer usability audit — 2026-09-23

Authenticated existing browser-test account, local canonical HTTPS; Chromium 1440×900 and 390×844. Disposable dashboard based on Accounts QA Sales (demo), plus duplicate of bookstore manual verification dashboard for date analysis. Original dashboards preserved. Every screenshot with `report-` prefix belongs to this audit; some early editor captures are full-page and include dimmed background below viewport. Prefer final viewport captures identified below for issues. No application source changes.

## Confirmed distinct issue: searchable-select inputs and dropdown toggles render as disconnected controls

Severity: medium usability/style consistency. Reproduce: open Schemer; create a dashboard; add a tile or edit an existing tile; inspect Analytics view, Add dimensions, Add measures. On desktop, the input is ~219 px wide while the Dimensions/Measures wrapper is 782 px; the dropdown toggle is anchored at the far right, leaving ~533 px of blank space between the text field and its associated arrow. Analytics view has the same defect. Mobile is less extreme but still shows detached dropdown arrows and an unexplained empty gap. The field menu works, but the visible hit areas no longer read as one control.

Evidence: `report-desktop-style-defect.png`, `report-mobile-style-defect.png`; also `report-desktop-time-options.png` demonstrates time controls with inconsistent field labeling/width.

Likely ownership: `src/schemii/common/web/assets/searchable-select.css` sets wrapper width 100%, input padding-right, and absolute toggle right:1px but does not give its input width:100%; Schemer does not supply the width that other host styles provide. Fix the reusable control's complete input geometry (width:100%, min-width:0, border-box) and align arrow inside the input border; preserve consistent visible field labels in forms. Verify desktop/mobile, wide/narrow grid cells, text truncation and long option lists.

## Shared issue for consolidated typography report

Configure tile footer Cancel and Apply & run computed font-size exactly 8px at both viewport sizes. The form/tab text is 12–13px. Footer primary action is disproportionately tiny and hard to read on a phone. Same evidence as above and `report-mobile-validation.png`. Consolidate with root's shared typography finding rather than creating duplicate issues.

## Coverage and successful workflow results

- Library switching; empty dashboard; create dialog/model selector; rename and duplicate dialogs; delete cleanup.
- Shared filter panel and filter chooser empty states.
- Created and executed all six kinds: detail, aggregation, bar, line, donut, KPI. Demo regional sum was 350 and detail showed two east rows.
- Each kind's field editor on desktop/mobile; each optional-filter tab on desktop/mobile; drill-through field editor for aggregations/charts/KPI.
- All six expanded views on desktop/mobile.
- Bar drill-through returned two contributing record IDs; result SQL desktop/mobile; cached CSV (`schemer-cached-rows.csv`) and full CSV (`schemer-full-results.csv`) download events succeeded.
- Temporal chart with weekly grouping, previous-period comparison, running totals; date-grouping options desktop/mobile; running-total expanded chart desktop/mobile.
- Empty tile validation desktop/mobile; create validation desktop/mobile.
- Tile more-actions menu; dashboard rename mobile, duplicate desktop.
- Loading state captured naturally during executions. No artificially injected network errors. Large-volume preview limits, runtime failure recovery, all temporal combinations, every export content value, real handset keyboard/gestures, or paid AI execution not tested in this sub-audit.

Useful coverage artifacts:
`report-desktop-dashboard-complete.png`, `report-mobile-dashboard-top.png`, `report-mobile-all-tiles.png`, `report-desktop-expanded-{Detail-report,Aggregation-report,Bar-chart,Line-chart,Donut-chart,KPI}.png`, corresponding mobile variants, `report-desktop-drill-results.png`, `report-mobile-drill-results.png`, `report-desktop-detail-sql.png`, `report-mobile-detail-sql.png`, `report-desktop-time-options.png`, `report-mobile-time-options.png`, `report-desktop-time-running.png`, `report-mobile-time-running.png`.

## Non-findings

Temporal selector was suspected of propagating clicks to tile expansion; actual click/keyboard selection in desktop/mobile did not open an expanded dialog. Do not file this as a defect. Mobile chart results require horizontal panning for many periods; no data corruption or inability to pan was established. CSV fresh-snapshot semantics are already covered by an existing issue and visible disclosure.
