# Portable manual UI testing suite

## Deterministic developer feedback

GitHub Actions is disabled. Local development owns feedback and acceptance using
`./ci.sh`; Python 3.12 and Node 22 are the former hosted reference versions. For a
fresh checkout, create separate test and quality environments with constrained
dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --constraint constraints.docker.txt --editable '.[dev]'
python3 -m venv .venv-quality
.venv-quality/bin/python -m pip install --constraint constraints.docker.txt --editable '.[quality]'
./ci.sh --plan --base origin/main
./ci.sh --feedback --base origin/main
```

`./ci.sh` uses the checkout's `.venv` for tests and `.venv-quality` for static
quality when present. `SCHEMII_CI_PYTHON` and `SCHEMII_CI_QUALITY_PYTHON` can select
prepared interpreters elsewhere. Install the project from the checkout being
checked, especially for quality: another worktree's editable installation changes
Mypy's imported module graph. The two environments preserve the former hosted
setup without relaxing its type rules; a correctly prepared combined environment
also works.

`npm test` needs Node, not an npm dependency installation; its frontend and harness
tests use built-in Node modules. Default pytest discovery includes `tests/` and
the deterministic provisioning, cleanup and
native-agent regressions under `testing/`, once each. No launcher rebuild is needed
for these checks. PostgreSQL cases report skips unless their explicit integration
credentials are supplied; live browser specs and load experiments are separate
commands, never implicit pytest discovery. Launcher subprocess tests still acquire
the shared deployment lease; wait for active manual QA to finish before running
the broad command.

See [the feedback inventory](../docs/testing-feedback.md) for focused ownership
commands, unique coverage, cost and the broader pre-merge acceptance path. A fast
subset or a skipped/live/manual layer is not complete application acceptance.
Use `./ci.sh --plan --base origin/main` for the complete change plan including
staged and unstaged edits. `./ci.sh --feedback` runs selected deterministic checks
with required database/browser acceptance explicitly pending. `./ci.sh` runs the
complete selected plan; `./ci.sh --full` deliberately selects every layer. Inspect
existing source-bound checks before scheduling acceptance. Follow
[the verification reuse policy](../AGENTS.md): the coordinator assigns focused
checks, reviewers inspect their actual evidence, and a handoff or merge does not
justify another local full suite. Private source-bound receipts retain failed,
blocked and pending layers; a successful feedback command is not acceptance.

Full database acceptance requires an explicitly provisioned disposable external
PostgreSQL database, `SCHEMII_TEST_METADATA_DSN` and
`SCHEMII_TEST_METADATA_PASSWORD`. The integration suite creates and removes owned
schemas; never point it at user data or the launcher's private metadata database.
The assembled metadata saturation/recovery case also requires
`SCHEMII_TEST_SOURCE_DSN` and `SCHEMII_TEST_SOURCE_PASSWORD` for a separately
provisioned disposable source PostgreSQL endpoint. Its role needs `CREATE DATABASE`
so the case can create and remove its UUID-owned source database. A different
database on the metadata host/port is insufficient: the application deliberately
blocks that entire endpoint as a query target. These request-composition checks
do not establish served HTTPS, lifespan, native UI or deployment capacity acceptance.
Browser acceptance requires an owned test account through private
`SCHEMII_E2E_CREDENTIALS_FILE` or explicit environment credentials, or explicit
`SCHEMII_E2E_BOOTSTRAP=1` consent on a fresh disposable stack. It runs the selected
desktop and Android shards and, where selected, isolated backup recovery. The
local runner retains one mode-0600 bootstrap credential file privately for all
shards and later explicit reuse. It never adopts an existing administrator; do
not share that credential file with report evidence. The
coordinator schedules this work, waits for manual QA leases to release, and uses
`./start.sh` as the only application lifecycle command. Keep account state,
screenshots, traces and receipts private. Missing prerequisites remain blocked
rather than silently skipped; there are no hosted artifact uploads or required
Actions checks.

## Manual fixture setup

Run `./test.sh` from the repository root, or `./testing/test.sh` from anywhere.
The suite lives here: `harness/` owns browser/agent coordination, `database/`
builds the portable PostgreSQL fixture image, `personas.json` defines permissions,
and `provision.py` preserves identities and uses the application's supported APIs.
All container lifecycle and database reset operations go through `./start.sh`.

## Running launcher subprocess tests

The launcher subprocess tests in `tests/test_startup.py` use temporary private
state directories and fake Docker commands. They still acquire the repository's
shared deployment lease, so do not run them while a live QA run holds that lease.
If a test is rejected because the deployment is leased, that is expected
coordination behavior, not a failed application check; finish the QA run and
rerun the tests after its lease is released.

## First setup

Supply a private mode-0600 administrator credential JSON (`{ "username": "…",
"password": "…" }` or `{ "admin": { ... } }`). Never commit it.
On this installation, check `.schemii/accounts-test-credentials.json` first;
the existing QA administrator is `accounts-admin`. Retained designer credentials
and account/profile IDs are already in `.schemii/testing/credentials.json` and
`.schemii/testing/registry.json`. Read their presence, ownership and file mode,
not their passwords, before creating accounts. These paths are installation
specific; use `--admin-credentials` with the actual private file when different.

```bash
./test.sh setup --copies-per-persona 20 --admin-credentials /private/admin.json
./test.sh personas
./test.sh verify-data
```

Setup creates **six personas × 20 copies = 120 retained accounts/data spaces**:
no access, modeler, designer, report author, report viewer, and administrator.
Same-persona copies have identical effective permission definitions and data;
they have distinct login credentials, schema names and profile IDs. The three
author personas receive 60 managed source profiles in total. Each report author
also receives Schemoo access and an account-owned saved Orders model and Schemer
dashboard on that account's QA schema. Viewer accounts still test the
empty/denied-authoring state; administrator accounts grant account administration,
not implicit product access. See [persona details](PERSONAS.md).

## Prepare a live Schemii AI chat lane

The ordinary designer persona has Schemii access but no AI provider grant. After
connecting an installation-owned ChatGPT Codex credential in the application,
provision one retained designer explicitly:

```bash
./start.sh
./test.sh provision-chat --account qa_designer_001 \
  --admin-credentials /private/admin.json
