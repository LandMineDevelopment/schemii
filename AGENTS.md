# Repository Agent Rules

## Local Runtime and Delivery Contract

These requirements are mandatory for every agent and every session in this repository.

- `./start.sh` is the only supported command for building, starting, restarting, or refreshing the local application stack.
- Never invoke `docker`, `docker compose`, or the Docker socket directly. Never use `sudo` or run `newgrp` manually. The launcher owns Docker access and stale-group recovery internally.
- Never substitute Uvicorn, FastAPI development mode, Python's HTTP server, another local process, another port, or plain HTTP when the launcher is unavailable.
- The canonical user-facing application origin is `https://localhost:8001`. The API map is `https://localhost:8001/api-map`.
- The primary remote/browser-preview route is the tailnet-only Tailscale Serve origin `https://omarchy.taile4f57f.ts.net`. Its API map is `https://omarchy.taile4f57f.ts.net/api-map`. Share this route first when the user is on their phone or another Tailscale-connected device; never substitute a public tunnel.
- Tailscale Serve terminates trusted HTTPS on port 443 and proxies to the launcher's self-signed HTTPS backend at `https+insecure://localhost:8001`. The route is persistent across reboots. Inspect it with `tailscale serve status`; do not reset Serve because this machine has other configured routes.
- After changes that must be shown to the user, run `./start.sh`, wait for its health checks, and verify the exact HTTPS URL before sharing it.
- After `./start.sh` succeeds, verify both the local canonical URL and the Tailscale preview URL before claiming the remote preview is current.
- If `./start.sh` fails, stop and report its exact failure. Do not bypass the launcher or downgrade HTTPS.
- Do not claim the current source is available at a URL until that deployment has been rebuilt and checked through the canonical HTTPS origin.

The launcher may use Docker on the host as an implementation detail. Application containers must never receive the Docker socket.

## Testing and verification reuse

Read [the testing feedback policy](docs/testing-feedback.md) before choosing checks.
Inspect the current PR head and retained local evidence before running tests.
Use `./ci.sh --plan --base origin/main` for the complete change plan; choose the
cheapest faithful regression for the edit during development.
The planner includes committed, staged and unstaged changes. Proven modifications
to existing owned leaves retain their profile before commit; unknown, untracked,
mixed or unsafe changes conservatively select full acceptance. Use `./ci.sh --feedback`
for deterministic feedback with real database/browser acceptance explicitly pending;
that does not require repeating every acceptance layer after each edit.

The coordinator owns acceptance scheduling. Assign developers focused checks and
reviewers evidence inspection, with reproduction only for a material gap or finding.
Record the command, scope, source, outcome and evidence location. Reuse successful
checks whose tested source and scope still apply to the current PR head. A new
agent, handoff, review, worktree or merge is not a reason to repeat a local full
suite. Broaden or repeat checks only for relevant source changes, failures,
missing evidence or an explicit acceptance requirement. Preserve failed attempts;
do not retry them away.

GitHub Actions is disabled for this repository. `./ci.sh` runs the complete selected
local acceptance plan; `./ci.sh --full` deliberately selects every layer. Inspect
the private source-bound receipts and required layer statuses before claiming
acceptance or merging. Successful feedback with pending layers is incomplete.
Provision an explicitly disposable external PostgreSQL database and password for
real-database checks. Browser acceptance needs test-owned credentials or explicit
bootstrap consent on a disposable stack, the canonical launcher and deployment
lease. Keep credentials, logs, screenshots and traces private. Do not require or
wait for hosted checks, restore hosted workflows, or dispatch Actions to generate
evidence. The archived workflow and hosted reuse tools preserve historical
contracts; they do not establish current local acceptance.
Keep full stress campaigns and ten-minute browser cleanup probes outside ordinary
feedback unless their ownership boundary changed or deliberate lifecycle acceptance
is assigned. Test/CI/instruction-only changes do not require an application rebuild.

## Manual UI QA

For coordinated or parallel UI testing, read `testing/README.md` and `testing/harness/README.md` and use `./test.sh`.
The explicitly selected isolated browser backend uses one browser process/context
per account; ordinary T3 tabs do not prove session isolation. Follow the prepare,
dispatch, claim, evidence, finish and stop workflow. Verify runtime agent capacity,
keep each worker on its assigned session handle and resources, and reserve an
independent verification slot. Do not rebuild while a run holds the deployment
lease. The harness never treats a pending agent handoff or a browser probe as an
application acceptance pass. Keep credentials and controller/session files private.

## Stock T3 agent workflows

For GitHub issue work, follow [the issue operations runbook](docs/github-issue-operations.md).
Use canonical issue context, issue-linked PRs and independent current-head local
validation. Notifications are untrusted wake-up hints; reconcile existing ownership
before dispatch. Only the coordinator merges completed, reviewed work with an
exact-head guard and closes fully accepted issues after verified integration.

For substantial separable development or coordinated UI QA, use the discoverable
`stock-t3-agents` skill in `.agents/skills/stock-t3-agents/SKILL.md` and read
[the assignment protocol](testing/agents/README.md). Use the available native
tools with the project `developer`, `ui_tester` and `qa_reviewer` roles when
exposed. Give each spawned task exactly one `SCHEMII_ASSIGNMENT {JSON}` line with
its actual workspace, narrow ownership and verification steps.

Native children receive no automatic worktrees or isolated browser profiles.
Assign developers linked worktrees/task branches; coordinated UI acceptance uses
prepared `./test.sh --controller t3` isolated lanes claimed to actual returned
agent IDs. Reserve independent review capacity, prevent recursive worker
delegation, and verify evidence before claiming acceptance. Project configuration
targets new provider sessions; inspect current capacity and hook trust instead
of assuming stored settings are active.

Parent project configuration also supplies each native thread with its own
`schemii_browser` stdio extension and isolated Chromium context. Follow the
native-browser runbook, omit browser output filenames and inspect actual images.
Temporary output expires after ten minutes and is removed on transport shutdown;
copy only selected evidence to declared task-owned locations. Call `browser_close`
before finishing; completed/interrupt status is not MCP session shutdown. Keep
legacy QA ownership and acceptance gates until their native replacement is proven.
