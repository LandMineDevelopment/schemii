# Frontend behavioral oracle evidence

This work addresses [#129](https://github.com/LandMineDevelopment/schemii/issues/129)
at baseline `77af33c`. Packaging, security/header, immutable migration and SQL
contracts retain their existing tests.

| Invariant | Cheapest faithful check | Distinct mounted coverage | Controlled defect |
| --- | --- | --- | --- |
| A visible status toast stays inside the viewport, leaves workspace tools unobscured and lets pointer actions reach them | Mounted desktop/mobile geometry, computed pointer behavior and actual Views/Help clicks in `toast-history-behavior.spec.js` | Real application styles and controls; hidden DOM text cannot pass | Append `.ui-toast { top: 0!important; bottom: auto!important; }`; the geometry assertion fails while pointer events remain `none` |
| Matching history confirmation updates revision/layout/history once without repainting the optimistic surface | Eleven deferred-response state-owner tests in `history-confirmation.test.js` | Hold real undo/redo responses; retain the mounted column field, value, focus, selection and existing table-editor draft behavior | Change only the confirmation's `render: !previewMatches` option to `render: true` |
| An authoritative mismatch applies once; an error rolls back once; stale responses cannot mutate a different workspace | Fast state-owner response-order tests | A controlled `design_changed` response rolls back and reloads one authoritative snapshot | Removing either operation-generation or workspace-identity ownership fails its two targeted cases |

The history owner is extracted from `app.js` into `design-history.js` without
changing the current draft/conflict policy. The browser scenario tests both an
untouched optimistic field and the table editor's existing dirty-draft retention;
it does not establish a new concurrent-edit merge policy. The error reload remains
the application's existing policy for `design_changed`, `nothing_to_undo` and
`nothing_to_redo` errors. Existing real save/undo/redo/reload tests retain durable
persistence coverage.

The seven new owner cases passed in 69 ms; fifteen focused history/delta/workspace
cases passed in 73 ms. The full Node frontend/harness suite passed **402/402** in
757 ms. A temporary module/test copy with the forced repaint failed the two
matching-confirmation cases and passed the other five in 97 ms. Python's temporary
directory owner removed those counterexample files automatically. No mutation
framework or dependency was added.

Independent follow-up review isolated operation-generation and workspace identity
changes into separate success/error cases. The final eleven owner tests retain
the original combined-owner cases. Removing either ownership guard now fails its
two targeted cases; forcing a matching repaint still fails undo and redo. The
nineteen focused owner/delta/workspace checks passed in 64.54 ms.

The Python frontend suite passed 30 cases in 11.58 seconds while the history
source guard was temporarily removed. That guard was then restored at its new
module location and checked separately. That earlier checkpoint retained both
source guards before the assembled validation below.

## Assembled verification and replacement

On the frozen `6a7600a` deployment, the final tests passed all six desktop/Android
cases in **9.812s**, with zero retries or skips. They measure actual visible
control bounds and pointer behavior, click Views/Help, retain the original
mounted field/focus/selection through real deferred undo/redo responses, and
verify one authoritative conflict reload. The served history owner byte-matched
reviewed source `f49f5332`.

The exact original later override, `.ui-toast { pointer-events: auto!important; top: 0!important; }`,
failed the computed pointer assertion on both profiles in **17.871s**, with zero
retries or skips. It created no API fixtures.

Both separate geometry-only controls failed the intended overlap assertion in **4.102s**.
Independent review inspected their actual images and geometry receipts: the toast
covered visible tools while viewport containment and pointer-events checks still
passed. Hidden controls inside closed details no longer create false overlaps.
The exact offending rectangle from the older hosted failure was not captured;
that historical detail remains unverified.

All six owned fixtures from normal/assertion-failure verification were deleted
using their current revisions and independently verified absent. Two additional
controlled body-timeout attempts preserved their intentional failures, disposed
the original request context, and exercised the unchanged cleanup hook. Both
exact fixtures were deleted and independently returned 404; cleanup contexts
were disposed. No retained account, credential, grant or unrelated workspace was
changed.

The two Python source-substring tests are now retired. Their named guarantees
are protected by the mounted tests and eleven response-order owner cases,
including the forced-repaint and independent supersession controls. Packaging,
security/header, immutable migration and SQL contracts remain. Removing those
strings does not provide a measured speed gain; it replaces misleading coverage.

Successful normal runs retained assertion outcomes rather than screenshots.
Negative-control images were independently inspected. This is automated frontend
regression evidence, not broad manual UI acceptance for #73 or native QA
replacement proof.