```

The private admin file must be mode 0600. This command tests the shared credential
against the live model catalog, grants only the selected designer access to
`gpt-6-luna` at default reasoning in its detached Schemii workspaces and exact
QA database profile, verifies the designer can select that model, and records the
choice in the retained fixture manifest. Repeating it preserves matching grants;
it refuses to replace a different grant policy. The grants are specific to this
deployment and are not included in the portable credential export.

Select the provisioned account explicitly for a chat run. The chat track now
checks for an authenticated, active model before dispatch, so a status HTTP 200
or a disconnected provider cannot count as readiness. A passing preflight still
does not prove a successful AI turn; the manual agent must send a prompt and
verify the reply, approvals and saved result.

Setup can extend the pool by increasing copies (up to 100 per persona); it never
shrinks the pool or rotates existing credentials. The application has a managed
profile capacity: default 100, including existing non-QA profiles. Larger pools
must fit that configured capacity; the suite never silently raises it. Unknown
remote username/profile collisions or changed permissions fail instead of adopting
or overwriting another user's resources.

## Report-author starting point and cleanup

Each retained report author starts with an Orders model and a dashboard showing
sample order rows. The model and dashboard belong to that QA account and use its
own `qa_report_author_NNN` profile. Setup verifies access while signed in as the
author. The dashboard is a saved place to begin; create a run-named dashboard
through the UI for changes and leave the retained starter objects unchanged.

Plan or prepare one report-author lifecycle lane with desktop and mobile
evidence:

```bash
./test.sh plan --persona report_author --agents 1 --parallel 1 \
  --tracks lifecycle --products schemer --viewports desktop,mobile
./test.sh doctor --persona report_author --agents 1 --parallel 1 \
  --tracks lifecycle --products schemer --viewports desktop,mobile --headless
./test.sh prepare --persona report_author --agents 1 --parallel 1 \
  --tracks lifecycle --products schemer --viewports desktop,mobile --headless
