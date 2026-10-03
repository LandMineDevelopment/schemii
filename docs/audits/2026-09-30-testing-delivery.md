# Measured testing delivery — 2026-09-30

This report retains delivered changes and measured feedback costs for the
[testing rollup #123](https://github.com/LandMineDevelopment/schemii/issues/123).
It makes no single-server capacity claim.

## Reviewed delivery

These seven focused PRs were independently reviewed and merged:

| PR | Delivered behavior |
|---|---|
| [#154](https://github.com/LandMineDevelopment/schemii/pull/154) | Replaced two frontend source-substring checks with visible toast geometry/pointer checks and history-owner response-order tests. |
| [#162](https://github.com/LandMineDevelopment/schemii/pull/162) | Aligned model Save readiness and strengthened keyboard, scrolling, persistence and cleanup oracles. |
| [#166](https://github.com/LandMineDevelopment/schemii/pull/166) | Added deterministic whole-file browser balancing with exact project discovery and disjoint coverage. |
| [#168](https://github.com/LandMineDevelopment/schemii/pull/168) | Reused immutable observations from necessary genuine installed builds, preserving all 39 inspection cases. |
| [#169](https://github.com/LandMineDevelopment/schemii/pull/169) | Documented the cheapest faithful checks, shared Node discovery and actual hosted feedback boundaries. |
| [#170](https://github.com/LandMineDevelopment/schemii/pull/170) | Preserved unknown Actions phase durations as `null`, including derived residuals, while retaining strict outcome/identity gates. |
| [#171](https://github.com/LandMineDevelopment/schemii/pull/171) | Refreshed browser weights from one complete natural cohort, including actual desktop/Android scopes and provenance. |

Use the existing focused commands in [the feedback guide](../testing-feedback.md)
during development, then the ordinary required PR checks for integrated
acceptance. Faithful state-owner tests and controlled defects supply useful early
feedback; source strings, browser connection probes and repeated full matrices
cannot establish the same behavior. The
[frontend evidence](../frontend-oracle-evidence.md) retains the assertion-failure
controls and limits. Test retirement itself has no measured speed claim.

PR #166's guide checks replaced a roughly 20.5 s combined playback body with
controlled per-product clock steps measured at **1.19–1.45 s**. Six focused normal cases
passed; four broken disappearance-handler controls failed the intended final
cursor assertion while completion stayed frozen. The **3 s assertion-body bound**
excludes before-hook navigation/network loading. Real-time/reduced-motion checks
remain separate. These retained focused results are documented in the
[guide evidence](../browser-shard-balance.md#controlled-guide-timing).

## Controlled inspection improvement

Three alternating focused before/after pairs compared current-main `093062f`
after PR #150 with candidate `a3a2dd867d14a065910bb8e82550184b94216b1b`.
The interpreter, dependency cache, production source, environment and exact
39-case inventory were held constant; every sample passed without skips.

| Median | Before | After | Incremental reduction |
|---|---:|---:|---:|
| Process wall | 75.953 s | 59.379 s | 21.82% |
| Disjoint setup + body + teardown | 67.113 s | 54.756 s | 18.41% |

The host was an Intel i7-11800H with 16 logical CPUs, approximately 31 GiB RAM
and Python 3.14.7. Heavy local checks were paused; ordinary desktop/T3/Codex
activity continued, so this was not exclusive CPU reservation. Reordered cases,
restored environment and ten intended mutation assertion failures verified the
coverage boundary. Fixtures share immutable documents/receipts, never mutable
apps, clients, services or lifespans. Changed-source/config counterexamples stay
fresh. See [the inspection inventory](../inspection-test-cost.md).

These percentages describe the incremental PR #168 improvement after the earlier
fixture work. They do not establish #126's original-baseline 50% criterion.

## Keyboard and fixture evidence

At clean source `0da5780f3b9eeeab5d1396992805bef8c6d36e4f`, two named automated
cases ran 20 times per desktop/Android profile: **80/80 first attempts passed**,
with no retries, skips or flaky results. The geometry case checked positive
scroll and visible relationship-anchor alignment. The keyboard case checked
trusted Space input, actual Save/revision advancement, a separate persisted GET
and reload. All **40 unique owned models** had revision-checked deletion and
independent fresh-context **404** receipts; all 40 verification contexts were
disposed. Served canvas/prototype bytes matched the recorded application source.

This bounded automated cohort does not establish the historical Save-disabled
cause, unbounded reliability or native manual UI acceptance. Browser process
identity absence was not recorded for these repetitions.

## Ordinary CI and timing boundaries

| Natural cohort, attempt 1 | Source identity | Creation to final gate completion | Input critical path | Source-job sum |
|---|---|---:|---:|---:|
| [Main 36726522134](https://github.com/LandMineDevelopment/schemii/actions/runs/36726522134/attempts/1) | `0da5780f3b9eeeab5d1396992805bef8c6d36e4f` | 12m 39s | 732 s | 35.217 job-min |
| [PR #171, 36736569335](https://github.com/LandMineDevelopment/schemii/actions/runs/36736569335/attempts/1) | PR head `e470a7a4bb218f906b86a267cabbabc0cb0f1924`; telemetry source `eb67859376a0bf5e10b6f2d7b2fc19c090758194` | 14m 37s | 850 s | 41.100 job-min |
| [Main 36738740979](https://github.com/LandMineDevelopment/schemii/actions/runs/36738740979/attempts/1) | `8b44cf35c5f71bf0d46dce395674e63c7c04fcfb` | 14m 13s; all 11 jobs green | 832 s | 45.767 job-min |

The PR head and its reported source identity are distinct; the latter object was
not available locally and GitHub identifies it as the synthetic merge into
`0da5780`. Gate completion above is distinct from workflow completion/update:
877 versus 878 s on PR #171, and 853 versus 854 s on latest main. Application source
was unchanged between `0da5780` and `8b44cf3`; intervening changes affected CI,
tests and documentation. The local deployment receipt verifies canonical HTTPS
and the Tailscale preview at `0da5780`, rather than claiming a new deployment of
each reporting commit.

PR #171's seven lanes collected 3,108 attempts: 3,006 passed, 102 skipped,
zero failures/retries. Latest main collected 3,145: 3,043 passed and 102 skipped,
with no failures/retries. Latest main had 494 Node passes, Python 1,987 passed /
90 skipped, 88 PostgreSQL passes and the unchanged browser inventory of desktop
247 cases/63 files and Android 239 cases/60 files. Python retained all prior IDs
and added 37 passing timing/gate controls, so whole-Python totals are not a
controlled suite-speed comparison. Its latest lane wall was 243.007 s; individual
phases sum to 71.639 s setup, 162.930 s body and 0.534 s teardown.

| Browser cohort | Desktop lane walls, shard 1 / 2; ratio | Android lane walls, shard 1 / 2; ratio |
|---|---:|---:|
| Seed observation, main `0da5780` | 344.761 / 249.621 s; 1.381 | 284.518 / 356.973 s; 1.255 |
| First PR #171 run | 303.420 / 347.453 s; 1.145 | 284.128 / 333.872 s; 1.175 |
| Latest main `8b44cf3` | 362.823 / 258.522 s; 1.403 | 382.828 / 343.511 s; 1.114 |

PR #171's per-case execution-phase ratios were 1.230 desktop and 1.143 Android,
and latest main's were 1.354 and 1.141, separate from the lane-wall ratios above.
The refreshed seed's phase
projections were almost equal; actual hosted costs drifted. Neither sustained
balance over ten natural runs nor an all-profile
ratio at or below 1.15 is established. See the
[seed refresh report](2026-09-30-browser-shard-refresh.md).

The installation logs isolate slow **OS package archive retrieval**:

| Lane | Apt archives / fetch time / rate | Complete install step, log interval |
|---|---|---:|
| PR #171 desktop shard 2 | 25.2 MB / 6m 37s / 63.5 kB/s | 418.7 s |
| Latest main Android shard 1 | 32.1 MB / 5m 1s / 107 kB/s | 323.4 s |
| Latest main Android shard 2 | 32.1 MB / 5m 5s / 105 kB/s | 326.7 s |
| Latest main desktop shards 1 / 2 | 34.9 MB each / approximately 1 s | 24.9 / 34.4 s |

PR #171's Actions timestamps report the install as 418 s, separate from its
352 s browser step and 836 s job wall. Its 426 s residual is infrastructure/other
job time, not per-test fixture setup. Chromium/headless downloads took roughly
4–5 s each. The underlying mirror, network or runner cause remains unresolved;
browser caching is unsupported as a remedy for these apt observations. A pinned
CI image with required OS dependencies preinstalled is a supported next candidate,
but has not been built or measured.

Node feedback arrived 13 s after the PR #171 unit job started, about 27 s after
workflow creation. Latest main's Node feedback was 14 s after job start, about
70 s after creation; the Node lane itself took 9.517 s. Scheduling can dominate
otherwise cheap checks.

Per-case setup/body/teardown, browser-lane wall, named test steps, launcher
startup and job wall are separate measurements. Workflow dispatch delay and
dispatch-to-job-start delay do not isolate pure runner queue time. Critical path
and job sums above exclude the rollup/final gate. Missing, malformed or reversed
phase timestamps and dependent residuals remain `null`; genuine zero and explicit
skipped-step zero remain known. A successful complete receipt cannot supply a
missing Actions step end. This distinction preserves acceptance outcomes while
avoiding false zero execution or inflated setup.

Four cancelled intermediate main runs (`36725552048`, `36725612368`,
`36725747466`, `36725791366`) are excluded. The subsequent `807ede3` main run
`36738092166`, created 15:36:59 UTC and cancelled 15:42:52 UTC after the `8b44cf3`
merge, is also excluded as normal ref-concurrency cancellation. PR #166 attempt 1
(`36720398339`) passed its source jobs but its archived Actions API receipt was
incomplete and the final gate rejected `required-evidence-unavailable`; later
receipt availability does not rewrite that failure. Its successful attempt 2
was an infrastructure rerun. PR #170 attempt 1 (`36733478309`) stopped after a successful artifact
download failed to finish; its gate never ran. Its successful attempt 2 is also
excluded from natural reliability comparisons. The artifact-action lifecycle
cause remains unproven; passing source receipts alone did not pass that gate.

## Cleanup and remaining acceptance

Retained mechanical tests observed natural screenshot removal at **629.10 s**,
normal transport exit, bounded cancellation, forced termination and startup
orphan recovery while preserving a live peer. The **20 MiB budget is soft**:
the current 27,086,453-byte image was retained; a later response pruned output.
Selected evidence was exported before expiry. See the
[cleanup validation](2026-09-29-native-cleanup-validation.md).

Independent read-only inspection of the stopped nine-lane native wave found
**109 recorded owned process identities and all nine disposable directories
absent**, including zombie entries. Recorded transport releases and subsequent
absence support that scoped cleanup; they do not establish disposal of every
completed developer/reviewer connection. `browser_close` closes context, while
completed/interrupted turns can retain MCP transport. This interface exposes no
supported native session-close control.

**13 integrated task worktrees/branches were manually removed** after preserving
their heads in Git bundles. Unfinished work, dependency caches and selected
audit evidence remain. This housekeeping is separate from automatic transport
cleanup. Two historical owned SQL-table receipts remain cleanup-pending; current
existence/locks were not queried. Fixture-cleanup parity remains incomplete.
[Manual acceptance #73](https://github.com/LandMineDevelopment/schemii/issues/73)
and [native QA #137](https://github.com/LandMineDevelopment/schemii/issues/137)
remain open, so the legacy UI execution harness is retained.

Selected receipts and review evidence are retained privately under
`.schemii/audits/2026-09-30/testing-delivery/`; credentials, account state and
fixture identifiers are excluded from this public report. No new suite,
application, browser or load campaign ran while assembling this report.

## Matched original inspection cost and next work

The corrected comparison ran the **same 36 original case IDs** in both arms,
using production, conftest and dependencies at `0da5780`. The reference overlaid
only the four original inspection test blobs from
`77af33cbcb97f0645a142c43744ade1e367dd96a`; the current arm used the optimized
tests/fixtures. All six samples passed 36/36 with zero retries/skips, in serialized
reference/current, current/reference, reference/current order.

| Pair | Reference phase sum | Current phase sum | Paired reduction |
|---|---:|---:|---:|
| 1 | 149.448 s | 51.437 s | 65.58% |
| 2, current first | 145.707 s | 52.260 s | 64.13% |
| 3 | 151.172 s | 52.369 s | 65.36% |

The **median paired phase reduction is 65.36%**, with all three pairs above 50%.
Separately, reducing the phase medians, 149.448 to 52.260 s, gives **65.03%**;
process medians fell from 162.626 to 57.021 s, **64.94%**. These are different
aggregations. This measures original test-only cost on identical current
production, rather than historical production or whole-CI speed. The three
additional current correctness cases remained in default discovery and passed
with all 39 inspection cases in ordinary CI; reordered 39-case and ten intended
product-mutation failures were already verified separately.
Independent review approved the six-sample result and scoped cleanup evidence.
The matched-36 measurement meets #126's 50% cost criterion; retained evidence
delivery requires review and integration of this report.

The corrected driver's `finally` automatically removed both owned disposable
roots, including all corrected fixture/scratch directories. All ten collection
and timing subprocesses were reaped, with PID/birth identities and process groups
absent. Both arms retained all 1,197 tracked-file hashes unchanged. Python-main
I/O audit and three reviewed module-app probes per sample with owned temporary environments
are narrower evidence than an OS syscall trace; none was installed.

The flawed ownership-helper pilot is excluded and preserved: three complete
samples and an interrupted fourth. Its archive/scratch roots were automatically
removed, but the shared default pytest root was preserved. Authoritative escaped
fixture-subdirectory identities were not recorded; exact residual ownership and
status are unknown. Corrected cleanup does **not** prove full pilot cleanup, and
guessed names/timestamps do not authorize deletion.

Next measured bottlenecks are [#125](https://github.com/LandMineDevelopment/schemii/issues/125)
for setup/installation and early hosted feedback,
[#128](https://github.com/LandMineDevelopment/schemii/issues/128) for natural
browser-cost drift and sustained balance, and
[#126](https://github.com/LandMineDevelopment/schemii/issues/126) for integration
of its accepted measurement evidence. Keep full stress runs outside ordinary
PR feedback; this testing cohort measured no server load capacity.
