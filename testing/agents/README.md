# Stock T3 agent workflows

Use stock T3 Code and the installed Codex native subagents for focused development,
UI testing and independent review. The project supplies `developer`, `ui_tester`
and `qa_reviewer` roles plus the discoverable `stock-t3-agents` skill. These files
do not modify T3 or its update mechanism, choose a model/effort, or change accounts.

## Activate and check

The trusted project `.codex/config.toml` requests 12 concurrently open spawned
threads, excluding the primary, and V1 nesting depth 1. This is configuration for
new provider sessions, not proof of live capacity. V2 ignores `max_depth`; workers
must not delegate recursively. Inspect the current interface's actual capacity
and reserve a coordinator slot and an independent reviewer slot. A four-slot
interface permits two active testers; the isolated harness allows at most ten.

```bash
python3 testing/agents/doctor.py
# Supply N only from the current agent interface, never the configured target.
python3 testing/agents/doctor.py --runtime-slots N --require-ready
```

The doctor reads installed Codex configuration and hook status without starting
AI work or printing credentials. `--require-ready` requires capacity for ten
workers plus coordination/review and a trusted dispatch guard; a smaller runtime
can still run fewer workers safely. It does not prove simultaneous inference.

Codex requires exact-definition review before non-managed hooks run. In Codex CLI
from this checkout, use `/hooks` to inspect and trust the project hooks, then check
the doctor again. A changed definition needs review again. T3 currently has no
verified hook-trust control here; this workflow does not manufacture trust or use
a bypass flag. Hooks can be skipped or fail open, so inspect loading errors and
do not claim enforcement from stored configuration alone.

Codex 0.159 loads linked-worktree hooks from the primary repository's `.codex/`
directory. The branch's hook files become discoverable across worktrees after
they land in that primary checkout. A branch-local doctor can load its agent
settings while reporting no hooks until then. Check the doctor after delivery
and trust review; a standalone checkout discovers its own `.codex/hooks.json`.

Use a new focused T3 chat in the checkout containing these files, with the same
chosen account, model, effort and permissions. Its first Codex turn starts a fresh
app-server and loads configuration. Sending another turn in an unchanged existing
session can reuse its old process. A desktop restart is unnecessary. Verify actual
capacity and role discovery in the new session; use available native roles with
the same assignment if custom roles are not exposed. Do not put `--worktree` in
T3's Codex Launch arguments: that flag belongs to the top-level CLI, not app-server.

Role sandbox values are defaults. Parent live permission overrides may supersede
them, including the reviewer's read-only default. Native children share the host
filesystem, and filesystem permissions do not isolate browser/MCP sessions.

## A browser for each native agent

The parent project configuration registers `schemii_browser`, a standard local
Microsoft Playwright MCP extension. Codex 0.159 creates its own stdio connection
for every native thread, including developer, tester and reviewer children. Each
connection starts an isolated Chromium process and fresh cookie/storage context;
there is no shared profile, CDP attachment, HTTP browser server or separate Codex
worker launcher. These browsers run headlessly and return screenshots to agents;
they are separate from T3's embedded preview. Stock T3 and its updates stay intact.

Warm the exact package once, then check the configuration:

```bash
npx --yes --prefer-online @playwright/mcp@0.0.83 --version
python3 testing/agents/doctor.py --runtime-slots 13 --require-native-browser-config
python3 testing/agents/verify_browser_isolation.py --clients 12
python3 testing/agents/verify_browser_cleanup.py
# Deliberate lifecycle acceptance; takes ten real minutes, outside PR feedback.
python3 testing/agents/verify_browser_cleanup.py --include-expiry
```

The launcher requires `chromium` on PATH and starts the cached package offline.
It never installs a browser, changes system packages or starts the application.
The pin contains its own Playwright dependency, leaving the application's existing
E2E package unchanged. A missing dependency blocks startup with a useful reason.
New provider sessions load the parent configuration. Role-only MCP settings are
ignored by this installed Codex version; do not assume current documentation for
newer clients changes that behavior. The current T3 tool surface has no exposed
MCP-reload control, so use a fresh provider session and confirm actual tools.

The verification command creates twelve ephemeral Codex thread-owned MCP clients
without AI turns. It checks browser state, output ownership, image transport and
cleanup. It is mechanical readiness evidence, not twelve AI testers, simultaneous
inference or application acceptance. Account/grant/fixture, uncertain-write,
scenario evidence and independent-review checks still matter when replacing the
legacy execution layer. Keep its saved data until those requirements pass.

