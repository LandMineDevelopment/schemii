# Android APK shared-source parity

[Issue #265](https://github.com/LandMineDevelopment/schemii/issues/265), under
[feature #263](https://github.com/LandMineDevelopment/schemii/issues/263), extends
[the existing runtime contract](runtime-parity.md). This first slice delivers
shared-source validation helpers and stronger PostgreSQL configuration controls.
It does **not** establish a built APK consumer, native runtime execution, full
service parity or Android acceptance. Issue #265 remains open.

## Agreed build boundary

The runtime #264 owner reserves `assets/shared/` for repository-relative shared
source bytes and `assets/runtime/` for native runtime data/probe metadata. Native
libraries, toolchain provenance and the shared-source record live outside
`assets/shared/`. The APK module is owned by #264; this helper does not invent a
module, require one on Linux/Termux or orchestrate services.

The API in `scripts/check_runtime_parity.py` is:

```python
record = apk_source_manifest(source_root, expected_commit)
result = check_apk_sources(source_root, extracted_shared_directory, record, expected_commit)
```

`expected_commit` must be a full lowercase Git SHA obtained from the intended
build source, independently of the packaged record. `source_root` must have that
committed Git tree available. The existing Android release builder owns source
selection, regular-file checks, private-state exclusions and clean-source checks.
The helper reuses that implementation; it neither copies its selector nor invokes
its Termux installer. Building the current HEAD requires committing all selected
source changes, including contract/dependency inputs. Validation of an older
explicit revision reads its committed bytes; it cannot substitute that revision
for the caller's independently selected expected build SHA.

The record extends the existing schema-1 provenance shape:

- `schema: 1`, `platform: "android-apk"`, `commit`: exact expected SHA.
- `source_files`: exact repository-relative path → SHA-256 mapping for the shared
  `src/`, `ai/prototype/`, `dev/postgres/` selection, the runtime contract and every
  dependency input named by that contract. This includes APIs, authorization,
  all package-owned web assets/migrations, Pi JavaScript and bootstrap SQL.
- `runtime_contract`: the committed `dev/runtime-contract.json` object. Dependency
  input bytes must match its reviewed hashes.

The source directory contains only that inventory. Validation hashes actual
files, rejects missing/extra/stale bytes and symlinks, and compares the record to
independently derived committed source. Editing both a stale payload and its
record cannot make it pass. The record is public source provenance, never a
place for credentials, signing keys, node identity, databases or operational logs.

For offline local validation of a real consumer's extracted shared payload:

```bash
python3 scripts/check_runtime_parity.py --root /path/to/source \
  --source-commit FULL_EXPECTED_SOURCE_SHA \
  --apk-source-manifest /private/build/shared-source.json \
  --apk-shared-payload /private/build/extracted/assets/shared
```

All three APK arguments are required together. Ordinary invocation retains the
existing Linux/Termux checks and does not look for an APK. CLI invocation first
checks those adapters and the reviewed contract, then checks the supplied shared
payload. `apk_source_manifest` and `check_apk_sources` alone validate shared source;
they do not evaluate a host's generated settings or installed dependency versions.

## PostgreSQL guard coverage

The common contract now declares two cluster names, durability/password settings
and application role flags. The source check executes the existing Termux
cluster configuration generator into disposable temporary directories with process
launch/wait suppressed. It first validates the actual startup cluster and bootstrap
call identities and ports, then exercises the generator with those cluster inputs.
It inspects the complete generated configuration,
rejects duplicate assignments, and verifies distinct ports, exact cluster launch
commands, connection headroom, loopback listeners and durability. It checks the
actual role-declaration expressions in the Termux bootstrap AST and the shared
Docker bootstrap SQL against the restricted role flags. These are deterministic
source controls; no PostgreSQL server starts and no database acceptance is implied.

Counterexamples mutate actual adapter source to disable durability, append an
overriding assignment, expose a listener, reuse the metadata port, change cluster
launch arguments, grant superuser/replication/inheritance or weaken contract
policy. Shared-source controls remove real API/auth/assets/migration/Pi/bootstrap
and dependency files, alter bytes, rewrite a stale manifest, add fork/private
files, and insert symlinks. Tests stage a committed source fixture, not an APK.

## Required integration and acceptance

1. #264's real APK build must generate this record from its intended committed
   source, validate its staged shared directory, and validate the bytes actually
   packaged under `assets/shared/`. Retain a real package-bound check and failing
   controls for missing/stale/mismatched packaged payloads. A passing temporary
   source fixture does not satisfy this gate. The coordinator arranges stacked
   source and independent review before the host #266 integration proceeds.
2. Native provenance remains separate: bind actual packaged executable/library
   bytes, ABI, compiler/NDK, SDK/build-tools, interpreter/native-extension/Node/Pi/
   PostgreSQL/ingress/tailnet versions and pinned build inputs. The agreed #264
   prototype uses compile/target SDK 36, min SDK 29, NDK r30, build-tools 36.0.0,
   and `arm64-v8a`; x86_64 diagnostic artifacts cannot prove Moto execution.
3. Integrate validation of actual host-generated service inventory and settings:
   all six services, two independent PostgreSQL clusters, restricted roles,
   metadata endpoint fencing, secure authentication/CSRF/cookies, loopback
   backend listeners, trusted tailnet-only ingress and bounded resources. Reuse
   shared contract inputs and document intentional differences; reject unknown
   exceptions. Source identity alone does not prove installed/runtime policy.
4. Bind default credential-protected post-unlock storage and explicit opt-in
   device-protected pre-unlock storage to real host behavior. Keep PIN and Termux
   data intact. Prove actual Android 16 execution and full pre-PIN tailnet
   readiness on the owned Moto, with independently reviewed private evidence.
5. The coordinator schedules selected local acceptance, deployment and physical
   device checks. No full suite, desktop rebuild, phone mutation, release or merge
   is authorized by this helper. Hosted Actions remain disabled.

A successful helper result explicitly retains `apk_runtime_acceptance: "pending"`.
It is a shared-source check, not a readiness or release receipt.
