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
toolchain lock. Peer libraries and runtime acceptance are not inherited. Earlier
canonical static packages omitted old guest archives: their recorded full contract
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

## Source-bound packaged shared runtime (264 continuation)

`build_payload.py assemble` assembles real pinned Debian PG17/Python3.13,
all canonical wheels (including native extensions), Node22 and locked Pi modules.
It does not launch PostgreSQL, Python guest code, Pi, PRoot or an application.
Only the pinned host Node/npm installer and build tools run. The explicit
`assemble --packaged-shared-runtime` mode additionally includes the frozen
nginx1.26.3-3+deb13u10 closure (ten extra packages per ABI); default diagnostic
assembly retains its original Debian package selection. Downloads and
assembly use a fresh private cache; no Termux state, credentials, database,
whole-checkout copy or previous guest archive is admitted.

Assembly now records the canonical helper's exact committed shared-source
manifest, helper/selector hashes, package/wheel/Node provenance, installed
Python/npm inventories and every ELF's machine, interpreter, LOAD layout,
DT_NEEDED and resolved library paths. Resolution honors guest symlinks and
inherited DT_RPATH versus direct-only DT_RUNPATH. Missing interpreters, native
libraries, required PG tools, wheels, Pi modules or runtime Python paths fail.
The immutable `/opt/schemii-venv/bin/python` path uses the pinned interpreter,
verified wheels and `/shared/src` through a `.pth` file; it does not fork or
install a second copy of the application. The existing `/opt/python-packages`
diagnostic path is preserved. Assembly and APK packaging reject changed producer,
dependency, contract, shared-source or ABI identities.

The explicit `--packaged-shared-runtime` build profile requires a clean exact
adapter commit, nondebug manifest, independently anchored native donor and one
independently anchored guest manifest **for every selected APK ABI**. Payload
arguments and their manifest anchors have matching order. It excludes boot,
wrapper, process and network diagnostic component combinations. Ordinary existing
profiles retain their separate behavior. The new profile checks complete guest
ZIP bytes/inventory/modes and native/dependency contracts at admission and again
from the actual signed APK extraction, alongside the canonical shared helper
and existing bionic ABI/16KiB/signature checks. A manifest rewritten together
with a substituted archive cannot satisfy its original independent anchor.

```bash
CACHE="$HOME/.cache/schemii-apk264-packaged-runtime"
python3 platforms/android/runtime/build_payload.py --cache "$CACHE" assemble \
  --abi x86_64 --build-id shared-x86-UNIQUE --packaged-shared-runtime
# Select the manifest digest independently from the retained assembly handoff.
python3 platforms/android/build.py --cache "$CACHE" build \
  --build-id shared-x86-APK_UNIQUE --abi x86_64 --non-debuggable \
  --packaged-shared-runtime --expected-adapter-commit FULL_COMMITTED_HEAD \
  --guest-payload "$CACHE/payloads/shared-x86-UNIQUE" \
  --guest-manifest-sha256 INDEPENDENT_ASSEMBLY_MANIFEST_SHA256 \
  --native-donor /private/original-build/receipt.json \
  --native-donor-receipt-sha256 INDEPENDENT_ORIGINAL_RECEIPT_SHA256
```

The continuation's actual build is explicitly x86_64/4KiB diagnostic packaging.
It makes **no dual-ABI complete-guest claim**. ARM64 assembly additionally enforces
16KiB-compatible guest LOAD layout; bionic APK native libraries always retain
16KiB gates. Neither static layout nor a signed artifact proves device execution.
Every new payload and packaged readback says `execution=NOT_RUN`,
`readiness=PENDING`; `stack_acceptance=false` remains authoritative.

The following exact launch interfaces still require their owners before this
package can become a safely hosted shared server:

- `apk_source_manifest` and `assets/shared/` keep their shared-only semantics.
  The separate `apk_runtime_source_manifest` composes that exact record with
  four committed support leaves: `dev/android/runtime.py`, `recovery.py`,
  `nginx.conf.in` and `dev/schemii.toml`. Their additional bytes live at
  `assets/runtime-source/` with repository-relative paths;
  `assets/runtime/packaged-source.json` records the complete union. Staging and
  signed extraction call `check_apk_runtime_sources` against the independent
  commit. Missing, extra, changed, linked or rebound inputs fail. The future
  host must verify and combine the two projections into the single `/shared`
  source mount; the existing diagnostic Activity does not extract support files.
