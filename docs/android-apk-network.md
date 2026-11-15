# Android APK network adapter

Partial implementation for [#268](https://github.com/LandMineDevelopment/schemii/issues/268)
under [#263](https://github.com/LandMineDevelopment/schemii/issues/263).
This is the networking feasibility slice, not a released APK or completed
Direct Boot/whole-stack acceptance. The Android build, entrypoint, signing and
emulator stay with runtime owner #264. Physical Moto, enrollment, DE storage,
network-transition and full backend acceptance stay coordinator-owned.

## One shared node and ingress implementation

`dev/android/tailnet-proxy/` retains the pinned Go/tsnet dependency graph. Its
common implementation owns enrollment, protected state, exclusive node locking,
authenticated node name, certificate verification/readiness, TLS, HTTP reverse proxy,
Host/Origin/forwarding semantics, streaming and shutdown. Two platform adapters
supply network observation, DNS, socket routing, public trust and ingress policy.

- The Linux/Termux command keeps its strict PREFIX and public CA validation,
  VPN resolver `100.100.100.100:53`, IPv4 ioctl observations, loopback upstream
  `https://127.0.0.1:8001`, and no privileged socket marks. Its CLI is excluded
  from the Android shared-library build. Existing lifecycle commands remain the
  supported Termux boundary; do not invoke a second helper against live state.
- The Android/ARM64 c-shared library is loaded by the APK's Java NetworkBridge.
  Its platform has no Termux paths or VPN-DNS assumption. Android owns network
  selection, DNS transport/policy, IPv4/IPv6 observations and per-socket binding.
  The shared Go SDK still owns its ordinary encrypted transport/bootstrap and
  certificate behavior. No dependency versions or shared core APIs are changed.

The library must live in a **dedicated host-owned network process**. SDK hooks,
Go resolver state and trust caches are process-global. The current runtime probe
can demonstrate the bridge in its worker thread without starting a tsnet node;
that does not authorize running the final node in the UI/main process. There is
no new Activity, Service, VpnService, TUN or competing APK scaffold in this change.

## Concrete runtime264 integration

New owned paths:

- `platforms/android/app/src/main/java/development/landmine/schemii/android/network/`
- `platforms/android/native/network/`

The Android-only cgo shim compiles the JNI C bridge from the second path. Build
from the repository's **real module location**, retaining those relative inputs;
a source snapshot must include both the Go module and native bridge. No floating
module downloads or prebuilt unbound network binary may substitute for source.

```bash
python3 platforms/android/native/network/compile.py \
  --go PINNED_GO/bin/go --ndk PINNED_NDK --abi arm64-v8a \
  --output-dir PRIVATE_NEW_OUTPUT
```

Use Go **1.27.4**, runtime264's pinned NDK **30.0.16248370**, min **29**,
compile/target **36**, ARM64 device ABI. An x86_64 emulator diagnostic uses
`GOARCH=amd64`, the matching NDK compiler and separately labeled evidence.
The helper refuses dirty inputs unless explicitly marked development-only with
`--allow-dirty`. It records original failures, exact source/tool hashes and Go
build metadata. Its bridge digest in CGO_CFLAGS prevents cgo's cache from reusing
stale external JNI includes; the same digest appears in the actual native probe.
It never downloads tools, writes the SDK/NDK or overwrites an existing build.
Never lower target SDK or copy executable code into writable app storage to run
it. Check ELF alignment and actual device page size separately.

Runtime264 owns the changes that package `libschemii_network.so`, compile the new
Java sources with the existing source set, add `ACCESS_NETWORK_STATE` alongside
`INTERNET`, and call this from its **existing worker-thread probe**:

```java
JSONObject network = development.landmine.schemii.android.network.NetworkProbe.run(context);
```

`NetworkProbe.run(Context)` must be called off the main thread. A 45-second
Future deadline covers network discovery, JNI work and normal cleanup. Timeout
or interruption requests native cancellation, unregisters callbacks and closes
DNS work; a still-unwinding native operation retains its explicitly owned JNI
reference until Stop confirms completion. The host must contain a process if
native cleanup cannot be confirmed; a timed-out Future is never a readiness pass.
`NetworkProbe.run(Context)` returns actual Java/Go/JNI callback outcomes and closes
the bridge. It performs no enrollment, no backend startup and no tailnet
listener registration. Its result includes network generation/handle, observed
IPv4/IPv6 addresses, DNS and TCP/UDP binding outcomes, public-root bundle digest
and count, verified public TLS, Java callback counts, and `stack_acceptance=false`.
`ready` in this named **underlay probe** means a stable-generation successful
network proof, not node reachability or Schemii readiness. UDP success is only
socket creation/binding/connect; no UDP round trip is claimed.

Keep native source hashes, Java/C/Go input hashes, library hash, Go build metadata,
ABI/SDK/toolchain identity and APK readback together. Probe results must belong to
a freshly installed exact-source APK/run, not an older retained report. The owner
must preserve original failed probe/build records. Cross-compilation, Java
compilation and mocked callbacks are not actual Android execution evidence.

## Network and DNS policy

NetworkBridge observes the Android **default** Network using
[ConnectivityManager callbacks](https://developer.android.com/develop/connectivity/network-ops/reading-network-state).
Only an INTERNET-capable, validated, non-VPN default is eligible. It does not
request another network or select a non-VPN underlying network to bypass a VPN.
A VPN/lockdown default, captive portal, incomplete metadata or absent network
fails closed. This conservative policy intentionally may report blocked even
when a different VPN would allow bypass; changing that behavior requires explicit
policy review and device evidence. It never changes the other VPN's settings.

Effective network/policy/LinkProperties changes advance a generation, cancel
pending DNS, revoke owned socket I/O and invalidate readiness. Identical framework
callbacks do not create a new epoch; blocked-status changes do. Each probe/node
lifetime pins DNS, metadata and socket binding to that one generation.
LinkProperties and NetworkInterface provide real interface names/indices, MTUs,
flags, addresses/prefixes including IPv6, gateway and Private DNS state. Go rejects
missing or invalid metadata and supplies non-nil AltAddrs to the SDK rather than
falling back to Android-denied netlink enumeration. This supports IPv6-only
*observation*; actual IPv6-only/NAT64 connectivity remains an acceptance case.

[DnsResolver.rawQuery](https://developer.android.com/reference/android/net/DnsResolver)
performs primary wire queries on that Network. Android netd owns that resolver's
selection, transport and Private DNS policy. Go's resolver and the SDK's primary
forwarder share an in-memory DNS connection adapter; no OS resolver file, custom
public DNS resolver or listening DNS socket is introduced. Cancellation is registered
before execution, closes pending operations and is bounded by deadlines. Both
request and response generation are checked; stale answers are discarded.

The owner explicitly approved **standard SDK bootstrap** after review R273-01.
The pinned Tailscale SDK retains its normal HTTPS bootstrap DNS when primary
resolution fails/returns no addresses, and its connection-failure retry behavior.
It contacts retained DERP bootstrap IPs over verified HTTPS on port443 using
`/bootstrap-dns?q=hostname`. These answers do **not** come from Android netd or
inherit Private DNS policy. The hostname is disclosed to the selected bootstrap
service. This is normal SDK behavior, not an Android-DNS-only guarantee. No custom
fallback resolver, SDK fork or dependency change is installed. This does not
permit public/LAN Schemii ingress or bypass of an Android VPN/lockdown default.

The actual pinned boundaries are `control/controlclient.NewDirect` and
`control/controlhttp.Dialer.resolver`, which construct their own `dnscache.Resolver`
with `dnsfallback.MakeLookupFunc`; replacing `dnscache.Get().Forward` alone never
disabled this fallback. `dnscache` also considers bootstrap after a failed dial.
`dnsfallback.bootstrapDNSMap` uses `netns.NewDialer` only with a non-nil NetMon;
its nil-NetMon branch uses a plain, **unbound** `net.Dialer`. Similarly,
controlhttp's absent Dialer selects `stdDialer`. Those branches are not safe
adapter alternatives: this consumer's tsnet engine sets the dialer's monitor;
NewDirect rejects a missing monitor and passes that dialer, DNSCache and monitor
through ts2021 to controlhttp. The startup logger is created earlier with a nil
monitor, but `logpolicy.TransportOptions.New`/`MakeDialFunc` replace nil with a
static monitor before `netns.FromDialer` or bootstrap. DERP constructors reject a
nil monitor; magicsock's UDP listener also uses netns. Host controls inspect these
construction boundaries and execute the logger's nil-monitor and SystemDial
paths. Future pin or consumer changes require rechecking these invariants. This
adapter does not expose tsnet's Loopback/SOCKS or general UserDial APIs; their
plain-dialer alternatives are not authorized network entrypoints.

[Network.bindSocket](https://developer.android.com/reference/android/net/Network)
is invoked before connecting each SDK socket via its mandatory Android hook.
Java duplicates the descriptor for the call and closes only its duplicate. Go
keeps the real socket. A kernel SO_COOKIE identity registry revokes owned socket
I/O on change/stop without retaining duplicate descriptors. A temporary duplicate
is obtained and checked against its original cookie before shutdown, so reused
FD numbers cannot revoke unrelated sockets. Missing kernel cookie support fails
closed and must be tested by the actual Android probe. TCP control/bootstrap/DERP and UDP transport use the same
binding boundary; loopback backend connections deliberately do not. Go leaves
Android netns hooks enabled, unlike the Termux platform. It installs binding and
cookie registration in `SetAndroidProtectFunc`, whose SDK control callback is
non-skippable, and leaves the optional `SetAndroidBindToNetworkFunc` nil. Despite
the SDK hook's name, this callback never invokes `VpnService.protect`: the app
owns no VPN. The authenticated netmap's disable-Android-bind capability may skip
the optional hook, but must never skip selected-network binding/registration.
Bind failures are errors, not ambient route fallback. Revocation also rejects
new bootstrap attempts; a cached/fallback address never authorizes a new epoch.

SDK link-change notification uses `Server.Sys().NetMon.Get().InjectEvent()` only
after completed SDK initialization. The pinned SDK exposes Sys but labels it
**unstable**. That use is isolated in the Android adapter and must be reviewed and
tested with any pin update; no dependency fork/private-field access is used.
Without event delivery the SDK's Android polling interval is ten minutes.

## Trust, node lifecycle and status

Java's modern-target default trust manager supplies public Android trust anchors.
The Go probe verifies the control-plane TLS chain with an explicit pool made from
those bytes. Before node startup, the adapter writes the public bundle atomically
into its private status directory and supplies it to SDK system-root loading.
That file contains public certificates only. Go and Java trust behavior are
independently verified; Network Security Configuration alone is not treated as
proof of native TLS policy. SDK `tlsdial` also retains its normal baked-in public
root fallback and certificate verification; this is not a strict Java-only root
allowlist. An empty `SSL_CERT_DIR` does not disable Go's default root directories.
The retained loopback CA is a separate pool used only
for the upstream hop. No insecure TLS or HTTP fallback is provided.

### Android certificate acquisition and renewal

R273-03 identified a deterministic unsupported API in the earlier node path:
SDK `local.Client.CertPair` calls `GET /localapi/v0/cert/`, whose only registration
file explicitly excludes Android. Cached files do not bypass that request. Linux
and Termux retain this supported SDK endpoint and its existing behavior.

Android instead uses the **public `local.Client.SetDNS`** operation and the
**existing pinned `tailscale.com/tempfork/acme.Client` library**. This is an import
of the SDK's published library, not a new fork or dependency. Its exported
`HTTPClient` supplies the required transport boundary. `HasACME` remains true in
the actual Android file selection; common LocalAPI registers `set-dns` and
`cert-domains`. No LocalBackend, reflection, private or test-only SDK interface is
used. Before acquisition, before SetDNS, and before committing a certificate,
the provider requires a running/online authenticated node, MagicDNS and its exact
hostname in current `StatusWithoutPeers.CertDomains`. The only DNS name sent is
`_acme-challenge.<that-hostname>`. Authorizations, order identifiers and the CSR
must match that single hostname; wildcard/foreign authorizations fail closed.
The SDK's SetDNS contract independently restricts accepted names/values at the
control server. Actual enrolled control-server acceptance remains a runtime gate.

The CA directory is the pinned library's normal production Let's Encrypt URL.
There is no host JSON/environment CA override. Every directory, nonce, account,
authorization, order and certificate request uses an explicit HTTPS client with
the Android public roots, TLS hostname verification, bounded timeouts and the
mandatory netns socket hook/cookie registry. Its resolver retains the approved
standard SDK bootstrap. CA-provided request URLs must stay on the directory's
HTTPS origin; proxy inheritance, redirects, plain HTTP, alternate TLS dialers and
`http.DefaultClient` are unavailable. The provider pins the same network epoch
for new and pooled connections, response bodies, cancellation and cached returns.
Loss/stop revokes socket I/O and prevents stale certificate success. No ACME
challenge listener or public ingress is opened; only dns-01 is used.
The supported SetDNS API has no delete operation (its backend rejects an empty
value); like the SDK's own dns-01 flow, this adapter leaves TXT lifecycle to
control rather than inventing a cleanup request. Its actual provider behavior is
part of later enrolled acceptance.

The SDK-compatible private files remain `certs/acme-account.key.pem`, `<host>.key`
and `<host>.crt`. Existing account and leaf keys are reused without rotation.
Missing, malformed, linked, unreadable or mismatched retained state fails rather
than creating a replacement identity. A genuinely empty state creates keys once,
persists them as mode0600, and reuses them after interruption. Each verified cert
replaces only its `.crt` through a mode0600 temp/fsync/rename/directory-fsync; its
key remains fixed, so a reader cannot see a mixed-key certificate generation.
The durable retry record also binds hashes of both key files before their first
CA use; losing/replacing all key/certificate files cannot silently reset identity.
The node lock and cancellable provider gate serialize operations. No private CA
is accepted as a public tailnet certificate root.

A cancellable worker checks renewal each minute, including when ingress is idle;
renewal is due after two thirds of the certificate lifetime. It joins before
provider/SDK shutdown and uses the shared certificate verifier and readiness
store. A still-valid verified cached certificate remains usable after a renewal
failure, with explicit `certificate_renewal_error` and `certificate_retry_at`
status fields. Expired, wrong-domain, untrusted or stale-epoch certificates never
become ready. The private atomic `<host>.renewal.json` checkpoint is written
**before** any CA operation. Failures back off from five minutes exponentially to
one day; ACME429 Retry-After can extend the delay and is not retried in-place.
The public SetDNS API does not expose response retry headers, so its failures use
that same durable conservative backoff. At most two short retries cover other
retryable ACME responses; every operation has a bounded caller/parent deadline.
Keys and checkpoints survive cancellation/process restart. Logs/status never
include challenge payloads, signed request bodies or private key material.
An in-flight renewal does not hold up handshakes that can still read and verify
the existing certificate/key/identity checkpoint. Cache reads remain fenced by
current node approval, public trust, time and network epoch.

A host can retain a NetworkBridge, then call `start(String configuration)` and
later `close()`. Start accepts only this JSON schema, with clean absolute private
paths supplied by the host:

```json
{
  "state_dir": "HOST_SELECTED_PRIVATE_ROOT/tailnet/node",
  "status_file": "HOST_SELECTED_PRIVATE_ROOT/run/tailnet.json",
  "hostname": "OWNER_SELECTED_DISPOSABLE_NODE_LABEL",
  "upstream": "https://127.0.0.1:18443",
  "upstream_ca": "HOST_SELECTED_PRIVATE_ROOT/tls/ca.crt"
}
```

The example path values are placeholders and fail path validation as written.
Port **18443** is the provisional ingress allocated by runtime264's
`runtime-interface.json`, distinct from live Termux's 8001. No alternate port
is chosen on a conflict. Changing the host allocation requires coordinated
validation of this adapter; do not point it at PostgreSQL or Pi. Omit `dns_server`:
APK primary DNS comes from the bridge, with standard SDK bootstrap as above.
Unknown JSON fields are rejected. No auth key,
control-URL override, passwords or live node state is accepted as configuration.

`started=true` means an asynchronous node operation was accepted, **not ready**.
The host reads the shared private status file and must match its `instance_id`
to the random per-start identifier returned by Start, in addition to the owned
process identity. A missing/mismatched identifier is not current readiness;
early preflight failure must never make an older status look like this start. Enrollment remains an explicit
owner login/approval flow; the login URL is private operational data. The actual
`.ts.net` name comes from authenticated status, never the requested label.
Tailscale ACL policy plus the existing shared Schemii authorization/CSRF/Secure
cookie behavior remain authoritative. No automatic application login is added.

Status schema1 retains existing fields and adds certificate expiry and Android
network generation/verification fields. `ready` here remains **ingress/node**
readiness, not complete backend acceptance. It requires a current unexpired
verified certificate, online node, registered listener and (on APK) a current
network-generation proof. Network callbacks invalidate proof immediately; a
bounded DNS/public TLS underlay check maintains at most 30-second freshness
within the pinned epoch. Old-generation results cannot overwrite newer state.
An SDK node whose epoch is
revoked stops; the host must recreate its dedicated network process before
restart, retaining its private node identity/state. This discards SDK DNS caches
instead of silently mixing old-network DNS with new-network sockets. Seamless
host reconnect/state continuity remains a later acceptance gate.
Authenticated hostname changes invalidate the existing listener and stop the
node so the host can reconfigure origins explicitly. The certificate callback
revalidates the exact SNI, chain, dates and key on each handshake. Expiry and
failed retrieval cannot leave a latched certificate-ready status. APK TLS/HTTP
forwarding is gated while the current network proof is unavailable.

The host must additionally prove actual shared backend readiness through the
trusted tailnet endpoint from a permitted peer. A loopback health response or
userspace-node status does not establish peer reachability. The retained Termux
OS-socket health probe assumes a system VPN route and cannot be transplanted
unchanged into the APK.

Shutdown retains the common drain and SDK-close order. Duplicate native
operations are rejected. If SDK Start is blocked/cancelled before initialization
finishes, the JNI host must **retain ownership of its dedicated process**:
Close must not race Start, and this library must not call os.Exit to pretend an
in-process stop succeeded. `close()` reports an incomplete native stop and keeps
the JNI owner alive; the host's reviewed process-containment boundary resolves it.
The full host/descendant-ownership gate is separate and remains pending.

Default CE and later opt-in DE paths belong to storage/host owners. This adapter
neither chooses a storage mode nor moves identities. A DE path does not prove
root availability, networking or whole-stack operation before PIN unlock. Keep
Termux data/identity intact; enroll disposable APK state separately.

## Focused checks and remaining acceptance

From the Go module, run the existing focused checks with the pinned compiler:

```bash
PINNED_GO/bin/go test -mod=readonly -count=1 ./...
PINNED_GO/bin/go vet -mod=readonly ./...
```

Regressions cover actual Go resolver wire use, cancellation, DNS identity,
IPv6-only snapshots, missing metadata, certificate expiry, hostname changes,
stale network epochs and private public-root-file handling, alongside retained
Termux configuration/security/proxy/streaming/path/startup tests. Unit fixtures
make no Android/device or before-PIN acceptance claim.

For the pinned SDK's actual Android socket-control and bootstrap paths, run:

```bash
python3 dev/android/tailnet-proxy/sdk_controls.py \
  --go PINNED_GO/bin/go --go-cache PRIVATE_GO_CACHE \
  --output-dir PRIVATE_NEW_CONTROL_OUTPUT --certificate-controls
```

This host-only control verifies inspected SDK source against the retained module
ZIP, extracts a private SDK source snapshot and uses an isolated test modfile/Go
overlay. It compiles the unmodified Android `controlC` body on Linux by changing
only its build constraint, excluding Linux SOCKS wrapping and upstream test files.
It extracts the adapter's real hook-registration statements, uses the real
socketEpoch implementation and sets the actual optional-binding atomic flag both
ways (the exported setter itself checks Android GOOS). Only JNI's framework bind
call is substituted. Real local TCP/UDP sockets must enter the cookie registry,
lose I/O after revocation and reject later creation. Hook errors must propagate.
Actual NewDirect/controlhttp resolver constructors and dnscache/dnsfallback calls
must reach that hook after primary DNS failure; bootstrap attempts are stopped
after registration, before connect. Logger nil-monitor/SystemDial controls and
source guards cover the construction chain described above. These checks do not
prove a successful public bootstrap HTTPS exchange or Android framework binding.
The receipt records every overlay, source/tool hash, command and exit. No cached
SDK or production dependency file is patched. `--registration-ref COMMIT` retains
the old registration as a failing counterexample without checking out old files.

Certificate controls additionally apply the SDK's real Android cert-route file
selection, reproduce the actual old CertPair404, verify common set-dns/cert-domains
registration, and run the real shared provider/ACME transport through the SDK
Android control body with both capability states. Owned TLS CA/LocalAPI fixtures
verify signed ACME requests, the exact DNS TXT challenge, CSR and issued chain;
they cover cache/key retention, renewal while idle, rate-limit persistence,
cancellation, wrong identity/trust/time, incomplete state, cookie ownership and
active epoch revocation. They are host controls, not enrolled Android/provider
acceptance. Unchanged Java/JNI evidence is reused by exact input/header hashes.

Required later evidence: actual signed API36 APK callback execution; native Moto
ARM64 execution/page size; independent control-plane/DNS/certificate issuance and
renewal before first PIN unlock; explicit enrollment and DE owner consent;
authenticated mutations/SQL/streaming/COPY/cancellation; denied peers/origins;
IPv4/IPv6 and LAN/backend listener isolation; network loss, Wi-Fi/cellular/VPN
policy changes and reconnect; stale callbacks, duplicate start and interrupted
initialization; process cleanup, default CE behavior and state continuity.
Coordinator alone schedules device/whole-stack acceptance and independent review.
Issue268 stays open. No secrets, operational logs, node keys or credentials belong
in Git or release payloads.
