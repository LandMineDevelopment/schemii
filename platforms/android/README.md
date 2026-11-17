# Disposable Android runtime probe

Refs [issue264](https://github.com/LandMineDevelopment/schemii/issues/264) and
[feature263](https://github.com/LandMineDevelopment/schemii/issues/263).

Read [the runtime guide](../../docs/android-apk-runtime.md) for the execution
decision, exact toolchain inputs, build/probe commands, descriptor and retained
failures. This is a signed target36 feasibility APK with disposable state; it is
not a release or a production supervisor. Native ARM64/Moto, ownership recovery,
HTTPS, Direct Boot and independent acceptance remain required.

- `build.py`: verified SDK/NDK bootstrap, native builds, exact shared-source
  staging, signing and actual APK readback.
- `runtime/build_payload.py`: explicitly resolved, pinned Debian/Python/Node data.
- `runtime/guest_probe.py`: real two-cluster SQL/native-extension/Pi/HTTP probes.
- `runtime/peer_inputs.py`: immutable peer Git inputs and source-bound libraries.
- `verify_apk.py` and `check_package_controls.py`: actual packaged-byte gates.
- `runtime-interface.json`: proposed loopback endpoints and provisional host boundary.

Native peer namespaces `native/process/`, `native/network/` and Java `android/network/`
are separately owned. Optional integration reads exact committed peer inputs
without editing their worktrees or copying shared core source into this subtree.

Native reuse requires both `--native-donor` and an independently selected
`--native-donor-receipt-sha256`; donor-local rebinding cannot select its own trust
anchor. The collector keeps journal I/O provisional and commits output/owner
release together only after a cancellation check under the lifecycle gate.
`runtime/check_wrapper_report.py` checks raw finite witnesses against actual
packaged fixture identity. Host latch controls and wrapper-only Android controls
do not establish general descendant recovery or production supervision.

The bounded `--integration-probes` profile admits only reviewed13e subreaper and
cde network inputs through independently selected handoff/library anchors. It
runs neither the wrapper-only controls nor old polling/guest/backend campaigns.
Each process case uses the fixed collector and an exact finite-oracle report
policy; unknown cleanup retains the captured Process and fences further launches.
`runtime/check_integration_report.py` binds actual reports to signed package,
component, ABI/debug and per-mode inventories. Callback readiness and finite
candidate completion remain separate from production ownership and tailnet
readiness. See the guide for commands and pending device/host gates.

## Closed CE acquisition observation v1 (R266-D02)

This is an observation-only producer in the same five-source/seven-TU profile.
It does not fix an unknown EACCES cause, expand the image/path rules, create a
control directory, admit C/A, initialize a journal or launch anything. Native
return values, existing UNKNOWN boot facts, raw16 `NativeOwner.status()` and
cleanup/tombstone rules are unchanged. `request()` still refuses. New products
and actual Android observations need separate independent gates.

`NativeOwner.observation()` is a **separate** immutable raw34 diagnostic ABI;
indices0/1 are version1/length34. Unknown schema/length, out-of-range finite
fields and inconsistent timing decode as unavailable. Even a valid post-close
report cannot clear the Java reservation unless legacy disposition, versioned
pre/post observations, same-worker completion, timing and fixed source binding
are coherent. Diagnostic read/allocation/log failures never skip native close.

| Indices | Meaning |
| --- | --- |
| 2,3 / 4,5 | First failing stage/substage / last attempted stage/substage |
| 6 | Framework phase at failure: 0unknown, 1Application, 2CE selection, 3loader, 4package query, 5closed package, 6current signer, 7package objects, 8SHA formatting, 9returned observation |
| 7,8 | Rejected image category (0none, 1short structure, 2null name, 3outside existing fixed inventory, 4own-count mismatch); visited count capped4096 (4096 means at least4096) |
| 9..13 | First failed object kind, hop, operation, actual syscall errno (0unobserved/not a syscall), logical error |
| 14,15 / 16,17 | First/secondary observed operation errors / first/secondary cleanup errors; legacy raw16 keeps its existing error semantics |
| 18..21 | Retained, busy, reserved, native phase (0unobserved, 1open running, 2open returned, 3close running, 4disposed, 5retained) |
| 22..30 | Unchanged boot relation/reason/status/stage/error/cleanup/current/prior/continuity facts; no admission inferred |
| 31,32,33 | Native monotonic begin/end ns (-1unknown/incomplete), observer is original acquiring worker |

Stages1..24 are frame, pins, class lookup, application borrow, selected borrow,
framework inspection, field extraction, package/signer match, dladdr module,
image inventory, pre-acquisition pair, held objects, members, custody open,
boot/custody observation, mapping, post-acquisition pair, framework reinspection,
field reinspection, held revalidation, final mapping, final images, publication
pair and close. Substages distinguish class/field ordinal, R-before-post-R
custody and close authority-before-objects. First failure is never reset by a
later stage, cancellation or safe disposal. Image names/maps/addresses and
object paths never enter the scalar report. Object kinds1..4 are APK/module/
selected control/maps; hop0 is root or nontraversal, subsequent hops are path
component ordinals. Operations1..7 are open/stat/reopen/read/mode/content/mapping.
A logical mode or identity refusal does not invent a syscall errno.

`InstalledSource` now has one ThreadLocal integer phase; it retains no Context,
Throwable or package/loader data. Its new class initializer only allocates that
scalar holder, not a loader/worker. Holder/set/get/remove failures disable phase
observation as explicit reason0/unknown; they do not replace a framework guard
result or escape as a new acquisition exception. This refines the earlier runtime guide's
pre-diagnostic initializer inventory. The JNI getter reads a finite phase only
after clearing an original framework exception and consumes it. JNI consumes any
old phase before an invocation, so a failed invocation cannot inherit a prior
operation's reason. These reads cannot change acquisition
results. No public report setter or caller-supplied native identity is added.

The builder emits `SCHEMII_OBSERVATION_SOURCE` from the admitted immutable source
commit into the generated pin header. `observationSource()` returns that fixed
source identity (unbound outside this closed profile), not a signature or trust
root. No final-APK self hash is introduced. Same-operation pre/post-close snapshots
are captured by the sole Java worker, cloned and retained through disposal.

Own-process tag `SchemiiAcquire` emits bounded ASCII scalar records (v1/profile,
fixed source or explicit unknown, service incarnation/operation sequence,
record/phase, Java/native monotonic timing, finite failure fields, cancellation
request, completion-point observation and raw34 pre/post values). Begin can be
source-unbound while storage selection is pending; later records bind that same
operation to the fixed source. A completion point is not thread death, a join,
service shutdown or durable recovery. No path/package/signer/raw exception,
loader content, endpoint, key or user data is logged. Stop/destroy/replacement
record a cancellation request separately from worker completion; repeated cancel
requests do not produce an unbounded stream. ID exhaustion is explicit unknown,
never wrap/reuse.

Each record is limited2048 characters. Failed/short log writes increment a loss
counter and surface observation loss; a positive log return **does not prove
delivery**. Every record states `delivery=unverified`, sequences expose missing
captures, incomplete begin/end remain explicit, and a final lost record may
require the UI observation-loss notice. Logcat rotation/drop/collection loss
must be reported externally, bound to actual installed bytes; this is not a
private durable export/recovery mechanism. No background control/start surface,
Direct Boot receiver, exemption, timer or provisioning path is introduced.
