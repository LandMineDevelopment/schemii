# PostgreSQL DevOps storage v1

This is the storage foundation for [#278](https://github.com/LandMineDevelopment/schemii/issues/278),
part of [#277](https://github.com/LandMineDevelopment/schemii/issues/277). It installs
five ordinary domain tables and five named reader datasets. It does not run CI,
import evidence, modify Schemii metadata, deploy an application, or establish
acceptance. The thirty reviewed metric definitions in #277 remain authoritative,
including review-obligation and failure-episode denominators. PR/review/finding
facts wait for #281/#282; the importer and analysis pack wait for #279/#280.

## Safe setup and migration

Requires the existing project dependency `psycopg` and PostgreSQL client tools for
backup controls; no new package dependency. PostgreSQL 15–18 is the initial
version boundary; real verification currently covers **18.6 only**. No database,
profile, credentials or shared service is discovered automatically.

1. An administrator approves a **new dedicated database** and reserves the five
   cluster role names. Inspect [provision.sql](../scripts/devops/provision.sql).
   It refuses occupied database/role names and invalid names/UUIDs before writing.
   Run it in an exclusive provisioning window, with explicit host/port/user/admin
   database arguments, `psql -X -v ON_ERROR_STOP=1`, `-v db_name=devops_analytics_NAME`
   and `-v database_id=NEW_UUID`. The SQL creates a database from `template0`, owned
   by `devops_maintenance`, with a `schemii-devops-v1:UUID` database comment.
   Provisioning is an administrative recipe, **not** a transactional migration:
   CREATE DATABASE cannot share the role-creation transaction. Preserve partial
   provisioning on failure and reconcile it; do not automatically delete or retry.
   One role set per dedicated analytics database/cluster is supported initially.
2. Separately provision authenticated LOGIN principals. A migration login must
   inherit CONNECT from `devops_migrator` and have SET authority through it to
   `devops_owner`. Create it with INHERIT **before granting** the migration role
   (PostgreSQL 16+ captures inheritance on the membership). `devops_migrator`
   itself is NOINHERIT. Collector and reader logins inherit only their respective
   roles, never owner/migrator/maintenance or other authority. Authentication and
   login credentials are administrator responsibilities, not generated here.
3. Store a connection profile and passfile outside Git, owned by the caller,
   mode 0600, under a private 0700 directory. Example shape (placeholders only):

   ```json
   {"host":"analytics.example.invalid","port":5432,"dbname":"devops_analytics_NAME","user":"analytics_migration_login","passfile":"/private/path/pgpass","sslrootcert":"/private/path/ca.pem"}
   ```

   These are the only accepted fields (`sslrootcert` is optional). Network
   connections use `verify-full`; a private absolute Unix socket path uses local
   authentication and an explicit passfile (empty for a disposable trust fixture).
   The CLI clears ambient PG variables while connecting; no default DB, DSN,
   options, password argument or ambient credential file is used. Keep profiles,
   server diagnostics and original receipts private; never paste credentials.
4. Run with the same explicit identity on every invocation:

   ```bash
   python scripts/devops/storage.py --help
   python scripts/devops/storage.py plan --profile /private/profile.json --database-name devops_analytics_NAME --database-id UUID --json
   python scripts/devops/storage.py migrate --profile /private/profile.json --database-name devops_analytics_NAME --database-id UUID --json
   python scripts/devops/storage.py status --profile /private/profile.json --database-name devops_analytics_NAME --database-id UUID --json
   ```

`plan` and `status` inspect without persistent writes. All actions first validate
name, UUID database marker, maintenance ownership, server version, dedicated
namespace, authority/runtime memberships and database/public grants. Migration
also checks stored UUID/contract version, the exact contiguous migration/checksum
history and the installed catalog digest **before any persistent target write**.
Unknown versions, changed migration bytes, schema/ACL drift and partial control
schemas refuse. No schema resets, automatic repair, down migrations or fallback
connections exist. A transaction-scoped advisory lock serializes migrations;
apply and version recording share one transaction. Reapply is a no-op. A failed
transaction rolls back all migration DDL/data. The digest checks catalog
structure/ACLs, not fact contents; it is not a signature against an authorized
owner changing both schema and stamps. Backup restore is verified on the same
PostgreSQL version, not promised across major versions.

The v1 JSON result always contains `schema`, `ok`, `reason`, `next_action` and
`exit_code` (argument errors included). Successful results add `action`,
`contract_version`, `database_id`, `schema_version`, `pending_versions`,
`applied_versions`, `migration_checksums` and integer `server_version`. Exit 0:
validated status/plan or committed migration; 2: unsafe/invalid input or refused
contract; 3: database/connection failure (`sqlstate` when available and
`commit_state="unknown"`). Errors never
include database exception text, SQL payloads, profile contents or credentials.
For `wrong-target`, reconcile with the provisioner; for `divergent-version` or
`divergent-schema`, preserve the target and investigate original files/backups.
For database or post-connection errors, preserve the original diagnostics: an
error during COMMIT acknowledgement or connection closure does not prove rollback.
Do not replay `migrate`; run `status` with the **same explicit database name and
UUID**, reconcile versions/checksums with the provisioner, then decide whether a
retry is safe. Only pre-connection input errors assert that no connection was
attempted. A validated status is not application acceptance.

## Tables and identities for #279

Read [001_foundation.sql](../scripts/devops/migrations/001_foundation.sql) as the
frozen field/type/privilege contract. There is no generic JSON input, event graph,
framework or importer CLI. Ordinary parameterized INSERTs in collector transactions
are the ingestion boundary.

| Table | Grain and stable identity |
| --- | --- |
| `repositories` | UUID registry, unique `(provider, repository_key)`; immutable, exact replay allowed |
| `import_batches` | One actual import invocation; `local-ci-v1` or `test-telemetry-v1`, collection status/counts/reason |
| `ci_runs` | One actual runner invocation, not one commit or one import |
| `commands` | One selected run slot, unique run/slot; executed/reused/pending/excluded |
| `test_attempts` | One observed case attempt, unique command/case/zero-based attempt number |

Every versioned table has `(repository_id, installation_id, id, revision)` as its
primary key. Each logical UUID has one immutable `source_key`, unique within its
table/repository/installation. Preserve an installation UUID across imports;
different checkouts/installations must not collapse simultaneous runs. Derive
logical UUIDs reproducibly from those qualified, **allowlisted non-secret**
identities or retain an explicit durable mapping. A millisecond run ID or HEAD
alone is insufficient: local CI's unique receipt invocation directory and source
context disambiguate it. Store a safe opaque invocation token, never the private
path itself. Retrying an import keeps the fact IDs/revisions; a new actual import
attempt gets a new batch ID. Re-importing unchanged facts preserves their original
batch references and observed-at; do not restamp them to the new batch.

All versioned rows carry `observed_at`, `change_kind`, `evidence_sha256` and
`evidence_ref`; run/command/attempt rows also pin an exact import-batch revision.
`evidence_ref` is a safe opaque lookup token (private evidence resolution belongs
to #279), not a URL/path. Rows pin exact historical parent revisions via composite
foreign keys including repository and installation. No cross-repository or
cross-installation donors/parents are supported in v1. Registry fields, stable
source identity, parent logical identity and attempt ordinal cannot be corrected
into a different fact.

Revisions start at 1/`initial`; subsequent contiguous revisions explicitly say
`correction` or `retraction`. Observation time cannot move backwards. Repeating
an identical row is a no-op; conflicting data at the same revision fails, including
when the caller tries `ON CONFLICT DO NOTHING`. Insert a new revision with its own
evidence to correct an assertion. UPDATE/DELETE are prohibited. The complete old
rows (including failures) remain. Current views choose the highest revision **then**
filter tombstones, so a retraction cannot reveal an older assertion. An intentional
later correction can reassert the fact and remains visible in history.

Commands may advance from pending to an actual disposition through a revision;
after selection becomes executed/reused/excluded its disposition, donor and
inventory/counts freeze. Reuse pins a matching **executed** donor command revision,
with matching command/project/shard/inventory/count. It has no new outcome,
execution timestamps or duration. Reuse is lineage, never acceptance; inspect the
original run/source/cohort and any subsequent donor corrections/retractions before
using it. Only an original executed command revision permits test attempts. Fake
`not-run` records are not attempts. Original failing attempt 0 and real passing
retry 1 remain separate rows, never a synthetic new pass for the reused slot.
Import only an actually observed lifecycle record; skip/cancellation placeholders
without observation belong to completeness/gaps, not `test_attempts`.

Transactions default to READ COMMITTED; per-logical-fact advisory locks serialize
replay/revisions, and unique indexes prevent source/slot/case aliases. Higher
isolation callers must handle PostgreSQL serialization failures by reconciling and
retrying their **whole** owned transaction; no automatic retry is implemented.
Database role grants are not per-repository tenant isolation. Collectors are trusted
sanitizers for their dedicated analytics store, not an untrusted multi-tenant API.

### Existing producer mapping and privacy

The existing producers are `scripts/ci/local_ci.py`, `scripts/ci/summary.py` and
its Node/Python/browser reporters; this change does not alter them or acceptance.

- Receipt `source.commit/fingerprint/index_fingerprint/dirty` maps to
  `head_sha/source_fingerprint/index_fingerprint/dirty`. Preserve all, not just HEAD.
- `base_ref` is not a SHA: resolve from authenticated original context only.
  `base_sha`, `policy_sha256`, `inventory_sha256`, `cohort_key` stay NULL when not
  actually emitted/proven. Do not hash a branch label and call it policy/cohort.
- `profile`, `mode`, run timestamps/duration retain producer meaning. Copy the
  receipt's `status` verbatim into `outcome`, using the complete v1 mapping below.
  Copy its explicitly emitted boolean `acceptance` into the nullable column;
  absent means NULL. Never derive acceptance from outcome, completeness or reuse.
- The indexed `commands` array defines slots, including pending ones. Use a closed
  command key (`python`, `node`, `static`, etc.), project/shard and slot token, never
  raw argv/executed_command. Missing expected inventory counts remain NULL.
- Sanitized telemetry `test_id` maps to `case_key`, its zero-based `attempt` stays
  zero-based, `execution_ms` maps to `call_ms`; setup/teardown retain milliseconds.
  Only observed attempts are imported. Unknown phase values remain NULL; real
  producer-emitted zero remains zero. Wall timestamps and monotonic `duration_ms`
  are different clocks and are not summed/subtracted as interchangeable quantities.

The local-CI v1 receipt mapping is closed and lossless. `completeness` describes
selected verification coverage, independently of the original execution outcome
and the producer's acceptance assertion:

| Receipt `status` / stored `outcome` | Stored `completeness` |
| --- | --- |
| `running` | `pending` |
| `pending` | `pending` |
| `passed` | `complete`, after validating stable source, every selected command and required evidence, and empty pending lists |
| `feedback-passed` | `partial` when `pending_commands` or `pending_layers` is nonempty; otherwise `complete` after validating the full selected inventory |
| `failed` | `partial` (the selected verification did not pass) |
| `cancelled` | `cancelled` |
| `incomplete` | `partial` |
| `source-changed` | `partial`; source instability is retained and excludes acceptance/comparable-cohort claims |

For all rows, preserve the actual emitted `acceptance` value, including false;
unknown remains NULL. A positive assertion is valid only for `passed`/`complete`
outside feedback mode; SQL refuses contradictory positive assertions. A
`feedback-passed` row can therefore have complete observed coverage while still
asserting **no acceptance**. Never normalize it or `source-changed` to `passed`.
Reject unknown statuses and contradictory receipt fields before inserting; these
storage checks do not replace #279's original inventory/source/evidence validation
or establish coordinator acceptance. Command and attempt outcomes have their own
SQL vocabularies and must not be substituted for run status. A command whose
receipt entry says `source-changed` retains that exact outcome too, even when its
process exited zero; never copy the earlier process `passed` value over it.

Keys are bounded ASCII tokens; hashes are lowercase 64 hex, Git IDs 40/64 hex,
ordinals/counts nonnegative, measured milliseconds finite nonnegative numeric(18,3),
statuses fixed SQL CHECK lists. Timestamps are timestamptz; unknown values are NULL,
not epoch/zero/empty strings. No raw JSON payloads, stdout, failure text, chats,
command lines, dynamic titles, URLs, DSNs, environment maps, credentials or user
content enter facts. **Hashing a secret is still prohibited**, including source keys.
Regex/type checks cannot determine semantic sensitivity: #279 must close its
allowlist and reject unsupported fields before insertion, not launder them into
hashes. Reject unknown source kinds/statuses; do not silently coerce them.

## Named reader datasets and useful queries

Reader access is limited to `devops.v1_repositories`, `v1_ci_runs`,
`v1_command_runs`, `v1_test_attempts` and `v1_collection_health`. Intermediate
`current_*` views and base/control tables are private to privileged roles.
Datasets include `definition_version=1` (except the registry), original revision,
evidence/batch lineage and available source/cohort context. Child datasets hide
retracted current parents while preserving original bases. Detailed provenance
inspection uses the authorized collector/maintenance connection, not analyst
access to all historical rows. These are flattened foundations for #280, not a
replacement metric catalog or a complete metric pack.

Example T02/T03 first-attempt population, with its exclusions visible:

```sql
SELECT repository_id, installation_id, cohort_key, profile,
       count(*) FILTER (WHERE outcome='passed') AS passed,
       count(*) FILTER (WHERE outcome='failed') AS failed,
       count(*) FILTER (WHERE outcome IN ('passed','failed')) AS denominator,
       count(*) FILTER (WHERE outcome NOT IN ('passed','failed')) AS excluded,
       count(*) FILTER (WHERE outcome='passed')::numeric /
           nullif(count(*) FILTER (WHERE outcome IN ('passed','failed')),0) AS pass_rate
FROM devops.v1_test_attempts WHERE attempt_number=0
GROUP BY repository_id, installation_id, cohort_key, profile;
```

This counts observed first attempts only; missing/unstarted cases require the
separate expected-inventory/completeness dataset and cannot be called zero.
NULL cohorts are unknown and must not be pooled into a comparable benchmark.
Example T08 on original executions, with sample counts and explicit cohort/window:

```sql
SELECT repository_id, installation_id, command_key, profile, cohort_key, outcome,
       count(*) AS samples,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_ms) AS p50_ms,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY elapsed_ms) AS p90_ms
FROM devops.v1_command_runs
WHERE disposition='executed' AND elapsed_ms IS NOT NULL
  AND cohort_key IS NOT NULL AND started_at >= now()-interval '30 days'
GROUP BY repository_id, installation_id, command_key, profile, cohort_key, outcome;
```

Elapsed is finish minus start in milliseconds; monotonic producer `duration_ms`
is available separately. Neither sum of attempt durations nor review wall time
is effort. Query collection status/rejected counts and completeness alongside any
metric. A synthetic fixture is never a natural metric sample. Full retry/completeness
and collection freshness definitions remain #280 work, not implied by this query.

## Authority and backup ownership

| Role | Authority |
| --- | --- |
| `devops_owner` | NOLOGIN schema/object owner, no superuser/BYPASSRLS/create-role/database/replication |
| `devops_migrator` | NOLOGIN NOINHERIT controlled SET path to object owner, used only for explicit migrations |
| `devops_collector` | INSERT/SELECT on five base tables and SELECT on named views; no UPDATE/DELETE/TRUNCATE/DDL/owner membership |
| `devops_reader` | SELECT on the five named views only; no historical/intermediate/control-table access |
| `devops_maintenance` | NOLOGIN database owner and SELECT for backup; no analytics-table deletion/DDL or membership in object owner |

Maintenance/database and migration authorities are administrative, never runtime
roles. Maintenance can control/drop its dedicated database; restrict membership
and use only for explicitly authorized operations. For a private custom-format
backup, use a dedicated maintenance login with explicit connection parameters,
`pg_dump -Fc -N public`. **Do not use `-n devops -n devops_control`: that omits
global default privileges**, which must survive restoration. Keep only the
approved schemas in this dedicated target. Write the dump to a precreated 0600
file inside a 0700 private directory, hash it, record source schema/migration and
identity, and preserve original evidence separately. Restore only into a **new**
disposable approved database with the same UUID marker, provisioned ownership and
public grants; use the migration login and `pg_restore --exit-on-error
--role=devops_owner`. Validate `status`, rows/history/tombstones, role denials and
lineage before treating restore as successful. Restore is an explicit maintenance
operation, not a migration fallback. It preserves source UUID and fact identities;
do not import restored synthetic fixtures as natural runs.

The focused control verifies a private unencrypted dump and same-version disposable
restore. File modes are **access control, not encryption**. Encryption/key custody,
off-host retention, production recovery objectives and cross-version restore are
pending operational acceptance; no production backup claim is made here.

## Focused verification

`tests/devops/test_storage_cli.py` runs without a database. Real controls in
`test_storage.py` require explicit `SCHEMII_DEVOPS278_FIXTURE` pointing to an owned
private marker under `/home/kasey/.cache/schemii-devops278`; they verify PID birth,
data/socket paths and no TCP listener before use. The marker/profile format is
visible in the test fixture; no service is started/discovered by tests. Setup is
coordinator-authorized and manual, not an implicit part of test discovery.

```bash
python -m pytest -q --confcutdir=tests/devops tests/devops
# Set the explicit fixture marker only for authorized real-database controls.
python -m ruff check scripts/devops tests/devops
python -m ruff format --check scripts/devops tests/devops
python -m mypy --explicit-package-bases scripts/devops/storage.py tests/devops
```

Keep first failures, source/command/scope fingerprints and restore evidence private.
The local `./ci.sh --plan --base BASE` currently selects full acceptance for new
unknown leaves. This focused storage campaign does not replace that plan;
coordinator-scheduled applicable acceptance and new independent current-head review
remain required. No application rebuild, shared fixture, browser run, deployment,
GitHub Actions or natural metrics collection is needed for this storage change.
