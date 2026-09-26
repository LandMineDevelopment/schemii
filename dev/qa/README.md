# Manual UI QA runner

`./test.sh` is the supported entrypoint. It runs **separate Chromium processes and
contexts against one application**, with one retained QA account per lane. It does
not launch the application test suite, replace the HTTPS app, or create accounts.

The browser backend is explicitly `isolated`: visible Playwright-controlled
windows, not additional T3 preview tabs. Choose an agent controller:

- `--controller t3` (default): `run` emits assignments and exits 3. The attached
  coordinator spawns and claims agents within T3's actual runtime capacity.
- `--controller codex`: `run` starts one independent installed Codex CLI process
  per ready lane, up to `--parallel 10`. These processes are separate from T3's
  subagent slots and each receives only its assigned browser session handle.

Workers use a workspace-write sandbox rooted at their lane artifact directory,
with networking enabled so harness commands can reach the local Unix socket.
This is cooperative session isolation, not an OS security boundary between agents;
all run under the same host user. Prompts restrict workers to harness actions.

The Codex worker uses the user's installed model configuration unless
`--agent-model MODEL` explicitly selects a model. `--agent-timeout` sets its
maximum lifetime in seconds (30–3600; default 600). Starting ten processes is not
proof that ten AI turns ran concurrently; the manifest records peak live worker
processes and observed active turns, while scenario results record actual work.

## Prepare

Install the repository's existing npm dependencies and Playwright Chromium if not
already present. A graphical display is required unless `--headless` is explicitly
selected. Keep the app's existing `.schemii` runtime configuration available in the
worktree; do not copy/reset retained database secrets to make startup succeed.

Credentials must be in a private mode-0600 JSON file outside version control:

```json
{
  "accounts": [
    {"username": "qa_subagent_01", "password": "existing private password"},
    {"username": "qa_subagent_02", "password": "existing private password"}
  ]
}
```

```bash
./test.sh plan --accounts qa_subagent_01,qa_subagent_02 --parallel 2
./test.sh doctor --credentials-file .schemii/qa-harness-accounts.json
./test.sh prepare --credentials-file .schemii/qa-harness-accounts.json \
  --accounts qa_subagent_01,qa_subagent_02 --parallel 2 \
  --products schemoo --tracks harness --viewports desktop,mobile
```

For up to ten independent Codex testers:

```bash
./test.sh plan --agents 10 --parallel 10 --controller codex \
  --products schemoo --tracks harness --viewports desktop,mobile
./test.sh doctor --agents 10 --parallel 10 --controller codex \
  --credentials-file .schemii/qa-harness-accounts.json
./test.sh prepare --agents 10 --parallel 10 --controller codex \
  --credentials-file .schemii/qa-harness-accounts.json \
  --products schemoo --tracks harness --viewports desktop,mobile
./test.sh run --run RUN_ID
```

`--agents N` selects `qa_subagent_01` through `qa_subagent_N`; explicit `--accounts`
may select another set. If both are supplied, their counts must match. Codex
preflight checks the installed CLI and login status locally without starting an
AI request. Actual workers require the user's authenticated Codex access.

Accounts 06–10 currently have Schemoo-only access and no database fixtures. Use
those accounts for the read-only Schemoo harness track; database/query/chat
acceptance needs separately verified permissions, resources, and prerequisites.
The runner does not create these accounts or broaden their grants.

Preparation starts the app **only through `./start.sh`**, checks local and tailnet
HTTPS plus API maps, records source fingerprint and launcher success, and holds a
shared-worktree deployment lock. It opens one browser process per active lane,
checks identity and product capabilities, and runs harmless browser probes for
click/type, screenshots, dialogs, drag and downloads. It verifies different cookie
values and storage markers, logs each account out in turn, verifies the others
remain authenticated, and restores that account. Probe results are harness
readiness evidence, not application acceptance results.

