# Fit readiness regression

The authenticated native pilot for issue #73 found an enabled Fit control during
navigation to an owned local design. The tester resized a ready mobile canvas to
desktop, opened the inspector, navigated to that workspace, then clicked Fit
before the design finished loading. Fit ran against the cleared catalog and
reported “No live tables are available to fit.” The table remained clipped until
Fit was used after readiness. A separate reviewer reproduced the finding in its
own account and browser; stable ready-state fitting worked.

Fit now follows the existing `startupComplete`, `catalogLoading` and loaded
catalog boundary. The button is unavailable at startup, without a catalog and
during design/catalog loads. The click handler checks the same boundary before
fitting or showing an empty-state toast, including a queued click before the next
header update. An empty loaded desired design identifies desired tables; a live
catalog identifies live tables. Successful Fit still schedules camera persistence.
`CatalogCanvas.fit` and its geometry are unchanged.

The cheap behavior tests in `tests/frontend/fit-readiness.test.js` cover
startup, absent catalog, deferred desired/live responses, the snapshot-render to
load-completion interval, queued-click admission, desired/live empty copy and
actual `CatalogCanvas`/`GraphViewport` geometry at 390-pixel mobile and 1280-pixel
desktop widths with an inspector inset. They assert that blocked actions leave
the camera and persistence untouched, and that ready fitting places both table
cards inside the usable viewport.

Original source validation at `36c8858`:

- Focused readiness/canvas/viewport tests: 35 passed, 0 skipped; the independent
  review recorded 77.69 ms, plus 15 adjacent request/navigation/state cases.
- The original ordinary Node suite: 418 passed, 0 skipped. Discovery and the
  navigation inventory expanded later; this is historical scoped evidence, not
  a current complete acceptance result.
- Controlled counterexamples: removing the loading admission check failed both
  deferred-load tests; removing action admission failed behavior tests; restoring
  the incorrect desired-design empty copy failed its output oracle. Each temporary
  mutation was restored before the passing suite.
- JavaScript syntax and Git whitespace checks passed.

Current main has newer startup navigation ownership: a manual workspace can
finish loading before server readiness finishes, and the older startup request
must not reclaim that workspace. The additive integration preserves those
navigation changes and the existing Fit policy. A ready catalog remains blocked
while `startupComplete` is false; startup's final header update re-enables Fit
without replacing the user's selected catalog. A focused test covers this
transition with unchanged camera/toast/persistence before admission and one
successful Fit afterward. Adjacent navigation tests retain the actual request
ownership checks; these deterministic seams do not exercise mounted app wiring.

The coordinator inspects `./ci.sh --plan --base origin/main` and source-bound
local evidence to schedule remaining selected acceptance. GitHub Actions stays
disabled. Current focused command receipts remain private under the task's
`.schemii/issue-reconciliation/` directory; their scope does not replace complete
selected acceptance or the native trigger below.

Native run `qa-mv03xfs5-e3f6de4f` tested the integrated Fit source at `e6e17557`.
Both isolated accounts observed “Loading saved design” with Fit disabled in the
original mobile-to-desktop, inspector and owned-root sequence. Their pointer calls
completed after loading, and neither captured a loading PNG. The desktop timing
case remains blocked; ready Fit geometry and saved readback passed separately.
The dependent mobile timing case was not executed. Mobile creation's late
independent review receipt also remains pending under the recorded scenario-order
guard. These gaps are preserved in the private run receipts.

`tests/e2e/design-editor-lifecycle.spec.js` adds a separate mounted regression.
It creates and revision-checks one owned three-column desired design, primes real
mobile Fit, resizes to desktop and opens the inspector. Only then does it hold
one genuine saved-design response fetched from the server. While delivery is held,
it captures loading evidence, sends a visible mouse/touch action to the disabled
control and separately dispatches the mounted click callback. Both must leave the
camera unchanged and emit no false empty-state toast. The unchanged response is
then delivered, and ready Fit must place the actual table inside desktop inspector
and mobile viewport bounds. Cleanup uses only the exact newly created workspace
ID. There is no product delay flag, fabricated snapshot or app-state injection.

Independent source review at `006793f2` found that toast and camera assertions
alone could miss an uncaught callback error when the loading catalog is null.
The test now records `pageerror` before interactions and queries Playwright's
recorded page errors after synchronous callback/DOM observation. Both error lists
must be empty; any messages are retained privately before the assertion fails.
The immediate toast/camera checks and retained empty-toast text assertion remain.
The test removes its listener in cleanup without suppressing application errors.

This mounted case has not yet run. The coordinator schedules the whole affected
E2E owner on desktop and Android with explicit test-owned administrator credentials,
zero retries and a verified source deployment under the shared lease. A pass would
provide deterministic mounted admission and geometry evidence; it would not
relabel the native timing cases, supply their missing loading PNG, resolve the
late-review receipt or complete the broader selected acceptance. No application
deployment or UI acceptance is claimed by the deterministic development checks.
