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

PostgreSQL enforces statement, lock and idle-transaction deadlines. Its idle
transaction expiry is explicitly mapped from psycopg's
`IdleInTransactionSessionTimeout`, which is an `InternalError`; unrelated
PostgreSQL internal/constraint errors are not broadly classified as outages.
A statement
deadline is not a whole-transaction/request deadline: a transaction can execute
several statements. Libpq's connect timeout is per selected host/address; a
multi-host DSN can exceed one connect allowance. These bounds do not establish
capacity or an absolute end-to-end SLA during network failure.

`MetadataRepositories.close()` rejects new normal work and drains native leases,
then closes readiness admission. The application lifespan calls it through
`asyncio.to_thread` after metadata consumers and the migration worker stop.
A drain timeout reports failure
and keeps remaining connections counted; it does not force-close a connection
being used by another thread or pretend its work stopped. Repeated close can
complete after that native owner closes its connection.

Direct metadata capacity errors use `metadata_capacity_exceeded`, and metadata
outages/timeouts use `metadata_storage_unavailable`, with HTTP 503, `retryable:
true`, `Retry-After: 1` and no session cookie changes. Repository-specific storage
adapters retain their existing safe 503 codes. PostgreSQL constraint errors and
application authorization/revision conflicts retain their original contracts.
Bulk jobs and AI metadata use the shared connection context to keep native
commit/rollback/close and timeout classification at this owner. Both retain their
existing transaction commit and business-conflict rollback behavior.

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

## Observed PostgreSQL verification

An isolated host PostgreSQL **18.6** cluster ran on a unique private Unix socket
with `listen_addresses=''`, 40 server connections and 32 MiB shared buffers. It
was never the application database or a deployment. The focused runtime suite,
existing persisted AI continuation tests, and account/role persistence tests
passed: **18 tests in 5.36 s**. Effective startup/readiness settings were read from
`pg_settings`; runtime saturation did not inherit their independent allowances.

| Owned test | Configured allowance | Observed completion |
| --- | --- | --- |
| Native statement timeout | 250 ms | 257 ms |
| Bulk repository API statement failure | 250 ms | 259 ms; retryable 503 |
| AI repository API statement failure | 250 ms | 257 ms; retryable 503 |
| Auth advisory mutation lock | 150 ms | 156 ms |
| Idle transaction expiry | 200 ms | 203 ms |
| Startup migration lock | 300 ms, separate from runtime's 20 ms | 311 ms |
| Shutdown draining a native lock waiter | 700 ms lock allowance | 701 ms |
| Canceled async waiter, native work completion | 700 ms lock allowance | 722 ms |
| Composed readiness during runtime saturation | 2 s statement allowance | 5.4 ms |
| Authenticated request rejected during saturation | Fail-fast admission | 1.6 ms; cookie retained |

Ten concurrent attempts admitted exactly two native connections and rejected
eight before connecting. A further pressure probe was rejected; after release,
one fresh read succeeded. Final snapshot: peak 2, active 0, admitted 3, rejected
9; total admitted connection establishment 15.5 ms. This is admission evidence,
not a user-concurrency or throughput capacity result. No pool was introduced.

The first real run retained its failure: nine cases passed and idle expiry exposed
the missing psycopg error mapping. Correcting that exact mapping led to the
passing result. Bulk/AI real transactions additionally proved committed writes
persist, business conflicts roll back their owned writes, and timeout replies
recover after the blocker releases. A real authenticated saturation case proved
readiness, retained cookies, recovery, immediate role revocation (403), and
session revocation (401).

The controlled fixture's `finally` block stopped its exact cluster with `pg_ctl`
and disposed its `TemporaryDirectory`. Both failed and passing runs recorded
normal stop exit 0, no remaining recorded PID/start-time identities, and absent
data/socket directories. Nine owned PostgreSQL processes were checked on the
final run. Selected private JUnit, output, process receipts and the reproducible
driver are retained under `artifacts/metadata134/`; credentials stayed ephemeral
and were not exported. This proves controlled fixture teardown, not native MCP
transport cleanup or the launcher stack's lifecycle.

Preserve existing role binding, revocation, expiry, share authority and revision
regressions. The CI PostgreSQL 17 service still supplies its separate version
check. Full assembled source-query/control recovery, browser acceptance, and
staged HTTP capacity require the launcher deployment and benchmark workstream.
This lane did not rebuild/start the application or use Docker.
