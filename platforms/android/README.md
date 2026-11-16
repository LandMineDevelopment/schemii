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
