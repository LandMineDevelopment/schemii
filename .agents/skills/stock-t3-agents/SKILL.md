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
- Give each developer a linked worktree/task branch and narrow owned paths.
  Native children share files; verify isolation before editing and preserve
  others' changes. Include exactly one `SCHEMII_ASSIGNMENT {JSON}` line in each
  spawn task, with actual workspace, ownership and focused verification steps.
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