- `/usr/sbin/nginx` now comes from frozen authenticated Debian metadata in
  `payload.lock.json:abi.*.packaged_runtime_packages`. Its ten-package extension
  includes the declared iproute2 closure, without executing maintainer scripts.
  Every new package binds its signed-release/index/stanza and archive SHA256.
  Original package/wheel/Node pins and the runtime contract remain unchanged.
  The retained original InRelease files were independently verified using the
  keyring from the pinned base; decompressed package indexes match their signed
  SHA256 and size. All32 selected dependency constraints per ABI were checked.
  This is package provenance and ELF/loader evidence, not an nginx runtime pass.
- The same Runtime hardcodes Termux endpoints5433/5434/4097/8000/8001. Its shared
  parameterization must consume the existing APK interface's loopback-only
  15433/15434/14097/18000/18443 with unchanged service identities and safety rules.
- `dev/android/recovery.py:_runtime_fingerprint` requires the ARM64-specific
  `/opt/schemii-runtime.json` installed-runtime identity. This x86 diagnostic
  build deliberately does not forge that identity. Physical backup/restore
  remains unavailable pending the reviewed installation/recovery adapter.
- Host266 must supply selected CE/DE source/state/control roots and the reviewed
  owner/FD/journal/boot bridge. Runtime's `captured_group_members` and
  `direct_children` depend on `/proc/PID/task/PID/children`, unavailable in prior
  API36 diagnostics. No PID-only ownership, guessed host, readiness stub or
  Java service ordering is supplied here.
- Later operator entrypoints are the **shared** runtime's attached `run`, `stop`
  and `status` through the authorized launcher/host boundary, after those adapters
  are reviewed. The existing APK Activity still launches its disposable guest
  diagnostic if operated; this packaging profile does not turn it into a server
  or grant installation/execution authority. Do not install/run it as acceptance.

Private source snapshots, command logs, original failures, donor-input inventory,
assembly manifests and signed readbacks are retained in the assignment's private
artifact directory and new cache. Full local CI, actual two-cluster/native/Pi/API
execution, general owner-loss/recovery, FGS, networking, ARM64/Moto/16KiB devices,
signing/update and release acceptance remain coordinator-owned and pending.

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

## Connected installed-source boundary (issue 266)

The packaged shared-runtime build now includes a private foreground-service
adapter, a native directory/control lease and compiled source/native/payload
expectations. This is a **source refusal boundary**, not an executable server
release. Start verifies the installed nondebuggable target36 package and public
signer pin, composes the exact committed `assets/shared/` and
`assets/runtime-source/` inventories into a fresh read-only source tree, selects
CE state/control roots, then requests native admission. A missing signer pin,
changed/missing/unknown/linked/aliased member, competing lease or unavailable
storage refuses. Partial trees and diagnostic failures are retained. A different
attempt never repairs or silently reuses a failed tree.

`build.py build --packaged-shared-runtime` accepts
`--connected-host-signer-sha256` for an independently supplied **public certificate
SHA256**, and checks that the eventual package signer agrees. Omitting it keeps
installed host admission closed. It is not a signing key, a C/A trust flag or
runtime execution authorization. Native owner libraries are new compiled leaves;
PRoot donor reuse remains separate and source-bound. No new APK build/install or
signing has been performed for this source change.

The canonical shared `Runtime` has closed `termux` and `apk` endpoint profiles.
Termux retains metadata5433/target5434/Pi4097/app8000/ingress8001. APK selects
15433/15434/14097/18000/18443, loopback only. Shared DSNs, role/bootstrap helpers,
metadata fencing, Pi listener/URL, app bind/readiness, target credentials and nginx
rendering use the same profile. Conflicts refuse before secrets/initdb/service
initialization; no alternate ports. `EndpointProfile.backend` is the selected
loopback upstream for the separately owned native tailnet integration; that peer
is not wired or accepted by this source slice.

The current production comparator still has no source-backed C/A producer.
The native control worker therefore returns UNKNOWN/refusal, never initializes
or advances the accepted single-run journal, never forks a managed process, and
never grants the Python launch consumer. APK `run`, service/helper launch,
external stop and recovery reject Termux `/proc/task/children`, process-group
and stale-pidfile policy. Native status and Stop remain responsive while its
observer is pending; the selected lease cannot be replaced. No journal ACK,
CLEAN_STOPPED, complete actor inventory, graceful whole-stack stop, healthy
Stop/Start, kernel transition or PostgreSQL recovery is claimed here.

