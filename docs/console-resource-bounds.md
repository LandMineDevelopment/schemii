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
