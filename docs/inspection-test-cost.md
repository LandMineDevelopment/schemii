# Inspection test setup and evidence

Work for [#126](https://github.com/LandMineDevelopment/schemii/issues/126),
against baseline `77af33c`. Full normal and reversed runs pass on the final
verification changes. Acceptance remains pending independent review, three
comparable final-code after measurements and integrated whole-suite timing.

## Boundary and invariant inventory

The four inspection modules retain every original collected case: **36 before,
39 after, zero removed**. The three additions cover source-sensitive snapshot
identity, configured gateway identity and recursive fixture immutability.
No retries, skips, timeouts or production inspection code were changed.

`tests/inspection_fixtures.py` constructs one explicitly configured installed
application graph per pytest session. It restores the caller's Schemii
environment afterward and retains only recursively frozen documents and route
registration order. Applications, mutable services, clients and lifespans are
never shared. Each module registers the same named pytest plugin, allowing
single-file invocation without adding a global conftest dependency.

| Existing checks | Protected invariant and cheapest faithful oracle |
| --- | --- |
| Snapshot version/digests/repeatability; cross-document operations | Full installed baseline validates content-derived digests and coherent operation/planned-capability membership; two fresh small graphs validate deterministic rebuilding |
| Route flow, dependencies, request/response models, source tokens and bounds | Same complete installed route document; every existing source/bound assertion retained |
| System registration order, services/repositories/gateway relationships, complete journeys, data shapes, argument flow, raw Console deferred execution | Same complete installed system document plus independently captured route order and installed OpenAPI document |
| Database gateway contract, ordered operations/calls, query discovery/results/source bounds | Same complete installed database document; every query and source-bound assertion retained |
| Four opt-in HTTP checks | Fresh applications and clients; only snapshot derivation is replaced by baseline documents. A private plain JSON copy is required by Pydantic's response serializer |
| Canonical HTTP snapshot/compatibility documents/no-store/hidden OpenAPI | Unchanged fresh full application build and real endpoint responses |
| Once-per-application derivation and installed-graph determinism | Same two fresh full application builds, strict counters for all three builders and repeated HTTP requests; their complete canonical snapshots now compare equal without an additional build |
| Authenticated durable configured graph and installed Pi graph | Unchanged fresh configured builds, completeness/bounds assertions, Pi/catalog no-I/O sentinels and secret exclusion |
| Configured repositories with runtime-only credentials/values | Unchanged fresh service graph and installed routes; all runtime name/host/database/user/password exclusions retained |
| Nested/exact/scoped/optional/unknown/ambiguous receivers, helper nonexecution, cursor discrimination and return contracts | Unchanged fresh installed services and focused AST expressions; these do not derive full snapshots |
| Static docstrings and complete-source hashes; nested dependencies; wide unions | Unchanged synthetic parser/model graphs and source counterexamples |
| Module application environment selection | Unchanged three fresh subprocesses, including real opt-in snapshot construction |
| Changed registered endpoint | Fresh small applications; now checks changed membership in routes/system as well as OpenAPI and snapshot identity |

Fresh source-change and configured-gateway counterexamples additionally prove
that identity can change while OpenAPI remains identical. The immutability
check rejects root, nested-object and sequence writes, and demonstrates that a
consumer's private JSON copy cannot change the shared documents.

## Measurements and verification

Observed environment: Python **3.14.7**, Linux **7.2.5-3-omarchy** x86_64,
Intel **Core i7-11800H**, **8 cores / 16 logical CPUs**, approximately **31 GiB
RAM**. Hosted CI uses Python 3.12, so these results are not CI timing estimates.
Dependencies were already installed in the shared project virtual environment.
Other developers paused heavy tests during the baseline window; ordinary
desktop/background workloads remained. The third run had more background load.

The identical original command ran three times in the assigned worktree:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_developer_inspection.py tests/test_system_inspection.py \
  tests/test_route_inspection.py tests/test_database_inspection.py --durations=20
```

| Baseline attempt | Pytest elapsed | Process wall | Result | Exit one-minute load |
| --- | ---: | ---: | --- | ---: |
| 1 | 157.85s | 171.359s | 36 passed, zero skips | 4.54 |
| 2 | 159.97s | 173.755s | 36 passed, zero skips | 3.91 |
| 3 | 183.67s | 200.343s | 36 passed, zero skips | 7.13 |

The initial bounded check exposed four fixture serialization failures; changing
the HTTP consumer boundary to a private plain JSON copy fixed them. The corrected
bounded check passed **21 cases, 18 deselected, zero skips in 21.95s**. It covers
every changed assertion and all three added counterexamples. Its selection omits
the unchanged expensive fresh-build cases, so **21.95s is not an after timing
sample and must not be compared with the baseline as an optimization saving**.

The selected cases use this additional expression:

```text
not runtime_binding and not configured_service and not installed_pi and not derived_once and not canonical_snapshot_and_compatibility and not derives_runtime_protocol_bindings and not module_application and not runtime_return
```

Collection comparison confirmed all 36 original node IDs survive and exactly
three new node IDs were added. Ruff 0.16.9 passed for all five changed Python
files; its formatter passed for the new fixture module. Compilation and
`git diff --check` passed. The worker's native browser extension was callable,
and `browser_close` confirmed no open tabs; this is not transport-cleanup or UI
acceptance evidence.

## Follow-up full verification

The follow-up verifier ran only this four-module selection with the same warmed
dependencies. An external observation plugin recorded each pytest setup, call
and teardown phase and checked that the process's Schemii environment was restored
after the full run. It does not change selection, assertions, skips or retries.
`--durations=20` was retained. The normal command used the module order above;
the reversed command passed all 39 collected node IDs in exactly reverse order,
including reversed parameter cases within modules.

Inspection of the changed assertions identified one missing explicit guarantee:
the small-graph rebuild check did not itself prove installed-graph determinism.
The once-per-app counter test already requires two fresh full builds. Comparing
their complete canonical HTTP snapshots preserves that guarantee without another
build; subsequent requests still leave all three derivation counters at exactly
two. This assertion was added after the first follow-up run.

| Follow-up attempt | Pytest elapsed | Process wall | Setup | Call | Teardown | Result | Start / exit one-minute load |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Normal, before added equality assertion | 76.25s | 85.763s | 8.610s | 66.299s | 0.0045s | 39 passed, zero skips | 4.38 / 4.27 |
| Reversed, final verification changes | 81.90s | 90.849s | 8.774s | 71.856s | 0.0042s | 39 passed, zero skips | 3.13 / 4.48 |
| Normal, final verification changes | 75.91s | 85.155s | 9.173s | 65.503s | 0.0039s | 39 passed, zero skips | 4.36 / 4.54 |

All three runs contain 39 unique passing call reports, successful setup/teardown
reports and restored Schemii environment settings. No fixture defect or order
dependency was observed. The installed baseline's first setup costs 6.67s,
6.76s and 7.18s respectively. Call time includes the retained fresh application
builds, configured graphs and subprocess checks; it is not pure assertion time.
Pytest elapsed and process wall also include collection, startup and reporting,
so the phase sums are not expected to equal either elapsed measurement.

Unlike the quieter baseline window, the follow-up took place during native UI
pilot work and parallel development/review. At the first follow-up dispatch,
the native interface reported **10 running agents including the coordinator**,
one pending initialization and two completed agents. Desktop/background work and
browser processes also remained active. The same machine/runtime and dependency
cache were used, but concurrency differs and was not controlled. These numbers
show observed reduced cost; they do not establish the issue's controlled 50%
criterion. The first follow-up also predates the added equality assertion and
does not count as a third final-code sample.

Collection comparison again confirmed all 36 original node IDs survive, with
only the same three added counterexamples. Follow-up Ruff, fixture formatting,
compilation and `git diff --check` passed. No full-suite timing sample, application
deployment or UI acceptance was performed by this verifier.

Pending: three comparable final-code after measurements, an integrated
whole-suite result, and independent review. No completed #126 acceptance is
claimed yet. Baseline and follow-up logs, exact collection inventories, per-case
phase reports, commands and timing JSON are retained privately at
`/tmp/schemii-audit-20260929/inspection/` for the coordinator to archive.
