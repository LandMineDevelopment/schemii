# Browser shard cost refresh

This follow-up to [#128](https://github.com/LandMineDevelopment/schemii/issues/128)
refreshes the existing inline whole-file weights from the complete ordinary
[main run 36726522134, attempt 1](https://github.com/LandMineDevelopment/schemii/actions/runs/36726522134/attempts/1)
at source `0da5780f3b9eeeab5d1396992805bef8c6d36e4f`. The previous observation,
run 36651493606 at `f2cf555e8a62263d2caf97dcae9f27407f8c7508`, preceded the
delivered clock and oracle changes and omitted two subsequently added files.

## Measured cohort

All four browser receipts have the same run, attempt and source identity, complete
collection and a passing lane outcome. Every collected case has exactly one
first attempt; no failures, cancellations, missing attempts or retry recoveries
occurred. The six skipped cases per profile retain their measured setup and
teardown cost. This change adds no skips or retries.

| Profile | Files | Collected / passed / skipped | First-attempt phase sum |
|---|---:|---:|---:|
| Desktop Chromium | 63 | 247 / 241 / 6 | 588.976 s |
| Android Chromium | 60 | 239 / 233 / 6 | 635.602 s |

Weights sum `setup_ms + execution_ms + teardown_ms` across the first attempts in
each file, including skipped attempts. Receipt path hashes were resolved against
tracked `tests/e2e` paths at the recorded source; each profile's case IDs and files
are disjoint across its two lanes. The table contains the 63-file union and omits
Android weights for the three files absent from actual Android discovery:
`bulk-jobs.spec.js`, `console-shared-capacity.spec.js` and
`raw-copy-streaming.spec.js`. The existing request-only exclusions remain intact.

The measured artifacts are `browser.jsonl` in each
`browser-timing-{desktop,android}-chromium-shard-{1,2}-attempt-1` artifact. Their
SHA-256 values, in desktop 1/2 then Android 1/2 order, are:

```text
0a1ce971096a6c6e384c68a6b7074db89df70064583037c5f08ee0202a262f20
99d15ddc2be63dcf5b5363ebb1b29642b94ac46b65a8e70a0f5777dd9332caad
cdb3772212d836db35e36b6743f20f89a2eb32e514c5fc4045da75853b16c647
799487521269959be207ebc055bdbe875f32e23e9db3447d22c7704f40948cf4
```

`OBSERVATION` also records each profile's file count, case outcomes, phase total
and SHA-256 of its sorted JSON `[file, milliseconds]` pairs. Those values describe
this source observation, rather than freezing future discovery to a file count.

## Balance and limits

| Profile | Observed lane wall, shard 1 / 2 | Observed wall ratio | Observed case phases, shard 1 / 2 | Refreshed-weight projected phases, shard 1 / 2 |
|---|---:|---:|---:|---:|
| Desktop Chromium | 344.761 / 249.621 s | 1.381 | 341.658 / 247.318 s | 294.482 / 294.494 s |
| Android Chromium | 284.518 / 356.973 s | 1.255 | 281.711 / 353.891 s | 317.789 / 317.813 s |

The unchanged whole-file scheduler assigns the refreshed desktop weights to
31/32 files and Android weights to 29/31 files, with projected phase ratios
1.00004 and 1.00008. Whole-file granularity allows a close balance in this sample.
These projections redistribute one run's observed case phases; they are not
measured future lane walls or a speedup. Lane startup, setup outside the recorded
case phases, hosted resource contention and subsequent cost drift can differ.
The next ordinary CI run supplies integrated evidence; no additional complete
suite was launched to produce this observation.

## Verification

The scheduler algorithm, one-worker serialization, file ownership, project
configuration, application tests and retry policy are unchanged. Updated
discovery controls verify that measured weights belong to the actual profile,
match the recorded totals and fingerprints, and that each discovered case is
assigned exactly once across two disjoint lanes. Unknown files still use the
existing profile median and remain scheduled, including the synthetic nested
new-file control.

`node --test scripts/ci/browser-shards.test.mjs tests/browser-infrastructure/shards.test.mjs`
passed all six tests with no skips. The real inventory checks use Playwright
`--list` only; no browser, application runtime or live API action was performed.
Both changed JavaScript files passed `node --check`; `git diff --check` passed.
