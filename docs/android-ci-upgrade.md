# Preserve CI analytics during an Android preview upgrade

This is the coordinator's rollout for [#308](https://github.com/LandMineDevelopment/schemii/issues/308)
under [#254](https://github.com/LandMineDevelopment/schemii/issues/254). The retained
Android release is `android-v0.1.0-preview.13` at
`10ef3618caeb64997a266f147d8c901f3b7ab69a`. The separately sealed
`android-v0.1.0-preview.14` candidate is
`4e78d6d9bd4e29474f5fa0f98ec75e4dda08d488`. Verify the retained manifest,
sealed archive, reviewed installer and active source against those exact
identities. Rebind and rehearse if any bytes or release identity change.
The candidate contains the foreground Termux:Boot change. The release-to-release
source diff has no added, removed or changed application metadata migration SQL.
Installed dependency compatibility, readiness and retained-data readback still
need proof. Keep the [#280](https://github.com/LandMineDevelopment/schemii/issues/280)
analytics view/model rollout outside this maintenance window. A later schema
version needs its own reviewed query and comparison contract.

The launcher interrupts the app and both PostgreSQL 17 services. CI facts,
database grants and importer receipts live in the target cluster. The metadata
cluster holds the saved connections, workspace, accounts, model definitions and
encrypted connection credentials. Retain its original credential encryption key.
A passing read proves that the approved stored credential can still authenticate
to the target through Schemii.

## Hold ingestion and bind the baseline

1. Use the private [Android operations guide](android-agent-operations.md) to
   verify the pinned SSH identity and the active-operation marker. The
   coordinator acquires the operation lease and verifies the exact currently
   running release and trusted Tailscale HTTPS origin. Keep the lease through
   final handoff or an explicit incident transfer.
2. Ask the [#307](https://github.com/LandMineDevelopment/schemii/issues/307)
   upload owner to durably hold draining for this operation UUID before taking
   the baseline. Collection and durable enqueue may continue. Confirm that all
   target writers are quiescent, the single uploader has zero in-flight writes,
   and uncertain commits have been reconciled with the existing importer status
   and operation IDs. Fence profile, account, model and workspace edits until
   readback completes. Separate sessions and per-page transactions depend on
   that fence.
3. Pin a private plan with the exact saved connection endpoint and PostgreSQL
   login, approved Schemii account and workspace IDs, foundation database UUID,
   migration 001 checksum, and both release manifest SHA-256 hashes. Select the
   intended v1 dataset and confirm the counts independently before collecting
   the baseline. Use four counts from the reviewed current reader views, covering
   CI runs, commands, attempts and collection batches. Keep the operation plan
   fixed through any subsequent candidate or rollback check.
4. Use an already authenticated, authorized reader session. Put only the
   `schemii_session` cookie value in a privately supplied file. The helper
   accepts a file path, uses only GETs through the explicitly named trusted
   HTTPS origin, and refuses redirects. Keep the plan, cookie, stage inputs,
   receipts and logs outside Git, with owned directories at 0700 and files
   at 0600. Keep them off public PRs and issue comments.

Example plan shape. Fill the hashes from independently verified inputs, select
the actual role and IDs privately, and use the approved live counts.

```json
{
  "schema": 1,
  "operation_id": "11111111-1111-4111-8111-111111111111",
  "origin": "https://phone.example.ts.net",
  "account_id": "user_approved_reader",
  "connection_id": "pg_00000000000000000000000000000001",
  "workspace_id": "ws_00000000000000000000000000000001",
  "target": {
    "host": "127.0.0.1",
    "port": 5434,
    "database": "devops_analytics_example",
    "database_id": "22222222-2222-4222-8222-222222222222",
    "username": "example_ci_reader",
    "ssl_mode": "disable",
    "postgres_major": 17,
    "migration_sha256": "<reviewed SHA-256 of scripts/devops/migrations/001_foundation.sql>"
  },
  "before": {
    "tag": "android-v0.1.0-preview.13",
    "commit": "10ef3618caeb64997a266f147d8c901f3b7ab69a",
    "manifest_sha256": "<SHA-256 of the verified installed release manifest>"
  },
  "after": {
    "tag": "android-v0.1.0-preview.14",
    "commit": "4e78d6d9bd4e29474f5fa0f98ec75e4dda08d488",
    "manifest_sha256": "<SHA-256 of the verified candidate release manifest>"
  },
  "counts": {
    "v1_ci_runs": 1,
    "v1_command_runs": 22,
    "v1_test_attempts": 3762,
    "v1_collection_health": 1
  }
}
```

For each phase use a fresh stage directory. The coordinator captures these from
the same authorized deployment and active operation:

| File | Required source |
| --- | --- |
| `hold.json` | Upload owner acknowledgment with integer `schema: 1`, the plan's `operation_id`, the exact `phase` (`baseline`, `readback` or `rollback`), `state: "hold"` and integer `in_flight: 0`. |
| `release.json` | Exact `ANDROID_RELEASE.json` bytes from the verified active release. Resolve the active release using the supported private access path and verify installed bytes with the release contract before collecting it. |
| `runtime.json` | Fresh JSON from the supported `schemii status` command for that active release. |
| `foundation.json` | Fresh JSON from the supported foundation status command on the exact target cluster/database. |

Use the existing scoped maintenance profile via the approved private target path.
The profile's database and route must match the saved application's database and
target endpoint. If the profile uses a private forward, verify the forwarded
endpoint against the app's target host and port. Use that same verified route for
every capture. The status file itself lacks an endpoint field. The coordinator
is responsible for tying it, the release and the runtime status to the live
HTTPS origin. Do this from trusted operator evidence, never from an event payload.
A copied or touched status file cannot establish a fresh observation. The helper
requires modification times within 15 minutes for the hold, runtime and foundation
files. Preserve capture times on private transfer and recapture after delays.

```bash
umask 077
python scripts/devops/storage.py status --json \
  --profile "$MAINTENANCE_PROFILE" --database-name "$TARGET_NAME" \
  --database-id "$TARGET_UUID" > "$STAGE/foundation.json"
python scripts/devops/phone_upgrade_check.py baseline \
  --plan "$PLAN" --stage "$STAGE" --session-file "$SESSION_FILE" --output "$BASELINE"
```

The foundation command checks the database marker and schema UUID, migration
ledger checksum, schema/ACL drift, and version with maintenance authority. Keep
that authority separate from the saved Schemii reader. The API checks durable
metadata, the exact authenticated account and saved connection/workspace, and
uses the stored credential to verify reader visibility of `devops` and absence
of `devops_control`. It executes the four v1 views via the existing relation
preview endpoint, which runs managed PostgreSQL reads without creating SQL
Console execution receipts or query history. The reader's namespace behavior
and view access are checked directly. Review the exact reader login's grants
during operator preflight. Confirm it is the intended reader login with no collector or maintenance authority.

The helper checks counts and hashes the complete typed result of each current
view, across all repositories and installations in this dedicated database. It
reads up to 250 rows per request, at most 10,000 rows per view and 2 MiB per
response. It stops on truncated results, count drift, repeated rows, changed
relation definitions or inconsistent cursors. Because the endpoint has no
ordered snapshot across pages, quiescence and matching counts and unique keys
are required. If paging remains unstable or the approved dataset outgrows the
bound, keep the hold and revise the read contract. The result covers the current
visible facts and batches. Inspect the importer's status separately for any
specific pending or uncertain upload, and use backup/recovery evidence for
historical revisions. Output contains counts, digests and fixed stage results
rather than raw rows, credentials, URLs or API diagnostics. Exit 0 is a
captured baseline or matching snapshot. Exit 2 is a hold/refusal. Each output
path is exclusive, so a later attempt cannot overwrite an earlier receipt.

## Back up and stage the candidate

Use a coordinator-approved maintenance window. The baseline and hold must
already be in place before shutdown.

1. Use the [Android cold backup path](android-release.md#operate-and-recover)
   with a fresh private destination. `schemii backup "$BACKUP"` stops the
   whole stack and leaves it stopped. Run `schemii verify-backup "$BACKUP"`.
   Verify the stopped status and backup inventory. Retain the paired metadata
   and target PostgreSQL 17 physical data and roles, original deployment
   secrets and metadata encryption key, original TLS material, and optional
   trusted proxy configuration and Tailscale identity. Copy the verified
   bundle to encrypted off-phone storage and verify the copy. Preserve the
   old release, exact installer, launch/runtime identity and current boot
   configuration alongside the backup record. A checksum pass establishes
   integrity. A successful isolated restore and authenticated readback
   establish recoverability.
2. Confirm the fallback with the matching Debian 13 ARM64 PG17 runtime in a
   disposable isolated rehearsal. Use the supported cold restore on a fresh
   matching state and verify both clusters, the metadata encryption key
   through actual connection readback, saved work and grants. Fence the
   restored proxy from production and keep at most one live copy of the
   original Tailscale identity. [Portable logical restore](android-portable-restore.md)
   has a different bundle and separate launcher acceptance.
3. Run the reviewed exact candidate installer through the normal Termux
   versioned installation path, following the [Android release guide](android-release.md#install-the-versioned-release).
   Verify the active manifest and runtime dependencies. Use only the owning
   `schemii` launcher for lifecycle commands. Refresh the owned boot task
   using `schemii enable-boot` for the foreground change. Verify that it
   retains the network gate and stays attached to the supervisor after
   readiness. Check status and trusted HTTPS, collect the fresh candidate
   stage, and run:

```bash
python scripts/devops/phone_upgrade_check.py readback \
  --plan "$PLAN" --stage "$STAGE" --session-file "$SESSION_FILE" \
  --baseline "$BASELINE" --output "$READBACK"
```

## Recover and prove device availability

If installation is interrupted, the status probe fails, the source is wrong,
or readback differs, keep the hold, preserve the first failure and stop at the
owning launcher boundary. Confirm which runtime and both clusters are running
before any retry. Preserve the failed installation and cold backup.

A rollback to the preserved preview.13 release is appropriate only after
confirming compatibility of both retained clusters, application metadata,
dependencies, boot configuration and keys. Use the verified previous installer
and the supported release/launcher path to select it on stopped state. Recheck
status and run the helper with phase `rollback`, the original baseline and a
fresh previous-release stage. A source link change alone leaves all runtime
and data compatibility checks pending. If compatibility is uncertain or
state has changed incompatibly, restore the paired cold bundle in a fresh
isolated matching environment with the old release and original keys, using
`schemii verify-backup` and `schemii restore` from the Android release guide.
Retain the original state and fence the recovered deployment until its
supported authenticated readback is proven. If unexpected writes occurred
after the cold point, reconcile them with the data owner before selecting
any recovery point. Only one deployment may accept uploads.

After the candidate passes its immediate readback, record distinct physical
observations under the same hold. Keep the PIN. The owner performs the reboot
and first PIN entry. Record what is reachable before first unlock, then record
first-PIN behavior, ordinary relock and idle, and VPN disconnect/reconnect as
separate observations with time and release identity. For each applicable
post-unlock condition record pinned SSH, trusted Tailscale HTTPS, live app and
both PostgreSQL readiness, and a fresh matching CI readback in a new receipt.
A successful first unlock does not establish relock, idle or VPN recovery.
A network or readiness failure stays visible in the coordinator ledger.

## Hand back to the upload owner

The helper always records `uploader: "hold"`, including when continuity
matches. A coordinator authorizes **ready** after release, backup/restore,
independent review, physical evidence and trusted matching readback gates are
satisfied. The handoff to #307 carries the operation UUID, verified deployment
and release identity, expected target name and UUID, schema version and
checksum, the matching readback digest and time, and the coordinator's
decision. Keep the handoff private. A failed, missing, stale or mismatched gate
leaves the durable hold in place. A later maintenance request or loss of verified
readiness stops draining again.

The upload owner acknowledges the same handoff, independently validates the
target and schema at drain time using its own importer guard, establishes the
single writer, and drains bounded batches through #307. Reconcile existing
operation IDs before replay. Retain the backlog and original receipts until
confirmed commit and source-to-row readback. This runbook and the helper grant
no lifecycle or upload authority.

## Disposable verification

Use the constrained project Python environment. Run the focused tests:

```bash
python -m pytest -q tests/devops/test_phone_upgrade_check.py
```

Set `SCHEMII_PORTABLE_POSTGRES_BIN` to an existing PostgreSQL 17 bin directory
and `SCHEMII_308_BEFORE_SRC` and `SCHEMII_308_AFTER_SRC` to absolute `src`
directories from the independently verified release sources, alongside their
matching `ANDROID_RELEASE.json` manifests. The opt-in test
creates two private socket-only PG17 clusters using the project restore fixture.
It publishes synthetic importer facts, provisions a synthetic reader and
saved workspace, and exercises the real authenticated ASGI routes over retained
state with each release's Python source, restarting both PostgreSQL clusters
between baseline and candidate readback. It tests interrupted-status refusal and
reopening the previous application source over compatible retained state.
This is source/API/PG17 compatibility evidence. The Android package runtime,
cold restore under the launcher, trusted ingress and physical device observations
are coordinator-owned acceptance gates.
