# Android APK process-discovery diagnostic

Refs [#266](https://github.com/LandMineDevelopment/schemii/issues/266) under
[#263](https://github.com/LandMineDevelopment/schemii/issues/263), for execution
by [runtime264](https://github.com/LandMineDevelopment/schemii/issues/264).
This is a disposable **diagnostic candidate**, not a production process adapter,
Schemii service, PostgreSQL substitute or completed ownership proof. It leaves
`dev/android/runtime.py` and the original `owner-control` probe unchanged.

## What it tests

The source is [native/process/](../platforms/android/native/process/):

- `proc_observer.c` reads actual proc observations. It receives only a verified
  direct child's root receipt and its own discovered-member ledger. It never
  receives the test fixture's pipe inventory. Numeric process enumeration,
  kernel UID text, PPID edges, birth ticks, boot ID, PGID and session identify
  the eligible descendants; UID or an old group number alone never admits one.
- `proc_discovery_control.c` independently creates a session leader, branch,
  grandchild, child forked by a nonleader pthread, and same-UID peer in another
  session. Each sends its own PID/birth and syscall ancestry through the oracle
  pipe. The pthread child execs the same packaged executable immediately after
  fork, using only async-signal-safe operations before exec. Its creator TID
  is reported and must differ from the parent process leader PID.
- `proc_observer.h` is private to this diagnostic. It is not an approved shared
  runtime interface or permission to integrate the scanner into the supervisor.

The scanner reads `/proc/PID/status` text for the kernel credentials before
inspecting birth/ancestry. It excludes tasks with a different real, effective,
saved or filesystem UID. That excludes unrelated setuid helpers without treating
their privileged credentials as app ownership. It does not use PRoot-emulated
`stat` ownership or `getuid()` as the host UID. An unreadable or malformed eligible
observation remains unknown. A bounded complete **enumeration** is not proof of
complete historical descendant ownership.

`proc_observe` distinguishes live, gone and unknown. A missing proc entry is
not alone proof of death: a corresponding `kill(pid, 0)` must return ESRCH.
Zombies are not running; oracle reaping is recorded separately. No signal is
authorized by the zero-signal existence test. Cancellation requires a previously
discovered birth/boot/membership receipt, current revalidation around pidfd
acquisition, and `pidfd_send_signal`. Unsupported/denied pidfd operations fail;
there is no numeric-PID or process-group signal fallback.

## Cases and deliberately incomplete results

| Case | Required observation |
| --- | --- |
| `stable`, `pthread` | Exact four-member PID/birth set matches independent witnesses; separate peer excluded. |
| `witnessed-parent-loss`, `leader-loss` | Witnessed birth survives parent reparenting; fresh membership permits pidfd SIGTERM, acknowledgement and exit. |
| `unwitnessed-orphan` | Intermediate forks and exits before a scanner snapshot. The oracle knows the grandchild but the candidate cannot prove its lineage. ENOLINK, no adoption/signal, live target, **incomplete**. |
| `group-change`, `session-change` | A known member moves group/session; its old receipt is refused with ESTALE, and the target remains alive. |
| `stale-birth`, `stale-boot`, `unknown` | Mismatched birth/boot or malformed receipt refused; unknown is distinct from gone, no signal, target survives. |
| `scan-limit`, `scan-deadline`, `scan-cancel` | Truncated/expired/cancelled enumeration returns EOVERFLOW/ETIMEDOUT/ECANCELED, prevents cancellation, never an empty-success result. |
| `sigterm`, `sigint` | Exact signal acknowledgement, identity-bound pidfd exit and gone observation. These are synthetic helpers, not real PostgreSQL shutdown evidence. |
| `cancel-timeout` | Synthetic helper deliberately ignores SIGTERM; ETIMEDOUT with signal sent, **incomplete**, retained ownership. No forced-kill shortcut. |
| `complete-helper-stop` | All four previously discovered synthetic members acknowledge SIGTERM and exit; peer survives. No group kill or UID sweep. |

Every case checks peer survival. The dedicated test owner uses a subreaper only
to contain/reap its disposable orphans. **The scanner does not acquire ownership
from that subreaper or the oracle pipe.** Each process has a private command pipe
and a finite lifetime. The oracle finally releases remaining fixtures and waits
for all children, including adopted orphans. This release is not candidate cleanup.
Unwitnessed-orphan and cancellation-timeout require oracle intervention and remain
incomplete. Unexpected self-expiry, cleanup error or emergency intervention fails
the case. There is no SIGKILL path and no real database or service is launched.

The diagnostic bounds snapshots to 32,768 numeric entries, 4,096 eligible records,
and 1.5 seconds; its ledger holds at most 16 identities. Control-pipe waits are
two seconds, pidfd cancellation at most one second in normal cases, and synthetic
children expire after eight seconds with a failing status. Oracle cleanup has
an 8.5-second deadline. These bounds are reported as failures when exhausted,
not increased or retried to hide missing evidence.

## Building and invoking

Use a private output directory. A host Linux build requires a C11 compiler and
pthreads; the focused tests require only Python's standard library:

```bash
python3 -m unittest discover -s tests -p test_android_process_probe.py -v
```

The tests compile with `-Wall -Wextra -Werror`, exercise real processes and check
exact pipe-oracle sets, refusal behavior, signal acknowledgements, peer survival
and both intentionally incomplete cases. They do not run the application or
establish Android acceptance. Temporary outputs belong to a unique directory
under ignored `artifacts/android-apk/host/process-probe/`.

Runtime264 owns SDK/build/APK/emulator integration. Cross-building can read its
already pinned NDK r30 `30.0.16248370`; do not bootstrap, download or mutate that
cache from this workstream. Example, with absolute paths supplied by the caller:

```bash
"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android36-clang" \
  -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -fPIE -pie \
  -Wl,-z,max-page-size=16384 -Wl,-z,noexecstack \
  -DPROCESS_PROBE_SOURCE_ID='"EXACT_COMMIT_OR_SOURCE_DIGEST"' \
  platforms/android/native/process/proc_observer.c \
  platforms/android/native/process/proc_discovery_control.c \
  -o "$PRIVATE_OUTPUT/libschemii_proc_discovery.so"
```

Use `x86_64-linux-android36-clang` for the diagnostic emulator ABI. This builds a
PIE executable, despite the packaging suffix. ELF alignment and cross-compilation
are not execution, 16KiB-device compatibility or Moto proof. Include all three
source-file hashes, the full source commit, compiler/NDK identity and executable
hash in runtime264's native provenance. The compiled `source_id` is only a label
until independently checked against those inputs.

Package it as a **new** native executable alongside the existing probe, from the
system-installed `ApplicationInfo.nativeLibraryDir`; no writable-home native
execution or legacy-SDK fallback. Run with the distinct command name:

```text
ABSOLUTE_PACKAGED_EXECUTABLE proc-discovery-control --case stable
ABSOLUTE_PACKAGED_EXECUTABLE proc-discovery-control --case unwitnessed-orphan
ABSOLUTE_PACKAGED_EXECUTABLE proc-discovery-control
```

`--case` accepts only the table's fixed names. No caller-supplied PID, proc root,
shell command or executable is accepted. `--internal-thread-leaf` is a bounded
self-exec fixture entrypoint requiring inherited pipe descriptors, not a public
control operation. Invalid public arguments return2 without starting fixtures.

The program writes one JSON object, bounded by the fixed case/identity inventory
(the Linux regression enforces less than64KiB). Do not truncate a report and
then claim the missing cases passed. Invoke **one case per existing runtime264
execution timeout** for native and PRoot runs; the full command's summed worst
case can exceed the prototype Activity's45-second limit. Keep its whole signed
report private; do not upload process/boot identifiers or operational logs.

## Report contract and Android handoff

Exit0 means all selected **diagnostic controls** completed. Exit1 means a failed
or incomplete control, including both deliberate incomplete cases. Exit2 means
invalid invocation. The full command must currently return1 even when
`controls_pass=true`: it deliberately demonstrates the ownership gap. Automated
collection must retain and inspect that report rather than discard it on nonzero.

Top-level fields include schema/probe/source identity, compiled platform/API/ABI,
page size, kernel release, `reported_uid` (possibly virtual under PRoot),
`kernel_uid` plus `kernel_uid_known`, oracle-subreaper result/errno and original
children-node open result/errno. Each case carries exact expected and discovered
identity sets, creator TID, enumeration counts/errno, observed live/gone/unknown,
refusal/signal/acknowledgement results, peer/target survival and cleanup state.
`expected` is the independent pipe oracle; `discovered` is the candidate's
birth-bound ledger at discovery and may retain pre-reparenting metadata.
`complete=true` on a negative-control case means it correctly refused the action
and independently proved survival, not that it solved that scenario's hosting.
`ownership_complete=false`, `runtime_acceptance=false` and Android acceptance
pending are unconditional. No UI should translate these into server Ready.

Runtime264 must execute every case natively and through the actual packaged
PRoot/loader, in debuggable **and non-debuggable** API36 configurations. Its wrapper
must bind exact installed APK/signature/native/source identities, Android build
fingerprint, SELinux domain/enforcement, API/target and execution mode to the
private reports. Preserve the original failing `owner-control` runs separately.
The old cached kernel's disabled CONFIG_PROC_CHILDREN finding still needs binding
to the actual running image; this probe's `uname` and errno are additional facts,
not that binding. Permission, subreaper, proc and pidfd failures remain failures.

The scan-only candidate intentionally leaves the unwitnessed orphan unresolved.
Its safe refusal is useful evidence for deciding whether the existing attached
PRoot tracer needs a narrowly scoped birth-event/continuity export. Do not
broaden adoption to same UID/PGID, add a competing lifecycle engine or modify
the shared supervisor until the coordinator reviews actual probe results and
assigns that boundary. Real PG17 helpers/two clusters, Python/native extensions,
Node/Pi/Go, retained-state recovery, ARM64 Moto and pre-PIN tailnet readiness
remain pending independently. No host controls, phone operation or release is
delivered by this diagnostic.