./test.sh run --run RUN_ID
```

The generated private fixture manifest checks effective Schemoo and Schemer
access, the saved model owner/source, and the dashboard owner/model relationship.
The lifecycle scenario permits writes only to a new dashboard created by that
run; its starter model and dashboard remain read-only. Harness cleanup preserves
both starter objects. To remove selected starter objects after all runs finish,
delete only their ledgered dashboard and model:

```bash
./test.sh cleanup-author-fixture --accounts qa_report_author_001
```

This command waits for the application deployment to be idle, refuses accounts
reserved by an active or unresolved run, verifies each recorded object belongs
to the selected account and source, then deletes the dashboard before the model.
It leaves the QA account, profile, credentials, and database schema in place. A
later full `setup` provisions fresh starter objects. Objects created by a manual
run are outside this ownership ledger and must be deleted through the UI that
created them.

## Isolated writable Schemii targets

The 120 retained QA database roles are deliberately read-only. For full SQL
Console, COPY, connection and migration UI testing, the suite supports three
separately marked, initially empty writer targets: `qa_designer_004`, `_010`,
and `_011`. Their exact `qa_write_designer_NNN` roles and schemas live in the
same dedicated QA database, with distinct private credentials. They never
replace or broaden the original reader roles/schemas. Prepare and attach each
writer through the supported launcher and application APIs:

```bash
./test.sh provision-writer --account qa_designer_004 --admin-credentials /private/admin.json
./test.sh provision-writer --account qa_designer_010 --admin-credentials /private/admin.json
./test.sh provision-writer --account qa_designer_011 --admin-credentials /private/admin.json
```

The command is repeatable only while its marked writer schema is empty and its
privileges remain isolated; reset and verify a used writer before provisioning
it again. It refuses unowned profile collisions or unexpected account grants.
`qa_designer_001` is the chat-only lane; provision
`qa_designer_002` too for the second AI lane with `provision-chat`. The full
Schemii assignment generator and ten-agent command are in
[the harness runbook](harness/README.md). It prescribes exact table types,
constraints, expected row counts and query totals for agents and the separate
verifier. All test-created objects use a unique run prefix. The writer fixture
limits COPY to 1 MiB and generated rows to 5,000 per writer schema.

## Run the same persona concurrently

```bash
./test.sh prepare --persona modeler --agents 10 --parallel 10 \
  --controller codex --agent-model gpt-6-astra --agent-reasoning high --headless
./test.sh run --run FIRST_RUN_ID

# In another terminal/chat, repeat prepare and run.
./test.sh prepare --persona modeler --agents 10 --parallel 10 \
  --controller codex --headless
./test.sh run --run SECOND_RUN_ID
```

Accounts are reserved atomically across linked worktrees. The second run selects
the next available copies rather than sharing the first run's logins. Each lane
uses its own browser process and schema. There are up to ten active testers per
run; total concurrent runs depend on host and model-service capacity.

Startup is serialized. The first run rebuilds through the launcher and records
source identity; concurrent runs reuse that verified deployment only if their
source matches. Runs hold shared deployment leases, so rebuilding or resetting
requires all runs to stop. Use the updated launcher in every checkout; old copies
cannot enforce this contract. A crashed run retains account reservations until
cleanup verifies its recorded processes have stopped; reservations are never
silently stolen. Concurrent resume/cleanup commands are serialized per run, and
account allocation/release uses a repository-wide lock so cleanup cannot remove
a newly assigned account lease.

```bash
./test.sh report --run RUN_ID
./test.sh cleanup --run RUN_ID
```

Finishing a lane closes its browser and releases that account. Cleanup stops the
remaining workers/controller and releases its deployment lease. It preserves QA
accounts, credentials, app models/dashboards and run evidence. It does not delete
application objects created by tests; declare and manage those fixtures explicitly.
For a full Schemii sweep, review its report and documented findings before
cleanup, then call `cleanup` for **every run**, including the independent verifier.
Only after all runs release their reservations, delete seed-created and
post-start exact-prefix app objects through the scoped cleanup helper, reset the three exact writer targets,
verify each is empty, and verify the retained read-only baseline:

```bash
python -m testing.harness.cleanup_schemii_sweep --run RUN_ID \
  --workspace-fixtures artifacts/qa/UNIQUE-workspaces.json
