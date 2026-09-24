# Schemoo usability audit — 2026-09-23

Live Chromium at https://localhost:8001/schemoo, desktop 1440×900 and mobile 390×844; authorized existing test account. 48 model-prefixed screenshots. Existing models preserved. Disposable QA Sales copies used for duplicate/save/query flows and all six cleaned via revision-checked DELETE (204). No warehouse writes or paid AI calls.

## Confirmed new findings

### P2: Validation does not identify or reveal the invalid model-editor fields

Reproduce both viewports: open Accounts QA Sales → Model filters → Add model filter → Fixed rule → Apply to model with no source selected. Footer says “Choose a valid source column for every condition.” Focus stays on Apply, zero aria-invalid elements, required source field partly below mobile footer. Derived variation: select Sales canvas header → Add calculated source → Calculation kind → Grouped summary → Apply. Generic amber warning requests name, source columns and grouping even though grouping already valid. Field-name control partly occluded on mobile after footer grows; missing source controls further down. Both errors do have role=alert; do not claim missing announcements.

Evidence: model-mobile-filter-validation.png, model-desktop-filter-validation.png, model-mobile-derived-validation.png, model-desktop-derived-validation.png. Verification logs model-validation.log record focus=Apply and invalid=[].

Fix: validate each field, mark invalid controls with aria-invalid and associated inline errors, focus and scroll first invalid input into the usable dialog body, only list actual failures, and use consistent validation presentation. Retest keyboard and touch with multiple fields and sticky footers.

### P2: Merely opening a saved model can create unsaved changes

Reproduce: /schemoo → Accounts QA Sales → do nothing. Desktop immediately says Unsaved changes; navigate away invokes beforeunload. Mobile hides draft status, leaving the automatic dirty state less apparent. The saved model has no persisted initial canvas positions; canvas.js lines 65–68 adds x/y, loadModel in prototype.js calls changed() after canvas initialization. Existing saved-layout models do not reproduce. Tests/e2e/schemoo-saved-previews.spec.js explicitly prepares real positions to avoid this unsaved-state behavior.

Evidence: model-desktop-model.png, model-mobile-model.png. Fix: treat generated initial layout as initialization, or persist it through an explicit supported layout initialization path; simply viewing a saved model should not produce an unsaved-change warning. Preserve warnings for real subsequent edits. Include missing-layout loaded models in navigation tests.

## Existing issue corroboration / shared findings

- #17 tall objects make fit-to-view labels unusably small: model-desktop-large-model.png / model-mobile-large-model.png (existing combined-current model). Do not create duplicate.
- AI assistant mobile clips unavailable-model guidance, reasoning and permissions text: model-mobile-assistant.png; parent consolidates typography/control-density issue. No AI completion sent.
- Model title hard-clips long names without ellipsis (model-mobile-large-model.png, desktop counterpart), reducing identification; minor compared with canvas #17.

## Coverage

Desktop and mobile screenshots: saved library/create-model entry; duplication dialog; one-table model; complex saved model; source inspector/exposed columns; alias editor; add-table picker; logical-relationship drawing mode; model filter landing; filter choice; report parameter editor; fixed-filter validation; row calculation editor; grouped summary editor/validation; preview field selection; report-filter editor; saved-preview dialog; generated SQL empty state; read-only query results; estimated and measured plans; model help; AI assistant.

Successful real workflows: duplicate QA model, save generated layout, add all exposed table columns to preview, run 2-row result query, Explain, Run & Analyze after confirmation. Editor validation correctly prevents invalid application. Modal layouts and fixed-footer actions remain reachable across both viewports.

Limits: library create-from-database not submitted; no deletion of existing models; alias, logical relationship, calculated source, and reusable filter changes not persisted into existing models; no paid AI request; no deliberate server failure injection or exhaustive PostgreSQL operation matrix; snapshots are representative screens, not every combination of field settings.
