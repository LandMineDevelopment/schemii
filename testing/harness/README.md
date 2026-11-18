# Manual UI QA runner

Start with the [testing suite setup and persona pools](../README.md).

For stock T3/Codex native roles, explicit assignments and independent review,
also read [the agent workflow](../agents/README.md). Its hooks guard dispatch;
the isolated runner remains responsible for browser lane ownership.

`./test.sh` is the supported entrypoint. It runs **separate Chromium processes and
contexts against one application**, with one retained QA account per lane. It does
not launch the application test suite or replace the HTTPS app. The separate
`setup` command provisions retained persona accounts and the portable QA database.

The browser backend is explicitly `isolated`: visible Playwright-controlled
windows, not additional T3 preview tabs. Choose an agent controller:

- `--controller t3` (default): `run` emits assignments and exits 3. The attached
  coordinator spawns and claims agents within T3's actual runtime capacity.
- `--controller codex`: `run` starts one independent installed Codex CLI process
  per ready lane, up to `--parallel 10`. These processes are separate from T3's
  subagent slots and each receives only its assigned browser session handle.
  Lanes without explicit `writeAuthorization` stay read-only. A fixture-backed
  lane with exact owned resources and operations may perform only those UI writes,
  including saves, chat turns, approvals, SQL and migrations. This is a cooperative
  prompt boundary; use disposable per-account resources and inspect the result.

Workers use a workspace-write sandbox rooted at their lane artifact directory,
with networking enabled so harness commands can reach the local Unix socket.
This is cooperative session isolation, not an OS security boundary between agents;
all run under the same host user. Prompts restrict workers to harness actions.

The Codex worker uses the user's installed model configuration unless
`--agent-model MODEL` explicitly selects a model. `--agent-reasoning EFFORT`
sets `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`.
The selected model must support that level; provider rejection remains a failure,
with no automatic downgrade. Omitted settings inherit the installed configuration.
Both overrides require `--controller codex`, apply to every worker in the run,
and are retained for resume and displayed in status/report output. `--agent-timeout` sets its
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

To select model and reasoning, add these flags to `prepare` (also accepted by
`plan` and `doctor`):

```bash
--controller codex --agent-model gpt-6-astra --agent-reasoning high
```

`run` uses the settings saved during preparation.

`--agents N` selects `qa_subagent_01` through `qa_subagent_N`; explicit `--accounts`
may select another set. If both are supplied, their counts must match. Codex
preflight checks the installed CLI and login status locally without starting an
AI request. Actual workers require the user's authenticated Codex access.

The legacy qa_subagent_06–10 accounts have Schemoo-only access and no database fixtures.
The suite persona pool is separate: see `../README.md` for its stable database spaces. Use
those accounts for the read-only Schemoo harness track; database/query/chat
acceptance needs separately verified permissions, resources, and prerequisites.
The runner does not create these accounts or broaden their grants.

Preparation starts the app **only through `./start.sh`**, checks local and tailnet
HTTPS plus API maps, records source fingerprint and launcher success, and holds a
shared-worktree deployment lease. Concurrent runs reuse the verified same-source
deployment and reserve different accounts; rebuilds and resets need an exclusive lease. It opens one browser process per active lane,
starts each on `/account`, checks identity and product capabilities, and runs
harmless browser probes for click/type, screenshots, dialogs, drag and downloads.
Before any lane enters its assigned product page, the harness parks active,
unclaimed pages on `/account`, verifies different cookie values and storage
markers, logs each account out in turn, verifies the other accounts remain
authenticated, and restores the tested account. This keeps those intentional
session transitions from interrupting product data streams. Probe results are
harness readiness evidence, not application acceptance results.

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

Normal persona setup creates an account-owned saved Orders model and sample-row
dashboard for each `report_author`, and grants that persona the Schemoo access
needed to choose the model. Its generated lifecycle scenario opens those saved
objects as the starting point and authorizes writes only to a new dashboard the
lane creates. Keep the starter model/dashboard unchanged; after all runs stop,
`./test.sh cleanup-author-fixture --accounts qa_report_author_001` removes only
the selected, ledgered starter objects. See the [persona fixture details](../PERSONAS.md).

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

