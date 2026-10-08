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

The original native viewport/navigation trigger still needs an independent UI
rerun after the corrected source is built through `./start.sh`, both HTTPS origins
are verified, and a new isolated lane owns the deployment lease. No application
deployment or UI acceptance is claimed by these unit checks.
