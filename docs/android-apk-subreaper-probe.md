# Dedicated subreaper orphan-continuity diagnostic

Refs [#266](https://github.com/LandMineDevelopment/schemii/issues/266) and
[#263](https://github.com/LandMineDevelopment/schemii/issues/263); actual APK
integration remains [#264](https://github.com/LandMineDevelopment/schemii/issues/264).
This is a separate feasibility control. Every tracked file from commit
`6242afe5fec7adfed4b67336de804b99e8b664a8`, including its polling diagnostic,
public CLI, tests, guide and shared supervisor, remains unchanged.

The implementation is
[subreaper_probe.c](../platforms/android/native/process/subreaper_probe.c).
It links the existing `proc_observer.c` and header read-only for proc snapshots,
birth observations and verified pidfd cancellation. It does not call the old
`proc_discover`: that method deliberately refuses unwitnessed orphans.
No production process adapter, host UI, new tracer or lifecycle framework is
introduced. No real PostgreSQL or other backend process is started.

## Ownership and independent witnesses

The executable's outer **oracle** starts an unrelated same-UID peer and a
separate **candidate owner process**. The candidate closes the oracle pipe's
read end. It cannot receive the oracle's expected PID/birth inventory. Only
single-byte `G` (begin) and `C` (continue) barriers enter the candidate; its own
observations and results flow outward through a different pipe.

Before descendants start, the candidate establishes its own session and birth
receipt, checks its initial subreaper state, sets `PR_SET_CHILD_SUBREAPER`, and
reads the flag back. The outer direct parent independently verifies that
candidate's birth before sending `G`. The candidate revalidates its own birth
before launch, each admission and cancellation, and final completion.
The oracle's own subreaper flag is **test containment**, not candidate authority;
the tests require the candidate to start with flag0 and independently reach1.
Both processes explicitly establish their own SIGCHLD disposition so statuses
are not silently auto-reaped due to an inherited disposition.

Disposable intermediates fork leaves, wait for readiness bytes, then exit.
For the nested case an additional intermediate also exits. The candidate reaps
its directly launched intermediate **before its first proc scan**. Leaf-side
witnesses independently send birth/ancestry/group/session records to the oracle
before their parent exits. They do not send those records to the candidate.

The candidate admits only processes with fresh, same-UID/boot observations whose
kernel PPID is its own verified PID. It also verifies direct-child waitability
with `waitid(P_PID, ..., WEXITED|WNOHANG|WNOWAIT)`. This is new kernel parentage
authority, rather than inference from UID, session or a historical group number.
An admitted leaf may have a different session/group: reparenting to a live
subreaper works across those boundaries. The external oracle separately compares
exact discovered birth sets against its expected leaves and excludes the peer.

Before stopping any member, the candidate validates all admitted receipts again.
Each individual cancellation also uses the unchanged observer's identity checks
around `pidfd_open` and `pidfd_send_signal`. There is no numeric-PID/group/UID
signal fallback. It reaps those specific children, checks successful exit status,
then requires ECHILD from a non-reaping final `waitid` and a still-current owner.
The dedicated oracle independently verifies signal acknowledgements and the
candidate's actual exit status. `waitpid(-1)` is used only by that standalone
oracle to contain its own disposable test tree, never in PRoot/Popen or another
runtime. The candidate never steals another owner's wait statuses.

Original expected witnesses remain separate from later mutation witnesses in
`changed[]`. This preserves original ancestry and proves reparenting even when a
later command changes a leaf's group/session. A subsequent birth witness cannot
replace the original expected inventory.

## Cases and interpretation

| Case | Required result |
| --- | --- |
| `fast-orphan` | Two leaves whose intermediate already exited are discovered as direct children, gracefully stopped and reaped. |
| `nested-orphan` | Three leaves survive two exited intermediates; all are accounted for, stopped and reaped. |
| `new-session`, `new-group` | A leaf changes session/group before the first scan; actual reparented PPID and waitability authorize admission. |
| `sigint` | Both synthetic leaves acknowledge SIGINT, exit successfully and are reaped. This is not real PostgreSQL shutdown evidence. |
| `deny-prctl`, `unsupported-prctl` | Candidate-local seccomp returns EPERM/ENOSYS for prctl; no descendant launch and no claimed subreaper success. |
| `deny-proc`, `unsupported-proc` | After topology launch, candidate-local openat is denied with EACCES/ENOSYS; unknown scan, no admission or signal, incomplete. |
| `deny-pidfd`, `unsupported-pidfd` | After discovery, candidate-local pidfd_open returns EPERM/ENOSYS; no signal, live retained child, incomplete. |
| `timeout` | Synthetic leaves ignore SIGTERM; verified signal times out, ownership is retained, no forced kill, incomplete. |
| `stale-owner` | Candidate's private owner receipt is deliberately altered; birth verification refuses before launching descendants. |
| `stale-group`, `stale-session` | After discovery, a leaf changes membership; old receipts are refused before the cancellation batch, incomplete. |
| `owner-loss` | External direct parent terminates only the birth-verified candidate through a pidfd SIGTERM. A known orphan survives and reparents away from that owner. **The candidate does not solve owner death.** Oracle release/reaping is intervention and the case remains incomplete. |

Permission/unsupported controls use real **per-candidate kernel seccomp errno
injection** after `NO_NEW_PRIVS`. They are not mocks, Android permission evidence,
or a proposed security sandbox. Failure to install a filter is a failed control,
not a skipped or successful denial test. Actual Android proc/prctl/pidfd behavior
must be recorded independently in positive executions.

Only the first five cases can have `candidate_complete=true`. Correct safe
refusal in the other cases can yield `control_pass=true` while candidate
completion stays false. The full command therefore returns1. Every case checks
the live peer independently and captures raw errors. Failure handling by the
outer oracle is never relabeled candidate cleanup. The probe contains no
SIGKILL or process-group kill. Unexpected fixture expiry/nonzero cleanup status
also fails the case; it is not retried away.

## Build and bounded invocation

Focused Linux checks require only a C11 compiler and Python's standard library:

```bash
python3 -m unittest discover -s tests -p test_android_subreaper_probe.py -v
```

Compile exactly this new file plus the unchanged observer; do not compile the
old polling executable's main into this binary. Runtime264 owns packaging and
emulator execution. Its pinned NDK r30 `30.0.16248370` can be read without cache
mutation. Given exported immutable source paths and a fresh private output root:

```bash
for target in aarch64-linux-android x86_64-linux-android; do
  mkdir -p "$SUBREAPER_OUTPUT/$target"
  "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/${target}29-clang" \
    -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -fPIE -pie \
    -Wl,-z,max-page-size=16384 -Wl,-z,noexecstack \
    -DSUBREAPER_PROBE_SOURCE_ID='"EXACT_COMMIT_OR_SOURCE_DIGEST"' \
    "$PROCESS_SOURCE/proc_observer.c" "$PROCESS_SOURCE/subreaper_probe.c" \
    -o "$SUBREAPER_OUTPUT/$target/libsubreaper_probe.so"
done
```

This is a standalone bionic PIE executable with native API29 floor; the APK
retains compile/target SDK36 and min29. `compiled_api` reports the native floor,
not the APK target. ELF 16KiB alignment or cross-build success is not Android
execution or 16KiB-device compatibility.

Package separately in `nativeLibraryDir/libsubreaper_probe.so`. Public argv is:

```text
[ABSOLUTE_PACKAGED_PATH/libsubreaper_probe.so,
 "subreaper-orphan-control", "--case", CASE]
```

No arbitrary PID, proc root, shell command or state path is accepted. Invalid
argv returns2 without starting a fixture. Use a fresh empty, private task-owned
cwd per case/mode/debug configuration, with stdout/stderr/envelope reports in a
separate sibling directory named for `subreaper-orphan-v1`. This probe writes no
persistent state and never touches Termux or database directories. Preserve the
old `libprocess_probe.so`/`owner-control` binaries and reports independently.

Run one case per **45000ms collector budget**, natively and through the actual
packaged PRoot/loader, in both debuggable and non-debuggable configurations.
The program has no global deadline argument or unconditional real-time guarantee
for blocked syscalls/scheduling. Internal bounds are: scan1500ms/32768 numeric
entries/4096 eligible records; candidate ledger16; readiness bytes2000ms;
intermediate wait3000/4000ms; candidate barriers5000ms; oracle result6000ms and
witness3000ms; normal cancellation1000ms (negative timeout20ms); member reap500ms;
owner reap1500ms; synthetic lifetime10000ms; oracle cleanup12000ms.
Collector expiry is failed/incomplete, and any externally assisted cleanup must
remain intervention. Do not increase bounds, retry to obtain a pass, use stale
PID/group authority or claim descendant cleanup from the candidate merely exiting.
The full16-case command's summed worst case exceeds one collector slot.

## Reports and remaining gates

One JSON object goes to stdout; tests enforce less than64KiB for the full fixed
inventory. Retain nonzero reports without truncation. Exit0 means all selected
candidate controls completed, exit1 means failed/incomplete, exit2 invalid argv.

Top-level fields include schema/probe/source_id, compiled platform/API/ABI, kernel
release/page size, reported versus kernel UID and knownness, oracle subreaper
status/errno, cases, controls_pass and selected_candidate_cases_complete.
`owner_death_solution=false`, `production_ownership=false`,
`runtime_acceptance=false` and Android acceptance pending are unconditional.

Cases include candidate owner birth, subreaper before/after/error, real fault
injection status/error, launch/barrier/first-scan facts, scan status/errors,
internal and external owner verification, child-waitability verification,
independent original `expected[]`, later `changed[]`, actual `discovered[]`,
signal/reap counts, acknowledgements, final ECHILD result, actual candidate exit
code/signal, peer survival, retained live children, owner-loss/reparented orphan,
oracle intervention and cleanup error. Birth identities contain PID, PPID, PGID,
session, kernel UID, start ticks and boot ID. Candidate results never include
arbitrary logs, credentials or database data; keep operational identities private.

Runtime264 must add its source/native/APK/signature identities, mode/debug flag,
actual SDK/API/ABI, SELinux domain/enforcement, Android fingerprint, collector
bounds/elapsed and owned-state metadata. The executable does not invent those
wrapper facts. PRoot tracing may affect parentage, reaping and signal delivery;
Linux success is not permission to assume the same Android result.

The Linux positive controls can close the demonstrated fast-reparenting gap
**while this dedicated owner remains alive**. They do not prove production
ownership, arbitrary future forks, nested subreaper semantics, descendants that
become unobservable, PRoot/Moto behavior, real PG/helper shutdown, or survival of
owner death. If real consumer evidence fails, retain NO-GO. The next already
identified boundary is the attached PRoot fork/clone/exit event inventory and
its identity/continuity export; no second tracer is implemented here. Any
production integration or shared `host.py`/`runtime.py` adaptation needs a
coordinator assignment after actual native/PRoot/non-debuggable APK evidence.
