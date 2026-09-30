# Inspection test setup and evidence

Work for [#126](https://github.com/LandMineDevelopment/schemii/issues/126),
against baseline `77af33c`. Acceptance remains pending the full changed-order
checks, independent review and three comparable after measurements.

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
| Once-per-application derivation | Unchanged two fresh full application builds, strict counters for all three builders and repeated HTTP requests |
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

Further heavy testing was deferred when the coordinator reprioritized native UI
acceptance. Pending: normal and reversed full four-module runs, three comparable
after timing samples, an integrated whole-suite result, and independent review.
No 50% saving or completed #126 acceptance is claimed yet. Baseline logs, exact
collection inventories and timing JSON are retained privately at
`/tmp/schemii-audit-20260929/inspection/` for the coordinator to archive.
