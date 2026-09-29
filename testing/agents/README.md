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

## Assign native workers

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
