---
name: stock-t3-agents
description: Coordinate Schemii development, manual UI QA and independent review with stock T3/Codex subagents, explicit worktree ownership and isolated test lanes. Use for substantial separable project work or coordinated UI testing.
---

Use native agent tools and the project roles `developer`, `ui_tester` and
`qa_reviewer` when actually exposed. Read the
[assignment and activation protocol](../../../testing/agents/README.md). If a role is
unavailable, use an exposed native role with the same explicit assignment and
report the discovery limit.

- Inspect actual runtime capacity and reserve coordination and independent
  verification slots. Run `python3 testing/agents/doctor.py` for configuration
  and hook checks; only supply `--runtime-slots N` from the current interface.
  Stored capacity does not increase an already running conversation.
- Native children inherit `schemii_browser` from parent project configuration;
  each owns an isolated stdio/browser connection. Use its ordinary UI tools for
  that child's browser, not shared T3 preview tabs. Verify the configuration and
  mechanical isolation first; follow the native-browser section of the runbook.
  Omit browser output filenames, inspect returned images and copy only selected
  evidence to its assigned task location. Temporary output expires after ten
  minutes and is removed on connection shutdown. Call `browser_close` before
  finishing to release the heavy backend while preserving same-thread follow-up.
  When the browser connection is genuinely finished, export selected evidence
  and call `browser_release` with no arguments if actually exposed. It permanently
  ends only this MCP connection; browser follow-up cannot resume on that connection.
  On each resumed turn and before finishing, inspect the current exposed tools and
  connection state. A previous release or completed/interrupted thread status does
  not prove current transport cleanup: resumed work may receive a new connection
  from the host. Do not assume one exists or will be provided. Close/release only
  your current own connection as appropriate, preserving active peers and their
  data. Report actual acknowledgements and the resource lifecycle you observed;
  distinguish unchecked process/session removal from confirmed cleanup.
  Keep connections needed for follow-up reusable. Existing endpoints cannot reload
  new tools, and fresh children may inherit a stale parent allowlist: verify
  activation in a fresh provider session and report unavailable terminal controls
  or retained endpoints honestly.
- Give each developer a linked worktree/task branch and narrow owned paths.
  Native children share files; verify isolation before editing and preserve
  others' changes. Include exactly one `SCHEMII_ASSIGNMENT {JSON}` line in each
  spawn task, with actual workspace, ownership and focused verification steps.
- Apply the testing and verification reuse section of `AGENTS.md`. The coordinator
  owns acceptance scheduling: assign focused checks, record source-bound evidence
  and inspect existing current-head PR results. Reviewers inspect that evidence
  and reproduce material gaps; handoffs and merges do not justify another full
  local suite or a manually dispatched CI run. Use the shared change planner and
  retain the workflow's required profile and gate.
- For coordinated UI acceptance, follow the existing harness runbook to prepare
  `./test.sh --controller t3`. Dispatch ready isolated lanes, claim each to the
  actual returned agent ID, then send its private session file. Workers keep that
  account, handle and declared resources. Ordinary T3 tabs do not prove isolation.
- Require case-specific functional observations, inspected visual evidence and
  independent review. Preserve failed and blocked outcomes; process completion
  is not acceptance. Use a separate reviewer lane or review after the tester
  releases its lane; never share control of a lane.
- Inspect saved state before replaying uncertain writes. Respect deployment
  leases; runtime changes use `./start.sh`. Keep credentials and handles private.
  Finish/report/stop through the harness; clean only recorded owned objects.

Workers do not delegate recursively. Omit model/effort overrides and preserve
parent permissions unless the user explicitly chooses otherwise. Exact-definition
hook trust is required; failures can fail open, so configured files alone prove
neither active enforcement nor a passed check.