The concrete next dependency is a reviewed native caller/custody producer and
pinned PRoot interface covering the child-birth/export gap, traced tasks,
sysvipc double-fork helpers, conditional F2FS helpers, exact real-parent waits and
flow closure. Live export/ACK or direct tracer exit cannot close that gap.
Extending journal actor/terminal/later-run/recovery schemas without those actual
producers would fabricate authority, so those changes remain pending. There is
no public flag, environment value, signer pin or test constructor to unlock it.
The experimental recovery fallback remains a **user-performed**, separately
verified new kernel boot and actual existing-only two-cluster recovery. The
current x86 guest is not given the Termux ARM64 physical recovery identity.
Final safe recovery without reboot remains open under issues263/266.

Control diagnostics use fixed scalar fields and private essential append-only
records, bounded at16MiB with pressure refusal. Their snapshot exporter preserves
exact bytes/length/SHA256 and marks unknown crash loss and incomplete run closure.
It exports only this control scope; it is not the full promised actor/service
support bundle. No stdout, commands, credentials, DSNs, UUIDs, admission tokens,
private origins or user operations enter those events. Android FGS eligibility,
actual selected-storage durability/DE/pre-PIN, native peer/network fencing,
complete correlated diagnostics, both-PG SQL/integrity, device and release gates
remain separately pending. No Direct Boot receiver is added.

### Finite native actor/FD source producer (#266, refs #263)

The managed PRoot source recipe now applies `native/proot-actor-channel.patch`
against immutable upstream `52d65a4dc140ca554d241a1438ddd4f6b00c9813`.
`payload.lock.json.actor_producer` binds the eight consumed donor leaves (including
fingerprinted, CLI-disabled ashmem), the patch, producer C/header and fixed wire
contract. Build preparation checks Git bytes and writes only a build copy. Old
native donors do not match this recipe; old APK/static evidence is not current.
This is source instrumentation with finite host controls, not Android proof.

`native/host/actor-channel-v1.json` is the cross-language byte/FD contract:
224-byte big-endian private seqpackets, strict per-direction sequence starting at
one, source/contract/run/generation/profile and owner/task/process/creator
incarnations. All reserved bytes are zero. Unexpected ancillary data is refused;
received SCM_RIGHTS descriptors are closed once, even on malformed frames.
Nonblocking pressure, loss, wrap, bad sequence/binding/incarnation and exhausted
128-slot tables latch uncertainty. No reconnect, eviction of unresolved evidence,
replacement owner or positive ACK erases it. Records contain fixed scalar facts,
no raw boot identity, pathname, credentials, journal handles or operation data.
No telemetry persistence/export or durable journal protocol is introduced here.

The actual native owner reserves a separate socketpair before its worker starts,
while retaining its sole selected-directory lease and responsive Stop flag. The
pair is deliberately unexported. Java calls JNI scalar methods; the native
internal pair carries four native-endian uint32 fields. Python's FD3 refusal
adapter uses 16-byte big-endian SCH1 frames (`struct.Struct("!4sIII")`), while
the actor channel uses 224-byte big-endian SCA1 frames. These are **disconnected
protocols**. A future actual native custodian must create/transfer only the
designated guest lease and perform an
explicit versioned conversion after genuine C/A and durable admission. No parsed
JSON, Java flag, signer/UID, environment FD number or `ac_proot_bind` source fixture
provides C/A or changes production admission. Managed executable `main` refuses
before parsing configuration or reaching any F2FS/helper/root launch path; no
public CLI or environment option attaches the in-memory source producer.

Managed `clone` refuses `CLONE_UNTRACED` with **EPERM before kernel resume**;
normal legacy fork/vfork/clone/thread paths retain existing translation. Every
managed `clone3` form returns **ENOSYS**, without reading mutable guest arguments,
so libc may fall back to legacy clone. Safe scratch/translation custody for exact
kernel clone3 bytes remains a later requirement. No namespace/untraced flag is
silently removed by the new policy. Existing namespace emulation is unchanged.

