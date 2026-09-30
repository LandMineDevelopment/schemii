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

The subsequent PostgreSQL 17 CI run exposed a wrapper compatibility defect after
metadata connection admission was merged: assigning `observer.autocommit = True`
changed a wrapper attribute while the native connection stayed transactional.
The observer's new table was therefore invisible to the raw connection. The same
failure was reproduced on PostgreSQL 18.6 with the defective admission wrapper;
the earlier PostgreSQL 18 evidence preceded that wrapper. Metadata admission now
forwards the native `autocommit` property getter and setter, including native
transaction-state rejection, without releasing its permit early. The lifecycle
fixture retains the standard property assignment and asserts native idle state
after setup DDL. Raw initialization still sets its namespace search path and
remains idle before the explicit transaction. The rollback, lock/backend release
and retained-admission checks are unchanged, and expiry/dispatch fixture setup
failures also close their owned raw sessions.
