# Frontend behavioral oracle evidence

This work addresses [#129](https://github.com/LandMineDevelopment/schemii/issues/129)
at baseline `77af33c`. Packaging, security/header, immutable migration and SQL
contracts retain their existing tests.

| Invariant | Cheapest faithful check | Distinct mounted coverage | Controlled defect |
| --- | --- | --- | --- |
| A visible status toast stays inside the viewport, leaves workspace tools unobscured and lets pointer actions reach them | Mounted desktop/mobile geometry, computed pointer behavior and actual Views/Help clicks in `toast-history-behavior.spec.js` | Real application styles and controls; hidden DOM text cannot pass | Append the audit's later `.ui-toast { pointer-events: auto!important; top: 0!important; }` rule |
| Matching history confirmation updates revision/layout/history once without repainting the optimistic surface | Eleven deferred-response state-owner tests in `history-confirmation.test.js` | Hold real undo/redo responses; retain the mounted column field, value, focus, selection and existing table-editor draft behavior | Change only the confirmation's `render: !previewMatches` option to `render: true` |
| An authoritative mismatch applies once; an error rolls back once; stale responses cannot mutate a different workspace | Fast state-owner response-order tests | A controlled `design_changed` response rolls back and reloads one authoritative snapshot | Matching-render mutation fails both undo and redo assertions while the five other owner cases still pass |

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
module location and checked separately; the final tree retains both source
guards pending mounted validation.

Mounted browser verification and the toast CSS counterexample are pending the
coordinator's deployment window. Both existing source assertions remain until
their mounted replacements and controlled counterexamples are verified. The
history source assertion follows the extracted owner but remains a weak check.
These checks are automated
frontend regression evidence, not manual UI acceptance for #73 or a native QA
replacement proof.

PR #154's first Android attempt failed the toast overlap assertion while its
screenshot showed the toast below the visible layer switch and inside the
412-by-839 CSS viewport. The trace records closed Download and Help menus. The
original oracle counted every descendant button with a nonzero rectangle, which
does not establish visibility for collapsed `details` content in Chromium.
The corrected mounted check filters controls through Playwright's existing
visibility boundary, then reports named visible controls and their bounds on
failure. It retains the viewport, pointer behavior and actual Views/Help click
checks. No application style change is justified by this evidence. The trace does
not contain the per-control geometry result, so confirmation of the exact old
overlap and the corrected desktop/mobile baseline and late CSS counterexample
remain pending a leased deployment. Syntax validation and collection of all six
mounted cases passed; collection is not an application acceptance run.
