# Portable manual UI testing suite

Run `./test.sh` from the repository root, or `./testing/test.sh` from anywhere.
The suite lives here: `harness/` owns browser/agent coordination, `database/`
builds the portable PostgreSQL fixture image, `personas.json` defines permissions,
and `provision.py` preserves identities and uses the application's supported APIs.
All container lifecycle and database reset operations go through `./start.sh`.

## First setup

Supply a private mode-0600 administrator credential JSON (`{ "username": "…",
"password": "…" }` or `{ "admin": { ... } }`). Never commit it.

```bash
./test.sh setup --copies-per-persona 20 --admin-credentials /private/admin.json
./test.sh personas
./test.sh verify-data
```

Setup creates **six personas × 20 copies = 120 retained accounts/data spaces**:
no access, modeler, designer, report author, report viewer, and administrator.
Same-persona copies have identical effective permission definitions and data;
they have distinct login credentials, schema names and profile IDs. The three
author personas receive 60 managed source profiles in total. Viewer accounts test
the empty/denied-authoring state until an explicit dashboard fixture is supplied.
Administrator accounts grant account administration, not implicit product access.
See [persona details](PERSONAS.md).

Setup can extend the pool by increasing copies (up to 100 per persona); it never
shrinks the pool or rotates existing credentials. The application has a managed
profile capacity: default 100, including existing non-QA profiles. Larger pools
must fit that configured capacity; the suite never silently raises it. Unknown
remote username/profile collisions or changed permissions fail instead of adopting
or overwriting another user's resources.

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
