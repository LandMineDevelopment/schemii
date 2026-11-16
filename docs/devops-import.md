# Original local-CI importer

This is the repository-local v1 interface for [#279](https://github.com/LandMineDevelopment/schemii/issues/279), under [#277](https://github.com/LandMineDevelopment/schemii/issues/277).
It imports existing original local-CI receipts and sanitized reporter streams into
[the foundation](devops-metrics.md). It never runs tests, changes acceptance
validators, discovers credentials, scans artifact trees or reads hosted Actions.
Natural retained-data intake and independent review are separate acceptance gates.
Synthetic verification is not a natural metric sample.

## Start offline

Use the constrained project Python environment described in
[testing](../testing/README.md). No new dependency is required.

```bash
python scripts/devops/cli.py --help
python scripts/devops/cli.py schema --json
python scripts/devops/cli.py metrics --json
```

The same CLI offers `doctor`, `plan`, `dry-run`, `import`, `resume`, `status`,
`explain` and `export`. Every action accepts `--json`. Human output renders the
same typed result fields as versioned JSON; there are no interactive prompts.
`export` emits the sanitized selected projection to stdout, not raw source files.
Redirect it only into an approved private destination.

## Explicit candidate intake contract

An intake owner must approve one private input root and an exact v1 manifest.
No manifest is discovered or created by ingestion. Keep the manifest and original
files outside Git, including the durable invocation/installation UUID mapping.
For each actual invocation assign one UUID once, bound to its original receipt
directory. Keep that mapping across copies/replays. Do not assign different UUIDs
to copies of one execution. Repeated selected receipt inodes are refused. Millisecond
`run_id` and HEAD alone cannot distinguish actual invocations/installations.
Repository registry values must be approved public identifiers, never user titles,
paths, credentials or hashes of those. Never place secrets in allowlisted fields.

Example shape (replace every UUID and file selection with approved values):

```json
{
  "schema": 1,
  "repository": {
    "id": "4b7ce1d2-88ef-4f08-8e1a-e91c6cff9423",
    "provider": "github",
    "repository_key": "LandMineDevelopment/schemii",
    "display_name": "schemii"
  },
  "installation_id": "4e5c83cf-17fc-4b8e-950e-0380b2644212",
  "runs": [{
    "invocation_id": "1f8cd70d-67b0-4dd6-9623-fd5dfb2cb3a9",
    "receipt": "original-run/receipt.json",
    "telemetry": [{"slot": 0, "file": "original-run/command-00/timing.jsonl"}],
    "summaries": [{"slot": 0, "file": "original-run/command-00/timing.summary.json"}],
    "revision": 1,
    "change_kind": "initial",
    "observed_at": "2026-11-16T00:00:00Z"
  }]
}
```

Select every `timing_evidence` file mentioned by each selected receipt, including
failed streams. Slots are zero-based indexes in its original `commands` array.
Only exact producer `command-NN/timing.jsonl` and corresponding
`timing.summary.json` names bind to that receipt. Missing saved summaries are
explicit gaps; the stream is still validated directly with the existing summary
validator. A success stream must satisfy the original evidence predicates and
scoped inventory validator. Failed, cancelled and partial streams are validated
structurally without requiring success. A malformed/truncated JSON record refuses;
valid streams without an end record remain incomplete. No prefix salvage occurs.

V1 supports current `scripts/ci/local_ci.py` schema 1, the exact recipes emitted
by `scripts/ci/test_selection.py`, and the optional exact native-document prefix.
Unknown schemas, commands, receipt fields and telemetry fields refuse. Known
private fields (`executed_command`, classification details, raw reason,
deployment details, browser credential filename, base-ref label) are discarded
before fact hashing/output. They are never stored or hashed into analytical IDs.
Only the curated public identity, typed source fields, closed states, numeric
measurements, safe reporter hashes and provenance enter the projection. Byte
fingerprints used to detect concurrent file changes remain transient in memory.
Input receipts are bounded to 20 MiB each, 64 MiB total, 512 exact files, 64 runs,
64 commands per run and 64 explicit reuse edges. All path components are opened
without following symlinks; nonregular files, escapes and changing files refuse.

The original producer `run_id` participates in the sanitized run evidence digest.
UUIDs are qualified by repository, installation, table and original invocation;
commands additionally include slot, attempts include case hash and original
zero-based attempt number. `node-unit` and `node-browser-infrastructure` have
separate command keys even though both use the node execution lane.

`source.commit/fingerprint/index_fingerprint/dirty`, mode/profile, producer
status, nullable emitted acceptance and observed durations retain their meaning.
`base_sha`, run policy/inventory and cohort remain NULL because these producers do
not emit them. A branch label is never interpreted as a SHA. Command inventory is
the digest of the actual sanitized plan cases; `expected_cases` is the emitted
start count, including NULL. Setup/call/teardown remain separate milliseconds.
Node's unmeasured setup/teardown placeholders become NULL. For nonpassing Node
and pytest attempts, zero phases that cannot be distinguished from missing
observations also become NULL. The projection records `unmeasured-phase-timing`
as a collection gap; original outcome and known nonzero timings remain intact.
Passing pytest lifecycle phases and Node call timing retain measured zero;
browser hook/body measurements retain their emitted values. Original receipts
stay unchanged. Wall timestamps and monotonic durations remain separate, and no
command wall timestamps are invented.

Original failure followed by recovery retains both attempts. The reporters emit
`not-run` and some `cancelled` records for unstarted cases without an observation
bit. V1 conservatively excludes these ambiguous placeholders from attempts and
reports `unobserved-placeholder`; it does not claim they executed. Cancellation
still survives on the run/command. Feedback, source-changed, pending and missing
source/terminal/summary remain distinct via original outcomes, nullable fields,
coverage and explicit gap codes. A run's producer assertion does not establish
coordinator acceptance. A collection with any gap publishes as `partial`, including
a successfully reconstructed stream whose saved summary was lost.

### Reuse and revisions

Optional manifest `reuse` is a curated applicability assertion, not a producer
success or permission to rerun. Its entries have `target_invocation`, `target_slot`,
`donor_invocation` and `donor_slot`. Both originals must be selected. The target
must be pending, the donor an original executed slot of the same closed command,
project and shard. The donor's exact inventory/count and revision are pinned.
Reuse has no new outcome/timing/attempt. It does not remove the original target's
pending coverage or establish acceptance. Hosted reuse contracts are unsupported.
An intake owner must independently justify applicability before selecting an edge.

Unchanged revision replay is immutable. To correct facts, explicitly advance the
manifest revision and `change_kind: correction`, retaining identity/source fields.
Optional per-selection `revisions` maps exact fact `source_key` values to
`revision`/`change_kind` overrides; this lets newly observed attempts start at
revision 1 while existing parents advance. Run source keys are invocation UUIDs,
command keys append `.SLOT`, attempt keys append `.CASE_HASH.ATTEMPT`.
SQL enforces contiguous history, immutable identities, parent revisions and
nondecreasing observation times. `observed_at` is an intake assertion and must
remain unchanged for replay, not the time an operator happens to replay.

A run `retraction` publishes only its next run tombstone. Named views hide its
children while preserving their original executed parent revisions/history.
Changing bytes at the same revision refuses; never overwrite evidence or restamp
a checkpoint to make it pass. This interface does not infer corrections,
retractions or applicability from Git history, chat text or file modification time.

## Validate, plan, apply

Provision the dedicated database and roles with the foundation workflow first.
Use two explicit owned 0600 profiles: `--profile` is the migration validation
login, `--collector-profile` is a login granted only collector authority. Both
must name the same explicit host, port and database. There is no ambient libpq
fallback. The guard reuses foundation target/UUID/roles/grants/schema/version/
checksum validation and retains its migration lock while the collector writes.
The guard connection never writes facts; the collector never receives migration
or owner authority. Doctor/status do not migrate or repair anything.

```bash
python scripts/devops/cli.py doctor --profile /private/migrator.json --collector-profile /private/collector.json --database-name devops_analytics_NAME --database-id UUID --json
python scripts/devops/cli.py plan --input-root /private/intake --manifest intake.json --database-name devops_analytics_NAME --database-id UUID --json
python scripts/devops/cli.py dry-run --input-root /private/intake --manifest intake.json --database-name devops_analytics_NAME --database-id UUID --json
```

`plan`, `dry-run`, `explain` and `export` are offline: no database connection,
config writes or checkpoint creation. They return `plan_id`, table counts, gaps
and the explicit-manifest coverage boundary. A plan does not prove target health.
Precreate an owned 0700 operation-state directory outside Git. Then use the exact
returned plan digest and a fresh operation UUID:

```bash
python scripts/devops/cli.py import --input-root /private/intake --manifest intake.json --database-name devops_analytics_NAME --database-id UUID --profile /private/migrator.json --collector-profile /private/collector.json --state-dir /private/operations --operation-id OPERATION_UUID --expect-plan PLAN_DIGEST --json
```

`import` requires a matching selected projection and revalidates input bytes before
writes and before commit. Schema/target checks repeat on every invocation.
A nonblocking transaction advisory lock refuses a busy repository/installation
stream. Repository, batch, new run/command/attempt revisions publish in one
collector transaction. Reused donors precede their children. Unchanged facts keep
their original batch and observation; a different actual import operation may
publish a zero-new-row batch, never new execution. Repeating an operation ID
reconciles the stored plan digest and returns `replayed` without new rows.

The private operation journal contains only UUIDs, sanitized plan digest,
timestamps and fixed states/reasons, and is fsynced before DB work. It records
attempts without overwriting previous failures. The database batch is the atomic
publication/checkpoint; there is no independently advanced local watermark.
After interruption, connection loss, COMMIT acknowledgement loss or journal-write
failure, state is **unknown**, never an unconditional rollback claim. Do not
blindly retry. Run `status` with the same arguments, then `resume` with the same
operation ID and exact plan. Resume reconciles a committed batch first; if absent,
it applies the same immutable plan transaction. No automatic retry occurs.

A torn journal tail is retained, reported as `journal_loss`/`cleanup_pending`, and
isolated from subsequent framed events. A valid durable operation header permits
exact resume and DB reconciliation; a lost header refuses. No evidence is deleted
or automatically cleaned. Keep files private and investigate journal loss even
when database reconciliation succeeds.

`status` requires the explicit manifest, target, state directory and operation ID.
It separates that operation's last attempt, the stream's last published batch,
the stream's last complete collection success, gaps, changed-plan/stale state,
unknown freshness outside the selected manifest, and cleanup pending. A partial
last batch is not last complete success. Both stream summaries select each
batch's latest revision before applying status filters. Retracted batches are
excluded, and superseded complete revisions cannot become current success.
Ties use stable batch identity; the exact operation lookup still reports its
latest revision, including a retraction, for reconciliation.
It cannot infer attempts belonging to
unselected operation journals. Status is read-only and never repairs a checkpoint.

## Results, refusal and reconciliation

All results have `schema: 1`, `ok`, `state`, `reason`, `next_action`, `exit_code`.
Exit 0 means an offline result, validated target/status, or committed/reconciled
import. It does **not** imply complete source coverage or application acceptance.
Exit 2 means refused arguments/input/identity/plan/permissions. Exit 3 means a
DB/interruption/I/O uncertainty requiring reconciliation. No exception text,
SQL payload, DSN, URL, private path, stdout or credentials enter diagnostics.

For `changed-plan`/`changed-input`, preserve the first plan and inspect the exact
selected inputs. For `immutable-digest-mismatch`, preserve both versions; use an
explicit supported correction, not a new identity for the same execution.
For `busy-stream`/`busy-operation`, reconcile the active owner; do not poll/retry.
For target/schema/role failures, ask the provisioner to reconcile the original
foundation contract; never reset or restamp it. For unknown commit state, query
status before exact resume. Original raw diagnostics stay private.

The selected collector session must itself be a nonprivileged login with actual
grant ancestry limited to `devops_collector` and optional `devops_reader`,
effective inherited collector access, and no database CREATE/TEMP or public
schema privileges. Superuser/role-management/database-creation/replication/RLS
bypass rights refuse even without explicit collector membership. A grant without
effective inheritance is insufficient; use the dedicated provisioned collector
profile. PostgreSQL 18 keeps inheritance on individual membership edges, so
changing only a login's INHERIT attribute does not repair an existing grant.

Before natural intake, the coordinator must approve an exact source-reviewed
manifest and stable identity mapping, original files/producer compatibility,
reuse applicability and target/operation authority. Capture a private source-to-row
reconciliation: selected receipt/slot counts, original observed attempts by
outcome/ordinal, excluded placeholders, gap codes, planned table counts, inserted
counts and exact batch references. Query the five tables scoped to repository,
installation and operation; unchanged rows retain donor batch IDs. Replay must
add zero facts. Compare sanitized `export` logical IDs/revisions/evidence digests
to stored rows and the selected raw originals privately. This document and the
synthetic controls do not authorize or perform that natural intake.

## Verification boundary

Focused tests use the normal project Python runner and telemetry plugin:

```bash
python scripts/ci/python-tests.py tests/devops/test_importer.py tests/devops/test_import_cli.py
```

Real controls require an explicit `SCHEMII_DEVOPS279_FIXTURE` marker in the new
private devops279 cache, verify PID birth, socket/data paths, no TCP listener and
actual PG18.6. Tests never start or discover a database. Preserve first failures
and source-bound receipts. Full planner acceptance is coordinator-owned; no UI
changed and this work does not require a deployment, browser campaign, production
analytics import, encryption claim or multi-major PostgreSQL claim.
