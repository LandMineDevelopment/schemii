# Backup, verification, and fresh-install recovery

Run recovery through `./start.sh`. The launcher serializes these operations with
normal starts. Recovery never requires direct Docker commands or elevated shell
privileges. Python 3 is required on the host.

## Make and verify a private backup

With the current application running:

```bash
./start.sh --backup
./start.sh --verify-backup /absolute/path/printed/by/the/backup
```

An optional new destination may be supplied to `--backup`. Existing destinations
are rejected. The default is `.schemii/backups/<UTC timestamp>-<process ID>`.
Use the same `SCHEMII_SECRET_DIRECTORY` and database identity settings used to
launch this deployment, especially when running from a separate worktree.

The bundle contains one PostgreSQL-consistent metadata dump, the original
encryption key, deployment role passwords, setup and sidecar tokens, nonsecret
database/role identities, a probe encrypted with the running application's key,
and SHA-256 checksums. It includes accounts, grants, saved work, history, and
encrypted AI/database credentials. The bundle is **sensitive and unencrypted**:
directories are mode 700, files mode 600. Copy it to encrypted storage outside
this machine using a transfer that preserves permissions. Checksums detect
accidental damage; they do not authenticate a bundle from an untrusted person.
Never restore an untrusted PostgreSQL dump.

Verification builds the current application image, creates an isolated temporary
PostgreSQL project with no published ports or production volumes, restores the
dump transactionally as a non-superuser application role, validates/applies the current migration history, reads
every application table, and decrypts every stored credential. The encrypted
probe also checks the key when no credentials are saved. Only counts are
reported. Temporary containers/network/storage are removed on success or normal
failure; an abrupt host shutdown can leave a stopped verifier project, which
must be identified by its exact `schemii-verify-*` name before operator cleanup.
Verification does not stop or replace the running application.

The verifier defaults to 2 GiB of temporary database storage and a 2 GiB memory
limit. Set `SCHEMII_VERIFY_DATA_SIZE` and `SCHEMII_VERIFY_MEMORY` higher for a larger
metadata backup. PostgreSQL working space can exceed the compressed dump size.
Keep at least one verified off-machine backup before upgrades and back up daily
when friends are saving work. Rehearse a fresh-install recovery periodically.

## Restore on a fresh host or clean installation

`--restore-backup-new` is deliberately a **fresh-install-only** operation. It
refuses existing canonical Schemii containers, either database volume, an existing
secrets directory, or a local Compose override. There is no force switch and no
in-place overwrite path. Do not delete a damaged installation to make it pass;
preserve that installation and recover on another host.

1. Install this source and the normal launcher prerequisites on the fresh host.
   Do **not** run plain `./start.sh` yet: that initializes a new installation.
2. Transfer the trusted bundle with its private permissions intact. Review its
   nonsecret `identity.json`. If it uses nondefault names, export
   `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, and
   `SCHEMII_METADATA_APP_USER` to those recorded values. Continue using those
   settings on subsequent starts. Do not put passwords on command lines.
3. Run:

   ```bash
   ./start.sh --restore-backup-new /absolute/path/to/backup
   ./start.sh
   ```

The restore first passes isolated verification, copies the original secrets,
initializes a new metadata volume with the original bootstrap identity, creates
the original application role, and restores all objects as that role in one
transaction. Application services start only with the subsequent plain launcher
command. If import fails, the transaction rolls back and the new installation
is retained for inspection; the original backup is unchanged. A second restore
attempt refuses that retained state rather than silently destroying it.

After launcher health checks, verify login at `https://localhost:8001`, review
saved accounts/workspaces/dashboards, and test an intended database connection.
Check the configured Tailscale preview independently before handing it out.

**Target PostgreSQL rows are not in this backup.** External sources need their
own database backup/restore process. The bundled demo database is reseeded by
normal startup, so treat edits to its example rows/schema as disposable. Local
TLS certificates are regenerated on a fresh host. Host configuration, local
Compose overrides, and `dev/schemii.toml` changes must be retained separately.
Sessions and provider credentials are restored as of the snapshot; rotate or
revoke them if the incident involved unauthorized access.

## Resource ceilings

The application defaults to 2 GiB RAM / 2 CPUs / 256 PIDs; metadata PostgreSQL to
1 GiB / 1 CPU / 256 PIDs; demo PostgreSQL to 2 GiB / 2 CPUs / 256 PIDs; ingress to
128 MiB / 0.5 CPU / 64 PIDs. Override these with `SCHEMII_APP_*`,
`SCHEMII_METADATA_*`, `SCHEMII_DEMO_*`, or `SCHEMII_INGRESS_*`, using suffixes
`MEMORY`, `CPUS`, and `PIDS`. These ceilings complement query/result admission
limits; they are not promised capacity. Budget for all containers and the host.

All stack services use rotating logs: `SCHEMII_LOG_MAX_SIZE` defaults to `10m`,
`SCHEMII_LOG_MAX_FILES` to `3` per container. Apply changes with `./start.sh`.
Watch disk space separately: database volumes and backup retention are not
bounded by container memory or log limits.
