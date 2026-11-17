# Queue sealed local-CI receipts for later upload

The collector can enqueue an approved local-CI manifest while the analytics
target is unavailable. Enqueue and backlog inspection are local operations.
The spool retains the original manifest selection, its SHA-256, the importer's
validated plan digest, and one operation UUID. A successful enqueue means
SQLite committed the receipt to the private spool. Import uses the original
repository, installation, invocation, revision, and observation fields. Intake
approval and the CI producer's recorded acceptance remain separate.

Keep the spool and the manifest's input root on owned private local storage
outside Git. Use a 0700 directory for each. An explicitly selected external
manifest can live in another owned 0700 directory. Retain the exact manifest,
selected receipt and timing files, and their original identity mapping as
sealed inputs. The spool contains references, not copies, so moving or
discarding the originals can block a drain. No raw timing stream, trace, log,
connection profile, or credential is copied into the spool. Keep manifests,
journals, results, and test evidence private. The [original importer
contract](devops-import.md) defines supported inputs and curator approval,
including historical producer selection. Lifecycle and T3 manifests are
outside this queue's intake contract.

## Collect and inspect offline

Run these from the repository root in the constrained project Python environment.
Here `$SPOOL`, `$INPUT` and `$GATE` denote absolute paths, with `$GATE` in a
private directory. `$DB_NAME` and `$DB_ID` are the dedicated analytics
database name and registered UUID. Use the approved SHA-256 of the exact
manifest for `$MANIFEST_SHA`.

```bash
python -m scripts.devops.receipt_queue --spool "$SPOOL" init \
  --database-name "$DB_NAME" --database-id "$DB_ID" --maintenance-lock "$GATE"
python -m scripts.devops.receipt_queue --spool "$SPOOL" enqueue \
  --input-root "$INPUT" --manifest intake.json --expect-manifest "$MANIFEST_SHA"
python -m scripts.devops.receipt_queue --spool "$SPOOL" backlog
```

Use one persistent spool per target. Initialization pins the target and
maintenance lock. Repeating it with the same values is safe. Submitting the
same validated plan returns the original operation ID, including after
publication. This keeps the first sealed reference even if the duplicate was
submitted from a different private location. A supported correction uses the
importer's explicit revision contract. Backlog reports total and pending
manifests, and the oldest pending entry's operation ID, enqueue time, last
event, and first recorded failure. Pending includes operations that need
commit reconciliation. Publication can contain incomplete or failed CI
observations.

## Drain one batch

For an authorized upload, the operator establishes the approved private
transport separately and supplies both existing [importer connection
profiles](devops-import.md#validate-plan-apply). The queue accepts an owned
private Unix socket directory or an established IPv4 loopback forward. The
importer still requires `verify-full` for TCP. Confirm the authorized
transport and target readiness before calling drain. The queue does not
discover credentials or establish a tunnel. Network profiles are limited to
the loopback listener. The importer verifies database identity, schema and migrations,
and collector privileges under its existing guard.

Provision `$GATE` as a stable owned 0600 regular file in its private directory.
Use the same gate for all drains and maintenance for that target. Before
maintenance, the operator takes an exclusive `flock` on that file and creates
`$GATE.hold` under the lock. Keep the marker through maintenance, reboots,
first unlock, and uncertain readiness. Remove it under the same lock only
after the owner has verified that maintenance has ended and the approved
transport and target are ready. Preserve the lock file's inode. Missing or
unsafe lock files block a drain, as does a held lock or any hold marker.
The local hold coordinates participating operators. Follow the
[Android operations guide](android-agent-operations.md) for the separate
phone-side operation marker and readiness requirements.

```bash
python -m scripts.devops.receipt_queue --spool "$SPOOL" drain \
  --database-name "$DB_NAME" --database-id "$DB_ID" \
  --profile "$MIGRATOR_PROFILE" --collector-profile "$COLLECTOR_PROFILE" --limit 4
```

A drain snapshots up to `--limit` pending manifests in enqueue order
(1–64). It holds the upload and maintenance locks for that batch. Enqueue
can continue while the importer is connecting or working. The first
blocked entry stops the batch and later entries remain pending. Inspect
the reason on the oldest entry and preserve its sealed input and journal.
Once the blocker clears, invoke another bounded drain. A successful
drain can report a remaining backlog.

The importer journal is durable before a queue attempt begins. After an
interruption or unknown commit, the next drain runs importer `status` for that
same operation and plan before exact `resume`. A committed operation is
replayed with zero additional facts. An unpublished operation can finish
under the original ID. A missing or invalid journal following a started
attempt blocks replay. Preserve original failure events, journals, and inputs
for investigation, including any journal loss. Never allocate a replacement
UUID or edit a checkpoint to move the queue past the oldest entry.

This is an explicit collector-side command. Existing automation remains
read-only pending independent review, coordinator rollout approval, and
physical reboot and private-transport recovery acceptance. See
[#307](https://github.com/LandMineDevelopment/schemii/issues/307) and
[#254](https://github.com/LandMineDevelopment/schemii/issues/254).
