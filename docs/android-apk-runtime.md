# Android APK runtime feasibility

Issue [264](https://github.com/LandMineDevelopment/schemii/issues/264), under
[263](https://github.com/LandMineDevelopment/schemii/issues/263).
This is a disposable diagnostic APK foundation, **not a working Schemii server**.
Runtime acceptance and downstream host integration remain blocked. It does not
launch Termux, change the phone, start the desktop stack, enroll a node, or expose
an application listener. No account/database/key migration is implemented.

## Execution decision and measured boundary

The first candidate is deliberately APK-packaged PRoot, with bionic PRoot and
its freestanding loader installed as `lib*.so` in `ApplicationInfo.nativeLibraryDir`.
`extractNativeLibs=true` makes the installed executable location available.
The guest payload will be immutable, independently verified runtime input, with
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
`x86_64` exists only for emulator diagnostics. The app is deliberately debuggable,
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

The normal build loads the canonical `scripts/check_runtime_parity.py` APIs from
#265. Until its separate branch is integrated, `--parity-helper /absolute/path`
can identify that exact helper for a diagnostic build. Native provenance records
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

## Host/runtime interface and coexistence

[runtime-interface.json](../platforms/android/runtime-interface.json) is explicitly
provisional and `ready=false`. The immutable descriptor consists of source,
contract and native identities, ABI/SDK, packaged executable/loader paths,
selected guest/source/data roots and mechanism. Host267 supplies selected CE/DE
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
  graceful stop/recovery. No PostgreSQL payload or database success exists yet.
- Package canonical Python/native dependency pins; actually import psycopg,
  pydantic-core, cryptography, pglast and the shared application. No import success
  is inferred from a wheel listing or the bionic probe.
- Package pinned Node22/Pi and existing Go component; run their real execution
  and protocol probes. No dependency-version substitutions are authorized.
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
