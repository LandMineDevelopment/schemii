# Portable QA database

This directory is a self-contained PostgreSQL 17 image build context. It contains no passwords. The project launcher owns its build, startup, reset, and verification through `testing/compose.yaml`; use `./test.sh` / `./start.sh` commands documented in the testing runbook rather than invoking Docker directly.

The `qa-postgres` service has its own persistent volume and internal network address, with no published host port. It never mounts or connects to application metadata or the demo database. Database `schemii_qa` contains one private schema and reader login per registered account. These database logins all have identical read-only permissions; application personas independently control feature and connection grants.

Each baseline contains 32 customers, 513 orders (three statuses, two customer regions, fixed dates and nullable notes), a relational foreign key, an identity sequence, and a customer totals view. Data is deterministic and contains no real customer information.

## Persistent credentials

The launcher mounts `.schemii/testing/database-admin-password` and `database-credentials.tsv` read-only. Both are mode 0600 and live outside the resettable volume and image. Each tab-separated registry row is `username`, matching schema name, and a 64-character hexadecimal password. Names follow `qa_PERSONA_NNN`. Preserve these files together with the account registry when moving the environment; never commit them. Existing PostgreSQL login credentials are authenticated and verified, never silently replaced.

## Operations

`manage.sh` is the container-side implementation, called only by `start.sh`:

- `prepare [SPACE|all]` creates missing reader roles and baseline schemas. It preserves existing fixture data and verifies its ownership/version marker and role safety.
- `reset SPACE|all` transactionally rebuilds only registered fixture schemas, restoring all baseline data, sequences, and permissions. Credentials, roles, application accounts, metadata and saved connection profiles remain intact. An unknown or unmarked schema is refused.
- `check-reset SPACE` requires one explicit registered space. It first verifies the baseline, then transactionally changes an order, removes another order, creates a scratch table and advances the identity sequence. It proves that ordinary verification rejects the drift, invokes the normal reset path and verifies the original data checksum and preserved login again. Its exit trap attempts restoration after an interruption or failure; a forced kill or failed database connection still requires explicit reset before reuse.
- `verify [SPACE|all]` checks baseline version, exact row contents in both directions, row counts, sequence position, read-only privileges, separation from peer schemas, and actual login/query access using the preserved password. Drift produces a failing exit status.

The launcher serializes operations against active testing leases. A PostgreSQL advisory transaction lock additionally serializes changes to each schema. Resetting `all` uses one transaction per space: if an error occurs, earlier completed spaces remain reset and the failing schema rolls back. `prepare` does not repair changed data; explicit reset is required.

`pg_isready` establishes service readiness only; the fixture verification command establishes dataset and permission readiness. The management command ends with a credential-free JSON summary. It requires the image's files plus the private registry; it does not download seed data or require Python, Node, or additional packages.
