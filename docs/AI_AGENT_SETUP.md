# AI Agent Setup Guide

This guide is for coding or terminal agents helping a user install, start, verify, update, or connect Schemii. Read `agent_guide.md` before changing application code or saved schemas. Use `README.md` as the user-facing setup source of truth.

## Default Goal

Unless the user requests another mode, use the complete launcher stack:

```bash
bash ./start.sh
```

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The default `ai-docker-db` mode includes Schemii, dedicated private metadata PostgreSQL plus its one-shot migrator, private tutorial PostgreSQL, private OpenCode, the linked Mercury Books tutorial, and the local Event Studio design. Every mode includes metadata PostgreSQL; it is not a user target. The launcher prints the instance name and exact loopback URL. Do not assume port 8080 or 8081. Use `schemer` for the explicitly no-AI sibling stack and `schemer-ai` for the sibling stack with one shared private OpenCode service. All launcher modes publish applications only on loopback.

## Help A User Install Docker

If `docker` is missing, direct the user to the matching official guide:

- Windows: <https://docs.docker.com/desktop/setup/install/windows-install/>
- macOS: <https://docs.docker.com/desktop/setup/install/mac-install/>
- Linux Engine: <https://docs.docker.com/engine/install/>
- Linux Compose plugin: <https://docs.docker.com/compose/install/linux/>
- Linux permissions/rootless guidance: <https://docs.docker.com/engine/install/linux-postinstall/>

Windows Docker Desktop must use Linux containers. On all platforms, Docker must be started before Schemii. Verify:

```bash
docker version
docker compose version
docker info
```

Treat a Linux Docker socket permission error separately from a stopped daemon. Docker access is effectively administrator-level access. Never weaken socket permissions broadly and never mount the Docker socket into Schemii.

The first launch verifies the selected immutable application images and downloads any missing pinned dependency images; it never builds. Registry, proxy, DNS, or firewall failures are setup failures, not application-code defects.

## Use The Launchers

Do not replace a launcher command with partial direct Compose commands. Launchers provide:

- Mode-specific Compose file selection
- Persistent instance-scoped metadata and OpenCode credential files with owner-only host permissions
- An exact-instance credential lock covering initialization, credential operations, and coordinated recovery
- Per-installation project, volume, credential, and port isolation with exact selected image identity
- Free-port selection and existing-port reuse
- Metadata migration, selected PostgreSQL/OpenCode services, and every selected application readiness check
- Safe legacy-container reuse and an explicit stop when only ambiguous legacy volumes remain

Application container health checks call `/api/readiness`, not the static root. The report keeps required metadata health separate from optional OpenCode and last-observed target health and includes process-local PostgreSQL admission metrics. Schemer depends on metadata migration and its own configured optional services; it does not depend on Schemii health.

Both application processes read the same PostgreSQL runtime controls: global capacity defaults to `12`, exact-target capacity to `4`, and catalog/read/Console/write class capacities to `8`/`8`/`4`/`1`. Override them with `SCHEMII_POSTGRES_GLOBAL_CAPACITY`, `SCHEMII_POSTGRES_TARGET_CAPACITY`, and `SCHEMII_POSTGRES_{CATALOG,READ,CONSOLE,WRITE}_CAPACITY`; target capacity must remain below global capacity. Explicit Console transactions default to maximum `4`, idle expiry `300` seconds, and absolute lifetime `1800` seconds. `SCHEMII_CONSOLE_TRANSACTION_MAXIMUM` is capped at `64`, `SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS` at `86400`, and `SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS` at `604800`; idle must not exceed lifetime. `SCHEMII_MIGRATION_PLAN_TTL_SECONDS` defaults to `900`, while the separate `SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS` defaults to `300`. All must be positive integers. Current checked-in Compose files do not forward host values for these new names; an advanced operator must add them explicitly to each enabled application's service environment. These are process admission/retention and connection lifecycle controls, not PostgreSQL statement or lock policy and not user-owned Console/AI settings. PostgreSQL's stricter `idle_in_transaction_session_timeout` remains authoritative.

AI operation maintenance uses `SCHEMII_AI_MAINTENANCE_` plus `INTERVAL_SECONDS` (`30`), `HEARTBEAT_SECONDS` (`20`), `LEASE_SECONDS` (`90`), `OPERATION_STALE_SECONDS` (`0`), `RESERVATION_STALE_SECONDS` (`300`), `DELIVERY_STALE_SECONDS` (`120`), `CLEANUP_RETENTION_SECONDS` (`604800`), `RECOVERY_BATCH_SIZE` (`100`), and `CLEANUP_BATCH_SIZE` (`500`). Heartbeat must be less than half the lease. Readiness reports maintenance health; do not declare AI write/recovery behavior healthy while it is degraded.

