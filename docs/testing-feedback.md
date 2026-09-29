# Testing feedback and coverage

The supported deterministic commands are `npm test` and `python -m pytest -q`.
Use the constrained Python bootstrap in [testing/README.md](../testing/README.md).
Default Python discovery includes `tests/` and `testing/`; it does not launch the
application or opt-in load/browser work. CI runs the Node checks before Python
setup. Dispatch delay and setup time still contribute to hosted feedback; the
30-second early-feedback target requires an ordinary hosted run, not a local claim.

## Focused inventory

Costs below are audit observations at the referenced baseline, not current
percentiles. Focused commands are from the repository root and do not replace
the broader acceptance path.

| Ownership / invariant | Unique coverage / cheapest faithful oracle | Focused command | Observed cost and broader layer |
| --- | --- | --- | --- |
| Auth and exact connection ownership | Denial, revoked sessions and cross-owner fencing; in-memory API outcomes | `python -m pytest -q tests/test_account_auth.py tests/test_query_cancellation.py` | Part of the remaining ~68s Python suite; real PostgreSQL verifies persistence and role enforcement |
| Migration execution | Stale revision/target, one execution, uncertain commit and no DDL replay; fake transport exercises lifecycle races | `python -m pytest -q tests/test_migration_execution.py` | Preserve real PostgreSQL rollback/catalog/restart and mounted UI review/apply paths |
| Console and cancellation | Session owner/turn/revocation fences, cancellation and raw transaction state | `python -m pytest -q tests/test_raw_console.py tests/test_query_cancellation.py` | Unit reaping does not establish lifespan scheduling or real lock/admission release; retain real transport cases |
| Semantic compiler | Safe AST, grain/filter/repetition and generated SQL contracts; pure compiler inputs | `python -m pytest -q tests/test_schemoo_filters.py tests/test_schemoo_repetition.py tests/test_schemoo_column_comparisons.py` | Real PostgreSQL result oracles cover type/NULL/calendar semantics that AST checks cannot |
| Changed frontend state | State ordering, late responses, aborts, cache budgets and fixture ledgers; Node component outcomes | `node --test tests/frontend/request-coordinator.test.js tests/frontend/schemer-result-cache.test.js` | Whole Node suite: 395 passes, ~0.63–0.68s local / ~2s hosted at audit; browser adds physical geometry and persistence |
| QA provisioning and cleanup | Owned fixture ledger, redaction, preserved peers and interrupted cleanup; temporary directories/mocked API | `python -m pytest -q testing/test_report_author_fixtures.py testing/harness` | 30 audit cases, ~0.08–0.14s; native guardrail cases are additionally collected, without duplicate unittest invocation |
| Native stock-agent guardrails | Assignment, runtime capacity/config, isolated connection ownership and cleanup algorithms | `python -m pytest -q testing/agents` | Deterministic subprocess/temp-data regressions; mechanical isolation probes and authenticated manual acceptance remain separate |
| Inspection graph | Graph completeness, no I/O/secrets, identity and derive-once; immutable document or small synthetic graph where faithful | `python -m pytest -q tests/test_developer_inspection.py tests/test_system_inspection.py tests/test_route_inspection.py tests/test_database_inspection.py` | 36 cases accounted for at least 149.46s / 67.2% of prior full run; fixture work tracked independently in #126 |
| Mounted browser workflows | Save/reopen, keyboard focus, paging, permissions and mobile geometry; real UI plus exact persisted/result assertions | `npm run test:e2e -- --project=desktop-chromium tests/e2e/schemoo-model-editor-audit.spec.js` | Requires `./start.sh`, test-owned fixtures and released deployment leases; all projects/shards remain in CI |

Check actual collection with `python -m pytest --collect-only -q`. The September 29
delivery baseline `77af33c` collected 1,628 default cases and 144 additional cases
under `testing/`; the original omitted provisioning inventory is 30 of those.
Counts grow as focused regressions land. Report collected, executed and skipped
counts together; PostgreSQL skips do not prove database behavior.

## Broad pre-merge acceptance

Run the full Node and default Python commands, incremental Python static quality
against the intended change base, the explicitly configured real PostgreSQL
integration job, and the assembled browser matrix. Browser CI uses the canonical
HTTPS stack through `./start.sh`; it does not replace manual native-agent UI
acceptance in #73/#137. Backup recovery remains an isolated-database check.
Live provider/report specs require their own authenticated fixture prerequisites;
track their skips and ownership rather than treating a green default run as proof.
Full capacity ramps/soaks in #135/#136 stay outside the normal PR feedback path.

Do not remove meaningful coverage, increase skips/timeouts or add retries to
conceal failures. Prefer a cheaper oracle only when it detects the same plausible
defect; distinguish source/package contracts from visible behavior guarantees.