Use ordinary UI tools: navigation, input, keyboard/pointer, dialogs, upload and
screenshots. Arbitrary JavaScript and direct page API tools are excluded from the
agent tool allowlist; WebMCP is disabled. Omit `filename` for screenshots,
snapshots, find and console output: automatic captures return inline content or
remain inside that connection's private output.
The assignment hook rejects named output paths when loaded and trusted. Inspect
fresh images; a saved path alone is not visual verification.

Temporary output lives in a unique private `artifacts/native-browsers/session-*`
directory. A 20 MiB soft file budget removes older output after responses; the
current response can exceed it. Files also expire after ten minutes, checked at
least every thirty seconds while the connection runs. Copy only selected report
or finding evidence to its declared task-owned location before expiry/shutdown.
Session ownership metadata sits outside the evicted output directory.

Export selected evidence before calling `browser_close`. A successful close now
stops that connection's Playwright backend and cleanup helper and removes its
temporary session directory. Only the small Python stdio endpoint stays alive for
stock Codex follow-up: the next browser call starts a fresh isolated backend and
repeats its genuine MCP initialization. Open browser contexts and active calls are
never discarded merely because they are idle. Failed calls are never replayed.

Always call `browser_close` before a worker finishes. A completed or interrupted
turn can retain its MCP connection for follow-up; it is not transport shutdown.
When the browser connection is genuinely finished, export selected evidence and
call the project-owned `browser_release` tool with no arguments. This terminal
operation cleans only this endpoint's backend, helper and disposable directory,
fully writes its success response, then exits the stdio endpoint. It rejects
target arguments and release while requests, callbacks, initialization or queued
backend traffic are pending. Cleanup or response-write failure is explicit;
failed calls are never replayed. Use `browser_close` when same-thread browser
follow-up is needed. Terminal release requires a fresh thread for future browser
use; ordinary turn completion alone is not a reason to destroy a reusable
connection.

The tool is advertised through standard MCP `tools/list` and enabled in project
configuration. Existing endpoints and provider tool lists do not reload it.
A fresh child can inherit its parent's old allowlist, so verify the actual
inventory in a fresh provider session before claiming activation. If the tool
is unavailable, close the backend and report the retained endpoint; do not
claim transport closure. No T3 source change or private host shutdown RPC is used.

Closing the native agent/session, when supported, ends the transport and removes
its temporary directory. Stock Codex can kill the launcher before normal cleanup
finishes. A small connection-owned cleanup process survives that targeted shutdown,
closes all inherited transport descriptors, waits for its recorded supervisor and
child identities to end, removes only its own output and exits. Normal disconnect
also reaps that helper. The launcher forwards shutdown only to its own captured
process group and enters bounded cleanup immediately on SIGINT/SIGTERM, escalating
an ignoring child to SIGKILL after five seconds. Signals cannot interrupt that
finalizer. On later startup it removes only validated orphan sessions whose
recorded owners have stopped; active, unknown, symlinked and user-owned directories
remain untouched. No permanent cleanup service or browser/controller daemon is added.

The cleanup verification uses disposable stock Codex app-servers and ephemeral
threads, without AI turns, app writes or a replacement browser launcher. It
exercises normal transport exit, browser-close backend release and same-thread
follow-up, the 20 MiB soft budget
including an oversized current image, supervisor and app-server SIGKILL, and
recovery after the supervisor and guardian both die. A separate live peer stays
usable throughout. It checks captured PID/start-tick identities, never process
names, and never manually removes a disposable session directory. The optional
expiry run waits for a fresh screenshot to age ten real minutes. Keep that long
run and destructive disposable lifecycle checks outside ordinary CI. Native
turn interruption still requires an actual coordinator interruption/follow-up;
the current collaboration surface exposes no session-close control. Neither
completion nor interruption implies MCP transport shutdown.

For a coordinator-owned, bounded terminal control, run
`python3 testing/agents/verify_browser_cleanup.py --cwd "$PWD" --terminal-only`.
It uses fresh disposable stock clients to test active-backend and
close-then-release success acknowledgements, complete endpoint cleanup, a live
peer, exactly one rejected same-thread browser follow-up, and fresh-thread use.
It does not repeat the unchanged ten-minute TTL, budget or forced/orphan campaign.
The result is mechanical lifecycle evidence, not Schemii application acceptance.

With thirteen available slots, twelve children can each own a browser. For manual
acceptance reserve one of those children for independent review: at most eleven
active testers plus that reviewer. The legacy isolated runner still caps its own
runs at ten; this is not a limit of the native browser extension.

## Assign native workers