Rerun the same launcher command to start or update an installation. For Git updates:

```bash
git pull --ff-only
bash ./start.sh
```

Do not move or rename an installation directory without explaining that its derived instance identity changes. Use a stable `SCHEMII_INSTANCE` when directory moves are expected.

If the launcher finds legacy config and schema volumes without a legacy container, follow its explicit choice: set `SCHEMII_INSTANCE=schemii` to reuse them, or set another unique name for a separate installation. Never hide or bypass this warning by deleting the volumes.

## Modes

| Mode | Services | User-target PostgreSQL profile host |
| --- | --- | --- |
| Default / `ai-docker-db` | UI, included PostgreSQL, OpenCode | `postgres` |
| `ui` | UI only | Docker Desktop host: `host.docker.internal` |
| `docker-db` | UI, included PostgreSQL | `postgres` |
| `ai` | UI, OpenCode | Docker Desktop host: `host.docker.internal` |
| `local-db` | Linux bridge-mode UI plus private host-PostgreSQL Unix relay | `127.0.0.1` |
| `ai-local-db` | Linux bridge-mode UI, private host-PostgreSQL Unix relay, and private OpenCode | `127.0.0.1` |
| `schemer` | Schemii, Schemer, included PostgreSQL, explicitly no OpenCode | `postgres` |
| `schemer-ai` | Schemii, Schemer, included PostgreSQL, shared OpenCode | `postgres` |

Base Compose does not add a Linux `host.docker.internal` mapping. Use a Linux `local-db` mode for PostgreSQL bound to host loopback. That override keeps Schemii in bridge mode and preserves profiles using `127.0.0.1:5432` through a private Unix-socket relay; existing Docker PostgreSQL instead requires an explicit shared private network override and its service name or network alias.

The metadata PostgreSQL remains private in every mode. No application backend or metadata PostgreSQL port is published. Per-app pinned nginx ingress services alone publish the selected `127.0.0.1` browser ports. Each application and its ingress share a dedicated internal HTTP network; the ingress alone additionally joins a singleton bridge because Docker cannot publish from an internal-only network. OpenCode and PostgreSQL never join those singleton publication bridges. A machine-local reverse proxy may be configured outside the repository to forward HTTPS to those loopback ports.

## Schemer Launch And Advanced Compose

Prefer the launchers because they provide stable identity, five-credential lifecycle, free loopback-port selection, health waiting, and exact URL reporting:

```bash
bash ./start.sh schemer      # explicitly no AI
bash ./start.sh schemer-ai   # shared private AI
```

PowerShell uses `-Mode schemer` or `-Mode schemer-ai`. Schemer joins the same project, profile volume, and included PostgreSQL network as Schemii, while retaining its own dashboard volume, runtime metadata role, session token, readiness, and optional AI workspace. Its readiness does not depend on Schemii health.

Optional public HTTPS is operator-owned. Configure the machine-local reverse proxy, certificate, hostname, listener, routes, and access policy outside this repository, then provide each enabled application's exact comma-separated HTTPS allow-list through `SCHEMII_PUBLIC_ORIGINS` or `SCHEMER_PUBLIC_ORIGINS`. The external proxy must send one `X-Forwarded-Host` equal to the original public authority and one `X-Forwarded-Proto: https`. The backend accepts those headers only from its already trusted Docker ingress peer and rejects duplicates, unknown forwarded headers, malformed values, and mismatches. This is request admission and proxy trust, not application user authorization or per-user privacy. Leave both variables empty for loopback-only admission.

The following direct Compose path is for advanced operators who deliberately manage identity, ports, credentials, and verified preloaded release image references themselves. Compose has no application build contexts and must be run with `--no-build`.

Without AI:

Set a stable instance and collision-free ports first. In a POSIX shell:

```bash
export SCHEMII_INSTANCE=my-schemii SCHEMII_HOST_PORT=18080 SCHEMER_HOST_PORT=18081
```

In PowerShell:

```powershell
$env:SCHEMII_INSTANCE = "my-schemii"
$env:SCHEMII_HOST_PORT = "18080"
$env:SCHEMER_HOST_PORT = "18081"
```

