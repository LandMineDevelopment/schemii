# Repeatable manual UI QA with parallel AI agents

Tracking issue: [#73](https://github.com/LandMineDevelopment/schemii/issues/73).
Status: implementation in progress. The runner supports up to ten independent
Codex workers; this document does not certify a successful ten-agent run or app
acceptance. See the [operating runbook](../../dev/qa/README.md) and each run's report
for commands, evidence, and remaining gaps.

## Outcome and scope

`./test.sh` is the single configurable entrypoint for planning, prerequisites,
startup, agent dispatch, browser actions, progress, recovery, and reports. It uses
one canonical HTTPS deployment, separate Chromium processes and contexts, and
separate retained QA accounts. It does not invoke the application test suite.

The original chat review identified shared logins, incomplete account grants,
mismatched schemas, insufficient pagination data, browser/dialog failures, missing
screenshots, and ambiguous completion reports. The implementation addresses
session ownership, repeatable readiness, real worker dispatch, and durable
functional/visual coverage. It does not imply those historical app scenarios have
all been retested.

## Implemented command interface

```bash
./test.sh plan --agents 10 --parallel 10 --controller codex \
  --products schemoo --tracks harness --viewports desktop,mobile
./test.sh doctor --agents 10 --parallel 10 --controller codex \
  --credentials-file .schemii/qa-harness-accounts.json
./test.sh prepare --agents 10 --parallel 10 --controller codex \
  --credentials-file .schemii/qa-harness-accounts.json \
  --products schemoo --tracks harness --viewports desktop,mobile
./test.sh run --run RUN_ID
./test.sh status --run RUN_ID
./test.sh report --run RUN_ID
./test.sh stop --run RUN_ID
./test.sh cleanup --run RUN_ID
```

`--agents N` selects the first N `qa_subagent_XX` accounts. `--accounts` explicitly
selects retained usernames; both options together must agree on count.
`--parallel` allows 1–10 active lanes. More tracks than accounts are rejected.
Every base scenario expands into selected product/viewport combinations with
separate result records, such as `harness-schemoo-desktop` and
`harness-schemoo-mobile`.

`--controller t3` remains the default: `run` supplies assignments, and the attached
coordinator uses real runtime agent tools to spawn and claim testers. T3's own
concurrency ceiling still applies; four slots with coordinator and verifier leave
two tester slots. Extra browser tabs never increase that capacity or isolate login.

`--controller codex` launches separate installed Codex CLI processes, one per
ready lane, independently of T3's subagent slots. Each receives its own scoped
browser handle. The user's installed model configuration remains in effect unless
`--agent-model MODEL` explicitly selects another model;
`--agent-reasoning EFFORT` selects its reasoning effort. Both overrides require the
Codex controller and persist across resume; omitted values inherit installed defaults. `--agent-timeout` is
30–3600 seconds, default 600. Local CLI/version/login checks happen before
preparation; actual inference starts only when workers are dispatched.

Codex workers currently receive a read-only manual harness assignment. They must
inspect screenshots, exercise harmless normal UI controls, and report blocked
write-dependent scenarios honestly. They do not run test suites, rebuild the
application, change grants, or use another browser client.

Exit codes are 0 success, 1 failure, 2 invalid arguments, 3 pending T3 agent
handoff, and 4 blocked prerequisite. A dispatched or prepared run is not a passed
run. Reports distinguish completed checks from failed, blocked, and unrun work.

## Startup consistency and session isolation

1. Validate arguments, private credentials, browser availability, display or
   explicit headless mode, and controller prerequisites before app startup.
2. Acquire the shared Git-common-directory deployment lease. `start.sh` also
   respects this lease across linked worktrees, preventing concurrent rebuilds
   and resets for its lifetime.
3. Invoke only `./start.sh`. Stop on failure. Verify canonical local HTTPS and
   Tailscale HTTPS plus their API maps; record launcher output and source
   fingerprint. Source changes during or after startup invalidate testing.
4. Open one Chromium process/context per active account. Independently log in,
   verify identity, product capabilities, and fixture-declared effective access.
   No shared administrator storage-state file is used.
5. Prove different cookies and browser-storage markers. Log each account out in
   turn and verify every other session remains authenticated, then restore it.
   Run harmless screenshot, click/type, dialog, drag, and download probes.
6. Publish ready assignments only after preflight. Scope actions to exclusive
   lane handles and generation numbers. Recheck identity before interactions;
   stale or foreign handles are rejected.

Application origins stay `https://localhost:8001` and
`https://omarchy.taile4f57f.ts.net`. No alternate app server, plain HTTP, direct
Docker access, or public tunnel is introduced. Fingerprints plus verified launcher
success record the tested source; there is no independent deployed build-ID API.

## Accounts and fixtures

Credentials are referenced from a private, ignored mode-0600 file such as
`.schemii/qa-harness-accounts.json`; they are never worker prompt text.
Accounts 06–10 currently have Schemoo-only access and no database fixtures.
They are suitable for the read-only Schemoo harness walkthrough, not an implied
query, database, or AI-chat acceptance run.

The runner preserves retained accounts and resources. It does not provision
accounts, reset fixtures, guess passwords, or broaden grants. Without a fixture
manifest, only the `harness` track is allowed. Feature tracks require declared
owned resources and expected same-origin API checks. Query and chat tests still
need meaningful expected data and provider/model prerequisites; an HTTP 200 does
not establish successful SQL or a successful AI interaction.

Reusable pagination fixtures, owned schema/model/dashboard chains, persistence
roundtrips, known exports, empty/error states, and chat-only action scenarios
remain acceptance work that must be prepared and explicitly demonstrated.

## Dispatch, supervision, and recovery

The coordinator owns the deployment and report; each tester owns one lane.
Codex dispatch records real process ownership, lifecycle events, and peak observed
worker processes/active AI turns. Ten browsers, ten processes, and ten overlapping
AI turns are different measurements; evidence must identify which occurred.
Workers checkpoint separate functional and visual results per product/viewport.
Visual pass requires image evidence and an explanatory observation.

A timed-out or disconnected worker loses its session lease. Recovery waits for
other claimed lanes to finish before login/logout probes. Old handles remain
invalid. Check persisted app state before repeating uncertain writes.

Resume requires the old controller and recorded browsers/workers to be stopped.
PID birth identities prevent confusion with reused process IDs. Pending launch
ownership blocks recovery if a crash left ownership uncertain. No broad process
kill is used to make recovery appear successful. Normal stop closes owned worker
and browser processes, revokes sessions, and releases the deployment lease.
Cleanup preserves app data, credentials, accounts, reports, and evidence.

Private run directories contain manifests, event logs, HTML reports, screenshots,
controller logs, and per-worker output. Bearer session/control files and raw
artifact directories must not be published. Independent review remains a distinct
coordinator responsibility rather than a claim automatically inferred from worker
self-reports.

## Remaining extensions and verification

Manual verification should demonstrate real overlapping AI workers, isolation,
useful desktop/mobile observations, stale-handle rejection, source/lease fencing,
and orderly shutdown/recovery. Report actual counts and gaps; no claim that ten
workers have passed is made here. Per the requested scope, application test suites
are not part of harness verification.

Native T3 isolated browser contexts, automatic disposable database provisioning,
recordings, visual baselines, regression-suite orchestration, and multiple app
instances are separate extensions. Multiple instances require launcher support
for isolated projects/resources; changing ports alone does not isolate cookies.

## References

- [Runner operating guide](../../dev/qa/README.md)
- [Launcher](../../start.sh)
- [Demo scenario provenance](../../dev/postgres/demo-scenarios/README.md)
- [Playwright browser-context isolation](https://playwright.dev/docs/browser-contexts)
- [Cookie isolation excludes ports](https://www.rfc-editor.org/rfc/rfc6265#section-8.5)
