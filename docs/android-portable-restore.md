# Portable Linux and Android backup contract

`schemii-portable-logical-v1` describes a fresh PostgreSQL 17 logical restore
between the Linux and Android deployment profiles. It carries application state,
both PostgreSQL planes, and the original application secrets. The Termux
launcher can stage and inspect a trusted bundle. Physical-host staging and
cutover acceptance are pending. The current
[Linux metadata backup](recovery.md) and [Android cold backup](android-release.md)
continue to serve their existing recovery paths. Neither contains the portable
input set in this contract. [Issue #256](https://github.com/LandMineDevelopment/schemii/issues/256)
tracks the broader deployment parity work.

## Bundle contents

The bundle is a private directory with exactly these entries.

| Entry | Contents |
| --- | --- |
| `manifest.json` | `format`, integer `postgres_major`, `clusters`, and a `sha256` map keyed by relative payload path |
| `metadata/roles.sql`, `target/roles.sql` | One native `pg_dumpall --roles-only --quote-all-identifiers --encoding=UTF8` export per cluster, including role memberships and password verifiers |
| `metadata/<database>.dump`, `target/<database>.dump` | One `pg_dump --format=custom` archive for each database in that plane, with object owners and grants |
| `secrets/` | `metadata_bootstrap_password`, `metadata_app_password`, `target_bootstrap_password`, `target_password`, `metadata_encryption_key`, `account_setup_token`, `opencode_password` |

`clusters` contains exactly `metadata` and `target`. Each records the original
`bootstrap_user`, `app_user` (the target login for `target`),
`primary_database`, and a sorted, unique `databases` array. Include exactly one
metadata database and every owned target database, up to 32. For example, the
default target inventory includes the primary database, `organization`, and
`schemii_migration_demo`. Database names and the recorded logins are lowercase
ASCII PostgreSQL identifiers of up to 63 bytes. The two logins within a cluster
must differ. A database or role can share a name across planes, so each archive
must be restored into its own cluster.

The recorded bootstrap name must be the source's initial PostgreSQL role
(`pg_roles.oid = 10`). The supported source uses default tablespaces, standard
maintenance databases (`postgres`, `template0`, `template1`), SCRAM logins, and
compatible destination locales and extensions. Inventory the source before export. Refuse v1 if the
source needs custom tablespaces, user state in a maintenance database, a
database outside the supported inventory, or a dependency the destination
cannot provide. External PostgreSQL sources have their own backup and restore
boundary. Logical restore preserves database, schema, role, and object names,
ownership and grants. PostgreSQL assigns physical OIDs on the destination.
Schemii object IDs, revisions, workspace bindings, layout coordinates, and
account data travel as metadata rows.

Keep the original 256-bit standard base64 metadata key byte-for-byte, including
its file encoding, so existing credential ciphertext can be decrypted with its
original owner and connection IDs. The application uses opaque session tokens
with hashes retained in PostgreSQL. There is no separate application session
signing key in the current profiles. TLS CA and leaf keys, tailnet node
credentials, and release signing keys have their own trust and recovery
boundaries. Provision destination ingress credentials through the destination
adapter. Keep any keys required to recover the original host in its private
platform backup.

Place the files in a fresh directory with directories at mode 0700 and files
at mode 0600. These are sensitive unencrypted files, including role password
verifiers. Transfer them using authenticated encrypted transport and retain
them in encrypted storage. Checksums detect accidental changes. Restore only
bundles from a trusted producer since PostgreSQL restore executes SQL with
bootstrap privileges. The producer calls `seal_portable` in
`dev/android/recovery.py` after writing all payloads. The consumer calls
`verify_portable` against its own private copy before opening any restore
connection. That check verifies the manifest, required entries and checksums.
A successful PostgreSQL import and application readback are separate gates.

## Inspect a bundle in Termux

Use an installed matching Android release and an authorized, stopped destination.
The bundle must be an absolute, symlink-free directory in Termux private home,
separate from the release, installed guest and launcher control files.

```bash
./start.sh --android stage-portable /absolute/private/path/to/bundle
```

The launcher refuses a busy destination or unverifiable process ownership and
does not stop it for you. It holds the launcher and guest leases while copying
and verifying the bundle. Each plane is restored with the installed PostgreSQL
17 tools using a separate private Unix socket with TCP disabled. The restore
must load the archived locales and extensions, retain the listed databases, and
authenticate with the original bootstrap and primary-database login using SCRAM.
The space check accounts for the private copy and reserves space for staging.
Archives can expand further during replay, especially when rebuilding indexes.

Success returns a guest path to the private `inspection.json` file under
`/home/schemii/.local/share/.schemii-portable-*`. The receipt records the
verified bundle manifest digest, the installed runtime fingerprint, and the two
distinct, cleanly stopped PostgreSQL system identifiers. The staged directories
keep the original bundle and all seven secrets. Treat the entire stage as
sensitive, including on failure. A late failure leaves `attempt.json` with a
sanitized phase and any unresolved process identity, without an inspection
receipt. Preserve an unresolved stage and its process evidence for review.

Ordinary start, boot, and cold restore cannot select a portable stage.
Application migrations, HTTPS readback, activation and endpoint rebinding
remain separate gates. APK and Linux do not yet have a portable launcher
action.

## Stage both planes before cutover

1. Take the deployment's operation lease and inventory the owned source
   databases, original identities, and file-backed secrets. Quiesce every
   writer, including external clients of the owned target cluster, across the
   whole export window. Each dump has its own snapshot, so quiescence is what
   makes the bundle a coherent recovery point across both clusters. Leave the
   source intact and record the source and its recovery point.
2. Create two fresh, private destination clusters with compatible PostgreSQL 17
   tools, locales, and extensions. Initialize each with its original bootstrap
   name and secret. PostgreSQL gives the initial role special grantor semantics,
   so recreating the same name as an ordinary role can break membership replay.
   Pass the verified role SQL through `portable_roles_for_restore` to omit the
   single redundant bootstrap `CREATE ROLE` while retaining its attributes,
   membership grantors, and all other statements. Feed the result into
   `psql -X --set ON_ERROR_STOP=1 --single-transaction --file -`. Then
   restore that plane's archives with `pg_restore --create --exit-on-error`
   against its maintenance database. Keep owners and ACLs enabled. A create
   restore can leave changes on failure, so quarantine or discard the entire
   private stage if either cluster fails. Do not point running services at it.
3. Verify the database inventory, original logins, ownership, ACLs, app
   migration history, saved work and layouts, and target rows and sequence
   state. Decrypt a credential with its original owner and connection IDs,
   or use the existing recovery key probe to check the original key on
   installations without credentials. Rebind only explicitly identified
   internal endpoints to the destination routes, keeping metadata and target
   fenced from each other. Verify the remapped target connection under the
   restored account and role. Confirm readiness and authenticated readback
   through the destination's supported HTTPS route before granting writes.
4. Promote both clusters, their matching secrets, and destination configuration
   together through the owning launcher under the operation lease. Keep the
   source and its recovery point available for rollback. Fence source writes
   from the final export through promotion, or reconcile any intervening writes
   before promotion. Route writers to only one deployment. After destination
   writes begin, rollback requires reconciling those writes before reactivating
   the source.

## Launcher integration required

| Boundary | Integration needed |
| --- | --- |
| Linux/Docker | Add a dedicated portable export/import path next to the metadata-only recovery command. Export the owned demo/target cluster independently and map `metadata_password`, `demo_admin_password`, and `demo_target_password` to the three corresponding portable secret names. Make metadata and target database/role selections independent. Linux currently shares `SCHEMII_TEST_POSTGRES_DB` and `SCHEMII_TEST_POSTGRES_USER` across different settings, while Android can require different values. Pass restored identities through Compose, bootstrap, app DSNs and readiness. Ensure bootstrap reconciliation does not reassign restored owners or reset credentials, and skip fixture bootstrap/seed for imported target databases. Use fresh volumes and the launcher's lease. |
| Android | Implement logical export and explicit activation under the Android recovery lease, alongside the existing Termux stage action and cold workflow. Use recorded identities through PostgreSQL bootstrap, application config and readiness. Bypass fixture seed for imported target databases and provision destination proxy and TLS configuration locally. |

Both adapters must refuse unknown capability or identity mappings. The
disposable rehearsal below runs PostgreSQL tooling on the workstation.

## Run the disposable rehearsal

With the project's development dependencies installed, use an absolute path to
native PostgreSQL 17 binaries containing `initdb`, `postgres`, `pg_ctl`,
`psql`, `pg_dump`, `pg_dumpall`, and `pg_restore`. Run as a regular user.

```bash
SCHEMII_PORTABLE_POSTGRES_BIN=/absolute/path/to/postgresql/17/bin \
  python -m pytest -q tests/test_android_portable_restore.py tests/test_android_portable_stage.py
```

The tests create generated data and SCRAM credentials in private temporary
clusters using Unix sockets with TCP disabled. They rehearse both profile
directions with the original database and role names, real metadata migrations
for the seeded records, encrypted credential readback, layout and target
inventory checks, role memberships, ACLs, and sequence state. A late target
import failure checks that the partially restored pair stays out of the
successful path. The stage tests run the guest dispatcher against disposable
native clusters and inject failures during target archive replay and shutdown
verification. Without the environment variable the PostgreSQL cases are
skipped. Device acceptance still needs an isolated Termux guest running the
actual launcher and PRoot socket path. Cross-host and authenticated HTTPS
acceptance require the exporter and activation paths.
