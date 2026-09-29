# Metadata resource budgets

Normal metadata repositories and targeted authentication share one process-local
connection factory. Admission is fail-fast: a full factory rejects before reading
secrets or connecting to PostgreSQL, without an admission wait queue. A permit
counts connection establishment and remains occupied until the native connection
closes. Canceling an `asyncio.to_thread` awaiter does not stop its native work or
release its permit. No connection pool, executor tuning or transaction replay is
introduced.

| Lifecycle | Concurrent admitted connections | Connect budget | Statement budget | Lock wait | Idle transaction |
| --- | --- | --- | --- | --- | --- |
| Normal metadata/auth | 16 | 5 s | 5 s | 1 s | 5 s |
| Readiness | 2, independent of normal admission | At most 2 s | 2 s | 1 s | 2 s |
| Startup migration | 1, separate from runtime | 5 s | 120 s | 30 s | 120 s |

These are per-process metadata budgets, separate from source PostgreSQL admission.
Multiply by application processes when assessing server connection headroom.
Readiness establishes dependency reachability; it does not establish spare normal
metadata capacity. Authenticated cancel/control requests continue to perform
authoritative session and role checks. Under saturation they may return a bounded
retryable 503; they do not bypass grants or use an authorization cache.

Configuration uses the existing deployment-owned `MetadataConfig` environment
boundary:

| Environment variable | Default | Allowed range |
| --- | --- | --- |
| `SCHEMII_METADATA_MAXIMUM_CONNECTIONS` | 16 | 1–256 |
| `SCHEMII_METADATA_CONNECT_TIMEOUT` | 5 | 1–30 seconds |
| `SCHEMII_METADATA_STATEMENT_TIMEOUT_MS` | 5000 | 100–120000 ms |
| `SCHEMII_METADATA_LOCK_TIMEOUT_MS` | 1000 | 10–30000 ms |
| `SCHEMII_METADATA_IDLE_TRANSACTION_TIMEOUT_MS` | 5000 | 100–120000 ms |
| `SCHEMII_METADATA_MIGRATION_STATEMENT_TIMEOUT_MS` | 120000 | 1000–900000 ms |
| `SCHEMII_METADATA_MIGRATION_LOCK_TIMEOUT_MS` | 30000 | 100–900000 ms |
| `SCHEMII_METADATA_SHUTDOWN_TIMEOUT_SECONDS` | 15 | 1–180 seconds |

PostgreSQL enforces statement, lock and idle-transaction deadlines. A statement
deadline is not a whole-transaction/request deadline: a transaction can execute
several statements. Libpq's connect timeout is per selected host/address; a
multi-host DSN can exceed one connect allowance. These bounds do not establish
capacity or an absolute end-to-end SLA during network failure.

`MetadataRepositories.close()` rejects new normal work and drains native leases,
then closes readiness admission. Call it after metadata consumers stop, through
`asyncio.to_thread` in the application lifespan. A drain timeout reports failure
and keeps remaining connections counted; it does not force-close a connection
being used by another thread or pretend its work stopped. Repeated close can
complete after that native owner closes its connection.

Direct metadata capacity errors use `metadata_capacity_exceeded`, and metadata
outages/timeouts use `metadata_storage_unavailable`, with HTTP 503, `retryable:
true`, `Retry-After: 1` and no session cookie changes. Repository-specific storage
adapters retain their existing safe 503 codes. PostgreSQL constraint errors and
application authorization/revision conflicts retain their original contracts.

`MetadataConnectionFactory.admission_snapshot()` reports active and peak admitted
connections, admitted/rejected totals, connection-establishment failures and total
connection-establishment seconds. Admission has no queue wait; this is not source
query latency or a measured user-concurrency capacity claim.

## Focused verification

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_metadata_admission.py
# Use only an explicitly allocated owned PostgreSQL fixture and private test env.
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/integration/test_metadata_runtime_limits.py --junitxml=/owned/evidence.xml
```

The deterministic tests cover concurrent admission, canceled async work during
connection establishment/execution/close, failed connects/commits/close, retained
permits at shutdown, and safe middleware/route error recovery without logout.
Real PostgreSQL tests hold owned advisory locks, trigger statement deadlines,
count exact tagged backends under admission pressure, check independent readiness,
and verify backend cleanup after cancellation and shutdown. They record timing
and admission properties in JUnit output. The auth test creates/removes its own
account/session without adding anonymous sign-in audit records.

Preserve existing role binding, revocation, expiry, share authority and revision
regressions. Full source-query/control recovery and staged HTTP capacity require
the assembled launcher deployment and the separate benchmark workstream. No live
database, launcher or manual UI acceptance was run for this implementation lane;
the real PostgreSQL tests remain pending coordinator scheduling.