Follow the [shared testing and verification reuse policy](../../AGENTS.md).
The coordinator owns acceptance scheduling; each assignment names focused checks
and records their source, command, scope, outcome and evidence. Developers use the
[change planner and feedback inventory](../../docs/testing-feedback.md). Reviewers
inspect actual existing evidence independently and reproduce material gaps instead
of repeating a complete suite by default. New threads, handoffs and merge delivery
do not justify duplicate local suites or manually dispatched CI. Required current-head
workflow checks and manual scenario/fixture ownership gates still apply.

Prepare a developer's linked Git worktree and task branch before dispatch. Read
the existing changes and assign narrow files; never switch another chat's checkout
or revert others' work. Native spawning does not create a worktree. For a small
shared-checkout task, `shared_checkout_exclusive: true` is an explicit cooperative
ownership exception, not a checked lock; use it only when the coordinator has
established exclusive access to those paths.

Every spawn message includes exactly one standalone single-line assignment:

```text
SCHEMII_ASSIGNMENT {"kind":"development","task":"Fix the assigned editor regression","workspace":"/absolute/path/to/task-worktree","owned_paths":["src/schemii/schemii/web/assets/design-editors.js"],"owned_resources":[],"verification":["Run the focused editor regression tests and report actual results"]}
```

Replace the example workspace and files with actual assigned paths. Supply normal
task instructions around that line, including why the change is needed. The guard
validates the contract, not the quality or completion of the work.

| Field | Meaning |
| --- | --- |
| `kind` | `development`, `research`, `ui-testing` or `review`. |
| `task` | A bounded, nonblank objective. |
| `workspace` | An existing absolute checkout in this repository. |
| `owned_paths` | Literal narrow relative paths, without traversal or globs. |
| `owned_resources` | Explicit resource labels; declare at least one path or resource. |
| `verification` | Focused steps or observable results; strings are recorded, never executed by the guard. |
| `shared_checkout_exclusive` | Optional boolean for development only. |
| `qa` | Required for `ui-testing`; also supplied for live UI `review`. |

Developer assignments need owned paths and a linked worktree on a task branch
unless the explicit shared-checkout exception applies. Research and code review
can inspect an existing checkout, with declared scope. Keep model and effort
omitted to inherit the parent selection. Workers return follow-up needs to the
coordinator and never recursively delegate.

## Isolated UI acceptance and review

Read [the testing setup](../README.md) and [the harness runbook](../harness/README.md).
Prepare and dispatch with `--controller t3`, then inspect actual ready lanes. The
runner holds the deployment lease and supplies separate browser processes/accounts;
never rebuild a leased deployment or edit its source during the run.

For a tester or live reviewer, the assignment must use only its lane evidence
directory in `owned_paths` and exactly `qa:RUN:LANE` in `owned_resources`:

```text
SCHEMII_ASSIGNMENT {"kind":"ui-testing","task":"Exercise the assigned desktop/mobile editor cases","workspace":"/absolute/path/to/prepared-checkout","owned_paths":["artifacts/qa/qa-REPLACE/lane-1"],"owned_resources":["qa:qa-REPLACE:lane-1"],"verification":["./test.sh checkpoint with case observations and inspected screenshot evidence","./test.sh finish after all assigned case results are recorded"],"qa":{"run":"qa-REPLACE","lane":"lane-1","claim":"after-spawn-before-actions","browser":"isolated"}}
```

Replace `qa-REPLACE` with the actual returned run ID. The guard reads the fixed
manifest and controller ownership metadata to check a live T3-controlled run,
verified deployment, matching checkout and ready unclaimed lane. Typed assertions
of readiness cannot substitute for preparation. Fixture-authorized application
writes come from the lane brief, not invented assignment resource labels.

Spawn first, then claim the actual returned agent ID with
`./test.sh claim --run RUN_ID --lane LANE_ID --agent AGENT_ID`. Only afterward send
that worker the private session-file path and brief. Workers use harness actions
exclusively within that account, handle and resources. Keep credentials, controller
files and session handles private. The guard does not claim or authenticate a child.

Record functional and visual results separately for each case, inspect captured
images and describe expected versus actual behavior. Preserve failures and exact
blocked prerequisites. A browser probe, API status, completed handoff or worker
exit zero is not an acceptance pass. The reviewer checks evidence independently
and reproduces material findings in a separately owned lane, or after the tester
finishes and releases its lane; never let two agents control one lane.

Inspect saved state before replaying an uncertain write. Finish, report and stop
through `./test.sh`; clean only recorded owned objects. An ordinary native T3
preview may support one sequential manual exploratory pass when available, labeled
cooperative. It cannot replace isolated multi-account QA or prove session isolation.

Configuration references: [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[hooks](https://learn.chatgpt.com/docs/hooks), and
[config reference](https://learn.chatgpt.com/docs/config-file/config-reference).
