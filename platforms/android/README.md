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