The `chat` track additionally requires a `chatProvider` entry in its lane
fixture, for example
`{"providerId":"instance-codex","modelId":"gpt-6-luna","reasoningEffort":"default"}`.
The runner checks the authenticated provider and active model during browser
preflight. For retained designer accounts, `./test.sh provision-chat` grants the
exact QA scopes and writes this entry into the generated private fixtures.
Use `--accounts qa_designer_001` with the provisioned account for a chat lane;
automatic persona selection may choose a different designer.

## Disposable guest

Use `--disposable-guest-binding FILE` with `plan`, `doctor` or `prepare` only
inside an explicitly approved disposable Linux VM. Give the VM its own engine,
Git checkout and locks, metadata and target databases, fixture accounts, browser
storage and backup destinations. Run the controller and isolated Playwright
processes in that VM. The binding does not enable native-thread mode or a
remote route. The default desktop and CI preview gates still apply to their
own workflows.

Start only through `./start.sh`. Install the guest launcher’s public localhost
certificate as a trusted TLS peer in the guest’s Chromium NSS store
(`certutil -A -t "P,,"` against the applicable NSS database). For the
Playwright API client, set `NODE_EXTRA_CA_CERTS` to the same certificate before
starting the controller. Use separate guest-owned browser profiles and leave TLS
verification enabled. The harness checks the declared certificate against the
launcher’s canonical certificate and verifies HTTPS without relaxing trust.

Provide a mode-0600 JSON file in private storage outside Git with these required
fields.

| Field | Value |
| --- | --- |
| `bootId` | Guest’s `/proc/sys/kernel/random/boot_id` |
| `commit` | Full commit from the clean guest checkout |
| `certificateSha256` | Lowercase SHA-256 fingerprint of the launcher’s `.schemii/tls/localhost.crt` in DER form |

The controller verifies kernel-held deployment locks, checks the clean source and
VM boot against a launcher receipt, and correlates fresh HTTPS response IDs with
the owned ingress records and their upstream application IDs. Correlation and
live process/cgroup checks are repeated before dispatch and lane claims. The
mode refuses machine-local Compose overrides and noncanonical launcher settings.
A shared deployment must have the same guest binding. Prepare a new run after
rebooting the guest. The receipt covers local readiness only and records remote
preview as untested.

The harness pins the launcher to the VM’s system engine endpoint with an
empty private Docker client configuration. Persistent client contexts do not
select the engine. Do not supply Docker or Compose overrides, inherited demo
reset, alternate secret/lock paths, or other launcher overrides. The supported
fixture settings are the database and role variables (`SCHEMII_TEST_POSTGRES_DB`,
`SCHEMII_TEST_POSTGRES_USER`, `SCHEMII_TEST_POSTGRES_PASSWORD`,
`SCHEMII_METADATA_APP_USER`), and optional fixture egress. To approve
fixture egress, add a private `fixture` object containing `mode` (`internal-only`
or `external`) and `hosts` (a bounded array of exact approved hostnames or IP
addresses). The launcher receives those values as `SCHEMII_TARGET_EGRESS_MODE`
and `SCHEMII_ALLOWED_TARGET_HOSTS`. Inherited versions of those settings must
match the binding exactly. If the standard QA database registry exists at
`.schemii/testing/registry.json`, explicitly include `qa-postgres` in
`fixture.hosts` because the overlay uses that host. The launcher selects the overlay under its
lock, checks the binding before mutation, and uses that same decision and the
approved host list throughout startup.

For one approved private PostgreSQL endpoint that uses a fixture CA, set
`fixture.mode` to `external`, list only that endpoint in `fixture.hosts`, and
add `fixture.ca` with an absolute private PEM `path` and the file's lowercase
`sha256`. Use a mode-0600 file containing only a currently valid self-signed CA
certificate. The launcher verifies the file again under its exclusive guest
lease, rechecks the binding's boot and clean source, and snapshots the CA to a
read-only application-container mount for libpq. CA launch omits machine-local
Compose overrides and disables automatic `.env` loading. Use the canonical
lowercase host spelling without trailing dots (or a canonical IP address).
The CA option is incompatible with the registered QA database overlay and
shared deployment reuse. Continue to use a managed profile with
`verify-full`, matching the exact fixture server name or IP in its certificate.
The guest's system and browser trust are unchanged. Keep the staged CA
snapshot with the disposable guest disk until the application is torn down.

