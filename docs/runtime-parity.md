# Linux and Android runtime parity

[Issue #256](https://github.com/LandMineDevelopment/schemii/issues/256) owns drift
prevention. [Android PR #255](https://github.com/LandMineDevelopment/schemii/pull/255)
is the unpublished base; this change neither merges it nor updates an installed
release. `demo-v0.1.0` remains frozen.

`dev/runtime-contract.json` is a small, reviewed inventory, not an orchestrator.
Android consumes its shared environment, target admission and smaller console/AI
configuration. Compose keeps explicit defaults and is checked against the same
values. `python3 scripts/check_runtime_parity.py` runs without Docker, a database,
credentials or device access. Full local CI runs it before expensive checks;
ordinary Python discovery also runs its controlled source mutations.

## Inventory and ownership

| Boundary | Linux/Docker | Android/Termux | Shared owner / control |
| --- | --- | --- | --- |
| Application | Python 3.12 digest-pinned image | Debian 13 ARM64 Python 3.13, binary wheels only | `src/`, `pyproject.toml`, exact `constraints.docker.txt` pins |
| Product APIs, migrations, assets | installed wheel | installed wheel from manifest-verified clean staging | `schemii.main:app`, packaged SQL migrations/web assets; no Android product forks |
| Metadata and target databases | two PostgreSQL 17 containers, separate networks/roles | two PostgreSQL 17 clusters, loopback ports 5433/5434 | existing bootstrap semantics and metadata endpoint target fence |
| Bootstrap and seed | metadata-bootstrap, demo-bootstrap, postgres-seed jobs | bounded supervisor bootstrap, idempotent target seed marker | shared `dev/postgres` SQL; application owns metadata migrations before readiness |
| Fixture differences | desktop account-access/demo scenarios and optional demo-fixture job | phone seeds organization alongside baseline/migration demo | deployment fixtures differ; product APIs and migrations do not |
| AI | Node 22.22.2 / Pi 0.87.1 sidecar container | same Node release and Pi lock under PRoot | `ai/prototype/*.js`, same package/lock (Pi requires Node >=22.19), `npm ci --omit=dev --ignore-scripts` |
| HTTPS | pinned nginx ingress; independent desktop Tailscale Serve route | guest nginx plus native Termux Go/tsnet proxy | `/api/v1/readiness`, Secure-cookie/Origin authentication; native proxy stays outside PRoot |
| Lifecycle | `./start.sh` | `./start.sh --android` / global `schemii` | separate adapters; neither launcher falls back to the other |
| State and recovery | private `.schemii`, volumes, logical metadata backup | Termux-private guest state, cold PostgreSQL backup | preserve original keys/accounts; explicit fresh restore and ownership checks |

The contract records complete Python pins, image digests, Node archive digest,
Pi identity, Go/Tailscale versions and hashes of dependency input files, including
transitive lockfiles. A change to these inputs must update the reviewed contract.
This binds identities; it does not prove upstream availability or installability.
Debian apt package patch versions are intentionally resolved at install time and
recorded in the installed manifest; PostgreSQL major, Debian image, Python minor
and ARM64 architecture remain fenced. Android needs glibc ARM64 wheels for every
Python pin; native Android wheels are not assumed.

PostgreSQL major parity does not promise byte-identical SQL results. The observed
Docker/source build is PostgreSQL **17.11 Alpine/musl**, while the phone was
**17.12 Debian/glibc**. Identical `en_US.utf8` labels produced different default
ordering in one `STRING_AGG` output; explicit `COLLATE "C"` comparisons matched.
These are owner-reported observations, not device checks executed by this task.
Preserve the native SQL/locale behavior. Deterministic comparison fixtures must
state ordering and collation explicitly; do not rewrite product SQL or normalize
results to hide a platform difference. Patch versions and locale implementations
remain part of deployment provenance, not a guarantee inferred from major 17.

Both PRoot launch paths retain `PROOT_DONT_SHARE_LIBANDROID_SHMEM=1`: the other
shared-memory registry deadlocked `initdb`. The parity negative controls remove
that setting from each real launch path. Active AI is `ai/prototype`, never the
archived OpenCode `ai/Dockerfile`.

## Critical budgets and platform exceptions

Metadata admission is **16** on both platforms. It is independent of target SQL
admission. Android PostgreSQL allows **24** connections per cluster, leaving
**8** beyond application metadata admission for readiness, migrations and
maintenance (including PostgreSQL superuser reserve). Linux explicitly retains
PostgreSQL's 100-connection budget. The parity check validates headroom separately
from target admission, never substitutes the phone's target limit for metadata.
The former phone override of 4 caused report/auth rejection; its negative control
mutates the actual adapter environment back to 4 and must fail.

| Setting | Linux | Android | Reason |
| --- | --- | --- | --- |
| Target SQL total / per identity | 20 / 4 | 8 / 2 | phone CPU/memory budget; metadata is separate |
| Tile preview | 10,000 rows / 8 MiB | same | bounded query data |
| Dashboard preview | 50,000 rows / 32 MiB | 50,000 rows / 16 MiB | smaller phone memory budget |
| Preview stream deadline | 120 seconds | 600 seconds | measured phone workload; row/byte fences and cancellation remain |
| Export/result/ingress window | 900 seconds | 900 seconds | streams finish inside retained result/ingress window |
| Console live read sessions | 12 | 4 | smaller phone concurrency |
| AI concurrent turns / per user | 4 / 2 | 1 / 1 | smaller phone concurrency |

Android's PostgreSQL buffers/workers remain small and its durability settings
remain enabled. PRoot shares Android's UID and is **trusted-owner SQL** operation;
it cannot reproduce Docker's network, UID, cgroup or read-only isolation. The
native trusted-HTTPS proxy must execute in Termux, outside PRoot: actual netlink
emulation stalled in the guest. These are deliberate exceptions, not alternate
application implementations. Docker resource overrides remain administrator-owned;
parity validates checked-in defaults, not arbitrary deployed environment values.

## Build and private-state boundaries

Docker's application image contains `/usr/share/schemii/runtime-build.json` with
source-file digests, runtime contract and source commit (`+dirty` is explicit),
and `runtime-installed.json` with installed Python versions. The Pi image carries
the same source revision label; the application manifest binds its sidecar source
and dependency inputs. An unversioned source export says `unknown`, never invents
a commit. The copied-file hashes remain the exact content identity.

Android's release manifest binds every selected file to the exact committed
source and lists dependency-input hashes explicitly. The release must include
`dev/runtime-contract.json`; installation hashes it, all Android adapters,
application sources and sidecar inputs into the installed identity, and records
installed Python and Debian package versions. Existing generated native proxy
provenance binds the static ARM64 binary to committed Go sources and toolchain.
Installed releases stay immutable; build a new version instead of patching one.

Release allowlists and Docker context exclusions keep credentials, `.schemii`,
keys, logs, caches, databases and backups out of published source/image provenance.
These manifests contain source identities, never operational state. Android cold
backups intentionally include both database clusters, original secrets, TLS keys
and optional native tailnet identity, excluding logs/transient locks. Desktop
logical backups include metadata, keys and configuration but **exclude target
rows**; target databases need separate owned backup. The backup formats are not
interchangeable. Keep the existing hostile-archive, fresh-restore, supervisor
ownership and crash-recovery controls; parity/API smoke does not replace them.

## Updating a feature, service or dependency

1. Implement product behavior once in `src/` or the shared sidecar. Add migration
   SQL and assets to the existing package-data owners and their focused tests.
2. For a new service, update the contract inventory, Compose dependency/readiness
   graph and Android supervisor startup/stop/status/bootstrap. Keep any native
   Termux component outside PRoot. State a platform exception's concrete reason,
   budget and regression coverage; do not silently omit a service.
3. Update dependency declarations, exact constraints/locks, image/archive digests,
   contract identities and all installers/build/recovery compatibility fences
   together. Recompute `dependency_inputs` SHA-256 values from the reviewed files.
   Check Python 3.12 and 3.13 ARM64 wheel compatibility before an Android release.
4. Update common defaults or explicit platform configuration in the contract and
   adapters. Preserve metadata headroom, target endpoint fences, authentication,
   durability, row/byte limits and recovery semantics. Add a negative control
   mutating the real adapter, not only a fabricated contract object.
5. Run `./ci.sh --plan --base <integration-or-stack-base>`, the parity command and
   focused controls. Reuse valid source-bound evidence; complete missing selected
   acceptance on explicitly disposable fixtures. Baseline static failures remain
   visible; successful unit checks are not full acceptance.
6. Run the [common authenticated API smoke](runtime-api-smoke.md) separately
   against both owned HTTPS deployments. Add new API families to that shared
   entrypoint using existing fixture helpers. It requires explicit disposable
   consent and a private origin-bound fixture; it never discovers credentials.
7. Build new immutable Android artifacts from a committed tree with the existing
   builder/pinned Go SDK, and rebuild Linux only through `./start.sh` on a leased
   owned fixture. Inspect manifests and archive exclusions. Obtain independent
   current-head review before integration; never modify the frozen demo.

Source checks here establish drift detection and packaging contracts. Actual
Linux image build, disposable PostgreSQL, authenticated HTTPS/browser, provider,
physical phone execution, pressure, cold restore, Doze/VPN/reboot acceptance need
their own retained evidence. A pending device gate stays pending.
