# PostgreSQL result oracle coverage

Issue [#130](https://github.com/LandMineDevelopment/schemii/issues/130) moves all
29 parameterized result cases from the two opt-in `*_sql_live.py` files into
ordinary PostgreSQL integration CI. Their 21 named test functions remain;
one additional large-numeric parameter detects precision loss at `10^20 + 1`.
The new total is **30 database cases: 16 semantic and 14 calendar**.

The cheapest faithful oracle for these cases is generated SQL executed on real
PostgreSQL against synthetic `VALUES`/CTE rows. Unit/compiler tests remain useful
fast checks, but SQL text assertions and SQLite transpilation cannot certify
PostgreSQL calendar, NULL or numeric behavior. No paid provider, application
startup, personal workspace or `SCHEMOO_LIVE_TESTS` flag is required.

## Semantic invariants

All function names below are in
[`test_schemoo_semantic_results.py`](../tests/integration/test_schemoo_semantic_results.py).

| Test (without `test_`) | Cases | Unique result contract |
| --- | ---: | --- |
| `optional_repetition_diagnostics_preserve_postgres_values` | 3 | Sum/count/average preserve deliberate join repetition and NULL behavior; diagnostics cannot silently deduplicate measures. |
| `domain_search_matches_label_or_uuid_case_insensitively` | 5 | Case-insensitive label/UUID search, literal `%`, `_` and apostrophe handling, and no-match empty rows. |
| `required_scope_person_rows_do_not_fan_out` | 1 | Required related scope selects one person without projecting fanout. |
| `required_scope_limits_returned_assignment_not_just_person` | 1 | Required scope also restricts the related rows returned. |
| `conditional_period_preserves_people_without_current_assignment` | 1 | Conditional date scope includes NULL endpoints and preserves owners without current related rows. |
| `existence_filter_retains_all_returned_certifications` | 1 | Exists filters select owners while retaining every projected certification; row filters restrict projected rows. |
| `not_exists_returns_people_without_matching_certification` | 1 | Negative existence includes both nonmatching and absent related rows. |
| `aggregate_summary_preserves_lists_and_counts_across_outer_fanout` | 1 | Aggregate grain protects lists/counts from unrelated outer fanout, preserving empty-owner NULL/list and zero/count values. |
| `summary_applies_conditional_source_parameter_in_postgres` | 1 | Parameterized source conditions apply inside aggregate summaries. |
| `per_output_temporal_filter_preserves_total_and_owner_rows` | 1 | Per-output date filters preserve unfiltered totals, open endpoints and empty owners. |

## Calendar/type invariants

All function names below are in
[`test_schemer_calendar_results.py`](../tests/integration/test_schemer_calendar_results.py).

| Test (without `test_`) | Cases | Unique result contract |
| --- | ---: | --- |
| `missing_calendar_period_zero_and_full_running_window` | 1 | Zero is distinct from a missing prior period; preview limits do not truncate analysis or running totals. |
| `leap_day_prior_year_clamps_and_running_partition_nulls` | 1 | February 29 clamps to prior-year February 28; NULL partitions compare correctly and do not mix with named partitions. |
| `week_boundary_is_explicit` | 2 | Monday/Sunday week starts preserve dates and measure totals. |
| `dst_local_buckets_and_drill_are_identical` | 1 | New York DST day buckets and drill predicates select identical instants. |
| `filter_scope_caps_history_and_refreshed_data_recomputes` | 1 | Report filters cap prior history/running totals; changed synthetic rows recompute comparison values. |
| `large_numeric_values_do_not_overflow_comparison` | 2 | Exact PostgreSQL numerics at `10^20` and `10^20 + 1` survive sum, delta, ratio and running totals. |
| `prior_year_week_rebuckets_to_same_week_boundary` | 1 | Prior-year week comparison rebuckets to the configured week boundary. |
| `month_groups_partial_periods_as_filtered` | 1 | Month buckets aggregate selected partial periods and compare calendar months. |
| `year_groups_leap_days_and_compares_calendar_years` | 2 | Previous-period/prior-year annual comparisons include leap days, missing years and full running history. |
| `year_timezone_boundary_and_drill_match` | 1 | Local calendar year boundaries and drill selection agree for timezone-aware timestamps. |
| `timezone_free_timestamp_retains_calendar_date` | 1 | Timezone-free timestamps keep their calendar date; NULL timestamps do not form buckets. |

Assertions compare actual PostgreSQL rows and values. `Decimal` values remain
exact, replacing the old HTTP helper's lossy float comparisons. Existing SQL-shape
and diagnostic assertions are retained alongside their value oracles.

## Ownership, execution and skip reasons

[`sql_oracles.py`](../tests/integration/sql_oracles.py) uses the existing
[`postgres_metadata`](../tests/integration/conftest.py) disposable-owner fixture.
Each case owns one connection, cursor and read-only transaction, with a five-second
statement timeout, UTC session timezone and `pg_catalog` search path. Source
relations are CTEs, so no source schema/table rows are created or read. An omitted
CTE fails rather than resolving to a shared public table. Teardown rolls back
even after a query/assertion failure and closes the cursor/connection. Existing
metadata fixture cleanup remains owner-scoped.

The existing `postgres-integration` CI job runs `tests/integration` against its
PostgreSQL 17 service; both new files are included without workflow-specific test
names. To run against an explicitly selected disposable local database:

```bash
SCHEMII_TEST_METADATA_DSN='host=... dbname=... user=...' \
SCHEMII_TEST_METADATA_PASSWORD='...' \
.venv/bin/python -m pytest -q \
  tests/integration/test_schemoo_semantic_results.py \
  tests/integration/test_schemer_calendar_results.py
```

Keep credentials private. With either fixture variable missing, all 30 cases
report the existing explicit `real PostgreSQL integration requires: ...` skip
reason. This is a database-prerequisite skip, not paid-provider or manual UI
acceptance. Collection and a skipped local run alone are not PostgreSQL proof.

## Authenticated assembled API boundary

[`semantic-api-owned.spec.js`](../tests/e2e/semantic-api-owned.spec.js) retains
one assembled scenario in the launcher-backed browser suite. It inherits normal
global-setup authentication, including `SCHEMII_E2E_CREDENTIALS_FILE` for an existing
installation and explicitly authorized bootstrap for disposable CI. It verifies
authenticated access and rejection of an anonymous request, creates its own
connection/workspace against the launcher's seeded target, and exercises:

- Completed synthetic results, including exact large-numeric JSON and NULL rows.
- Explicit result close and rejected access to the closed result.
- Cancellation after PostgreSQL reports `PgSleep`, followed by a successful read.
- An owned raw session with an open transaction, explicit close/rollback and
  rejected access after close.
- Ledger-scoped finally cleanup, deleted workspace/connection and removed
  terminal execution receipts. Cleanup failures fail the test.

The account and suite authentication session remain owned by global setup; this
scenario closes its own anonymous API context and SQL session/resources. It never
logs out a shared suite account or deletes unrelated fixtures. Only synthetic
read queries and `BEGIN` execute on the target; no user tables are mutated.

After rebuilding through `./start.sh` and observing deployment leases, run:

```bash
SCHEMII_E2E_CREDENTIALS_FILE=/private/credentials.json \
npm run test:e2e -- tests/e2e/semantic-api-owned.spec.js --project=desktop-chromium
```

This API lifecycle regression complements PostgreSQL value tests. It is not
native manual UI acceptance or a replacement for isolated multi-account QA.
