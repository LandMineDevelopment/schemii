# ADR 0001: Dedicated PostgreSQL For Server Metadata

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

AI chat policy, grants, proposals, operations, query-result delivery, and durable migration execution require atomic transitions across multiple records. The current JSON stores provide atomic individual-file replacement but cannot make these multi-record transitions crash-atomic. SQLite is explicitly not an accepted dependency for this project.

The optional included `postgres` service is a user-facing target/tutorial database. It is absent from some launch modes and can be reset, backed up, or removed independently. Server authority must not share that lifecycle or be selectable as a user target.

## Decision

Use a dedicated private PostgreSQL service and volume for server metadata.

- Default database: `schemii_metadata`.
- Distinct no-login owner, migration role, Schemii runtime role, and Schemer runtime role.
- No host publication in bridge modes.
- No metadata host publication; Linux host-PostgreSQL modes use a separate private Unix-socket relay.
- Packaged checksummed SQL migrations guarded by a PostgreSQL advisory lock.
- Application readiness requires metadata connectivity and current schema version.
- Authority-dependent workflows fail closed with `metadata_unavailable`; no JSON or in-memory fallback.
- User target credentials and data never enter this database.

## Consequences

Positive:

- Transactional approval and operation ownership.
- Cross-process correctness and indexed lookup.
- Durable restart recovery and bounded cleanup.
- Independent backup and lifecycle from user target databases.
- Shared authority foundation for Schemii and Schemer without merging their domain models.

Costs:

- One additional PostgreSQL service and volume in local deployments.
- Migration, credentials, readiness, backup, retention, and outage behavior become explicit operational responsibilities.
- Linux host-PostgreSQL modes require a private Unix-socket relay, but metadata PostgreSQL remains on the private dependency network and needs no host port.

## Rejected alternatives

- **SQLite:** rejected by explicit product direction.
- **JSON plus more locks/journals:** recreates transactional database behavior and retains complex crash recovery.
- **Database/schema inside the included target PostgreSQL:** breaks UI-only/external-target modes and couples authority to user-target resets and privileges.
- **Store authority in a user-selected profile:** violates target isolation and makes server operation depend on mutable user credentials.
