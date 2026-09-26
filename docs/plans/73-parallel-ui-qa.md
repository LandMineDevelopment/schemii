# Repeatable manual UI QA with parallel AI agents

Status: proposed design; this PR does not implement `test.sh` or certify a QA run.
Tracking issue: [#73](https://github.com/LandMineDevelopment/schemii/issues/73).

## Outcome

One executable, `./test.sh`, prepares reproducible manual UI testing, validates
readiness, supplies precise agent assignments, and maintains a durable report.
Real AI agents explore the app through normal user interactions. Existing
Playwright checks provide regression coverage and selected visual comparisons.
Five retained QA accounts remain available across runs; individual test runs own
only explicitly recorded disposable resources.

The preferred deployment is one canonical HTTPS app with independent browser
contexts/profiles. Multiple app instances are an explicit last resort for state
that cannot be isolated inside the shared deployment. Different ports alone do
not isolate cookies.

## Evidence and present constraints

The September 24–26 T3 chats, especially **Explore and Review Admin UI** and
**Validate App Start Guides**, repeatedly encountered shared logins, missing
account grants, mismatched source schemas, small pagination fixtures, stalled
confirmation dialogs, unavailable screenshots, and incomplete coverage reports.
The app defects #65–#68 were subsequently fixed; the workflow still needs work.

Repository boundaries to preserve:

- `start.sh` alone builds/starts the application. It owns container access and
  failure diagnostics; the QA runner must never invoke Docker itself.
- Local HTTPS is `https://localhost:8001`; remote preview is
  `https://omarchy.taile4f57f.ts.net`. Never downgrade to HTTP, start a replacement
  web server, or reset existing Tailscale Serve routes.
- `start.sh` accepts a port but hardcodes the `schemii-test` project and recreates
  shared services. A second invocation on another port is not another stack.
- Authentication uses a fixed `schemii_session` cookie with path `/`. Cookies are
  not port-scoped. Browser context isolation is the primary solution.
- Current Playwright setup uses a common administrator state file, which global
  setup unlinks. Concurrent invocations need separate run/lane output and auth
  paths before enabling parallel execution.
- Existing fixtures contain reusable patterns, but shared mutable demo objects
  cannot be reset independently by concurrent agents.
- Available T3 preview tools expose tabs, clicks, snapshots and recordings, but
  do not expose independent browser-context creation, native-dialog handling,
  tab close, or pointer drag controls. These are real adapter prerequisites,
  not capabilities a shell wrapper can manufacture.
- The current agent runtime permits four concurrent agents including the parent.
  With one coordinator and one verifier, two testers can run concurrently. Five
  account tracks therefore run in waves unless runtime capacity increases.

## Proposed command interface

All examples below describe the target interface, not currently runnable commands.
The shell entrypoint delegates structured work to small Node modules using the
existing Playwright dependency. It does not become a large shell framework.

```bash
# Inspect the exact selection without starting the app or changing data.
./test.sh plan --products schemoo,schemer --agents 5 --parallel 2 \
  --tracks lifecycle,canvas,rules,query,chat --viewports desktop,mobile

# Read-only dependencies plus fresh capabilities supplied by the controller.
./test.sh doctor --browser t3 --controller t3 --parallel 2

# Build once, provision fixtures, and emit pending browser-preflight actions.
./test.sh prepare --products schemoo,schemer --agents 5 --parallel 2 \
  --browser t3 --controller t3 --fixtures standard \
  --credentials-file /private/path/qa-accounts.json --json

# Attached controller completes browser preflight before requesting dispatch.
./test.sh run --run RUN_ID --evidence screenshots,video --timeout 30m

./test.sh status --run RUN_ID
./test.sh resume --run RUN_ID --failed-only
./test.sh report --run RUN_ID --format html
./test.sh verify --run RUN_ID --suite focused
./test.sh verify --run RUN_ID --suite final
./test.sh cleanup --run RUN_ID --owned-only
```

Additional selections: `--accounts` (explicit retained account IDs), `--scenario`,
`--model` (in-app AI model), `--browser t3|isolated`, `--controller t3`,
`--isolation contexts|instances`, and `--reuse-build`. Defaults are context
isolation, desktop plus mobile, retained accounts, and a new run directory.
`--reuse-build` requires an exact deployed-source identity match; it is never an
unchecked skip-build flag. `--parallel 1` is an explicit sequential choice.
Unknown flags, incompatible tracks, duplicate writable account assignments,
insufficient runtime capacity, or unavailable required adapters fail early.

Exit codes: 0 completed action; 1 failed acceptance/check; 2 invalid arguments;
3 pending controller handoff; 4 blocked prerequisite; 5 interrupted run. Status
JSON distinguishes a successfully prepared run from a successfully tested run.
No command reports pass merely because assignments were emitted.

## Startup consistency: one barrier before testing

1. **Plan and validate.** Resolve scenarios, account assignments and fixture
   requirements. The attached coordinator supplies a freshness-bound capability
   report with runtime slots, tool signatures, backend identity and probe results;
   the shell cannot discover chat-only tools itself. `doctor` marks these checks
   pending when the controller is absent. Allocate a run ID
   and private state directory. Refuse a requested parallel mode that cannot be
   supported; never silently serialize while reporting parallel testing.
2. **Lease the deployment.** Acquire a stack-wide QA lease, shared across worktrees.
   Extend the launcher to respect this lease so another chat cannot rebuild or
   reset the stack during testing. Keep its existing short startup lock as well.
   Concurrent incompatible QA runs fail with owner/run information. Only the
   coordinator can release the lease after workers quiesce.
3. **Build once.** Delegate to `./start.sh`, stop on its exact failure, and verify
   both HTTPS origins. Add a non-secret deployed build identity containing commit
   and source-content fingerprint, and compare it with the selected source tree.
   A local Git SHA, health 200, or fixture's seed-time revision alone is not proof
   of the deployed source. Record build identity, origin and fixture digest.
4. **Prepare fixtures.** Lease each retained QA account exclusively. Verify its
   private credential reference without printing values or rotating passwords.
   Create run-owned schemas/resources through supported setup APIs and UI flows;
   source SQL, when needed, goes through supported application mechanisms.
   Provisioning may use the administrator, but acceptance must use the assigned
   account. Explicitly record the schema → model → dashboard → connection chain.
   Do not broaden grants to make an unrelated dashboard work.
5. **Allocate sessions and verify each account.** Create one unique context and
   separately issued login per active lane. In each browser session, check auth identity,
   product access, source catalog and expected row counts, own-object create/save/
   reload, and required viewer/export/drill grants. Check intended denials too.
   Chat lanes verify provider/model selection and a bounded successful chat turn.
   A saved admin grant is insufficient evidence of effective user access.
6. **Prove browser isolation.** After logging into every active account,
   recheck every identity. Log one
   out and prove others remain signed in; restore it and verify all identities.
   Verify a per-context storage marker as well. Probe screenshots, click/type,
   dialog accept/dismiss, downloads, and any pointer interactions the tracks need.
   Run probes on a harmless test page inside the existing HTTPS app, not a new
   server. Missing required capabilities block the corresponding run/track.
7. **Release the start barrier.** Persist each lane's readiness evidence and exact
   session/resource bindings. Only ready lanes receive mutable test assignments.
   A selected blocked track remains visible in the aggregate result; independent
   ready tracks may continue without converting the blocked run into a pass.

Standard fixtures include known joins and aggregates, at least 250 result rows
with page size 100, nulls, long labels, empty results, and deliberately invalid
input. Empty-account bootstrap is a distinct scenario with a disposable account;
it must not erase a retained account's models to simulate first use.

## Browser implementation and recovery

Preferred backend: T3 collaborative preview with explicit isolated context/session
handles, per-session close/restart, native-dialog handling, pointer controls and
reliable screenshots. Implement this through supported T3 controls if available;
otherwise it is a separate T3 integration dependency. Merely opening five tabs is
never accepted as isolation. Keep the active lane visible so the user can observe.

The explicit `--browser isolated` alternative would use a small local broker
backed by the installed Playwright library, one headed context per lane, scoped
commands, screenshots, recordings and per-lane event logs. It must expose only
that lane's browser handle to its agent and support human observation. It must
pass the same isolation probes. This is a deliberately selected backend, not an
automatic switch after a transient T3 error, and its windows are not claimed to
be T3-native previews. First implement and prove one backend end to end before
adding the second; T3 capability discovery determines whether the preferred
backend can satisfy the contract. With the currently exposed T3 tools, the first
feasible parallel implementation is the explicitly selected isolated backend;
T3 remains the agent coordinator. Native collaborative-browser support can follow
when its required controls are exposed. This does not require multiple app stacks.

An expected dialog has an explicit scenario action; unexpected destructive
confirmation is dismissed and logged. On a stall, capture available diagnostics,
stop the lane and attempt one bounded session recovery. Persisted state is checked
before repeating any action. If identity changes, immediately suspend all lanes
sharing that backend until isolation is proven again. If screenshots cannot be
captured, visual approval is blocked even if functional checks can continue.

## Real agent dispatch and supervision

A shell process cannot directly call chat-only `spawn_agent` or T3 MCP tools.
The design separates the test runner from the attached agent controller.
Preparation has two phases: shell-controlled stack/fixture setup, then browser
preflight executed by the controller. Both are required for readiness.

- `prepare --json` writes versioned, non-secret assignments, setup results and
  pending browser-preflight actions. It returns code 3 when controller work is
  needed; it does not claim that T3 identity/isolation probes have already run.
- In T3, the coordinator follows a repository QA runbook referenced by AGENTS.md:
  supply capabilities → prepare → claim run → execute and record browser
  preflight → run → spawn workers → collect findings → verify → finish. `run`
  returns a pending handoff when no controller has claimed it or required probes
  remain pending; it never pretends to have started agents.
- The coordinator adapter invokes the runtime's supported agent tools and writes
  returned agent IDs into the run ledger using machine-facing `controller claim`,
  `controller capabilities`, `lane ready`, `lane checkpoint`, and `lane finish`
  subcommands of the same `test.sh` entrypoint. A future standalone
  process controller can implement the same contract, but is outside the first
  slice; the first slice supports execution with an attached T3 coordinator.
- Each brief includes run/lane/scenario IDs, expected account, exact session handle,
  schema/model/dashboard IDs, allowed setup/test/cleanup actions, viewport,
  expected results, evidence paths, deadline, and where to checkpoint or report.
  Credentials are scoped private references, not prompt text or report contents.
- Reserve capacity for a verifier and honor the runtime's actual ceiling. Requested
  five tracks with parallelism two means two active testers and a queued remainder.
  Do not confuse test workers, browser contexts, and AI-agent slots.
- Every mutating browser action requires a current exclusive lane lease. Lease
  generations must be enforced by the browser adapter, not merely stated in a
  prompt. Workers get scoped adapter handles; stale generations are rejected.
  A backend exposing only unrestricted native tab tools cannot claim this
  guarantee: it must gain scoped control or use explicit sequential operation.
  Reassignment also requires confirmed worker interruption and old-session closure.
  Checkpoints follow scenario
  steps; controller heartbeats continue during tool waits. An expired lease pauses
  work; it never automatically authorizes a replacement writer.
- Independent verification uses its own session/account/fixture where possible.
  Otherwise, it acquires the original lane only after the tester releases it.
  Shared administrator/provider configuration tests acquire a global exclusive
  phase; they cannot run beside ordinary feature testing.
- The coordinator persists findings as they arrive, deduplicates them, keeps
  unaffected scenarios moving, and reports milestones. Agents do not independently
  rebuild the app, reset fixtures, or alter another lane's grants.

## Durable results and acceptance rules

Store ignored `artifacts/qa/<run>/` with `manifest.json`, an append-only event log,
per-lane checkpoints, evidence, cleanup ledger, and generated JSON/HTML reports.
Use private 0700 directories and 0600 session/credential files; exclude auth state,
headers and secrets from shareable bundles. Serialize report updates through the
coordinator or atomic ledger operations, not competing JSON overwrites.

For every scenario record functional and visual status independently:
`passed`, `failed`, `blocked`, or `not-run`. Include expected/actual results,
viewport, deployed build, account/resource references, timestamps and evidence.
Save/reload is mandatory for persistence scenarios. Download checks inspect the
actual file contents. Pagination checks compare known rows across real pages.
Chat-only tests perform their scoped actions in chat; DOM invocation, API calls,
or an editor workaround do not count as successful chat/manual interaction.

Retain screenshots and recordings for selected successful checkpoints as well as
failures. Style checks include clipping, overlays, spacing/readability, focus,
errors/loading/empty states, reduced motion and narrow layouts. Emulation is
labeled as emulation; selected touch/keyboard flows need physical-phone review.
A failed scenario stays failed after a workaround; downstream testing may resume
from a separately documented starting point.

Resume repeats build, session and resource checks, reconciles saved revisions,
and skips only proven completed steps. Cleanup removes only ledger-owned objects
and verifies ownership first. Preserve retained accounts, credentials, useful
saved models and reports; stop rather than guess when ownership is ambiguous.

## Optional multiple-instance fallback

`--isolation instances` remains unavailable until `start.sh` supports isolated
instance IDs, project names, volumes, runtime directories, secrets, images/build
identity, locks, TLS ingress ports and cleanup. All lifecycle operations still go
through that launcher. Preserve the canonical default stack and Tailscale routes.
Separate app instances must still use separate browser contexts (or deliberately
isolated hosts/cookie scope); port numbers alone never satisfy the session test.
Do not add this cost until shared server state demonstrably requires it.

## Delivery order and verification

1. **Runner contract and records:** implement CLI parsing, plan/doctor, private
   manifests, account/resource leases, structured briefs, reports and QA runbook.
   Test invalid arguments, conflicting leases, interrupted writes and secret
   exclusion. No claim of parallel capability at this stage.
2. **Startup and fixtures:** add launcher lease participation, deployed identity,
   effective-account preflight and deterministic per-lane fixtures. Verify stale
   build rejection, missing grant/source/model failures and owned-only cleanup.
3. **Browser and dispatch vertical slice:** prove two independent contexts on one
   app, attach the real T3 agent controller, and run two AI testers concurrently
   with the verifier. Demonstrate login/logout isolation, modal recovery, evidence
   capture, stale-worker fencing and correct stop/resume after interruption.
   If native T3 cannot provide the required controls, deliver the explicitly
   selected isolated backend rather than advertising unsupported T3 parallelism.
4. **Complete five-track acceptance:** lifecycle, canvas, rules, query/report and
   chat-only work run in capacity-aware waves. Require successful create/save/
   reload, export contents, real second-page results, desktop/mobile evidence and
   an independent retest of findings. Keep all coverage gaps explicit.
5. **Regression integration:** update current auth/artifact paths for concurrent
   runs, attach review evidence to reports, and add narrowly selected approved
   screenshot baselines. After manual findings are reviewed, run focused tests;
   run the consolidated relevant suite at the final verification gate. Do not
   launch the full suite after every exploratory action.
6. **Only if justified:** implement and verify launcher-owned multiple instances.

The parent issue stays open until the end-to-end parallel run, recovery and
reporting acceptance criteria are demonstrated. This planning PR has no runtime
changes, so it requires document/link review, not an application rebuild.

## References

- [Playwright browser-context isolation](https://playwright.dev/docs/browser-contexts)
- [Cookie isolation excludes ports (RFC 6265, section 8.5)](https://www.rfc-editor.org/rfc/rfc6265#section-8.5)
- [Current browser configuration](../../playwright.config.js)
- [Current launcher](../../start.sh)
- [Demo scenario provenance](../../dev/postgres/demo-scenarios/README.md)
