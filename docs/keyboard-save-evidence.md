# Keyboard exposure and Save diagnostics

Work for [#131](https://github.com/LandMineDevelopment/schemii/issues/131), part
of [#123](https://github.com/LandMineDevelopment/schemii/issues/123). The historical
retry-dependent Save-disabled cause remains unproven. The changes fix a confirmed
Save admission mismatch and strengthen diagnostics without claiming that cause.

## Product boundary

The exposure checkbox emits a trusted change event to `setFieldExposure`; the
canvas redraw restores focus, and `changedParts` determines dirty state. Tests
retain the real Space press and verify focus, checked state, trusted keyboard/
input/change delivery and synchronous model dirty state before Save readiness.

Previously Save could appear enabled while the preview library was pending,
although its handler rejected the action. `prototype.js` now computes button
readiness from the same saving/busy/conflicted/preview-pending condition and
refreshes it on pending/busy transitions. A held real preview response verifies
that admission remains disabled until readiness, followed by one actual save.
Clean-on-open behavior and the existing draft policy remain covered.

## Faithful test oracles

The tall-field case reads the current connected list, row and relationship
anchor together. It compares the anchor to its row or visible clipping boundary;
an already-bottom scroll need not change its position. Before a native mobile
gesture, the test checks the visible list/canvas/viewport intersection and actual
hit ownership. Desktop wheel and Android CDP touch remain real input. Persistent
missing-anchor controls exercise the same eventual alignment assertion through
its existing timeout. No retries, sleeps or longer timeouts hide failures.

The controlled missed-change case captures the trusted unchecked event and fails
specifically at the dirty-state owner. A later healthy redraw may re-check the
unchanged model's field; that readback cannot invalidate the captured event.
Controlled clipping, redraw and missing-input cases preserve distinct failures.

The mocked tall-field case proves event/geometry/dirty-state behavior. A separate
owned real API model proves one semantic PUT, revision advancement, independent
GET, reload persistence and clean saved state. No retained model, source database,
account, connection or grant is modified. An exact-ID ledger in `afterEach`
uses a fresh authenticated request context even after a body timeout, reads the
current revision, requires DELETE 204 and GET 404, and disposes that context.
The timeout-hook controls retain their primary failures and unsafe identities
never reach deletion.

## Observed verification

The frozen assembled source was `6a7600a596d07c3a0cefd232e22a0d8eb6edc8ed`;
served `canvas.js` and `prototype.js` byte-matched that source. This is automated
browser regression evidence, not manual/native application acceptance.

| Verification | Result | Boundary |
| --- | --- | --- |
| Original 20 mocked + 20 real repetitions per profile | Desktop 38/40 in 88.729s; Android 40/40 | Both desktop failures occurred at the old anchor-position assertion before keyboard input; original first attempts remain preserved |
| Original delayed/missed-change controls | 4/6 passed; 2 missed-change readback failures | Failures and images retained; neither was the historical disabled-Save failure |
| Earlier affected cases and real-persistence selection | 10/10 passed in 13.422s | Includes real Save/API/reload and delayed-preview readiness; four additional owned models |
| Final desktop/Android geometry selection | 2/2 passed in 3.506s | Current connected geometry and visible native gesture ownership |
| Final affected cases plus faithful controls | 12/12 passed in 26.911s | Includes persistent missing-anchor rejection, clipping, redraw and missing-input detection |
| Final missed-change reload-readiness selection | 2/2 passed in 2.313s | Captured missing model change and unchanged checked, clean state after reload |

All final focused attempts used one worker, zero retries and zero skips. Seven
fresh selected images were inspected independently. The additional Android
scrollTop-zero failure encountered while strengthening the guard remains in its
original receipt and trace; clipping/gesture readiness was then fixed at the test
boundary. Initial immediate missing-anchor observations were insufficient;
final controls prove persistent rejection with the actual polling oracle.

All 42 original owned models and four additional models were deleted using their
current revisions and independently verified absent; request contexts were
disposed. Seven synthetic body-timeout controls separately proved exact-ID cleanup
and rejection/disposal error reporting while retaining intentional body failures.
Focused source/state, syntax and diff checks passed. Independent review approved
final spec `29b841364ed7ecc9ffa751286174180c03f94c31` and inspected results,
images, diagnostics and cleanup receipts.

## Remaining limits and reproduction

The final oracle changes have focused verification, not a new 20-repetition
cohort. The original 80 attempts and six controls are preserved rather than
rewritten as final-code success. Ordinary required CI still supplies integrated
acceptance. Historical Save-disabled causation and long-run reliability remain
open; no full-suite speed improvement follows from these observations.

For a future bounded repetition, select the two original/real-save case names in
`tests/e2e/schemoo-model-editor-audit.spec.js` with `--repeat-each=20`, one profile
at a time, `--workers=1 --retries=0`. Use only an assigned deployed source and
private authenticated fixture state, respect deployment leases, and start only
through `./start.sh`. Default bootstrap is not authorization to replace a retained
account or grants. Preserve every first attempt, inspect uncertain persisted state
before replaying writes, and verify exact fixture cleanup.

Raw traces, screenshots, diagnostics and storage state stay private. Public
reports use reviewed counts, stage categories and cleanup outcomes; existing CI
uploads only validated timing JSONL/summaries. A native `browser_close` closes the
context; worker completion does not establish MCP transport shutdown.
