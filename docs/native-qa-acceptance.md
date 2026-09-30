# Native manual QA acceptance

`--browser native --controller t3` is an opt-in adapter for stock T3/Codex's
thread-owned `schemii_browser`. The harness remains the account/fixture/grant,
deployment-lease, claim, evidence and independent-review ledger. It closes its
transient readiness Chromium fleet before handing off. That readiness proof applies
only to those disposable browsers. Authenticated native isolation and application
acceptance must be demonstrated by the actual testers and reviewer below.

Legacy `--browser isolated` and its saved runs remain supported. Native lanes
reject `test.sh action`; their session file authorizes ledger operations, never
an unrelated legacy browser. Normal T3 preview tabs are shared and cannot be used
for this run. No alternate worker launcher or permanent browser controller is added.

## Prepare and assign

After generating the narrow disposable fixture manifest and verifying retained
accounts, prepare two testers plus an independent reviewer:

```bash
./test.sh prepare --browser native --controller t3 \
  --accounts qa_designer_012,qa_designer_013,qa_designer_014 \
  --reviewer-account qa_designer_014 --parallel 2 --runtime-slots 13 \
  --credentials-file /private/retained-credentials.json \
  --fixtures /private/pilot-fixtures.json --tracks lifecycle \
  --products schemii --viewports desktop,mobile --headless
./test.sh run --run RUN_ID
```

`--parallel` counts testers; the reviewer has its own reserved account and ready
lane. Supply observed runtime slots, never a configuration target. The native
adapter allows ten testers plus one reviewer, requiring coordinator capacity too.
A larger requested mission cannot silently count a smaller wave as equivalent.
Preparation uses only `./start.sh`, verifies local and Tailscale HTTPS, checks
permissions/resources and holds the existing deployment lease. Freeze source for
the entire run. Feature scenarios need their normal fixture resources/access
checks; a native browser does not grant application permissions.

Use the actual prepared checkout, run/lane IDs and native return ID. Each prompt
contains exactly one standalone assignment, with `qa.browser` set to `native`:

```text
SCHEMII_ASSIGNMENT {"kind":"ui-testing","task":"Complete the assigned pilot cases through visible UI","workspace":"/actual/prepared/checkout","owned_paths":["artifacts/qa/qa-REPLACE/lane-1"],"owned_resources":["qa:qa-REPLACE:lane-1"],"verification":["./test.sh checkpoint for every assigned scenario with viewed fresh evidence","./test.sh finish after browser_close and explicit case results"],"qa":{"run":"qa-REPLACE","lane":"lane-1","claim":"after-spawn-before-actions","browser":"native"}}
```

The reviewer uses `kind: review`, the separately prepared reviewer lane, and its
own evidence/resource. The guard rejects a tester assignment to that reserved
lane. It is a cooperative dispatch guard, not an OS isolation/security boundary.
Spawn first, claim the actual returned ID, then privately deliver only that
worker's session-file and brief paths:

```bash
./test.sh claim --run RUN_ID --lane LANE_ID --agent ACTUAL_NATIVE_ID
```

## Native worker sequence

1. From that thread's native tool output, obtain its own generated
   `artifacts/native-browsers/session-*/output/` path. A blank-page screenshot can
   establish this path; omit its filename. Bind the parent session directory:
   `./test.sh native-bind --session-file SESSION --args-json '{"directory":"/actual/session-DIR"}'`.
   The adapter checks private metadata, PID birth identities and session inode,
   and exclusively records a run/lane/agent binding. Other lanes/runs cannot reuse
   that connection. Metadata proves connection/process ownership; scheduler-to-
   connection binding still relies on the actual worker/coordinator observation.
2. Read only the `credentialFile` in this lane's brief, keeping its contents and
   tool transcript private. Navigate to `/account`, visibly sign in using ordinary
   native UI tools, resize to the declared viewport, snapshot the actual signed-in
   account, and take an unnamed PNG at CSS scale with `fullPage:false`. Record:
   `./test.sh native-auth --session-file SESSION --args-json '{"username":"ASSIGNED_ACCOUNT","url":"https://localhost:8001/account","file":"/own/output/page.png","viewport":{"width":1280,"height":800},"invocation":"actual account snapshot tool reference","note":"Expected assigned identity; actual visible signed-in identity"}'`.
   Authentication evidence is not product-scenario pass evidence.
3. For a newly UI-created prefixed workspace, immediately record `resource-receipt`
   with `kind`, exact `id`, `name`, `createdAt`, current `url`, fresh native PNG
   `file`/`viewport`, actual creation snapshot `invocation` and expected/actual
   `note` in `--args-json`. This persists an exact lane cleanup receipt. The name
   must use this fixture's prefix and creation must occur during this run; another
   lane's resource cannot be adopted. No raw app API call substitutes for creation.
   For evidence in this new workspace, `begin` additionally accepts
   `--args-json '{"url":"/?workspace=EXACT_RECEIPTED_ID"}'`. Only declared static
   fixture IDs or exact creation-receipted IDs are selectable; arbitrary routes
   remain rejected.

   Begin the exact scenario before captures:
   `./test.sh begin --session-file SESSION --scenario EXACT_ID`.
   An expected-denial case additionally supplies
   `--args-json '{"expectedState":"denied"}'`; the lane must explicitly declare
   that product denied. The expected route comes from the scenario/lane URL,
   including its workspace query. Begin records generation, agent, source,
   resources, route, viewport and a new scenario-attempt identity.
