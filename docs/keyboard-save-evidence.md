# Keyboard exposure and Save diagnostics

This is the source review and focused acceptance plan for
[#131](https://github.com/LandMineDevelopment/schemii/issues/131), part of
[#123](https://github.com/LandMineDevelopment/schemii/issues/123). The historical
desktop first attempt toggled `Expose people.field_35` with Space but left Save
disabled; its retry passed. The cause remains unresolved. Passing source checks,
or a future passing repetition, do not establish the historical cause.

The incoming diagnostic head was `2295344` on main `402ef8f`. This phase used the
linked `audit/keyboard-save` worktree. The diagnostic work was followed by an
explicitly assigned minimal Save-readiness fix in `prototype.js`, with main
`aacfdfc` merged to inherit PR #148's artifact policy. This phase did not start
the application, use browser tools to visit it,
authenticate, create a fixture or execute browser tests.

## Source findings and diagnostic boundaries

The checkbox's `change` listener in
[canvas.js](../src/schemii/schemoo/web/canvas.js) calls `setFieldExposure`, then
the `changed` callback. [model-state.js](../src/schemii/schemoo/web/model-state.js)
mutates the exposure allowlist and separates definition, layout and Explore
changes. [prototype.js](../src/schemii/schemoo/web/prototype.js) redraws the canvas,
then computes dirty state with `changedParts` and updates Save. Canvas redraw
captures the focused node/column before replacing cards and restores focus to
the replacement input. Model loading records generated layout as its clean
baseline. No defect in this path has yet been reproduced.

The spec keeps the original tall-field, scroll and relationship-anchor case and
its real Space press. Intermediate checks distinguish these stages:

| Stage | Faithful observation |
| --- | --- |
| Clean opening | Save disabled and exact saved revision status; exposure initially checked. |
| Keyboard delivery | Actual checkbox focus before Space; one trusted keydown, keyup, input and change; Space key and unchecked input/change values. |
| Redraw | The intended field remains unchecked and focused; event receipts record target connection, active field and workbench inert state. |
| Model mutation | `Unsaved changes` appears before checking that Save becomes enabled. |
| Real save | One UI click produces a successful semantic PUT and advances its revision exactly once. |
| Persistence | A separate real API GET observes the absent exposure; page reload reads an unchecked field and a clean saved state. |
| Ownership cleanup | Delete only this attempt's new model with its current expected revision; a subsequent GET must return 404. |

The original fixture still mocks immutable model GET/PUT, catalog, validation and
preview-list responses. It proves keyboard, geometry and dirty-state behavior;
it does not prove persistence. A separate case creates an owned real model using
the existing Organization browser fixture, edits `certification_dim.id`, saves,
reads the API, reloads and deletes that exact model. It does not mutate the
source database, retained connection, account or starter models. Creation and
cleanup are pending runtime verification.

Three controlled cases reuse the same keyboard assertion. Suppressing delivery
of the trusted change event to the model handler must fail specifically at the
dirty-state assertion, while the DOM checkbox is unchecked and Save remains
disabled. A held initial catalog response uses an explicit promise gate, not a
sleep; the editor must remain inert until the gate releases, then accept the
trusted toggle. A held real preview-list response must keep the dirty model's
Save button disabled, then release it and permit one real save, independent API
read, reload and owned-model cleanup. These controls are test perturbations, not claimed production
causes. They have been discovered but not executed.

An adjacent source mismatch was fixed at the Save owner: previously `save()` did
not disable Save while the preview library was pending, although `saveModel()`
returned without a write in that state. Model load starts that preview-list
request after making the workbench interactive; its pending callback refreshed
model-library actions rather than Save state. The button and handler now share
the saving/busy/conflicted/preview-pending condition. Preview pending changes and
query busy transitions refresh Save, preserving the existing dirty-state display
and clean-model behavior. Initialization is safe because preview-library creation
does not invoke its pending callback; rendering does not change pending state,
so refreshing Save from that callback introduces no render recursion. The new
controlled delayed-preview case detects the original enabled-but-rejected action
and requires recovery after readiness, but remains unrun. This fixes the
source-confirmed readiness inconsistency; it does not establish the historical
disabled-Save cause.

## Checks actually run

On Node 25.2.1, `node --check` passed for both edited JavaScript files. Exact Playwright list
discovery found five selected cases on each of `desktop-chromium` and
`android-chromium`, ten cases total. The repetition command's list-only mode
found exactly 80 cases: twenty original-fixture and twenty real-persistence
attempts per profile. List mode did not run global setup or launch a browser.
The existing focused `schemoo-model-state.test.js` passed all eleven cases in
72.5 ms in the final focused run, covering exposure mutation, definition/layout separation and clean
generated layout. These checks do not prove trusted browser event dispatch,
actual UI readiness, model persistence or automatic runtime cleanup.

## Pending acceptance and evidence

| Profile | Original keyboard case | Real API persistence | Missed-change control | Delayed-catalog control | Delayed-preview control |
| --- | --- | --- | --- | --- | --- |
| desktop-chromium | 0 of 20 run | 0 of 20 run | Not run | Not run | Not run |
| android-chromium | 0 of 20 run | 0 of 20 run | Not run | Not run | Not run |

The coordinator first assigns the deployment window, private evidence directory
and authenticated fixture ownership. Respect all active `./test.sh` leases. The
application is built/refreshed only by `./start.sh`; verify both
`https://localhost:8001` and `https://omarchy.taile4f57f.ts.net` before recording
that the source is deployed. Use an existing explicitly supplied mode-0600
administrator test credential file and verify the required Organization fixture
and grants. Do not bootstrap, reset data, create accounts or copy credentials
merely to make these commands work.

After that preparation, execute the following focused commands. Set
`SCHEMII_KEYBOARD_EVIDENCE` to the task-owned private evidence directory and
`SCHEMII_E2E_CREDENTIALS_FILE` to the assigned private credential file without
printing its contents. Create evidence files with `umask 077`. Run profiles
sequentially with one worker; the CLI retry override applies even under CI.

```bash
PLAYWRIGHT_JSON_OUTPUT_NAME="$SCHEMII_KEYBOARD_EVIDENCE/desktop-results.json" \
  npm run test:e2e -- tests/e2e/schemoo-model-editor-audit.spec.js \
  --project=desktop-chromium --workers=1 --retries=0 --repeat-each=20 \
  --grep '(missing saved positions stay clean; tall fields scroll with visible relationship anchors|keyboard Space exposure saves through the owned API model and stays changed after reload)$' \
  --reporter=line,json --output="$SCHEMII_KEYBOARD_EVIDENCE/desktop" \
  >"$SCHEMII_KEYBOARD_EVIDENCE/desktop.log" 2>&1

PLAYWRIGHT_JSON_OUTPUT_NAME="$SCHEMII_KEYBOARD_EVIDENCE/android-results.json" \
  npm run test:e2e -- tests/e2e/schemoo-model-editor-audit.spec.js \
  --project=android-chromium --workers=1 --retries=0 --repeat-each=20 \
  --grep '(missing saved positions stay clean; tall fields scroll with visible relationship anchors|keyboard Space exposure saves through the owned API model and stays changed after reload)$' \
  --reporter=line,json --output="$SCHEMII_KEYBOARD_EVIDENCE/android" \
  >"$SCHEMII_KEYBOARD_EVIDENCE/android.log" 2>&1

PLAYWRIGHT_JSON_OUTPUT_NAME="$SCHEMII_KEYBOARD_EVIDENCE/control-results.json" \
  npm run test:e2e -- tests/e2e/schemoo-model-editor-audit.spec.js \
  --workers=1 --retries=0 \
  --grep 'controlled (missed change|delayed catalog|delayed previews)' \
  --reporter=line,json --output="$SCHEMII_KEYBOARD_EVIDENCE/controls" \
  >"$SCHEMII_KEYBOARD_EVIDENCE/controls.log" 2>&1
```

Record the source and deployed source SHA, profile, repeat index, retry index
(always zero), first-attempt outcome and failing intermediate stage for every
case. Verify expected counts rather than treating a process exit as acceptance.
Keep all failures and interruptions; inspect persisted state before replaying an
uncertain write. Do not use `check()`, synthetic success events, sleeps, increased
timeouts, retries or extra full-suite samples to get green results. The forty
first-attempt repetitions per profile above cover two distinct cases; they are
focused reliability acceptance, not the ordinary-CI sample collection in #125.

Diagnostics attach only task-owned fixture identity, event metadata and bounded
UI state. Attachments, traces, screenshots, authenticated storage state and raw
logs remain private. Public issue/PR reports receive reviewed counts, stage
categories, cleanup outcomes and selected credential-safe evidence only, following
[#125](https://github.com/LandMineDevelopment/schemii/issues/125). Main `aacfdfc`
has been merged; its workflow uploads only validated timing JSONL and summaries,
with no whole Playwright result/report upload. That inherited policy is unchanged.
The private local recipe above does not authorize raw uploads. Confirm deletion
receipts for every created model; preserve the exact private fixture ID when
cleanup fails so the coordinator can recover only owned resources. Independent
review must distinguish these browser results from native manual UI acceptance
and verify the diagnostics catch the planted failure before #131 is closed.