Then run in either shell:

```text
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.schemer.yaml up --no-build -d
```

With one shared private OpenCode service, first create the five owner-only secret files documented in `README.md` and set `SCHEMII_CREDENTIAL_DIR` to their absolute directory, then run:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.ai.yaml -f compose.schemer.yaml -f compose.schemer.ai.yaml up --no-build -d
```

The examples use instance `my-schemii` and loopback ports 18080/18081. Direct Compose defaults to project `schemii` and ports 8080/8081 when those variables are omitted; it does not choose free identities or ports. Replace the example values with one stable project identity and collision-free ports. When adding Schemer to an existing direct-Compose project, use that project's exact `SCHEMII_INSTANCE` so both applications share `schemii-config`, PostgreSQL networking, and one `opencode` service; Schemer keeps dashboards in its separate `schemer-dashboards` volume.

## Verify Startup

Use the instance and URL printed by the launcher.

1. Confirm `schemii`, `metadata-postgres`, `postgres`, and `opencode` are healthy in default mode.
2. Confirm `metadata-migrate`, `example-seed`, and `example-profile-init` exited with status 0.
3. Open the printed URL in a browser.
4. Verify the affected UI or API behavior.

When Schemer is enabled:

1. Confirm both `schemii` and `schemer` are healthy and `example-profile-init` exited with status 0 after `example-seed`.
2. Fetch Schemer `/` and `/api/session` at its exact loopback port without printing the session response body.
3. Confirm Schemii and Schemer list the same saved profile identities.
4. Confirm the Mercury dashboard becomes live only after `schemii_example_postgres`, the exact database, and `bookstore.order_summary` are verified.
5. If AI is enabled, confirm one `opencode` service is running and both `/workspace` and `/workspace-schemer` are mounted read-only.

Instance-aware Docker status:

```bash
docker ps -a --filter "label=com.docker.compose.project=<instance>"
```

For logs, use the exact container name returned above:

```bash
docker logs <container-name>
```

Do not use unqualified `docker compose ps` or `docker compose logs` for a launcher-created instance. Those commands can target the legacy project instead.

HTTP clients are optional host tools. Never print or persist the body from `/api/session`; it contains a secret token. If API verification is necessary, keep each token in memory, send it only to the matching loopback application origin that issued it, and redact it from all output. Schemii and Schemer issue independent session tokens; never reuse one application's token against the other.

## Data Safety

Launcher-created volumes are scoped under the printed instance:

- `schemii-config`: shared profile credentials and non-authoritative local/example compatibility state
- `schemii-schemas`: saved designs and user-owned layout
- `schemii-postgres`: included database data
- `schemii-metadata-postgres`: authoritative chats, versioned AI settings/policy snapshots, grants, proposals, operations/leases, Console settings/receipts, migration plans/executions, and bounded result references, separate from user target data
- `schemii-opencode-data`: provider credentials and chats
- `schemii-opencode-config` and `schemii-opencode-state`: OpenCode configuration/state
- `schemii-opencode-cache`: recreatable cache
- `schemer-dashboards`: saved Schemer dashboards when Schemer is enabled
- `schemii-recovery`: normally empty coordinated-restore snapshots retained only until commit or for failed-rollback review

List the exact volumes with:

```bash
docker volume ls --filter "label=com.docker.compose.project=<instance>"
```

Never run `docker compose down --volumes`, `docker volume rm`, or equivalent destructive commands without explicit approval. Disclose that volume deletion can remove designs, Schemer dashboards, widget configuration, dashboard and canvas layouts, viewport state, profiles/passwords, migration history, user-target PostgreSQL data, metadata authority/history, provider credentials, chat history, and AI state. Back up metadata separately because it can contain sensitive transient query-result payloads.

The only unlabeled-volume compatibility path is explicit adoption of the exact historical `schemii_schemii-config` and `schemii_schemii-schemas` pair. Use `SCHEMII_INSTANCE=schemii bash ./start.sh legacy-volume-adopt ADOPT:schemii` or PowerShell `-Mode legacy-volume-adopt -ConfirmInstance ADOPT:schemii` only after every `schemii` project container is stopped. Adoption must verify the exact credential marker and stable local-volume identity, inspect every current/exited container mount, require an expected project/service/repository-working-directory witness for both volumes, and reject every foreign consumer before atomically publishing owner-only evidence. It must never modify volume data or labels. Recovery and uninstall may trust an unlabeled volume only while the complete host/repository-bound evidence and both identities remain exact; missing, extra, tampered, permission-drifted, moved, or replacement evidence fails closed. Container IDs are deliberately not persisted so expected containers may be recreated. These manifests are not portable backup content and must never be copied, edited, or automatically replaced.

Metadata and OpenCode passwords have no Compose or environment defaults. Launchers generate and persist five owner-only files in the exact instance credential directory; each contains one optional trailing LF newline after 16-256 characters from `[A-Za-z0-9_-]`, and direct Compose requires the same files and format through `SCHEMII_CREDENTIAL_DIR`. Never log their contents, regenerate them on restart, delete a stable metadata volume, or claim rotation from a file-only change. The launcher holds one exact-instance credential lock beside that external owner directory during initialization, stale transaction cleanup/recovery, backup, restore, rotation, and coordinated instance backup/restore. It releases the lock before ordinary Compose startup; the uninstaller does not share it. Contention waits for at most 60 seconds and then fails explicitly. POSIX stale owner-PID locks are recoverable after crashes; PowerShell holds a real exclusive owner-only file handle that the OS releases on process exit. Links, unsafe ownership/permissions, malformed lock metadata, and instance/path collisions fail closed. On Windows, every reused or new credential directory/file, marker, transaction stage, and backup is recursively restricted to the owner/current user and the resulting ACL is verified; any error fails closed. The bootstrap password is used only to initialize a new cluster; setup makes that role `NOLOGIN`, and rotation retains its existing value instead of generating another. Rotation and restore use staged old/new sets and must wait for metadata PostgreSQL before the first forward update, then succeed through the bootstrap-owned `schemii_admin.rotate_metadata_passwords` function, in-place updates that preserve mounted file identities, container restart, readiness, and authentication verification; failure or interruption uses the retained transaction set for deterministic rollback. Restore also requires an exact instance-marker match. A legacy volume lacking that function requires the reviewed one-time bootstrap-owned installation in `docker/metadata/002_rotation_function.sql`; never grant `CREATEROLE` or runtime-role administration to the migration login.

Metadata custom-format archives must preserve their archived owners. Inspect `pg_restore --list`, restore without `--no-owner`, and verify the exact role attributes/memberships plus migration history, database, schema, default, table, sequence, function ACL, policy, row-security, authority foreign-key, and object-inventory matrices before declaring recovery complete. Backup/source verification supports only metadata versions 10 through the current packaged version 13 and must compare every recorded migration name/checksum with the same packaged prefix. Require `public` ownership by `schemii_metadata_owner`, `schemii_admin` ownership by `schemii_metadata_bootstrap`, and enabled row security on every protected metadata table; version 12 additionally requires forced row security and owner-maintenance policies. Version 13 makes operation-usage evidence operation-owned with `ON DELETE CASCADE`; every older supported version retains the historical `RESTRICT` action until migration. In particular, verify the rotation function remains bootstrap-owned and `SECURITY DEFINER`, has its fixed `pg_catalog` search path, grants no public execute privilege, and grants execute only to the migration role. Before mutation, require the destination to match the exact current matrix so unknown drift cannot be hidden by restore. Older archive restore must reset the owner-managed `public` schema and version-specific global default-function privilege, verify the restored source at its recorded version, then migrate and verify the complete current matrix.

Coordinated `instance-backup` owns shared config, byte-exact Schemii schemas/layouts, Schemer dashboards/layouts, metadata, the exact instance marker, and all five credentials. It excludes every target PostgreSQL database, including the tutorial target, and all OpenCode volumes. Its manifest must contain exactly the complete critical path set once; unknown, missing, duplicate, symlinked, or traversal entries fail before hashes are checked. `instance-restore` requires every instance container stopped plus literal `RESTORE:<instance>` intent, rejects newer/incompatible archives and destination catalog drift before mutation, durably stages and validates rollback snapshots before publishing the marker last, retains destination snapshots and credential state until validation, verifies restored metadata at its recorded version, runs the packaged metadata migrator, requires the complete current security check before commit, and leaves the instance stopped. Commit atomically publishes an instance-bound forward-cleanup marker before deleting rollback evidence. Rerun the same reviewed restore after interruption: it rolls back only before that marker and otherwise completes idempotent data and credential cleanup without rollback or restore replay. Never bypass or delete either state marker. Follow README's full disaster-recovery order for the intentionally excluded target PostgreSQL and non-cache OpenCode volumes.

The documented combined AI deployment keeps Schemer in the same Compose project and OpenCode service so provider credentials are shared without copying them. Use `compose.schemer.yaml` with `compose.schemer.ai.yaml`; `/workspace` and `/workspace-schemer` keep application chats, skills, and proposal tools separate. Schemer depends on metadata migration and OpenCode health in this mode, never on the Schemii container's health or readiness.

For a user-requested Schemii uninstall, use `uninstall.sh` or `uninstall.ps1`. The scripts inventory Schemii and Schemer-only projects for the current Docker user, remove their dashboard and application resources plus exact marker-matched instance credential directories, and then remove their own repository. Unlabeled historical volumes are removable only through unchanged adoption evidence; ambiguous or drifted volumes remain untouched. The scripts require typing `UNINSTALL` unless the user explicitly requests unattended `--yes`/`-Yes` operation. Never run an uninstaller merely to troubleshoot startup, and back up requested data first.

The browser shutdown action saves pending design edits and stops only Schemii. Sidecars can remain running. To stop all project containers without deleting volumes, use Docker Desktop or stop containers selected by the exact project label.

## Examples And Layout

First-run markers prevent deleted examples from reappearing automatically. In included-PostgreSQL modes, `example-profile-init` is the single shared profile writer after `example-seed`; both applications consume that profile without depending on Schemii health. **? > Restore examples** restores missing designs and connection records. It never replaces an existing saved schema or layout. It can refresh the reserved tutorial profile password from current environment settings, which invalidates current plans; re-preview afterward.

Before generated schema writes, introspection synchronization, or server restarts after schema changes, follow `.opencode/skills/preserve-schemii-layout/SKILL.md`. Compare parsed layouts for every pre-existing record before and after the operation.

## Connection Verification

Use **Save & test** when possible. Before any introspection, SQL, preview, or apply:

1. Verify the exact profile.
2. Verify the database returned by PostgreSQL.
3. Verify the exact namespace.
4. Verify the matching saved schema record.

Do not preview or apply against an inferred target.

PostgreSQL owns permissions, SQL semantics, and role/database/session `statement_timeout`. The applications retain connection-establishment and external HTTP/provider deadlines, narrow namespace mutation lock waits only when PostgreSQL is looser, and may narrow statement duration only for an explicitly bound AI `operationTimeoutMs`. Treat privilege calculations as advisory and preserve PostgreSQL SQLSTATE, primary message, detail, hint, phase, rollback, and retry/reconciliation diagnostics when troubleshooting.

## Embedded AI Boundary

Read `docs/AI_ASSISTANT.md` before changing AI behavior. Keep OpenCode pinned, private, and Basic-authenticated. Schemii sessions are restricted to read-only `/workspace` with six Schemii skills; Schemer sessions are restricted to read-only `/workspace-schemer` with four Schemer skills. The applications share provider credentials but retain separate sessions, tools, instructions, and action policies.

Never expose or mount host OpenCode files, the Docker socket, raw OpenCode endpoints, shell, filesystem writes, PTY, dynamic MCP, sharing, tasks, LSP, or formatter access.

Provider credentials flow browser -> the active application's local API -> private OpenCode. Never return, print, commit, or put them in browser storage. Model interaction remains user-initiated. Do not send prompts, connect a paid provider, or widen model disclosure without the user's request.

## Troubleshooting Order

1. Run `docker info` and `docker compose version`.
2. Read the launcher error and any image download or Compose output.
3. Inspect containers using the printed project label.
4. Inspect the exact failing container with `docker logs`.
5. Confirm the profile host matches the selected mode.
6. Confirm PostgreSQL health, access rules, and role permissions.
7. Change application code only after environment and routing failures are excluded.

A new launcher instance chooses a free instance-specific UI port unless `SCHEMII_HOST_PORT` is fixed. An existing instance reuses its prior port; if another process has taken it, stop that process or deliberately select a new port. Do not assume a port-8080 collision caused a launcher failure.

## Completion Report

Report the selected mode, printed URL, instance name, services started, whether PostgreSQL or a model provider was contacted, persistent volumes in use, verification performed, and uncommitted changes. Report suite results separately rather than embedding volatile test counts in durable docs. Verify documentation with any repository documentation/link checks and `git diff --check`; server/API changes still require exact-origin `/`, `/api/session` (body never printed), readiness, and affected-route smoke checks in both enabled products. Never include passwords, provider credentials, or session tokens.