./test.sh reset-writer --account qa_designer_004
./test.sh reset-writer --account qa_designer_010
./test.sh reset-writer --account qa_designer_011
./test.sh verify-writer --account qa_designer_004
./test.sh verify-writer --account qa_designer_010
./test.sh verify-writer --account qa_designer_011
./test.sh verify-data
```

Writer reset preserves the private credentials, app profiles and account grants,
and returns the exact marked writer schema to an empty baseline. Harness cleanup
preserves evidence. Registered reader-schema reset is separate and only needed
for a changed reader baseline; it also requires no active run or reservation.
The scoped helper accepts both the full sweep and the follow-up fixture version;
give it the workspace map for that exact run tag. It also accepts a stopped
subset-account follow-up and cleans only its listed lanes. Run it a second time
to confirm zero remaining owned objects. It preserves preexisting and
other-tag objects. A new follow-up wave uses a fresh tag after this cleanup and
the writer resets. Its preseed step records four exact two-table desired designs
and two permissioned chats for the chat-only tester; see the harness runbook.

## Restore stable data

The dedicated `qa-postgres` container has an internal-only network and its own
persistent volume. It is separate from both app metadata and existing demos.
Every slot has a private schema with 32 customers, 513 orders, a relationship,
a sequence, fixed dates, null cases and a totals view. Database identities are
read-only; application authoring permissions are separate.

```bash
./test.sh reset --space qa_modeler_001
./test.sh reset --space all
./test.sh verify-data --space qa_modeler_001
./test.sh check-reset --space qa_modeler_001
```

Reset validates the registered schema and ownership marker, recreates only that
schema transactionally, restores exact rows/relationships/sequence/grants, then
verifies it. It preserves database logins/passwords, application accounts,
connection profiles and the metadata database. Resets require no active runs or
unresolved reservations. `check-reset` deliberately perturbs one baseline, checks
that drift is detected, and restores it through the normal reset path.

## Credentials and portability

Private runtime state lives in `.schemii/testing/` (or the explicit
`SCHEMII_QA_STATE_DIRECTORY`): registry, generated account credentials, database
credentials, admin secret and fixture references. The directory is mode 0700 and
files mode 0600, excluded from Git and the application image. Database resets and
normal setup never regenerate existing secrets. Back this directory up privately;
the Docker image intentionally contains no passwords.

The pinned [database Dockerfile](database/Dockerfile) and baseline are portable.
To transfer the same credentials to a fresh deployment, use the private export
and import commands; environment-specific account/profile IDs are excluded:

```bash
./test.sh export-credentials --output /private/testing-credentials.json
# On the destination checkout with an empty testing state directory:
./test.sh import-credentials --input /private/testing-credentials.json
./test.sh setup --copies-per-persona 20 --admin-credentials /private/admin.json
```

Import refuses to replace existing testing state. Keep the exported bundle private.
Restoring an existing complete app deployment additionally requires its metadata
backup and original metadata encryption key; this credential bundle does not
replace the application's recovery workflow.

See [harness controls](harness/README.md), [database contract](database/README.md),
and [manual validation](harness/VALIDATION.md). No application test suite is
implicitly launched by any command here.

## Disposable served metadata-pressure qualification (#134)

The opt-in `./start.sh --qualify-metadata-pressure` path is authored for the
remaining served HTTPS/lifespan boundary. It does not run during normal startup,
unit feedback or a load campaign. Run it only after independent review and
coordinator scheduling; it temporarily leases the canonical deployment and may
stop **only the exact recorded retained ingress** to reserve port 8001. Existing
application/database containers, volumes, secrets, accounts and configuration are
preserved. An empty or unhealthy retained deployment is refused before mutation.

Prerequisites are the current source/application-image and configuration match,
healthy canonical and tailnet origins, existing pinned database/ingress images,
a prepared primary `.venv` with the project's existing Psycopg/HTTPX dependencies,
and the committed, unchanged v2 `testing/load/source_monitor.py` dependency from PR #238. Its repaired helper fingerprint is pinned before creation; old/unbound helper bytes are rejected. The
source witness is reused, not reimplemented; its exact bytes and empty baseline
are recorded. A missing witness fails before fixture creation. Do not copy an
unreviewed helper into place to bypass the prerequisite.

The launcher records its private journal first under
`PRIMARY/.schemii/metadata-pressure/pressure_<32 hex>/journal.json`. It creates a
new owner-labeled project with three fresh volumes (metadata, source, observer), fresh private credentials, low
fixed resource/metadata limits, the exact reviewed application image, and a
separate HTTPS ingress at `https://localhost:8001`. Tailscale continues to use
`https://omarchy.taile4f57f.ts.net`; no Serve route is changed. Only this disposable
fixture grants bootstrap consent. Private source/metadata witnesses record exact
database, role, PID and backend-start scope. The source provisioner records its private TLS endpoint, actual server address/port/postmaster start/database OID, and a fresh owned database comment marker before any source query. The fixed recipe covers auth advisory
waits, normal saturation, safe errors/cookie retention, readiness/control,
source cancellation, native close, actual application stop/start lifespan,
fresh authenticated `SELECT 42`, revision conflicts and role/session revocation.
It accepts no arbitrary URL, DSN or SQL.

