# Repository state and debugging audit

**Date:** November 11, 2026, US/Eastern. Execution receipts may use November 12 UTC.

**Baseline:** `b3159d0dcea7ebd8b2999371e8ae2838ccc8ab01`, identical to fetched `origin/main` at investigation start.

**Scope:** source investigation across the three products, authoritative GitHub issue and PR reconciliation, retained local acceptance validation, repository hygiene, and fixes for reproduced ownership defects. All CI runs locally; GitHub Actions remains disabled.

## Assessment

The baseline has complete source-equivalent local acceptance and substantial regression coverage. Its principal delivery gap is unfinished work: fixes remain in draft PRs or unpublished commits, and several implemented infrastructure capabilities still need their explicit mounted, concurrency, or capacity qualification. Passing deterministic tests does not resolve those acceptance requirements.

The investigation reproduced additional asynchronous ownership defects in assistant submission, dashboard filter saving, and raw-query CSV export. The audit branch addresses these specific boundaries and the previously retained CI bootstrap-isolation fix. It does not attempt to implement the entire feature backlog or establish production capacity.

## Reproduced defects and bounded fixes

| Issue | Reproduction on baseline | Required correction |
| --- | --- | --- |
| [#220](https://github.com/LandMineDevelopment/schemii/issues/220) | Delay first-chat creation for model A, switch to model B and load B's conversation, then complete creation: A's prompt can be sent to B. Typing a subsequent draft during an existing message request also loses that draft on completion. | Bind submission and creation to their initiating subject, conversation, and generation before awaiting; fence stale completions and preserve later edits. |
| [#221](https://github.com/LandMineDevelopment/schemii/issues/221) | Save available filters for dashboard A while stream shutdown is deferred; close the dialog and open B; completion can save A's filter choices into B. | Own the save from its first asynchronous step, bind its original dashboard state, and preserve error/retry behavior. |
| [#222](https://github.com/LandMineDevelopment/schemii/issues/222) | Actual ASGI header/body send failures leave an export lease active after result close; the retained database session cannot be reclaimed normally. Separately, revoking a managed connection grant denies ordinary result access while the legacy CSV route still exports rows. | Put authorization and closeable export ownership at the shared boundary; close safely on success, denial, disconnect, and never-started iteration. Cover both public routes. |
| [#216](https://github.com/LandMineDevelopment/schemii/issues/216) | An inert inherited `SCHEMII_SECRET_DIRECTORY` causes the bootstrap helper control to consume the wrong token. Main reproduces one failure and one pass; the retained correction passes both cases. | Reuse the existing fixture-isolation patch from `f2b3b9e2`, preserve the ambient sentinel, and verify it never enters output or receipts. |

The frontend reproductions execute actual component handlers with controlled deferred requests. The backend reproductions use actual authorization and Starlette response code with in-memory source/session controls. These are behavioral failure evidence, distinct from live browser and real PostgreSQL acceptance.

## Baseline validation and evidence provenance

An independent reviewer reconciled the full local acceptance receipt from PR #218 at `7b806400` with integrated main. Their Git trees, source fingerprint, index fingerprint, policy, and inventory match exactly. All 16 retained timing lanes were revalidated with the current summary validator and matched their summaries.

| Original local acceptance layer | Result |
| --- | --- |
| Node | 617 passed, plus 4 browser-shard infrastructure checks |
| Deterministic Python | 3,035 passed, 110 skipped |
| Disposable external PostgreSQL | 112 passed |
| Desktop and Android browser acceptance | 490 passed, 12 skipped; all 12 shards complete |
| Launcher and isolated backup recovery | Passed |

The successful run completed all 22 commands with no pending layers, failures, or retries in approximately 19m59s. Three earlier failed attempts remain preserved. The original private receipt is in the PR #218 worktree at `.schemii/local-ci/run-20261110T200215Z-9f110e860831/receipt.json`; it retains its original source identity.

A fresh baseline `./ci.sh --feedback --base origin/main` also passed: 617 Node tests, 3,037 Python tests passed with 108 skipped, selected static quality, and compilation. Its receipt is in the primary checkout at `.schemii/local-ci/run-20261112T010235Z-9995075e2a17/receipt.json`. The differing Python skip/pass counts do not change the 3,145-case inventory. Feedback explicitly records `acceptance: false`; baseline full acceptance comes from the separately reconciled original run.

Neither baseline receipt establishes acceptance of changed audit-branch source. Opt-in provider, stress, and manual-concurrency requirements retain their separate scope.

## Open PR reconciliation

All five preexisting open PRs were drafts. None was ready for issue closure merely because its code existed.

| PR | Source and retained state | Remaining delivery |
| --- | --- | --- |
| [#211](https://github.com/LandMineDevelopment/schemii/pull/211) | Published `8ae7543f`; clean local branch `9c97927d` adds drill freshness work. | Publish the intended final source, independent review, selected local acceptance and mounted/manual lifecycle evidence for #204 and #5. |
| [#212](https://github.com/LandMineDevelopment/schemii/pull/212) | Published `1c07a55c`; clean local branch `9ae36057` also includes the bootstrap-isolation correction. | Reconcile overlapping #216 delivery and stale coordination metadata; preserve failed acceptance evidence. |
| [#213](https://github.com/LandMineDevelopment/schemii/pull/213) | `fea20780`; revised source review passed, including correction of an earlier quadratic-memory finding. | Full selected local and mounted desktop/Android acceptance for #77. |
| [#214](https://github.com/LandMineDevelopment/schemii/pull/214) | `174bd293`; revised source review passed after working-state disclosure correction. | Full selected local acceptance and readable mobile/desktop geometry/accessibility evidence for #80. |
| [#215](https://github.com/LandMineDevelopment/schemii/pull/215) | `effb158d`; independent source review and later faithful checkout quality passed. | Complete broader acceptance for #7; reconcile the older quality-failure wording in the PR. |

The two-commit initial source-selection implementation at `e34fccc8` remains unpublished without a PR. It needs independent re-review and mounted/full acceptance for #6. Existing issue references describe substantial work, not integrated delivery.

## Existing issue disposition

The initial tracker held 34 open issues: 15 P1 and 19 P2; 8 in progress, 23 planned, 2 monitoring, and 1 deferred. Three newly recorded audit issues bring the open count to 37 before any subsequent delivery. No preexisting issue was closed during reconciliation.

| Issue | Validated state and next requirement |
| --- | --- |
| [#2](https://github.com/LandMineDevelopment/schemii/issues/2) | Dashboard UI still cannot repair invalid tiles and upgrade the model revision atomically. Backend capability does not provide the author workflow. |
| [#5](https://github.com/LandMineDevelopment/schemii/issues/5) | Drill snapshot disclosure is absent from main; local freshness and explicit Refresh work is ahead of PR #211. |
| [#6](https://github.com/LandMineDevelopment/schemii/issues/6) | Initial model creation still imports all sources before bounded validation; unpublished subset-selection implementation awaits delivery. |
| [#7](https://github.com/LandMineDevelopment/schemii/issues/7) | Main still rejects model-wide cycles; draft participating-path correction awaits acceptance. |
| [#8](https://github.com/LandMineDevelopment/schemii/issues/8) | Product switching remains generic; explicit schema-to-model-to-dashboard context handoff is missing. |
| [#9](https://github.com/LandMineDevelopment/schemii/issues/9) | Detached-design deployment still needs the complete reviewed target/conflict/drift/copy workflow. |
| [#10](https://github.com/LandMineDevelopment/schemii/issues/10) | Opaque-object and materialized-view migration safety restrictions remain intentional; preservation analysis is required before widening support. |
| [#11](https://github.com/LandMineDevelopment/schemii/issues/11) | Relationship contracts remain single-column within a model namespace; composite and cross-schema contracts need evolution. |
| [#12](https://github.com/LandMineDevelopment/schemii/issues/12) | Calculated-output filtering and nested derived composition remain explicitly unsupported. |
| [#13](https://github.com/LandMineDevelopment/schemii/issues/13) | Sequential tile draining and shared preview limits leave fair delivery unfinished; no capacity result was inferred from source alone. |
| [#14](https://github.com/LandMineDevelopment/schemii/issues/14) | Persisted semantic sorting, top-N, and aggregate-result filters remain planned. |
| [#26](https://github.com/LandMineDevelopment/schemii/issues/26) | Pivot tables, subtotals, and grand totals remain planned. |
| [#27](https://github.com/LandMineDevelopment/schemii/issues/27) | Report calculations and KPI targets remain planned. |
| [#28](https://github.com/LandMineDevelopment/schemii/issues/28) | Business number formats and conditional formatting remain planned. |
| [#29](https://github.com/LandMineDevelopment/schemii/issues/29) | Report packages and scheduled delivery remain explicitly deferred. |
| [#41](https://github.com/LandMineDevelopment/schemii/issues/41) | Roles/profile foundations are delivered; managed write-capable identity policy and final acceptance remain. |
| [#73](https://github.com/LandMineDevelopment/schemii/issues/73) | Native parallel QA foundations and bounded pilot exist; full authenticated concurrent acceptance and cleanup parity remain. |
| [#77](https://github.com/LandMineDevelopment/schemii/issues/77) | Main desired views still report empty analyzed columns; PR #213 awaits mounted acceptance. |
| [#80](https://github.com/LandMineDevelopment/schemii/issues/80) | Mobile AI-header draft fix awaits readable 390×844 and desktop evidence. |
| [#123](https://github.com/LandMineDevelopment/schemii/issues/123) | Testing/performance rollup remains open for the unresolved qualification below. |
| [#125](https://github.com/LandMineDevelopment/schemii/issues/125) | Monitoring: retain naturally occurring comparable reliability observations, including failed attempts. |
| [#128](https://github.com/LandMineDevelopment/schemii/issues/128) | Monitoring: retain natural browser-shard timing observations; do not create sample-only full runs. |
| [#134](https://github.com/LandMineDevelopment/schemii/issues/134) | Metadata admission/deadline implementation and assembled PostgreSQL controls exist; disposable canonical-HTTPS saturation, readiness, and lifespan qualification remain. |
| [#135](https://github.com/LandMineDevelopment/schemii/issues/135) | Authenticated load harness exists; backend attribution and generator-headroom evidence remain insufficient for capacity claims. |
| [#136](https://github.com/LandMineDevelopment/schemii/issues/136) | Sustainable throughput and active-user sizing remain unestablished; depend on #134/#135 and deliberate campaigns. |
| [#137](https://github.com/LandMineDevelopment/schemii/issues/137) | Native agent workflow rollup remains open for real concurrency, independent acceptance, and cleanup qualification. |
| [#138](https://github.com/LandMineDevelopment/schemii/issues/138) | Session/evidence/image/download gates are substantially implemented; supported actual confirmation/download cases and independent visual acceptance remain. |
| [#139](https://github.com/LandMineDevelopment/schemii/issues/139) | Lease fencing/recovery exists; ten-tester-plus-reviewer overlap, interrupted-write recovery, and native capacity reclamation remain unproved. |
| [#140](https://github.com/LandMineDevelopment/schemii/issues/140) | Some QA missions still prescribe unsupported import behavior or empty viewer fixtures. Correct missions before a full wave. |
| [#141](https://github.com/LandMineDevelopment/schemii/issues/141) | Evidence separates completion from acceptance, but sticky historical failures still need explicit later-source remediation semantics. |
| [#204](https://github.com/LandMineDevelopment/schemii/issues/204) | Main retains unfinished drills and an unbounded selection map; draft PR #211 awaits lifecycle acceptance. |
| [#206](https://github.com/LandMineDevelopment/schemii/issues/206) | Provider connection UI remains duplicated across assistant implementations; extraction remains bounded planned work. |
| [#207](https://github.com/LandMineDevelopment/schemii/issues/207) | Current result-explanation prompt explicitly reruns the tile; bounded displayed-result provenance is missing. |
| [#216](https://github.com/LandMineDevelopment/schemii/issues/216) | Valid bootstrap fixture-isolation defect; retained patch included in this audit branch. |

## Repository and resource hygiene

At initial inspection, 24 registered worktrees existed and primary main was clean. `audit/browser-output-privacy` contains three uncommitted native-browser files; those edits remain untouched. The frozen `demo-v0.1.0` checkout is retained. Its version command resolves the intended `6acf2fe6` snapshot; main and the frozen demo both pin published `pglast==7.20`.

Old worktree names do not prove incomplete or disposable work. Conversely, lack of simple commit ancestry does not prove a branch is unintegrated after squash merging. The clean PR #218 worktree is source-equivalent to main but holds the current full-acceptance evidence. The ancestor `audit/native-acceptance` worktree also contains ignored runtime/TLS artifacts. Neither should be removed without preserving evidence and confirming live task ownership.

The operations ledger predates November's demo delivery and retains old assignment heads. This audit records a fresh reconciliation alongside the original ledger rather than overwriting historical ownership or erasing failed attempts. Unpushed drill/backlog/source-selection commits, dirty browser work, user data, retained credentials, and shared caches remain preserved.

## Delivery and remaining verification

Focused audit checks passed: 19 assistant behavioral cases, 18 filter/adjacent dashboard cases, 103 CSV/authority/cancellation/adjacent cases, and 54 local-CI controls. Selected Python quality and JavaScript syntax checks passed. These overlapping focused scopes are not summed into a distinct-case total.

Independent source review found and resolved an additional same-subject assistant reopen case: reopening while its first conversation was pending could abandon the consumed prompt. The corrected component retains the active submission on same-subject reopen while preserving cross-subject generation fences. The original failing review, remediation, and five passing recovery controls remain in private evidence. Final frontend and CSV source reviews passed without unresolved findings. The backend reviewer separately exercised both route-built responses through actual native-session cancellation code with a deterministic blocking transport, confirming fetch completion, source closure, and lease removal.

Final local acceptance uses `./ci.sh --base origin/main` with a fresh disposable metadata/source PostgreSQL pair, the test-owned browser account, all selected desktop/Android shards, and launcher-owned recovery. Its source-bound outcome lives under `.schemii/local-ci/`; the private driver log and fixture cleanup record live under `.schemii/audits/acceptance-final/`. The task PR records the resulting head, receipt, and delivery status. Required layers that are blocked or pending remain explicit; the baseline's accepted receipt is not relabeled as a pass for changed source.

Full stress campaigns, ten-minute cleanup probes, ten-worker native manual acceptance, and live provider qualification were not folded into ordinary feedback. They remain deliberate issue-owned acceptance work. The source investigation is extensive but does not establish absence of all defects or production readiness.
