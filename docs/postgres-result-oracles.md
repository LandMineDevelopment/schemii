# PostgreSQL result acceptance, issue #130

On 2026-09-29, `audit/postgres-oracles` ran the portable value oracles against
**PostgreSQL 18.6**, using the existing `postgres_metadata` integration fixture.
The test database belonged to a new task-owned host cluster. It listened only
on its unique Unix socket inside a private temporary directory; no application
deployment, Docker command, shared PostgreSQL server or personal workspace was
used.

## Measured results

**33 passed in 0.97 seconds**, without retries or skips:

- All 30 semantic/calendar value cases: all 29 original parameterized oracles,
  plus exact large-numeric precision at `10^20 + 1`.
- Read-only transaction, `pg_catalog` search path, UTC timezone, five-second
  statement timeout and PostgreSQL date/numeric type compatibility.
- Rejection of persistent schema creation, with no catalog object left behind.
- Real server-side cancellation of `pg_sleep(10)` by a transaction-local 50 ms
  statement timeout; the exception retained SQLSTATE `57014`.

The first integration fixture setup took 0.15 seconds; the timeout assertion
took 0.05 seconds. Cluster initialization plus the test subprocess took 2.35
seconds in this local acceptance run. These are focused local measurements,
not an ordinary CI duration comparison or application capacity estimate.

```bash
.venv/bin/python -m pytest -q --durations=5 \
  tests/integration/test_schemoo_semantic_results.py \
  tests/integration/test_schemer_calendar_results.py \
  tests/integration/test_console_postgres_results.py
```

The process received `SCHEMII_TEST_METADATA_DSN` for the disposable socket/database
and a private nonempty `SCHEMII_TEST_METADATA_PASSWORD`, following the existing
fixture contract. No credentials appear in the retained output.

## Automatic fixture disposal

The fixture controller captured its postmaster PID and process start identity,
recorded only that postmaster's descendants, and used `pg_ctl` against its exact
data directory to shut down in `finally`. It waited for owned processes to end
before removing its directory. Had shutdown failed, it would have retained the
directory and reported failure instead of deleting live server data.

After pytest and before server shutdown, the observer found:

| Owned resource | Remaining |
| --- | ---: |
| Test database connections, excluding the observer itself | 0 |
| Integration owner users, workspaces and models | 0 |
| Rejected-write guard schemas | 0 |

Shutdown exited **0**, every recorded process identity disappeared, and automatic
`finally` disposal removed the temporary fixture directory. No manual deletion
or broad process termination was used. Other live peers were untouched.

Selected private evidence is retained at
`artifacts/issue130/results130/pytest.txt`, `postgres-results.xml` and
`result.json` in the task checkout. The JSON receipt records the version, timings,
resource counts, recorded process IDs, shutdown result and directory-removal
assertion. These selected files can be exported before task-worktree removal;
the disposable cluster itself is gone.

## Remaining acceptance

The [coverage map](postgres-oracle-coverage.md) describes every transferred value
oracle and the authored authenticated assembled API regression. The latter still
requires its scheduled launcher-backed deployment window. This PostgreSQL result
run does not establish application route/cookie/cancellation acceptance, native
manual UI acceptance or PostgreSQL 17 CI execution. Ordinary integration CI uses
its existing PostgreSQL 17 service to run the same test files.