The first wave opens at most `--parallel` browsers. Subsequent waves open only after
the current wave finishes, so logout/isolation probes cannot interrupt a tester.
Never run another launcher or edit source during a run. The current launcher
honors the deployment lease; old checkouts that lack this change cannot enforce
it. The runner rejects source changes on subsequent actions. This detects source
changes; it is not an independent remote build-ID endpoint.

Without a fixture manifest the only allowed track is `harness`: a read-only app
walkthrough with no saved app data changes. Feature tracks require declared owned
resources and effective-access checks. The harness preserves retained account
permissions and resources; it does not guess missing grants or silently seed/reset
a user's schema. Prepare fixtures separately through supported app workflows.

A fixture manifest is JSON with `lanes` keyed by username. For example (replace
IDs and expected values with actual assigned resources):

```json
{
  "lanes": {
    "qa_subagent_01": {
      "url": "/schemoo",
      "resources": {"schema": "qa_subagent_01", "modelId": "model_REPLACE"},
      "checks": [
        {"path": "/api/v1/schemoo/models/model_REPLACE", "status": 200,
         "equals": {"namespace": "qa_subagent_01"}}
      ],
      "scenarios": [
        {"id": "model-roundtrip", "title": "Save and reopen a disposable model",
         "instructions": "Use only the assigned scratch model. Verify save/reload and capture desktop/mobile evidence."}
      ]
    }
  }
}
```

Use `--fixtures /path/manifest.json --tracks lifecycle` when preparing. Checks are
GET-only same-origin `/api/v1/` requests, support expected `status`, dot-path
`equals`, and dot-path `minLength` for arrays. Missing source/model access blocks
the lane. Each base scenario expands into separate results for every selected
product and viewport, for example `harness-schemoo-desktop` and
`harness-schemoo-mobile`. A scenario may declare `product` to restrict it to one
selected product. Select at least one account per requested track so no track is
omitted. Query/export/chat scenarios must include their own meaningful data and
provider prerequisites in the manifest and checklist; a successful GET does not
prove query correctness or a successful chat turn. Use data exceeding the actual
page size for pagination acceptance.

## Coordinator runbook

1. Read `plan` and `doctor`. With T3, reserve runtime slots for coordination and
   independent verification; four slots permit two active testers. With Codex,
   independent worker processes allow up to ten active testers, subject to local
   resources and authenticated service capacity. Arrange independent review
   separately; ten worker processes do not establish ten completed tests.
2. Prepare, capture the returned run ID, and inspect `status`. If blocked, fix the
   exact prerequisite; never bypass the launcher or call readiness a pass.
3. `./test.sh run --run RUN_ID` launches ready Codex workers when the run selects
   Codex; inspect `status`, worker events, and results to verify progress. For T3,
   it returns structured lane briefs: spawn one agent per ready lane with its
   objective and the instruction to wait for its session file.
4. For T3, claim using the **actual returned agent ID**:
   `./test.sh claim --run RUN_ID --lane lane-1 --agent AGENT_ID`.
   Send that agent its brief and private session-file path. Never send passwords or
   the controller token. Workers must use runner actions exclusively, not T3 tabs,
   another browser client, direct app APIs or shell SQL.
5. Monitor reports and findings. Heartbeat a waiting agent through its scoped
   `heartbeat` command; every action/checkpoint also renews its ten-minute lease.
   A stale lease pauses the lane. A failed product scenario stays failed even if
   the tester uses an explicitly documented workaround for later scenarios.
6. Arrange independent verification in another owned lane, or after the original
   tester finishes and releases its lane. Never have two agents control one lane.
   The runner enforces session ownership and stale-handle rejection; the
   coordinator is responsible for genuinely independent review and runtime slots.
7. After all active agents finish, `advance --run RUN_ID` prepares the next wave,
   then `run --run RUN_ID` dispatches it. Codex workers are currently assigned
   read-only harness exploration; write-dependent scenarios remain blocked.