For independent review in the guest, pass `--reviewer-account ACCOUNT` and
`--runtime-slots N` along with the binding and both accounts. The account must
be separately selected and claimable. Reviewed guest runs require one tester
wave. Set `--parallel` to the number of selected testers. Reserve one observed
slot each for the coordinator and reviewer. Both lanes run their
configured cases with isolated browser processes. The reviewer uses their own
claimed session to inspect the tester’s evidence and submit an adjudication.
The controller rechecks each cited target image’s hash at review time and
preserves the original checkpoint outcome.

Treat `plan`, `prepare`, `status`, launcher logs and all run receipts as private.
Clean up the harness lease and its scoped test objects before shutting down the
guest. Keep backups in that guest’s private storage.

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
   then `run --run RUN_ID` dispatches it. Do not call `advance` during an active wave.
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
`download`, `upload`. Upload accepts `selector`, `fileName` ending in `.csv` or
`.json`, and UTF-8 `content` up to 1 MiB; it requires an explicitly authorized
fixture operation. Locator arguments accept `role`/`name`, `label`, or `selector`.
`press` uses `key`; `scroll` uses `x`/`y`; `drag` uses `from:{x,y}` and `to:{x,y}`;
`dialog` uses `action:accept|dismiss`; `download` clicks the supplied locator and
may set `browserFallback: true` to exercise an application's browser-download
path when its native File System Access save picker cannot be automated. The
action saves the resulting file in the lane directory. There is no arbitrary JavaScript
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

Report an observed defect immediately with `./test.sh finding --session-file FILE
--scenario ID --title TEXT --severity low|medium|high|critical --steps TEXT
--expected TEXT --actual TEXT --evidence SCREENSHOT`. The screenshot must have
been captured by that lane in its current session. Findings persist as unverified
drafts in the run manifest and HTML report; a separate Sol verifier should
reproduce candidates in its own account and browser. Checkpoint the scenario
separately, retaining functional or visual failure even if a workaround succeeds.

For the complete Schemii sweep, first seed exact test-owned local, reader and
writer workspaces through the application's normal APIs, then generate a
private manifest:

```bash
python -m testing.harness.schemii_workspaces --tag UNIQUE \
  --output artifacts/qa/UNIQUE-workspaces.json
python testing/harness/schemii_sweep.py --tag UNIQUE \
  --workspace-fixtures artifacts/qa/UNIQUE-workspaces.json \
  --output artifacts/qa/UNIQUE-fixtures.json
```

It assigns ten named designer accounts, 60 prescriptive base scenarios (120
desktop/mobile outcomes), exact types, baseline values, owned scratch prefixes,
pre-opened workspace checks, chat provider checks and three isolated writer targets. Provision the target
accounts and writer profiles first as described in `../README.md`. Use
`--accounts` in the manifest's insertion order, `--agents 10 --parallel 10
--controller codex --agent-model gpt-6-luna --agent-reasoning high
--agent-timeout 3600 --products schemii --tracks lifecycle,canvas,rules,query,chat
--viewports desktop,mobile --fixtures artifacts/qa/UNIQUE-fixtures.json` for
`plan`, `doctor` and `prepare`. Do not treat scenario instructions as a pass:
each agent must record actual result and evidence for every outcome.
After `report` and `cleanup`, use
`python -m testing.harness.cleanup_schemii_sweep --run RUN_ID
--workspace-fixtures artifacts/qa/UNIQUE-workspaces.json` to remove recorded
seed-owned workspaces and chats plus exact-prefix app objects created after that
run started. Preexisting and other-tag objects are preserved. Then reset and
verify the exact writer targets as shown in `../README.md`.

For blocked dependent workflows, start a new wave with a **new** tag after the
first run has stopped and its app objects and writer schemas have been cleaned:

```bash
python -m testing.harness.schemii_workspaces --tag UNIQUE2 \
  --output artifacts/qa/UNIQUE2-workspaces.json
python -m testing.harness.schemii_preseed \
  --workspace-fixtures artifacts/qa/UNIQUE2-workspaces.json \
  --output artifacts/qa/UNIQUE2-workspaces-ready.json
python -m testing.harness.schemii_followup --tag UNIQUE2 \
  --workspace-fixtures artifacts/qa/UNIQUE2-workspaces-ready.json \
  --output artifacts/qa/UNIQUE2-followup-fixtures.json
