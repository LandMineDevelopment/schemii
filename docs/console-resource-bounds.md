# Console resource bounds

Raw human SQL sessions expire after 1,800 seconds of inactivity. The application
lifespan sweeps them every 30 seconds, even when no subsequent raw-session request
arrives. A deadline reached during a sweep is rechecked under the session signal
lock before closing. Running SQL and COPY hold an operation lease and are exempt
from idle expiry; release starts a fresh idle interval. This adds no raw statement
timeout. Role, database and user-selected session settings still govern SQL.

Expiry closes the owned PostgreSQL connection, rolling back uncommitted work and
releasing locks, the backend and its retained admission permit. A previously
obtained session object cannot start new work after closure. Shutdown stops and
joins maintenance before cancelling active operations and waiting for their
leases, then closes the registry once. Repeated shutdown/close calls are harmless.
SQL and COPY cancellation share the actual wire-dispatch gate. A stop before
dispatch remains sticky until an explicitly claimed fresh operation; shutdown
cannot lose a signal while policy setup or COPY adaptation is pending. The
gate is released before waiting for results, so an executing statement remains
cancellable. Whole-run rollback cleanup is allowed after cancellation without
rearming arbitrary SQL. Transport failures with an unknown persisted outcome
remain uncertain even if Stop raced with them. Views recheck closure under the
same signal lock before reading native connection metadata.

The 30-second interval bounds scheduling delay during a healthy event loop;
operating-system or database failure can delay native connection cleanup.

`tests/test_console_lifespan.py` advances an injected monotonic deadline clock
and exercises the assembled lifespan without waiting 30 minutes. The real-driver
regression in `tests/integration/test_raw_session_lifecycle.py` owns a disposable
schema, observes `pg_stat_activity`/`pg_locks` from another connection, checks
rollback and reacquires retained capacity. It performs no raw request after
abandonment. Integration checks require the repository's explicit private test
PostgreSQL environment; a skipped integration test is not rollback evidence.


Shutdown keeps metadata admission available until raw/chat maintenance, catalog
and credential workers, bulk jobs, raw sessions, managed Console sessions and the
migration worker have been joined or have reported their own stop failure.
Metadata closes last. An individual stop failure still runs the remaining owned
cleanup callbacks and then propagates. Raw and chat maintenance use cooperative
stop signals: cancellation of the lifespan cannot abandon a running native
thread. Repeated cancellation waits for owned shutdown to finish before the
caller receives cancellation.

The delivery regressions include an actual catalog-worker refresh failure that
previously skipped raw-session and metadata closure, failed raw maintenance, and
repeated shutdown cancellation while either raw or chat maintenance is blocked
in a native thread. They assert raw/bulk closure and metadata ownership order,
and check that the scheduled workers and shutdown task have ended.

The retained #132 real-PostgreSQL evidence was measured at reviewed raw source
`253feb9` on 2026-09-29 with PostgreSQL 18.6, Psycopg 3.3.4 and libpq 18.0. Six
cases in `tests/integration/test_raw_session_lifecycle.py` passed: lifespan expiry
rolled back an abandoned write and released its lock/backend/retained permit;
pre-dispatch shutdown prevented SQL, COPY upload and COPY download from starting;
and active cancellation recovered a fresh operation in both each-statement and
whole-run modes with no raw statement timeout. The owned fixture stopped all
nine recorded PostgreSQL process identities and automatically removed its
private cluster/socket/password directory. This integration changes lifespan
shutdown composition; the raw-driver/session code is unchanged from those
reviewed cases. The real database cases were not rerun for this composition-only
change. Managed result memory and latency measurements belong to the separate
#133 delivery.

## Managed result execution

Managed SELECT, command-only statements and DML RETURNING use libpq single-row
delivery before conversion and preview byte checks. Each validated statement is
sent once using extended query protocol. PostgreSQL's terminal result supplies
column metadata even for zero rows, and the completed command receipt. Psycopg
loaders retain typed values and existing JSON conversion rules. A byte/cell cap
raises the existing explicit limit error; it never returns incomplete rows as a
complete result or replays a write. Interrupted delivery is cancelled/drained
before reuse. Broken cleanup closes the owned connection and releases admission.
The existing transaction owner still governs commit/rollback after an error.

Retained named reads fetch one row at a time. Their page keeps accepted rows and
at most one carry row for the next page, so fetching a large row-count batch does
not allocate hundreds of wide rows before the byte boundary. Forward ordering,
snapshot ownership, separate export cursors and owner-scoped cancellation remain
the same. This increases FETCH round trips for narrow-row pages; real latency
and capacity measurement are required before drawing throughput conclusions.

The configured JSON byte caps are not a whole-process RSS limit. Python object
overhead, serialization copies and native input/result buffers add memory. A
single PostgreSQL row/value must be received before conversion: an oversized
cell can allocate substantially more than the 256 KiB serialized cell limit.
For fixed row/column width, expected retained memory stops growing with source
row count once the preview is rejected. RSS plateau/tolerance and the native
buffer allowance require isolated real-driver measurement. No measured plateau
is claimed by unit tests or the implementation alone.

`scripts/console_memory_probe.py` compares a buffered reference of the old
execute-before-limit boundary with current incremental delivery. Each sample
runs in a fresh child process, records `/proc` RSS, `ru_maxrss`, phase timing,
fixture cell size and driver/server versions, then verifies rollback and a fresh
read. RETURNING owns a connection-local temporary table; it vanishes on exit.
The probe terminates its owned child on timeout/interruption. Credentials stay
in the explicit private integration environment. Run only in a scheduled real
PostgreSQL window, for example:

```bash
PYTHONPATH=src .venv/bin/python scripts/console_memory_probe.py \
  --rows 1000 10000 100000 --width 8192 --path select
PYTHONPATH=src .venv/bin/python scripts/console_memory_probe.py \
  --rows 1000 10000 100000 --width 8192 --path returning
PYTHONPATH=src .venv/bin/python scripts/console_memory_probe.py \
  --rows 1000 10000 100000 --width 8192 --mode named
```

Large buffered references intentionally expose the former native memory cost;
choose staged counts that fit the host. The probe is opt-in and ordinary PR
feedback never launches it. Real result/RETURNING/cell-limit and source-state
regressions are in `tests/integration/test_postgres_gateway_execution.py`.