Every create/stop/start/cleanup has an intent record and exact ownership guard.
Failure or cancellation attempts guarded cleanup and restoration of the retained
ingress, then checks both HTTPS origins. SIGKILL leaves the journal for explicit
`./start.sh --recover-metadata-pressure ABSOLUTE_PRIVATE_RUN_DIRECTORY` after the
recorded controller has exited. Recovery refuses active leases/controllers,
changed source, replaced containers, different ownership and unsafe port state;
it never repeats creation or deletes unknown resources. Unknown resources remain
quarantined. If their exact owned ingress can safely be stopped, restoration is
attempted while cleanup stays explicitly incomplete. Preserve the journal and
all original failed evidence; do not prune containers or volumes to force recovery.

The fixture overrides only the ASGI entrypoint with `dev/pressure/app.py`, which
forwards the unchanged application and its original lifespan messages. A fixed
100ms sampler calls the existing normal/readiness owner snapshots directly,
without a metadata query, admission permit or public endpoint. Its bounded 16KiB
private receipt, with at most sixteen owner-change samples, lives in the fresh observer volume, initialized by a one-shot,
networkless service from the same image. Only the launcher copies that fixed path
from the exact owned app container, with a 32KiB archive/time bound. The saturated
case requires the actual normal owner active/peak/maximum counts of two, bound to
the authenticated process PID and current container/process incarnation. A peak sample must fall inside the actual pressure window; an old cumulative peak alone is insufficient.

A lifespan pass requires the original stock `shutdown.complete`, observed closed
normal/readiness owners with active zero, matching run/process incarnation and
native clean exit without OOM. Exit zero alone, a forced timeout, crash, stale or
missing receipt, or unknown owner snapshot cannot pass. Captured PostgreSQL
backend closure remains a separate requirement. Native exit/OOM, command outcome
and stop classification are retained before evaluating lifecycle proof.

This is an **instrumented qualification fixture**, never an uninstrumented stock
throughput, capacity or manual UI pass. Sampler/receipt overhead is unmeasured and
explicitly recorded. All actual peak/lifespan/cleanup/restoration evidence remains
pending until coordinator-scheduled execution and independent review; fake tests
establish only the ownership and discriminator controls.

### Fixed pressure and representative load profiles

Exactly two immutable, versioned fixture profiles are allowlisted. They share the
same disposable ownership, private data, canonical leases, HTTPS ingress and
restoration/recovery path. There are no resource, environment, DSN or SQL tuning
arguments. Ambient profile-budget variables are discarded and replaced with the
selected fixed values.

| Allocation or budget | `pressure-v1` | `representative-load-v1` |
| --- | --- | --- |
| Application | 1 CPU, 512 MiB, 128 PIDs | 2 CPU, 2 GiB, 256 PIDs |
| Metadata PostgreSQL | 0.5 CPU, 256 MiB, 64 PIDs | 1 CPU, 1 GiB, 256 PIDs |
| Source PostgreSQL | 0.5 CPU, 256 MiB, 64 PIDs | 2 CPU, 2 GiB, 256 PIDs |
| HTTPS ingress | 0.25 CPU, 64 MiB, 32 PIDs | 0.5 CPU, 128 MiB, 64 PIDs |
| One-shot observer initializer | 0.25 CPU, 64 MiB, 16 PIDs | Same |
| Normal/readiness metadata admission | 2 / 2 | 16 / 2 |
| PostgreSQL max connections/shared buffers, each database | 24 / 32 MiB | 100 / 128 MiB |
| Metadata statement/lock/idle/shutdown deadlines | 3000ms / 1500ms / 3000ms / 2s | Stock 5000ms / 1000ms / 5000ms / 15s |
| Source ordinary total/per identity; retained total/per identity | 20 / 4; 19 / 3 | Same |