```

The preseed step uses normal authenticated app APIs and records its ownership
incrementally. It replaces only empty desired designs in four exact owned
workspaces; it never executes SQL or applies a migration. It creates separate
reader and local-design chats with explicit permissions for `qa_designer_001`.
That agent completes its scenarios through in-app chat alone. Shared Codex
inference is single-turn across these QA accounts: other agents may test UI in
parallel, but must not send competing AI turns. The follow-up generator emits
ten substantial missions and 100 desktop/mobile outcomes, including exact
column types and row-specific selectors, catalog paging, real downloaded file
inspection, isolated migration apply, and cleanup boundaries. Prepare/run it
with the same ten-account Luna/high harness command above, changing only the
fixture path. Once the follow-up report is saved and its browsers stopped, run
the same scoped cleanup helper with its `UNIQUE2-workspaces.json` map, then
reset and verify all three marked writer schemas and the 120 reader baselines.

Before dispatch, compare the retained account registry with the fixture's
account list, verify the selected AI model with `./test.sh provision-chat` and
the harness preflight, verify three empty writer schemas, and reserve a separate
Sol verifier account/browser. Keep the full run and any focused subset follow-up
on distinct tags. A subset follow-up uses only its declared accounts and the
same tag's workspace map for cleanup. Authorize chat-only agents explicitly to
approve **read-only SELECT** proposals in their assigned reader schema; prohibit
all reader writes. Without this permission, an Ask-mode approval card blocks the
exact-answer checkpoint even when the provider and chat work. Give desktop and
mobile stateful design scenarios separate fresh workspaces or make mobile a
readback-only scenario; one viewport's saved tables otherwise invalidate the
other viewport's create test. For design exports, open the header
`summary[aria-label="Download"]` menu before choosing
`#download-catalog-button` or `#export-design-sql-button`. For SQL Console COPY
download, use the harness download action's `browserFallback` on the supported
browser path and inspect the private downloaded bytes. A hidden menu button,
native file picker, or unapproved read must be marked blocked with its exact
precondition, not called an application failure.

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

## Opt-in native thread browser adapter

Use `--browser native --controller t3 --reviewer-account ACCOUNT --runtime-slots N`
for the stock native `schemii_browser` adapter. Read the complete
[native acceptance contract](../../docs/native-qa-acceptance.md) before dispatch.
Preparation retains existing account, fixture, capability and deployment checks,
then closes its transient readiness browsers. Claimed native workers visibly log
in through their own thread tools; `test.sh action` is rejected for those lanes.
The CLI records native binding/authentication, scenario-begin, selected captures,
image/download inspection and distinct reviewer adjudication. It never mixes a
legacy session handle with an unrelated native browser.

`--parallel` counts native testers and reserves a separate selected reviewer
account/lane. The guard checks the matching prepared backend and reported capacity.
Normal `browser_close` removes the backend, guardian and generation output while
leaving the reusable stdio endpoint live. After exporting evidence, visible signout,
`browser_close` and harness `finish`, a worker whose connection is genuinely done
calls its own `browser_release` with no arguments. The coordinator then uses
`native-release --run RUN --lane LANE` to observe stopped captured identities and
removed output; repeated verification is safe. Missing generation output with a
live endpoint returns explicit owning-thread terminal-release guidance, persists
pending cleanup and retains the deployment lease. If that tool is unavailable,
report the retained endpoint; an old PID does not authorize stopping a new backend.
Even present private directory/inode/process proof cannot fence a later backend
replacement on the same endpoint. The harness never signals native endpoints;
their owning thread must use the supported terminal tool. Neither verification
nor terminal release closes a native agent/thread.
Execution-complete and review-pending are
separate states. Existing isolated runs/evidence stay available until live native
application acceptance and cleanup parity are demonstrated.

Both backends keep independently reviewed cases pending while any recorded worker,
recording stream, browser-closure stage or controller intervention remains
unresolved, including the reviewer lane. Worker receipts must match their recorded
PID/birth/generation; delayed old receipts remain history and cannot clear a fresh
owner's pending cleanup. Verified cleanup retains a failed worker exit as a failure,
and native transport teardown does not establish native thread termination.