4. Use only ordinary `schemii_browser` navigation, snapshot locators, input,
   keyboard/pointer, dialogs, upload/download and screenshot tools. No arbitrary
   JavaScript or direct page/API requests. Execute every feasible assigned step,
   including saved-state readback, validation/error recovery and exact data oracles.
   Respect desktop/mobile fixture instructions; resize is layout emulation, not
   physical-phone/touch acceptance. Inspect saved state before replaying an
   uncertain write. A recovery brief contains pending cases and prior results.
5. Export only selected fresh PNGs before ten-minute expiry:
   `./test.sh capture --session-file SESSION --args-json '{"file":"/own/output/page.png","url":"https://localhost:8001/?workspace=EXACT","viewport":{"width":1280,"height":800}}'`.
   Copy the returned lane-relative evidence path into later commands. Files must
   come from the bound live connection's output, postdate begin, and have exact
   viewport PNG dimensions. Preserve previous evidence for reports; it cannot
   satisfy a new scenario/generation.
6. Actually inspect the fresh image inline or through `view_image`, then record
   `./test.sh inspect --session-file SESSION --evidence lane-1/image-ID.png --note 'Concrete expected and actual visible geometry/readability' --args-json '{"tool":"native-inline-image","invocation":"actual screenshot/image delivery reference"}'`.
   `view_image` is the other supported tool label. The receipt records the image
   hash and viewing/delivery attestation. It does not prove visual correctness;
   distinct review is still required. Hidden DOM text cannot be a visual oracle.
7. For downloads, use `capture` with `kind:"download"`, then
   `./test.sh inspect-download --session-file SESSION --evidence lane-1/download-ID.json --args-json '{"expected":{"json":{"EXACT":"VALUES"}}}'`.
   Exact `text`, byte count or SHA256 are also supported. CSV/JSON/SQL/text exports
   are bounded; content inspection is limited to 1 MiB. Expected values/content
   remain private; receipts retain hashes/results. A click/download path alone
   cannot prove correct bytes. A fixture can require this gate with `downloadOracle`.
8. Record findings as they occur and checkpoint functional/visual outcomes
   separately. Visual pass needs current-route/resource/viewport/scenario/source
   PNG and its inspection receipt. Checkpoint and finish recheck source identity,
   including changes after the last UI action. Initial failures survive reruns.
9. Visibly sign out of the assigned account, call native `browser_close`, then
   `./test.sh finish --session-file SESSION`. The harness observes no live owned
   Chromium descendants, deletes only its temporary per-lane login file, fences
   the session handle, and releases the account. Worker completion is execution
   completion, not independent acceptance or MCP transport shutdown.

## Independent review and release

The claimed reviewer logs into its own separate account/context and records its
own native binding/authentication. It may view a tester's declared evidence through
`view_image` and record `inspect --target-lane TESTER_LANE --evidence TESTER_IMAGE`
with actual invocation and expected/actual notes. The hash must still match the
registered image. Reproduce material findings using the reviewer's own fixture,
scenario begin and fresh inspected evidence; never take over a tester's account.

```bash
./test.sh review --session-file REVIEWER_SESSION --target-lane TESTER_LANE \
  --scenario EXACT_TESTER_SCENARIO --verdict accepted \
  --note 'Concrete independent observations and covered reproduction' \
  --evidence REVIEWER_OWN_REPRODUCTION_IMAGE
```

Use `--finding FINDING_ID` to adjudicate a material candidate with reviewer-owned
reproduction evidence. Verdicts are `confirmed-defect`, `prerequisite-mistake`,
`unsupported-action`, or `not-reproduced`; include provenance for external review.
An unadjudicated finding remains pending. Historical evidence/findings are retained;
issue closure or a reviewer name typed by a tester does not rewrite their history.
Every accepted attempt requires a distinct agent/account/lane/connection and a
hash-bound review of the exact current-source attempt image. Findings are separate
from acceptance; a confirmed defect cannot be an accepted passing attempt.

After workers close browsers and finish, the coordinator releases each finished
transport, then stops the ledger:

```bash
./test.sh native-release --run RUN_ID --lane LANE_ID
./test.sh report --run RUN_ID
./test.sh cleanup --run RUN_ID
```

This interface currently has no supported native agent/session-close tool.
`native-release` therefore records **harness-owned extension transport termination**,
not native thread closure: it rechecks the exact private connection path/inode/PID
birth, uses Linux pidfd to signal only that supervisor with SIGTERM, and waits for
supervisor, child, guardian and temporary directory removal. It never deletes
browser output. A live browser prevents release; call `browser_close` in its owning
thread first. Cleanup keeps the deployment lease if owned shutdown is unresolved.
Other native peers, user data, retained fixtures and selected exported evidence
remain intact. Completed/interrupt status does not close transports.

Reports separate execution and independently reviewed acceptance, with intended,
completed and reviewed coverage, findings and cleanup reasons. Old saved reports
without these receipts stay review-pending. No declaration in this runbook proves
live acceptance. Keep the legacy execution path until the pilot and subsequent
concurrent writable/paging/modal/upload/download/interrupted-write workflows pass
with independently reviewed desktop/mobile evidence and cleanup parity.