Root/native births publish an intent before fork and a correlated failure or
observed PID result afterward. A send failure after the real birth retains the
live slot and unresolved intent. Guest entry intents bind attach-child identity;
child stops arriving before creator events are held in a bounded local table and
exported only after attachment. Lost/truncated chronology remains UNKNOWN.
Direct real-parent helper waits are dispatched **before** guest Tracee creation.
The SysVIPC managed helper is a direct child running the same production service
without the detached second fork/exec. Its pipe EOF is service shutdown input;
only its exact parent wait is terminal proof. F2FS closes unrelated FDs before its
fork-only work and uses a checked targeted wait. The ARM64 workaround uses checked
raw resume/targeted-wait wrappers; host compilation/extraction is not ARM64 target
execution. Raw failure fences further managed resume/birth and preserves uncertainty.

Guest ptrace terminal observation remains distinct from the guest's real-parent
wait: it retains an unresolved wait slot. Nonleader exec records the remap and
retains displaced same-process task waits, refusing cross-process identity
collisions with ESTALE rather than manufacturing terminal evidence. Complete task retirement and guest real-parent
wait consumption remain integration gates. `ac_highwater` is only a local finite
actor observation: no pending intent/early event/loss/live actor/task/helper/wait
and every sent record receipt-ACKed. It is never a durable journal ACK, CLEAN,
process-closure grant or network closure; #268 owns flow/epoch closure separately.

FD allowlists apply **before** TRACEME, noexec service/probe work and failed exec:
root/F2FS retain only standard streams; SysVIPC initially retains two service pipe
ends, then only its standard streams. The producer channel/private state are
closed in children; child copies cannot write the parent's sequence. Future
PRoot admission must additionally close the native owner's journal, selected
lease, Java control and other private descriptors before transfer, retaining only
its designated actor channel and intentional mount inputs. Future Python receives
only its own guest lease and must close it in subprocesses. There is no current
native-to-PRoot or Python FD transfer. Close operations are never retried after an
ambiguous result; first and secondary errors are retained in the producer boundary.

All production APK births, C/A and classifier/admission remain REFUSED. Connected
journal actor/terminal/new-run/retry transitions, actual source-bound transfer,
complete guest wait/exec closure, writer/receipt Stop integration, Android target
and real device/lifecycle/recovery/network acceptance remain pending independent
review. Final safe no-reboot recovery stays OPEN under #263/#266. Experimental
owner-loss recovery preserves/refuses until a USER-performed, separately verified
new kernel and guarded existing-only BOTH-PG recovery; no automatic reboot,
force-stop, adoption, forced cleanup, PID-file deletion or reset is introduced.
Healthy fully proved Stop/Start remains graceful.

The R293-02 successor controls link complete pinned PRoot translation units into
an isolated host fixture. Real native fork, vfork, clone, thread and nonleader
exec generate production ptrace events; the fixture waits only its captured child
and kernel-event TID identities. The nonleader-exec transport passes native
filesystem/loader syscalls through without guest translation, while birth entry,
attachment, remap and event/wait handling remain production code. Separate
explicit register/event injections cover early child stop/death and cross-process
exec collision. A collision now stops before PRoot mutates task identities.

The full event loop also consumes real SysVIPC service terminals before and after
native fixture events. Failed readiness/pipe EOF cannot replace a helper while
its exact parent wait remains outstanding. An observer fixture proves a native
helper can stay live in the actual service's blocking accept after its real
parent/tracer dies. Its later observer ptrace wait is deliberately **not** a
real-parent receipt and cannot clear the channel's retained loss or helper wait.
This test-only observer does not provide a production recovery/adoption path.

Managed terminal observations no longer trigger another resume of the terminated
task. Wait errors, invalid statuses, ECHILD with live local obligations, failed
initial stop/options, raw ARM64 mask/register/resume/wait/restore failures and
secondary cleanup errors preserve uncertainty. The whole raw workaround is
exercised under explicit host syscall injections and compiled by the actual
ARM64/API29 production link; it is not Android execution. Fixed scalar channel
records replace the managed raw workaround's memory-address/value debug dump.
Owner allocation/thread/close controls additionally retain primary and two close
errors through private diagnostic variants; existing JNI control remains unchanged.

Both 128-entry task and early-observation bounds, incarnation/sequence wrap,
critical intent/birth/export/receipt loss, descriptor allowlists and same-lease
pending observation/Stop are finite controls. No connected writer/receipt Stop
proof is implied: endpoints remain unexported and actual native C/A, guest-lease
conversion, journal transitions and authoritative guest real-parent waits remain
OPEN integration dependencies. The current draft's source milestone and fresh
production native link still require independent exact-head acceptance.
