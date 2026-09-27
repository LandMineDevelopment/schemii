# Portable manual UI testing suite

Run `./test.sh` from the repository root, or `./testing/test.sh` from anywhere.
The suite lives here: `harness/` owns browser/agent coordination, `database/`
builds the portable PostgreSQL fixture image, `personas.json` defines permissions,
and `provision.py` preserves identities and uses the application's supported APIs.
All container lifecycle and database reset operations go through `./start.sh`.

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