The representative allocations match the stock `compose.test.yaml` application,
metadata, demo-source PostgreSQL and ingress defaults. Its PostgreSQL server
settings explicitly select the stock PostgreSQL defaults, while its normal
metadata and source admission budgets match `MetadataConfig`, the actual
`create_metadata_repositories` composition and `dev/schemii.toml`. Readiness is
always the unchanged stock independent factory budget of **two**, not a fixture
capacity reduction. Both profiles run exactly one API worker from the same image
and unchanged stock application, through the disclosed fixture-only observer.

`--qualify-metadata-pressure` and `--hold-metadata-pressure SECONDS` retain the
small `pressure-v1` scope. The separate explicit
`./start.sh --hold-load-fixture SECONDS` selects `representative-load-v1`; the
pressure saturation recipe cannot run against that profile. The same bounded
60–3600s holder supports a real 1800s lane plus preparation/drain allowance when
scheduled with sufficient total duration. This is configuration capability;
actual ramp, hold, soak, idle expiry and overhead qualification remain unrun until
coordinator scheduling.

The intent journal, private authority and runtime receipt bind the exact profile
name/version and complete fixed budget object. Preparation reads actual owned
PostgreSQL `max_connections`/`shared_buffers` before provisioning. A startup
`runtime.json` receipt records actual inspected container CPU/memory/PID limits,
normal/readiness owner maxima and source ordinary/per-identity maxima. The
authenticated API PID must match the observer process incarnation. Any mismatch
or unknown prevents held authority publication. Retained 19/3 budgets are bound
to the reviewed stock source/configuration; the receipt does not label them as
live retained-permit measurements. `stockCapacityEligible` and
`capacityAcceptance` stay false; sampler overhead is still unmeasured.

This startup configuration receipt is distinct from the load observer's OS
PID/birth/cgroup receipt. The current normal-launch exporter only accepts the
retained `schemii-test` topology; it cannot supply that witness for the disposable
project. A reviewed launcher-owned disposable OS export remains required before
a qualified load smoke/campaign. Do not relabel configuration as OS evidence or
start the retained stack to manufacture a substitute receipt.

### Bounded attachment for the #135 fixture consumer

`./start.sh --hold-metadata-pressure SECONDS` (60–3600), or the explicit load mode above, prepares the selected unique
fixture, bootstraps its private administrator, prints an `authorityFile` path and
retains both canonical exclusive leases. The default short qualification still
cleans up immediately. The held authority is a private version-1
`launcher_owned_disposable_pressure` receipt, bound to run/nonce, holder PID/birth,
fresh heartbeat, expiration, exact app/source identities and instrumentation.
It supplies the owning `launcherPath`, canonical origins, principal password-file
path, source-witness config and private consumer-credentials-file path. Source
admin and metadata-admin passwords are distinct; consumers receive source-only
administrative authority, never retained-stack credentials or metadata authority.

Before any API/source mutation, the dedicated Node `testing/load/controller.mjs
LOAD_ID` process invokes the owning launcher with
`--metadata-pressure-attach RUN_DIRECTORY PID`. Its session/process-group leader
must be its own PID, owned by the same UID/common Git repository, with a committed
unchanged source tree matching the explicitly reviewed consumer fingerprint.
That pin intentionally remains unavailable until the constrained consumer is
integrated; older detached workload engines cannot attach. Successful attachment
returns a private `attachmentFile`. Every ten seconds or less invoke
`--metadata-pressure-heartbeat RUN_DIRECTORY ATTACHMENT_FILE`; thirty seconds
without a heartbeat ends the attachment. Only one consumer is admitted at a time.
These callbacks use private metadata IPC and never acquire a competing deployment
lease or access the runtime themselves.

After joining its API/source work, the consumer invokes
`--metadata-pressure-detach RUN_DIRECTORY ATTACHMENT_FILE`, observes `detaching`,
and exits its dedicated group promptly. After a one-second acknowledgment window,
the holder joins the entire exact opted-in group, using bounded TERM/KILL if
necessary, before another attachment or restoration. Hold expiry/cancellation
also joins consumers first. PID/argv/birth drift or unknown surviving group
identity refuses all deletion/restoration and leaves explicit recovery state.
SIGKILL retains that journal; explicit launcher recovery enforces the same join
and exact resource guards. An unknown callback response is not permission to
replay a write or continue work. No fixture has been created by authoring these
contracts.
