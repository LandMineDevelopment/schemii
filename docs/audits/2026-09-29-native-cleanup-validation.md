# Native browser cleanup validation — September 29, 2026

Baseline: `77af33c` (merged PR #144). Task branch: `audit/browser-cleanup`.
Related rollup: [#137](https://github.com/LandMineDevelopment/schemii/issues/137).
This records lifecycle acceptance for native browser resources, not Schemii
application acceptance or a replacement for the legacy UI harness.

## Confirmed defect and fix

Independent review reproduced an unbounded termination path: a disposable stdio
child ignored SIGINT/SIGTERM, the supervisor forwarded the signal but continued
waiting, and its five-second SIGKILL escalation was never reached. At six seconds
the supervisor, child, guardian and owned output were still present.

The launcher now enters its bounded finalizer immediately after forwarding a
transport shutdown signal. Repeated signals cannot interrupt the finalizer;
original handlers are restored after child shutdown, output cleanup and guardian
reaping. The fix also protects a normal finalizer from its first arriving signal.
SIGTERM exits 143 and SIGINT exits 130.

Review also found a failed-start ownership gap in the new verifier: initialization
could raise before its connection was registered for final cleanup. The constructor
now closes its owned transport before rethrowing initialization failures or
interruption. Independent real-subprocess checks confirmed automatic closure for
both `ProbeError` and `KeyboardInterrupt`, without fallback teardown.

Independent controlled verification observed SIGTERM plus repeated SIGTERM
complete in **5.025 seconds**, and SIGINT plus repeated SIGINT in **5.023 seconds**.
Both removed owned output automatically, ended the ignoring child and reaped the
guardian. A first SIGTERM during deliberately delayed normal finalization also
left no owned output or processes and preserved exit status 0.

## Actual native interruption and browser closure

The fresh assigned child exposed its own `schemii_browser` extension. A filename-free
capture of the synthetic “Cleanup boundary probe” page returned an inline image;
the image was inspected. The coordinator deliberately invoked
`collaboration.interrupt_agent`, then resumed the same child with `followup_task`.
The same session, page title/DOM, supervisor, guardian and original screenshot
remained. The expiry watcher continued. This was a real native interruption and
established retained-transport behavior, not shutdown.

The current collaboration/tool inventory exposes no supported native agent or
session-close operation. `browser_close` was called before completion, and a
birth-identity-scoped process snapshot found **zero Chromium processes** under
the assigned supervisor afterward. The transport/session and guardian remained
live at that observation. Completed/interrupted turns must not be counted as
transport exit.

After browser closure, the coordinator authorized termination of this thread's
exact bound supervisor. Its returned output directory, private metadata, session
inode, repository and PID/start-tick identities were checked before sending only
that supervisor SIGTERM. All **four captured owned process entries** and the
session directory disappeared automatically in **0.135 seconds**. No manual
directory deletion occurred. This terminated the owned browser transport; it did
**not** close the native agent/thread or claim a supported native session-close
operation. No later browser tool call restarted that connection.

## Real ten-minute expiry

The fresh native screenshot was created at **23:45:14.704 UTC**. Its timestamp was
not changed or artificially aged. A separate watcher observed automatic removal
at file age **629.10 seconds**. The session and external ownership metadata
remained valid. This satisfies the 600-second TTL with a sweep at least every
thirty seconds. No manual deletion established this result.

## Controlled stock Codex lifecycle run

`python3 testing/agents/verify_browser_cleanup.py` creates ephemeral native
threads through disposable stock Codex app-servers, using the configured browser
extension, warmed pinned package and synthetic data URLs. It starts no AI turn,
changes no provider configuration, writes no application data and kills only
captured PID/start-tick identities. Its long `--include-expiry` option is outside
ordinary PR feedback; the real TTL run above used the actual assigned thread.

| Check | Observed result |
| --- | --- |
| 20 MiB soft budget | 20,971,572 bytes before old-output eviction; old output automatically evicted |
| Current response exemption | A fresh noise screenshot remained at **27,086,453 bytes** (25.83 MiB), matching the inline PNG response |
| Subsequent budget recovery | Next small response removed the oversized prior image; **53 bytes** remained |
| `browser_close` | Browser context closed and could be reopened on the same transport |
| Normal owned app-server shutdown | One browser close, one unsubscribe; zero live owned processes/directories |
| Owned launcher SIGKILL | Guardian preserved a still-live child session, then removed output after client exit |
| Owned app-server SIGKILL | Child/browser/guardian exited and output disappeared automatically |
| Supervisor and guardian loss | Valid orphan remained until next launcher startup, then was automatically swept |
| Separate live peer | Output unchanged and browser still usable after every destructive case |
| Final cleanup | Six disposable native connections released; zero owned process entries (including zombies) and directories; zero manual session-directory removals |

Each connection captured 15 owned process identities, including the supervisor,
MCP child, Chromium descendants, guardian and disposable app-server. Identity
checks prevent reused PIDs from receiving signals. Focused regression coverage
also preserves unknown, malformed, nonprivate, symlinked and live-owner sessions;
cleanup does not follow symlinks or remove replaced directory inodes.

## Checks and limits

- `python3 -m unittest testing.agents.test_browser testing.agents.test_browser_probe`:
  **69 passed in 7.356 seconds**, including a real ignoring-child regression and
  failed/interrupted initialization cleanup coverage.
- `python3 testing/agents/verify_browser_cleanup.py`: passed normal, forced,
  orphan, budget and peer-preservation checks. No application acceptance claimed.
- `python3 testing/agents/doctor.py --runtime-slots 13 --require-native-browser-config`:
  configured 12 children, actual reported 13 total slots, trusted guards and no
  hook loading errors. This is configuration evidence, not UI acceptance.
- Native interruption, real TTL and inspected fresh screenshot: observed as above.
- Ruff 0.16.9 check and format check, Python compilation and `git diff --check`:
  passed using the existing pinned quality environment.
- Application rebuild was unnecessary; application URLs, accounts and deployment
  leases were not touched.

Selected useful evidence is this report, command summaries and the independent
reviewer's controlled reproduction receipts. Disposable screenshots/downloads
were not exported. Required shutdown of owned transports must be observed
separately from native turn status. The legacy harness remains required until
its authenticated application pilot and ownership/evidence/cleanup parity pass.

## Selected machine-readable receipts

Actual assigned-thread expiry watcher and subsequent bound transport termination:

```json
{"phase":"real-expiry-result","file_removed":true,"observed_age_seconds":629.1,"session_retained":true,"metadata_retained":true}
{"termination":"own bound supervisor SIGTERM after browser_close","supervisor_pid":1409880,"supervisor_birth_tick":120688635,"captured_owned_processes":4,"cleanup_seconds":0.135,"owned_directory_removed_automatically":true,"remaining_owned_process_entries":0,"manual_session_directory_removals":0,"native_agent_session_closed":false}
```

Final verifier run after the initialization ownership fix and strict process-entry
checks (including exited zombies):

```json
{"result":"passed","ai_turns_started":0,"session_directories_manually_removed":0,"limits":["Mechanical native transports and synthetic pages; no Schemii application acceptance.","Forced tests target only recorded disposable identities, never the attached T3 session.","Native turn interruption is a coordinator test; this utility does not request inference turns.","Stock thread/unsubscribe has a grace period; owned app-server exit ends these transports."],"budget":{"budget_bytes":20971520,"before_eviction_bytes":20971572,"older_output_evicted":true,"current_response_exempt_bytes":27086453,"after_next_response_bytes":53},"browser_close_retains_transport":true,"normal_shutdown":{"browser_close_ok":1,"unsubscribed":1,"app_server_stopped":true,"session_directories_removed":true,"live_owned_processes":0,"cleanup_seconds":0.019,"owned_process_identities":15,"remaining_owned_process_entries":0,"remaining_owned_directories":0},"forced_launcher_shutdown":{"cleanup_seconds":0.758,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},"forced_app_server_shutdown":{"cleanup_seconds":0.774,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},"orphan_recovery":{"cleanup_seconds":0.026,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},"live_peer_preserved":true,"final_cleanup":[{"cleanup_seconds":0.016,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},{"cleanup_seconds":0.017,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},{"cleanup_seconds":0.031,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},{"cleanup_seconds":0.017,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},{"cleanup_seconds":0.016,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0},{"cleanup_seconds":0.018,"owned_process_identities":15,"live_owned_processes":0,"remaining_owned_process_entries":0,"remaining_owned_directories":0}]}
```
