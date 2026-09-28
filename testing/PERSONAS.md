# Retained QA personas

The catalog in `personas.json` defines six permission sets. The default pool has
20 independent identities per persona, or 120 accounts. Accounts with the same
persona have identical application capabilities but distinct database schemas,
passwords, and owned application resources. Runs must lease individual accounts
before starting browsers; a persona name alone is not an isolation boundary.

`no_access` exercises product denial. `modeler` and `designer` receive their
respective product and their own managed QA source. `report_author` receives
`schemer:access`, `schemer:author`, and `schemoo:access`, plus an account-owned
saved Orders model and Schemer dashboard backed by its own managed QA source.
The model and dashboard are private to that report-author account; they are not
shared with viewer accounts. `report_viewer` has Schemer access without authoring
or Schemoo access, so its initial coverage is empty-state and permission-denial
behavior. `administrator` can administer accounts but receives no implicit
product capabilities.

Only the three author personas receive managed profiles: 60 profiles at the
default pool size. All 120 slots still have isolated PostgreSQL identities and
schemas. The managed pool shares the application's saved-connection ceiling
(100 by default) with existing managed profiles. Increasing copies can reach
that ceiling; provisioning stops with an actionable error and preserves completed
work. It never raises the global limit or deletes existing connections.

The single public entry point is `./test.sh`. Its setup commands delegate to
`provision.py`; direct Python examples below document the provisioner interface
for maintenance:

```bash
python testing/provision.py init --copies 20
python testing/provision.py app --admin-credentials /private/admin.json
```

Both accept `--state-dir`; its default is `.schemii/testing`, or
`SCHEMII_QA_STATE_DIRECTORY` when set. `app --accounts username,username` verifies
or provisions only selected retained slots. Administrator credentials must be a
private mode-0600 JSON file containing either `{"username":"…","password":"…"}`
or `{"admin":{"username":"…","password":"…"}}`.

Setup must start the QA database through the repository launcher before app
provisioning. The provisioner uses only `https://localhost:8001`, permitting its
local self-signed certificate. It creates application accounts and managed
connections using supported APIs, checks PostgreSQL connectivity, and signs in as
each account to verify capabilities, expected accessible products, and denied
products. A slot becomes `provisioned: true` only after those checks succeed;
run selection must require both that flag and `accountId`. It never mutates
application metadata directly.

Private state comprises:

- `registry.json`: retained app/DB passwords and incrementally saved resource IDs.
- `credentials.json`: app credentials consumed by browser preparation.
- `database-credentials.tsv`: database-only login/schema/password records.
- `database-admin-password`: retained QA database bootstrap credential.
- `fixtures.json`: expected permissions and source identities, with no passwords.

State files use mode 0600 inside a mode-0700 directory and remain outside version
control. Do not bake them into the database image. Repeated initialization
preserves passwords and existing slots, adding identities only when requested;
it never shrinks the pool. Repeated provisioning verifies recorded resources and
fails on unknown username/profile collisions or changed permissions instead of
adopting accounts or silently granting more access.

Fixture reset must reset only the QA target data. Preserve the registry,
PostgreSQL login roles, app accounts, app metadata volume, and metadata encryption
key. App password hashes cannot be exported as usable passwords, and saved
connection credentials depend on the retained metadata encryption key. Restore
missing private state from backup instead of regenerating credentials against
existing accounts. An interruption between a successful API creation and saving
its ID fails closed on the next setup; reconcile that exact owned resource before
retrying rather than adopting objects by name.

## Moving credentials to a fresh installation

Resource IDs belong to an application's metadata database. Copying a complete
registry to a fresh application would refer to missing accounts and connections.
Use credential-only export/import to preserve passwords while allowing setup to
create new local IDs:

```bash
./test.sh export-credentials --output /private/transfer/qa-credentials.json
# Securely transfer that private file to the destination machine, then:
./test.sh import-credentials --input /private/transfer/qa-credentials.json
```

The backing interfaces are `python testing/provision.py export --output FILE`
and `python testing/provision.py import --input FILE`. They accept `--state-dir`.
The bundle format is `schemii-testing-credentials-v1`, containing a credential-only
registry and `databaseAdminPassword`. It retains usernames, persona/schema names,
app passwords, and DB passwords; it strips `accountId`, `connectionId`, and
`provisioned`. Export writes atomically with mode 0600, requires a private parent
directory (created as mode 0700 if absent), and never overwrites an existing file.
Import validates the entire bundle first and requires an empty state directory,
apart from its provision lock. It never replaces existing credentials.

After import, run normal suite setup against the fresh target/application to
create and verify local resources. Import alone does not provision accounts or
mark slots ready. An existing same-named account on the destination remains an
explicit collision; credentials are never used to silently adopt another
installation's resources. For recovery of the **same** metadata database, restore
the complete original state backup instead so its IDs remain consistent.

Database resets do not remove saved app models, reports, or account-owned history.
Those resources require separate, ownership-tracked cleanup; provisioning never
deletes them.

## Report-author starter model and dashboard

Normal `./test.sh setup` creates one disposable, account-owned Schemoo model and
one Schemer dashboard per report-author slot. The model starts from the QA
account's `orders` table. The dashboard contains an Orders detail tile with
sample rows. The report-author persona receives Schemoo access so it can select
the saved model while creating its own dashboards. Provisioning signs in as the
author and verifies the effective product routes and both saved-object owners.

The private registry stores each fixture's model and dashboard IDs under
`reportAuthorFixture`; the generated `fixtures.json` records expected owner,
connection, namespace, and model relationships for harness preflight. Interrupted
API creation is never reconciled by name: an object created before its ID was
recorded is reported as an unowned name collision and must be reconciled
explicitly.

For lifecycle review, select `--persona report_author --tracks lifecycle
--products schemer`. The assigned starting dashboard and model are read-only for
the lane. Create a run-named dashboard through the UI, verify its save/reload
behavior, and delete that new dashboard before finishing. The explicit
`./test.sh cleanup-author-fixture --accounts qa_report_author_001` command
removes only the selected account's ledgered starter dashboard and then its
model. It refuses an account reserved by a run and preserves the QA account,
profile, credentials, source schema, and unledgered objects. Running normal
`setup` later creates fresh starter fixtures.
