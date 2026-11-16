# Android APK runtime feasibility

Issue [264](https://github.com/LandMineDevelopment/schemii/issues/264), under
[263](https://github.com/LandMineDevelopment/schemii/issues/263).
This is a disposable diagnostic APK foundation, **not a working Schemii server**.
Runtime acceptance and downstream host integration remain blocked. A real
Debian/Python/PostgreSQL/Node payload is now available for disposable diagnostics;
component execution is distinguished from whole-stack acceptance. It does not
launch Termux, change the phone, start the desktop stack or enroll a node. The
optional real guest probe opens temporary loopback-only diagnostic listeners.
No account/database/key migration is implemented.

## Execution decision and measured boundary

The first candidate is deliberately APK-packaged PRoot, with bionic PRoot and
its freestanding loader installed as `lib*.so` in `ApplicationInfo.nativeLibraryDir`.
`extractNativeLibs=true` makes the installed executable location available.
The guest payload is immutable, independently verified runtime input, with
separate writable data. It is not the existing Termux installation.

[Android's execution restriction](https://developer.android.com/about/versions/10/behavior-changes-10#execute-permission)
for target29+ rules out directly executing extracted app-home binaries.
[PRoot's loader](https://github.com/termux/proot/tree/52d65a4dc140ca554d241a1438ddd4f6b00c9813/src/loader)
loads guest ELF segments; its host/loader execution location must itself be
permitted. We built this pinned source rather than taking Termux binaries.

| Path | Actual evidence | Remaining cost/gate |
| --- | --- | --- |
| Packaged bionic executable | Runs under target36 on owned Android16 x86_64 emulator; writable-copy direct exec returns EACCES | Does not prove PostgreSQL, Python extensions, Node or Moto |
| Packaged bionic PRoot + packaged loader | Executes the packaged and writable-copy bionic ELF controls under SELinux Enforcing | Debian/glibc syscalls, process trees, real services, resources and Moto still unproved |
| Fully Android-native runtime | The bionic syscall probe compiles for ARM64 and x86_64 and executes on x86_64 | No PostgreSQL/Python/Node native port built; requires real bionic-compatible dependencies and relocation, not manylinux wheels |

The PRoot candidate preserves the canonical Debian/glibc dependency strategy
without introducing separate application implementations. This is a provisional
engineering choice, not proof that the full runtime works. Native builds remain
an alternative if actual guest probes fail. Termux's
[package-building constraints](https://github.com/termux/termux-packages/wiki/Building-packages)
include application-prefix coupling; installed Termux packages cannot simply be
renamed and treated as an independently hosted runtime.

## Foundation and reproducible build

The probe application ID is `development.landmine.schemii.runtimeprobe`, with
min SDK29 and compile/target SDK36 (Android16). Main device ABI is `arm64-v8a`;
`x86_64` exists only for emulator diagnostics. The default app is debuggable;
`--non-debuggable` builds the same target36 probe with that flag disabled. It
has no boot receiver/service, uses credential-protected `files/probe-v1`, disallows
Android backups, and requests only Internet permission. This is not a release
manifest or a foreground-service implementation.

[toolchain.lock.json](../platforms/android/toolchain.lock.json) pins Google SDK
archive URLs, metadata checksums, independently retained SHA256 hashes, NDK
r30 `30.0.16248370`, platform36 revision2, build-tools36.0.0 and platform-tools37.0.3.
The initial downloaded repository metadata SHA256 is recorded. Google's
[SDK terms](https://developer.android.com/studio/terms) apply to the authorized
local bootstrap. License/source URLs and scope are retained in private cache
`licenses.json`. PRoot is GPL-2.0-or-later; talloc2.5.0 is LGPL-3.0-or-later.
Their pinned upstream source and license notices accompany the build recipe;
release distribution/source/relink obligations remain a release-owner gate.

Java/javac27 and Python3.14 were preinstalled on this workstation; build provenance
records their exact version output and resolved executable SHA256. A plain Java
Activity needs no Gradle, AGP, Maven dependencies or system-wide installation:
`aapt2`, `javac` with Android's lambda stubs, `d8`, `zipalign` and `apksigner` build
and verify the APK. Debug signing uses a private generated PKCS12 and password
file under the owned cache, never a repository/APK payload or command value.
Release signing remains coordinator-owned.

```bash
python3 platforms/android/build.py bootstrap
python3 platforms/android/build.py build --build-id arm64-UNIQUE
# Optional additional emulator ABI, explicitly selected:
python3 platforms/android/build.py build --build-id dual-UNIQUE \
  --abi arm64-v8a --abi x86_64
```

Default private cache/lease: `$HOME/.cache/schemii-apk264/`, resource
`apk264:private-sdk-ndk-cache`; initial owner/workspace is in `lease.json`.
A nonblocking lock serializes bootstrap/build. Use a fresh build ID for every
attempt. Failed inputs, builds and logs are retained; no retries or source
resets conceal failures. Downloads verify SHA256 before extraction, Unix links
are preserved, and existing unverified SDK directories are refused.
The initial bootstrap exposed and corrected lost Unix links, talloc's absent
bionic C23 wipe primitive, PRoot's out-of-tree Makefile issue and missing
`string.h`, and Android Java lambda stubs. The talloc shim uses volatile writes
for explicit wiping; one source-visible PRoot include patch is applied to a
private per-build copy. No core application source is patched.

## Shared-source consumer and provenance

The normal build loads the integrated canonical `scripts/check_runtime_parity.py`
APIs from accepted #271. Earlier diagnostic builds used
`--parity-helper /absolute/path` to identify an external immutable snapshot.
That option remains for reproducing their original labels. Native provenance records
the helper head, helper SHA256 and its committed-file-selector SHA256.
An immutable coordinator snapshot with adjacent `provenance.json` is supported;
changed snapshot bytes or dirty helper/selector checkouts are refused, and both
inputs are checked again before the receipt. This external-path bridge is not
integrated-source acceptance.

The build selects an independent full Git HEAD, calls `apk_source_manifest`,
stages each listed file from `git show HEAD:path`, and calls `check_apk_sources`.
Any exception fails the build. Shared source is never cloned into the Android
subtree. The source closure is canonical `src/`, selected `ai/prototype/` and
`dev/postgres/`, runtime contract and its dependency inputs.

| APK path | Contents |
| --- | --- |
| `assets/shared/` | Exact repository-relative committed shared bytes only |
| `assets/runtime/shared-source.json` | #265 record, outside shared directory |
| `assets/runtime/native-provenance.json` | SDK/ABI/native hashes, toolchain/adapter/helper identities, explicit pending gates |
| `assets/runtime/licenses/` | Native component notices |
| `lib/ABI/libproot.so` | Bionic host executable |
| `lib/ABI/libproot_loader.so` | Freestanding PRoot loader |
| `lib/ABI/libruntime_probe.so` | Real execution/ownership controls |

After signing, the build verifies signatures and the actual manifest's ID/min/
target, extracts the actual signed APK and revalidates shared bytes. Native
readback verifies inventory, SHA256, ELF ABI/type, no writable/executable LOAD,
and >=16KiB LOAD alignment. Google documents
[16KiB requirements](https://developer.android.com/guide/practices/page-sizes);
ELF alignment alone is not runtime compatibility. Compressed/extracted libraries
avoid an uncompressed ZIP-mmap alignment claim. Runtime data/guest extensions
must also be checked once packaged.

```bash
python3 -m unittest discover -s platforms/android/tests -v
python3 platforms/android/verify_apk.py /private/build/schemii-runtime-probe.apk \
  --expected-commit FULL_SOURCE_SHA
python3 platforms/android/check_package_controls.py /private/build/schemii-runtime-probe.apk \
  --expected-commit FULL_SOURCE_SHA
git diff --check
```

The counterexamples mutate copies of a real APK: omitted/changed shared source
and omitted/changed native code must fail byte validation. Their deliberately
invalidated signatures do not establish installation or device acceptance.
Both CLIs accept the same temporary `--parity-helper` bridge when needed.


## Real guest payload build and diagnostics

Current canonical consumer builds use the default in-checkout
`scripts/check_runtime_parity.py` and its committed `build-android-release.py`
selector, integrated from accepted main8fa3. The independently selected checkout
HEAD is passed to both committed staging and actual signed-APK extraction checks.
Both helper/selector digests and checkout revision are bound before/after build;
`staging-readback.json`, `package-readback.json` and
`apk-extracted-readback.json` retain their separate results. Earlier8f snapshot
packages and runs keep their original identities and are not current-source proof.

`--native-donor /private/prior-build/receipt.json` can reuse only the three base
bionic executable bytes per ABI. Reuse also requires
`--native-donor-receipt-sha256 EXPECTED_SHA256`, selected independently by the
coordinator before donor admission. Missing, malformed or mismatched anchors fail
closed before any executable is copied. The expected and observed receipt digests
are recorded in the new APK provenance. This anchors the immutable product; it
is not compiler attestation. It verifies the donor APK/provenance/file hashes,
committed clean source, native recipe/ABI mapping, native source files and unchanged
toolchain lock. Peer libraries and runtime acceptance are not inherited. Current
canonical static packages omit old guest archives: their recorded full contract
digest differs from the integrated contract. Those archives/manifests remain
preserved and are not relabeled or re-resolved merely for source packaging proof.

```bash
python3 platforms/android/build.py build --build-id canonical-UNIQUE \
  --abi arm64-v8a --abi x86_64 --non-debuggable \
  --native-donor /private/prior-build/receipt.json \
  --native-donor-receipt-sha256 INDEPENDENT_EXPECTED_RECEIPT_SHA256
python3 platforms/android/check_package_controls.py /private/canonical-UNIQUE/schemii-runtime-probe.apk \
  --expected-commit INDEPENDENT_FULL_GIT_SHA --source-only
```

No `--parity-helper` override is used for this canonical gate. Its counterexamples
remove/change actual shared bytes or the source record, and also provide an
independently wrong expected source. Any validation exception fails closed.

The native guest is APK data, shipped at build time and verified before use. It
has no runtime executable-download path and does not use a writable Termux
installation. `runtime/payload.lock.json` binds exact per-ABI Debian packages,
Python3.13 wheels, Node and the common contract. Debian release signatures were
verified with the keyring from the pinned base; each apt-selected archive matches
its authenticated metadata SHA256. PyPI wheel hashes and Node official checksums
are frozen, and ARM Node must match the existing contract digest. `npm ci` uses
the canonical lock/integrities with lifecycle scripts disabled. Actual Pi module
and protocol execution are separate probes.

```bash
python3 platforms/android/build.py bootstrap
python3 platforms/android/runtime/build_payload.py assemble \
  --abi arm64-v8a --build-id arm64-PAYLOAD_UNIQUE
python3 platforms/android/build.py build --build-id arm64-APK_UNIQUE \
  --abi arm64-v8a --guest-payload /private/cache/payloads/arm64-PAYLOAD_UNIQUE
```

Use x86_64 for the owned emulator's native diagnostic path. The exact x86 base
comes from the same official Debian image build as the canonical ARM base and is
recorded as an architecture-specific diagnostic exception. `assemble` consumes
committed pins and fails on drift; it does not resolve newer dependencies.
Maintainers may explicitly run `prepare-resolver`, then `resolve` in a fresh
owned cache to propose reviewed pin updates. The build-only apt sandbox downloads
packages but never installs host packages or starts PostgreSQL. Existing resolver
or payload directories are preserved/refused. Resolver/compiler failures remain
in original private logs.

Assets include `guest-ABI.zip`, an external `guest-ABI-manifest.json`, and the
source-bound `guest_probe.py`. A fresh extraction validates archive and per-file
hashes before use, writes all ordinary files before creating verified guest
symlinks, and keeps new database/state roots separate from code. Source bytes
come from the same validated `assets/shared/`; there is no second core clone in
the Android subtree. Build receipts refuse source/helper changes during a build.
Generated passwords, encryption/setup keys, cluster data and logs exist only in
private disposable app state, never these assets. Reproducible extracted code
copies may be reclaimed for emulator capacity while retaining original signed
APKs/manifests and every failed run's state/logs/reports.

The x86 guest exposed a real ABI boundary: Android app seccomp traps the legacy
x86 rename syscall (direct packaged control exits with SIGSYS); glibc/Python
rename under PRoot returned ENOSYS. An explicitly x86-only guest libc adapter
maps POSIX rename to atomic renameat with AT_FDCWD, like bionic. This preserves
atomic replacement, symlink and current-directory rename semantics; actual
Android conformance and PostgreSQL restart probes passed. The adapter is scoped
to guest processes via guest `/usr/bin/env`; the JVM and bionic PRoot never load
it. No core source or PostgreSQL binary is patched, no report-only exception is
swallowed, and the original failure remains. ARM64 has no legacy rename syscall
and does not load this diagnostic shim. Its compiled artifact requires only
GLIBC_2.2.5 and uses 16KiB LOAD alignment.

An offline audit of the assembled ARM64 payload found955 ELF files, all ARM64
and all with 16KiB-compatible LOAD layout; this is static evidence only. The
x86 diagnostic payload has956 correct-ABI ELF files, of which955 have 4KiB
layout; it is explicitly limited to the current 4KiB emulator. Packaged bionic
native libraries retain their stricter 16KiB checks. Native hardware/runtime
page compatibility remains a physical-device gate.

The guest probe refuses occupied configured endpoints before initialization;
uses real SCRAM PostgreSQL clusters and a NOSUPERUSER role owning a dedicated
app database; exercises native operations and canonical migrations; and preserves
state on failure. Both normal service stop and helper deadlines use bounded
SIGTERM/SIGINT. There is no implicit subprocess-timeout SIGKILL, no database
reset/reinitialization on a failed run, and no repeated signals hiding a retained
child. A retained child or unfinished PRoot runtime remains an explicit failure.
These direct-child diagnostic controls do not prove descendant/owner-loss cleanup.

The producer manifest also binds its own source, the rename adapter, payload lock,
common contract and both canonical npm inputs before/after assembly. A build
rejects reusable payloads when these inputs drift. The x86-only glibc adapter
records the exact host clang executable digest/version; that observed diagnostic
compiler is separate from the pinned ARM64 Android NDK toolchain. The installer
is the verified Node22 distribution's npm, not an ambient npm executable.

## Optional peer diagnostics and non-debuggable execution

Process controls can be selected by immutable peer Git head. `peer_inputs.py`
reads committed bytes with `git show`, verifies the handoff identities, and
compiles snapshots inside this build's private output without modifying the peer
checkout. The6242 process candidate runs one case per bounded invocation, in
native and PRoot modes. A successful control includes deliberately incomplete
unknown/orphan/timeout cases; it does not establish scanner completeness or a
production owner-loss guarantee. Original owner-control remains present/failing.

```bash
python3 platforms/android/build.py build --build-id closure-nondebug-UNIQUE \
  --abi x86_64 --guest-payload /private/cache/payloads/x86-PAYLOAD_UNIQUE \
  --process-checkout /private/peer-checkout \
  --process-head 6242afe5fec7adfed4b67336de804b99e8b664a8 \
  --non-debuggable --log-report \
  --parity-helper /private/immutable-helper/scripts/check_runtime_parity.py
```

The optional network input uses `--network-checkout` and `--network-handoff`:
source hashes, committed head, dependency contract, clean build receipt and
provided ELF digest must all agree. **Do not use the cc7fffc5 handoff for further
execution or acceptance.** Independent review found a mandatory socket-binding
capability bypass. Historical callback reports remain retained, not accepted.
The owner approved standard SDK HTTPS bootstrap DNS; selected-network binding,
socket registration and revocation are still mandatory. Corrected immutable
source/library handoff and independent review are pending with owner268.

The network consumer also requires each immutable handoff's build-receipt digest,
ABI and compiled bridge digest. It derives SHA256 from the committed `bridge.c`
followed by `bridge.h`, checks both handoff and receipt, and records this expected
identity in APK provenance. A future actual probe must return that exact
`native_bridge_sha256`; missing/mismatching values mark the report failed while
preserving its original contents. Byte/packaging controls do not lift the execution
hold. Initial5ff15825 is an ancestor of heldcc7 and cannot substitute for the
corrected, independently reviewed input.

`--log-report` is explicit, default-off diagnostic export for the owned disposable
emulator. It emits only the structured result, including process identities,
as base64 chunks to Android's `SchemiiProbe` tag. Keep those logs private. Capture
`adb -s OWNED_SERIAL logcat -v raw -s SchemiiProbe:I '*:S'` before launch, group
`RUN_UUID:INDEX/COUNT:BASE64` lines by fresh run UUID, require every index once,
decode JSON, and verify source commit and native-provenance SHA256 against the
exact signed APK. Missing/truncated/stale chunks are incomplete evidence. This
requires neither root, `run-as`, instrumentation nor an exported data provider.
Chunks are paced because liblog can drop a large nonblocking-socket burst. Android
accessibility also truncates this large TextView at100000 characters; a UI dump
does not repair a missing report tail. Both original transport failures remain.
To re-export an existing atomic report without rerunning any probe, launch the
diagnostic build with `--ez export_report_only true --es expected_native_provenance
ORIGINAL_SHA256`. This mode requires explicit log export in the build, checks the
saved identity, reads only that report and never starts/stops services or rewrites
state. Collect fresh log lines, validate against the **original** signed APK and
retain its original run/source identity; re-export does not retest a newer head.
It is not a release logging policy. Installation/replacement is permitted only
after the owned runtime is quiescent; use its no-retained-runtime/children report
plus live process witnesses. When report transport fails, retain the failure and
record an independent same-UID process inventory before replacing the diagnostic
APK; refuse any live guest/service or unknown ownership. This is an emulator
operation safeguard, not production descendant-completeness acceptance. Never
force-stop a retained PostgreSQL runtime to obtain a pass.

The real guest runs Pi's canonical handler and Python PiClient. Its HTTP probe
starts `python -m uvicorn schemii.main:app` on descriptor port18000 with lifespan
enabled while Pi and both clusters are live. It checks durable readiness, enabled
authentication, denial of an anonymous session, then graceful lifespan completion
and listener closure. This internal transport is the eventual proxy backend,
not a substitute for trusted tailnet HTTPS or full API/browser acceptance.
The pinned Uvicorn0.52.4 intentionally re-raises its captured SIGTERM after
shutdown; signal exit alone cannot pass. The probe records the actual status,
requires startup/shutdown-complete markers and listener closure. The original
non-debuggable failure from an incorrect exit-zero-only oracle is retained.

## Diagnostic collector contract

Each invocation now has a fresh private cwd beneath an attempt/mode/debug/run
root; its original output and atomic journal are separate siblings under
`reports/`. Packaged PRoot receives that same fresh cwd through `-w`; its absolute
packaged argv0 stays intact. The journal binds run/source/native provenance,
installed APK and signing-certificate hashes, API/debug/SELinux/build labels,
wrapper owner birth/boot identity, command, deadline and monotonic elapsed time.
Cwd witnesses compare filesystem device/inode identity: Android can expose the
same directory as `/data/user/0/...` to Java and `/data/data/...` to native code.
Both path labels remain in the report; no string-prefix alias assumption admits
a different directory. The first literal-path oracle failure is preserved.

The collector reads complete UTF-8 output through65536 bytes. Overflow, invalid
or incomplete JSON, or trailing data is explicit failure with no accepted prefix;
the original output file is retained. Direct-child exit does not establish
descendant cleanup. Android's public `java.lang.Process` API exposes no PID; the
collector retains its actual Process object and never adopts a numeric PID,
same-UID process, name or group as ownership.

Deadline/cancellation sends no automatic signal and never discards an uncertain
owner. It retains the Process reference, cwd/output/journal and uncertainty,
fences later launches and records a concrete reconciliation action. Activity
destruction synchronizes cancellation with the same completion gate used to
accept output and release the captured owner. File reads, JSON handling and
journal I/O run outside that gate. The durable journal is explicitly provisional:
it contains no accepted output and retains ownership. Only after those operations
finish does one short, synchronized in-memory commit accept the candidate output
and release the direct-owner reference. The final run report records that commit;
a provisional journal alone never proves it. Cancellation or interruption during
read or journal preparation prevents the commit, writes a retained/cancelled
outcome where possible and keeps the launch fence. Escaping completion or journal
I/O failures leave the last conservative journal and captured owner intact.
Caught output-read, parse and overflow failures instead commit a rejected-output
result and release the exited direct-owner reference after journal preparation
succeeds. No lock-held file operation
postpones cancellation through slow report handling. Destruction interrupts
collection rather than killing owners. This is fail-closed
diagnostic behavior, not a production owner-loss solution. If cleanup cannot be
proved, stop and preserve the owned state for coordinator review.

The opt-in `--wrapper-controls-only --log-report` build mode excludes guests,
network and peer process campaigns. Its new source-bound finite native fixture
checks two native fresh cwds and one PRoot cwd, exact64KiB,65537-byte overflow,
partial/trailing JSON refusal, deadline, interruption and two latch-controlled
late cancellations inside the actual read and journal handling paths. The original
nine observations retain their original906 identity. The updated eleven-control
campaign adds those two boundaries; host Java controls independently exercise the
production gate during read/journal cancellation, lifecycle closing and thread
interruption, plus uncontested completion. The cancellation controls retain
ownership at collection failure and refuse another launch. A separately labeled
fixture oracle then waits for its known one-child finite program to finish itself
and specifically reap that child; only this source-bound fixture reference can be
released by that oracle. It sends no signals. Original timeout/cancellation
failure labels and generic descendant uncertainty remain unchanged. This does
not prove arbitrary descendants, owner death, PostgreSQL cleanup or13e behavior.

`runtime/check_wrapper_report.py` validates the original Android report against
independently selected signed APK/source/signing-certificate inputs. It hashes
actual packaged fixture bytes, verifies their ELF ABI/source identity and checks
observed executable identity. Finite cleanup is recomputed from observed owner
exit, parent source, exact child birth, specific reaping, status/count and child
done witnesses. Contradictory exit, cancellation, timeout, ownership or output
fields fail even when a summary claims success. Fixture
success remains `stack_acceptance=false`. The original68 polling reports retain
their old shared-cwd/32KiB/deadline limitations and original source labels.

## Bounded13e/cde diagnostic integration

The separate `--integration-probes` profile admits immutable subreaper
`13e04d8fd9c65d206967cfc50b60f4571bba5f08` and network
`cde504d128d2a1ba8e3f50b02028db7d93395529`. Handoff digests are independently
selected in the adapter. Every source hash is checked against committed Git;
the full38-file network inventory, original build receipt and exact ABI-specific
library digests are retained. Only the new subreaper C plus its unchanged
observer/header are compiled with NDK30/minAPI29; the APK keeps compile/target36.
The admitted Go libraries are reused without rebuilding their SDK/dependencies.
No peer source is edited or integrated into this branch.

```bash
python3 platforms/android/build.py build --build-id integration-UNIQUE \
  --abi arm64-v8a --abi x86_64 --integration-probes --log-report \
  --native-donor /private/original021/receipt.json \
  --native-donor-receipt-sha256 INDEPENDENT_EXPECTED_RECEIPT_SHA256 \
  --network-handoff /private/reviewed-cde/integration-handoff-cde504d1.json \
  --network-checkout /readonly/network-checkout \
  --subreaper-handoff /private/reviewed13e/integration-handoff-13e04d8f.json \
  --subreaper-checkout /readonly/process-checkout
# Add --non-debuggable for the independently labeled nondebug package.
```

The profile excludes guests, wrapper-only controls, old6242 polling and legacy
owner-control probes. It first calls the admitted `NetworkProbe.run(Context)` on
the existing worker. A normal API return follows reviewed close/stop/detach;
exceptions retain a native-operation uncertainty record and fence later launches.
The underlay report preserves actual framework DNS, selected validated non-VPN
default generation, TCP/UDP binding, SO_COOKIE prerequisite and public-root TLS
observations. It starts no node and attempts no enrollment, ACME or provider
operation. Normal cancellation/close is distinct from tested network transitions
or actual Activity destruction; standard SDK bootstrap behavior is unchanged.
This callback probe does not establish tailnet/private-ingress readiness.

Each of16 subreaper cases then runs once natively and once through the actual
packaged PRoot/loader. Each invocation has a fresh private empty cwd, separate
report and PRoot scratch,45000ms wait budget and complete64KiB JSON bound. The
component Git/source/compiler/native API and library identities stay distinct
from the adapter revision. Only five positive cases may complete while their
dedicated owner lives. Eleven refusal/timeout/owner-loss cases stay incomplete;
`control_pass` is not `candidate_complete`. Nonzero reports remain evidence.

The collector requires the exact source/case/ABI and finite raw cleanup contract
before release. It also reads only source-bound known fixture births after the
oracle finishes, recording absent or different-birth observations; permission
errors, live/zombie births or unknown identity escape with the Process retained
and the launch fence intact. These are read-only checks, never PID adoption or
signals. The original reviewed outer oracle alone releases/reaps its finite test
tree. Owner loss explicitly preserves a surviving orphan and oracle intervention;
that intervention never becomes candidate or production cleanup. If Android or
PRoot defeats this contract, preserve the blocked report/state and stop. No
tracer redesign, second tracer, shared supervisor change or kernel workaround is
part of this diagnostic.

Actual signed readback checks all449 canonical shared bytes with the default
in-checkout helper/selector and independently expected adapter SHA, exact native
inventory/ELF ABI/alignment and actual manifest permissions/debug/SDK. The new
report checker additionally requires independently selected APK/source/signer
and debug profile:

```bash
python3 platforms/android/runtime/check_integration_report.py /private/report.json \
  --apk /private/integration-UNIQUE/schemii-runtime-probe.apk \
  --expected-commit INDEPENDENT_ADAPTER_SHA --signer-sha256 EXPECTED_CERT_SHA256 \
  --aapt2 /private/pinned-sdk/android-16/aapt2 --debuggable
```

A valid measurement can still be blocked or show failed/incomplete candidate
controls. Neither ELF alignment nor emulator execution proves native Moto ARM64,
physical16KiB pages, general descendants/owner death, PostgreSQL shutdown, full
backend, production FGS, CE/DE/pre-PIN, trusted node certificates or release
acceptance. Original53d eleven-control evidence and all old reports retain their
original identities; this new profile never relabels or replays them.

## Host/runtime interface and coexistence

[runtime-interface.json](../platforms/android/runtime-interface.json) is explicitly
provisional and `ready=false`. The immutable descriptor consists of source,
contract and native identities, ABI/SDK, packaged executable/loader paths,
selected guest/source/data roots and mechanism. Host266/storage267 supply selected CE/DE
state/control roots and stable state identity. There is no silent CE fallback,
empty-state initialization at unlock or executable download. Default is CE;
opt-in DE and pre-unlock availability are not implemented here.

Proposed disposable endpoint profile: `127.0.0.1` only; metadata15433,
target15434, Pi14097, app18000, ingress18443. Before any data initialization,
refuse occupied endpoints; never choose another port silently. The same profile
must feed both cluster configurations, DSNs, metadata fencing, Pi/API/ingress,
tailnet backend and authenticated probes. These are reserved proposal values,
**not yet consumed by the shared supervisor**. Existing Termux defaults
5433/5434/4097/8000/8001 and all its data/listeners remain unchanged.

Host266 intake is consumed: reuse attached shared Python supervisor `run`, bounded
`stop/status`, exact process-birth identity and existing lock/lifespan/SQL cleanup.
Do not copy service sequencing or migrations into native UI. No production
supervisor descriptor is exported until the actual runtime/ownership gates pass.

## Observed failure and remaining acceptance

On the owned API36 x86_64 emulator, native and PRoot owner-control read child
`/proc/PID/stat` birth ticks, boot ID, exe, cwd and cmdline and stop that child
with graceful SIGTERM. `/proc/PID/task/PID/children` is unavailable. The control
returns nonzero. Existing shared `captured_group_members` and `direct_children`
depend on that interface: descendant cleanup is **unproved**, not silently
replaced with PID-only signalling. A coordinator-owned shared adaptation or a
proven permitted discovery mechanism is required; Moto behavior remains unknown.

Still required before runtime264 or host integration can be accepted:

- Package and run exact PG17 `initdb`, both independent clusters on distinct
  endpoints, roles/fencing, shared locks, real SQL, fsync/WAL persistence and
  graceful stop/recovery on the actual Moto. The owned x86_64 API36 emulator now
  runs both PG17.12 clusters: real initdb/start, distinct system identifiers,
  pgcrypto, NOSUPERUSER roles, advisory-lock isolation and fsync-enabled committed
  rows surviving graceful restart passed. This is diagnostic 4KiB/x86 evidence,
  not native ARM64, abrupt-crash, owner-loss or complete SQL acceptance.
- Package canonical Python/native dependency pins; actually import psycopg,
  pydantic-core, cryptography, pglast and the shared application on Moto. The
  x86_64 guest verifies all20 canonical distribution versions and performs
  explicit native imports plus psycopg/libpq, Fernet encryption, pglast parsing
  and pydantic-core JSON parsing operations.
  Shared app import correctly required explicit authenticated mode, app-owned
  metadata storage and first-account setup-token configuration; original denials
  remain in private evidence. No wheel listing substitutes for execution.
- Package pinned Node22/Pi and existing Go component; run their real execution
  and protocol probes. The x86 guest now executes Node22.22.2/Pi0.87.1 canonical
  health/models and Python-client transport. Existing cc7 Go callback results
  are held after independent NO-GO; no dependency-version substitution or
  network acceptance is authorized.
- Prove descendant/process-group ownership, owner loss and bounded whole-stack
  cleanup, including PostgreSQL preserved on uncertain shutdown.
- ARM64 native execution on Moto Android16 and applicable page size; an emulator
  advertising ARM64/native translation is not that proof.
- Coordinator-scheduled tailnet/trusted HTTPS, six-service readiness, physical
  install, pre-PIN boot with retained PIN, full local acceptance and independent
  exact-head review. No phone or shared deployment was changed by this work.

Probe reports carry a fresh run ID, timestamp, packaged source head and ABI
inventory. Launch exceptions are persisted atomically, so a failed translated
launch cannot leave an old success report as current evidence. ARM64 translation
is diagnostic only and may reject direct executable launches.

Private build logs/receipts and emulator reports live in the owned cache.
`./ci.sh --plan --base origin/main` selects full acceptance for these new files;
coordinator schedules it. Focused native/package controls do not waive that plan.
GitHub Actions stays disabled. Keep issue264 and its draft PR open with Refs to
264/263; no release or merge is authorized here.