8. `report --run RUN_ID` renders a durable HTML report. Review function/style
   separately. Then `stop --run RUN_ID` closes browsers, revokes their app sessions,
   and releases the deployment lock. `cleanup` is idempotent and preserves app
   accounts/data and evidence. It does not delete things it did not create.

## Worker controls and results

```bash
./test.sh action --session-file /private/session.json --kind snapshot
./test.sh action --session-file /private/session.json --kind click \
  --args-json '{"role":"button","name":"Open model"}'
./test.sh action --session-file /private/session.json --kind type \
  --args-json '{"label":"Name","value":"Scratch model"}'
./test.sh action --session-file /private/session.json --kind resize \
  --args-json '{"width":390,"height":844}'
./test.sh checkpoint --session-file /private/session.json \
  --scenario harness-schemoo-desktop --functional passed --visual passed \
  --note 'Describe what was exercised, expected results and actual observations.' \
  --evidence lane-1/screen-EXAMPLE.png
./test.sh finish --session-file /private/session.json
```

Available browser actions: `identity`, `snapshot`, `navigate` (same origin),
`click`, `type`, `press`, `scroll`, `resize`, `screenshot`, `dialog`, `drag`,
`download`. Locator arguments accept `role`/`name`, `label`, or `selector`.
`press` uses `key`; `scroll` uses `x`/`y`; `drag` uses `from:{x,y}` and `to:{x,y}`;
`dialog` uses `action:accept|dismiss`; `download` clicks the supplied locator and
saves the resulting file in the lane directory. There is no arbitrary JavaScript
or raw app API action. Password fields are masked in screenshots. Native dialogs
are reported and must be explicitly accepted/dismissed, never automatically
accepted during application testing.

Snapshots include a saved image path, an accessibility tree with exact role names,
visible text, and control attributes for CSS fallbacks. Prefer the accessibility
tree for role/name locators; text content is not an accessible-name calculation. Inspect the
image before claiming style approval. Visual pass requires an evidence image;
results also require an explanatory note. Every scenario must have both results
before finish; use `blocked` rather than inventing a pass. The runner records
claims and evidence, but cannot prove a human/agent actually inspected an image.
Emulated mobile checks are not physical-device approval.

## Recovery and private artifacts

`recover --run RUN_ID --lane lane-1` closes the old browser, revokes the old handle,
reopens/login-checks the lane and repeats preflight. First interrupt the old agent
and let other claimed lanes finish: isolation probes must run at a wave boundary.
Claim the new generation for its assigned agent afterward. The old session file
is rejected even if its holder retries an action.

For an interrupted controller, `resume --run RUN_ID` requires that its recorded
process and every recorded owned browser/worker process have exited. Unresolved
launch ownership also blocks recovery instead of risking a duplicate tester. It verifies the source fingerprint, rebuilds only through
`./start.sh`, reauthenticates unfinished lanes, and preserves recorded results.
Before repeating an uncertain application write, the tester must inspect saved
state. Resume is not permission to replay every action. Changed source requires a
new prepared run. Normal SIGTERM/SIGINT closes browsers; after a machine/process
crash, inspect leftovers before resuming. No broad process killing is performed.

`artifacts/qa/RUN_ID/` contains private `manifest.json`, `events.jsonl`, HTML report,
startup/controller logs and per-lane evidence/checkpoints. Codex lanes also have
private `agent-events.jsonl` and `agent-final.txt` files. `control.json` and lane
`session-*.json` are bearer handles: never commit or publish them. Credential files
are referenced privately and passwords stay in controller memory. Do not upload
the whole artifact directory. Share reviewed screenshots and report only; app data
visible in screenshots may itself be private. All run directories/files are private.

No app test suites run as part of this harness. Native T3 context support,
automatic disposable DB provisioning, multi-instance deployment, recording, and
suite orchestration remain separate extensions; unsupported flags fail explicitly.
