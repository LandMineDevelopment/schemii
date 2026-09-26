# Manual harness validation — 2026-09-26

No application test suite was run. These were live harness checks against the
canonical HTTPS application, started only through `./start.sh`.

## Ten concurrent AI testers

Run `qa-muiwlg6s-983c5f33` used `--agents 10 --parallel 10 --controller codex
--agent-timeout 900 --headless`, retained QA accounts 01–10, the read-only Schemoo
harness track, and separate desktop (1280×800) / mobile (390×844) scenarios.

- Ten independent Codex processes and ten Chromium processes; independent review
  matched all 20 live PIDs and birth identities.
- Ten agent turns started within 318 ms, with peak active turns and processes both
  ten. Every lane performed actual identity, snapshot, resize and navigation
  actions during the overlap.
- Ten distinct accounts, cookies, storage markers and browser processes passed
  preflight. Logging out each account left the other nine authenticated.
- All ten workers exited 0; all 20 viewport checkpoints were recorded. Workers ran
  from 21:31:20 to 21:34:42 UTC. Stop left none of the recorded worker/browser PIDs
  live and preserved accounts, application resources and artifacts.
- Results: 12 function/style passes, two function-blocked/style-passed, four
  function-failed/style-passed, and two failed both. The run correctly retained
  `finished-with-gaps`; process completion does not mean application acceptance.
- Independent review viewed lane 2 and lane 8 desktop/mobile evidence and found
  their narrow visual observations supported by the screenshots.

Reports, screenshots, structured events, scoped session files and private agent
logs remain under `artifacts/qa/<run-id>/`; they are deliberately not committed.

## Findings and limits

Several testers attempted Filters before selecting a model. The workbench is
intentionally inert in that state: these are scenario-prerequisite mistakes, not
confirmed application defects. Historical failures remain preserved. The harness
snapshot/action diagnostics were subsequently improved to expose unavailable
controls. A persistent empty-state “Loading relationships…” label and lane 4's
unsaved-on-open/beforeunload behavior and clipped title need separate app triage;
this change does not attempt to fix them.

Accounts 06–10 have Schemoo-only access and no database fixtures. This run does
not prove SQL, pagination, chat/provider, concurrent application writes or all
product workflows. Harness probes separately exercised typing, dragging,
dialog acceptance/dismissal, downloads and screenshots.

## Other manual checks

- Two T3-dispatched testers completed independent desktop/mobile inspections;
  retained model revision counts stayed unchanged.
- Duplicate claims, cross-lane tokens, stale generations after recovery, early
  wave advancement and recovery during an active peer lane were rejected.
- Probe screenshots could not satisfy application visual checkpoints; missing
  viewport evidence and unrecorded scenarios could not silently pass.
- A second `./start.sh` correctly refused the live deployment lease. Source edits
  during startup were detected and prevented agent dispatch.
- The first external worker exposed a read-only sandbox socket denial. Its run
  was stopped; the implemented worker uses a lane-artifact workspace-write sandbox
  with networking enabled for the broker socket. A repeated single-worker run
  completed before the ten-worker run.
- Final manual run `qa-muiwsznf-0c298183` confirmed snapshots identify the inert
  workbench, selector and role-based clicks reject unavailable Filters immediately,
  available library controls still work, and SIGTERM/resume rebuilds and restores
  the correct identity while rejecting the pre-interruption session handle.
- Bash/Node syntax and CLI validation checks were used; no unit/E2E suite was run.

Isolation here separates cooperative testers' app sessions and ownership. It is
not an adversarial OS security boundary: workers share the host user. Explicit
stop/timeout drains tracked owned process groups; arbitrary background children
created after an unexpected worker exit are not comprehensively guaranteed.
