# Android backend preview

This experimental release runs the complete backend on an **unrooted ARM64
Android phone** using Termux and a dedicated Debian 13 environment. The initial
target is the Moto g power 2026 with Android 16. On that device, preview 11 has
completed installation, authenticated API and report work, trusted tailnet HTTPS,
and a supported full-stack restart while preserving its databases and saved
diagram positions. These observations support an experimental preview; they do
not establish unattended availability or production recovery guarantees.

The phone hosts the Python application and all product APIs, a PostgreSQL 17
metadata cluster, a separate PostgreSQL 17 target cluster, the Node 22.22.2 Pi AI
sidecar, and HTTPS ingress. Python runs on Debian's 3.13 interpreter. The database
files and original encryption keys stay in Termux-private internal storage. No
computer, Docker daemon, public tunnel or off-phone database is required to run
it. AI provider operations still need their normal account credentials and
internet access; this release contains no provider credentials or local model.

Dashboard previews share a 50,000-row budget across all tiles, matching the
standard backend. Each tile remains capped at 10,000 rows and 8 MiB, and the
complete dashboard remains capped at 16 MiB. This lets several detail tables
display their previews without the first table exhausting the dashboard's row
budget; larger results still stop at the applicable row or byte limit.
Android preview streaming allows 600 seconds of processing, based on the measured
phone workload exceeding the desktop's 120-second deadline. Row and byte caps,
cancellation behavior and the result TTL are unchanged; the deadline remains
below the 900-second result/ingress window. Desktop streaming stays at 120 seconds.

## Install the versioned release

1. Install the official **Termux** app from F-Droid, and the official **Tailscale**
   Android app. Sign the phone into your existing tailnet and leave Termux included
   in Tailscale app routing. If using Termux:Boot later, install it from the same
   source as Termux; do not mix APK signing sources.
2. Keep the release under Termux's private `$HOME`. Do not install or put database
   files in `/sdcard`, shared Downloads, or a memory card. Make room for Debian,
   dependencies, two databases and backups; the installer needs network access.
3. In Termux, download the installer for this exact version:

   ```bash
   pkg install curl
   curl --fail --location --proto '=https' --tlsv1.2 \
     --output "$HOME/install-android.sh" \
     https://github.com/LandMineDevelopment/schemii/releases/download/android-v0.1.0-preview.13/install-android.sh
   ```

   Inspect the downloaded file, then run it:

   ```bash
   bash "$HOME/install-android.sh"
   ```

The standalone installer embeds the exact release archive checksum and source
commit. It verifies the download, complete file inventory and safe archive paths
before extraction. It installs a new version directory under
`$HOME/.local/share/schemii/releases/android-v0.1.0-preview.13`, then delegates runtime
installation to that source's `./start.sh --android install`. After dependency
installation succeeds, it installs the global `schemii` command and points the
visible `~/schemii` folder to that version. Existing changed source is preserved
and rejected rather than overwritten. A repeated install of
the same unchanged version can repair an interrupted dependency installation;
it never resets databases or credentials.

The runtime uses the dedicated PRoot-Distro alias `schemii-android`. An unrelated
existing alias with that name is preserved and blocks installation. This preview
requires the current OCI-capable PRoot-Distro 5.x CLI (5.10.3 is compatible);
an older distro-alias CLI is not silently substituted. Preview 2 fixed the preview 1
help-output check that wrongly rejected 5.10.3. Preview 3 passes
`PROOT_DONT_SHARE_LIBANDROID_SHMEM=1` explicitly to every PRoot-Distro login,
selecting PRoot's built-in SysV IPC handling. This addresses the upstream
[PostgreSQL initialization deadlock](https://github.com/termux/libandroid-shmem/issues/23)
that matches the first phone's stalled `postgres --check` probe. All backend
services stay in one foreground PRoot session because its IPC table is per
session. Durability and PostgreSQL WAL/fsync settings remain enabled. The Moto
has completed both PostgreSQL initializations and application migrations;
full durability, restore and availability acceptance remain pending. Debian's
default Node 20 is too old, so installation uses the separately verified
Node 22.22.2 ARM64 archive.

Preview 4 corrects the HTTPS proxy Host header to match the backend's existing
localhost allowlist, as the desktop proxy already does. This fixes the
`400 Invalid host header` response for the phone's Tailscale address. TLS names
and the explicit trusted browser origins remain the configured phone addresses.

## Your folder and global commands

After installation, your active application folder is **`~/schemii`**. Run
`cd ~/schemii` whenever you want to inspect its guide or exported setup files.
It is a managed link to the installed version under `.local/share/schemii/releases`,
so upgrades can select a new version without moving or resetting database state.
The installer preserves an unrelated existing `~/schemii` folder or `schemii`
command and reports the conflict before changing packages.

These commands work from **any directory in Termux**:

```bash
schemii start
schemii stop
schemii restart
schemii status
```

Start brings up both PostgreSQL clusters, Pi, the Schemii application with all
product APIs, and HTTPS together, then waits for readiness. Stop shuts that whole
stack down. Restart stops it fully before starting again, and does not start a
replacement if shutdown fails. Status reports the same complete stack without
changing it. The global command delegates lifecycle work to the active release's
`start.sh --android`; it does not create a second server or require shell aliases.
When trusted tailnet HTTPS is enabled, these commands also own its proxy process.

Metadata admission keeps the normal bounded 16-connection budget for account,
report-control, foreground and background transactions. It fits within the
metadata cluster's 24-connection ceiling with separate readiness and maintenance
headroom. Target-query admission remains separately bounded at eight total and
two per connection identity; raising metadata headroom does not raise query or
report row/byte limits.

Other launcher actions are available the same way, for example `schemii logs pi`,
`schemii doctor`, `schemii credentials DEST`, and `schemii backup DIRECTORY`.
Use `schemii --help` for all actions. Logs and databases remain in the dedicated
private runtime; `~/schemii` is the application folder, not shared Android storage.

## Configure and start

In the Tailscale app, find the phone's `100.x.y.z` address and its complete MagicDNS
name, such as `moto-power.example-tailnet.ts.net`. Use the phone's name, not the
desktop server's `omarchy` address. Then, from any Termux directory:

```bash
schemii configure \
  --tailnet-ip 100.X.Y.Z \
  --hostname moto-power.YOUR-TAILNET.ts.net
schemii start
schemii status
```

Replace both example values. The address must be the phone's Tailscale address,
and Tailscale must be connected before starting. Startup checks local and tailnet
HTTPS readiness. If binding or health checks fail, read the reported private log
and resolve the failure; do not change to plain HTTP or another server.

The configured application origins are:

- On this phone: `https://localhost:8001`.
- On another tailnet device: `https://PHONE.YOUR-TAILNET.ts.net:8001`.
- API map: append `/api-map` to either origin.

Only HTTPS ingress listens on the phone's Tailscale address. The application,
AI sidecar, metadata database on `127.0.0.1:5433`, and target database on
`127.0.0.1:5434` listen on loopback. Allow the intended HTTPS port in your tailnet
access rules. The service deliberately does not listen on the phone's Wi-Fi
address. Startup before the VPN exists fails visibly instead of widening the bind.

## Automatic trusted tailnet HTTPS

Preview 9 runs the optional on-phone `tsnet` proxy natively in Termux, using the official pinned
Tailscale library. It enrolls a **separate node** named `schemii-phone`; it does
not extract or reuse the Android Tailscale app's credentials. Keep that app
connected for the existing phone endpoint and SSH. The desktop is not a proxy.
The native host supervisor owns both this proxy and the five-service Debian
backend. The proxy stays outside PRoot: actual device testing found a PRoot
netlink emulation hang before enrollment. It reads real interfaces through the
SDK's supported adapter, uses the existing Android VPN's DNS endpoint within its
own process, and trusts Termux's maintained public CA bundle. No global resolver
file, additional APK or desktop route is changed.

After installation and the usual phone address configuration above, run:

```bash
schemii stop
schemii tailnet-enable
schemii start
schemii tailnet-login
```

The first start returns an actionable `login_pending` state (exit code 2) when
owner login or node approval is needed. The supervised proxy keeps waiting;
**the databases and remaining services start after approval**. Open the private
URL printed by `tailnet-login` in your browser and sign in normally. Do not paste
it into chat or a public issue. Tailnet device approval or Tailnet Lock signing
may also be required. No auth key is needed in a command, environment variable
or repository file. Run `tailnet-login` again if enrollment is still pending.

Enable MagicDNS and HTTPS certificate provisioning in your tailnet settings.
Certificate issuance publishes the service's DNS name in Certificate Transparency;
network access stays private to the tailnet. Initial enrollment, issuance and
renewal need internet access. The proxy registers only HTTPS port 443, never
Funnel or a general localhost forwarder. It does not expose PostgreSQL, Pi or
the application's internal listener on its additional node identity.

After approval, `schemii status` reports the actual `tailnet.origin`, such as
`https://schemii-phone.YOUR-TAILNET.ts.net`, with no `:8001`. Use that origin for
the app and `/api-map`; supported browsers trust its public certificate without
installing the private CA. Readiness verifies the real HTTPS response and public
certificate chain at that origin, in addition to the legacy endpoint. Generic
status never prints the enrollment URL. `schemii logs tailnet` supplies private
diagnostics for connection, tailnet HTTPS settings or certificate failures.
The guest's readiness probe connects directly to an authenticated live proxy's
own tailnet IP while keeping the actual DNS name for HTTP Host, TLS SNI and
system-root hostname verification. It does not depend on Debian resolving private
MagicDNS names, change guest DNS settings or fall back to insecure TLS. Browsers
still use the normal trusted DNS URL through their Tailscale connection.

The proxy verifies nginx's loopback HTTPS certificate against the retained
private CA; it never skips upstream verification. The existing port-8001 origin,
account, databases and CA remain available. To return to that endpoint:

```bash
schemii stop
schemii tailnet-disable
schemii start
```

Disabling preserves node identity and certificate state. Re-enabling with the
same hostname reuses it. Do not edit installed source or launch the proxy outside
the supported lifecycle. Android background, battery and VPN recovery limits
still require device validation.
`schemii doctor` reports whether the guest can verify the native proxy's birth
identity, parent chain and executable through the mounted release. A failure
stops guest startup; it never adopts a PID from the helper's status alone.
Stop and restart own both process groups. If the native owner dies, the proxy
receives a parent-death signal and the guest shuts down its services when it
loses the verified owner. Existing database keys and node state remain private
at the same retained paths inside the dedicated rootfs.

## Legacy certificate trust and the first account

Schemii uses Secure cookies and HTTPS origin checks, including when traffic travels
through encrypted Tailscale. The Android app does not provide the desktop
`tailscale serve`/`tailscale cert` setup. The legacy endpoint uses its own private CA
and a server certificate for localhost, the configured phone name and its tailnet
address. Install **only the public CA certificate** in every intended browser's
trust store.
This client CA installation is unnecessary when using the optional trusted
tailnet origin above. The local nginx CA remains necessary for the proxy's
verified upstream connection.

From `~/schemii`, export the public CA into the mounted source directory:

```bash
schemii export-ca /opt/schemii/.schemii/android-ca.crt
```

On Android, use `termux-setup-storage` if needed, then copy that public file to
Downloads so Settings can select it:

```bash
termux-setup-storage
cp .schemii/android-ca.crt "$HOME/storage/downloads/schemii-android-ca.crt"
```

In Android Settings, search for **CA certificate** and install this certificate.
Menu wording varies by firmware. Install the same public CA in each remote
client that will open the phone's HTTPS URL. Keep all private keys, databases and
credential exports out of shared storage. Verify that the browser trusts the
configured name before entering credentials; bypassing certificate checks is not
the intended setup.

Export the first-account setup reference and restricted target connection into
a new private file:

```bash
schemii credentials /opt/schemii/.schemii/android-credentials.json
```

The command reports the file path rather than printing credential values. Open
`.schemii/android-credentials.json` locally in a private editor. At
`https://localhost:8001/login`, use its setup token to create your administrator.
The file also contains the local target connection information. Its target host
is `127.0.0.1`, port `5434`, database `schemii`, with the restricted
`schemii_target` role. These are addresses seen by the phone backend, even when
your browser is on another device. Do not attach this file or `.schemii/` to an
issue, screenshot, chat or release. The metadata password is not a target
connection, and the metadata endpoint is intentionally blocked by target policy.

## Recover a forgotten account password

On the hosting phone, open Termux and use the local operator command:

```bash
schemii start
schemii reset-password kasey
```

Replace `kasey` with the existing account's username. Enter the new password at
the two hidden prompts; use 12–256 characters. Do not put a password after the
command, pipe it in, or send it in chat. The command requires a real terminal
and a running local metadata database. It does not start a temporary database
or expose a password-reset endpoint over HTTP.
For a username beginning with a hyphen, use `schemii reset-password -- -username`.

Recovery changes only that enabled account's password, clears its login lockout
attempts and revokes its existing sessions. It preserves the username, account
privileges, database data, models and reports, and records a local-reset audit
event. Unknown or disabled accounts are refused. After success, refresh the
phone's tailnet login page and sign in with the new password; other sessions for
that account must sign in again. No server restart or database reset is needed.

## Operate and recover

All lifecycle actions use the same global command and active launcher:

```bash
schemii doctor
schemii logs
schemii restart
schemii status
```

Startup reports its current stage. Setup helpers have deadlines and cancellation;
configuration and recovery commands report busy locks immediately. `stop` requests
graceful supervisor shutdown before acquiring the lifecycle lock, so a starting
supervisor cannot block the shutdown request. Logs are private and bounded.
`doctor` supplies compatibility observations and explicit pending checks; it is
not full product acceptance. If startup stops, retain the failure and logs,
inspect them locally, and report only sanitized
diagnostics. Do not repeatedly reinstall or regenerate keys to clear an error.

The older preview 2 shared-memory initialization hang cannot be reliably stopped
through the guest because its native PRoot tracer can also be blocked. Automatic
`--stop-only` recovery of that old hang is not validated on the Moto. A disposable
first installation can be restarted from fresh Schemii state after Android Force
stop ends the old Termux processes. Do not reset an installation containing user
data; retain it and collect sanitized diagnostics.

When upgrading a normally stopped installation, run the new version's installer
before starting it. Node packages are installed in each version's source directory;
extracting a new archive alone does not install them. Existing database state and
configuration stay in the dedicated userland.

The live state is inside the guest at
`/home/schemii/.local/share/schemii`. It includes both PostgreSQL clusters, secrets,
TLS material and configuration. Uninstalling Termux, clearing its app data, or
removing the PRoot userland can delete this state. Keep verified backups elsewhere.

Make a **cold backup**, which cleanly stops the backend and leaves it stopped:

```bash
schemii backup /opt/schemii/.schemii/backups/phone-first
schemii verify-backup /opt/schemii/.schemii/backups/phone-first
schemii start
```

Choose a new destination each time. The snapshot includes metadata, all local
target database files, required roles, original encryption keys and TLS material
from one stopped-state consistency window. When present, it also preserves the
optional proxy's configuration, Tailscale node identity and ACME/certificate keys;
logs and transient status are excluded. Never run the original and a restored
copy of the same tailnet identity concurrently. Treat this backup as access to
the node as well as the databases. It is a sensitive, unencrypted physical
PostgreSQL 17 backup for the matching Debian 13 ARM64 runtime, not a portable SQL
export. Move it to encrypted off-phone storage with its private permissions intact.
Never restore a snapshot from an untrusted source. Integrity verification checks
contents and checksums; a rehearsed fresh restore and database readback remain
separate acceptance checks.

On a fresh matching runtime, place the trusted bundle under the new source's
private `.schemii/` directory and run:

```bash
schemii restore /opt/schemii/.schemii/backups/phone-first
schemii start
```

Restore refuses existing database or secret state and never overwrites an active
installation. Preserve a damaged installation and recover separately; do not
delete its data just to satisfy a fresh-restore guard. This preview does not
automatically migrate the desktop Docker volumes, and the desktop metadata-only
backup format is a different format.

For a newer Android preview, stop the old runtime and make a verified backup
first. Download and run that version's installer, then use `schemii start`.
Successful installation atomically updates `~/schemii`; global commands follow
the new active version. The previous version directory is retained. The dedicated
userland and database state survive source installation, but
database migrations are not automatically reversible. Keeping old source alone
does not guarantee a rollback; recovery needs the matching backup and runtime.

## Boot and Android availability

For optional startup after reboot, install Termux:Boot from the same source as
Termux, open it once, and use:

```bash
schemii enable-boot
```

Use `schemii disable-boot` to remove only Schemii's own boot script. Enable Tailscale's
always-on VPN and exempt **both Termux and Tailscale** from battery optimization
using the phone's Settings. Boot startup needs the phone's first unlock and a
ready VPN. The generated script runs `schemii wait-network`, which waits up to
120 seconds for the configured Tailscale IPv4 address to become bindable, then
calls `schemii start` once. It uses the existing `configure` network settings in
the retained userland; no separate boot address is needed. A timeout or invalid
configuration stops before starting services. Inspect the Termux:Boot failure,
connect Tailscale and run `schemii start`; startup failures are not retried.
After upgrading, rerun `schemii enable-boot` to refresh an existing owned script.

Termux:Boot dispatches scripts independently, so filename sorting does not
guarantee one script finishes before another. Schemii's script owns its VPN gate
and start sequence. First unlock remains required; this is not pre-unlock or
unattended-reboot acceptance.

After an interrupted run, the guest verifies each retained PostgreSQL lock
against that cluster's exact guest path and previously captured, now-dead PID.
PostgreSQL keeps responsibility for removing its own stale lock and recovering
WAL. Unknown, live or symlinked locks are refused with retained files preserved;
do not delete locks to force startup.

A wake lock or process supervisor cannot guarantee that Android keeps the
backend alive. Doze, memory pressure and firmware process limits still need
device testing. Begin with a small personal workload, keep connection/concurrency
budgets bounded, and measure memory, temperature, storage and battery behavior
before relying on unattended availability.

## Acceptance and security limits

This profile is for **trusted-owner SQL use**. Termux and PRoot processes share
one Android UID. Separate database ports and PostgreSQL roles preserve application
policy but do not reproduce container filesystem isolation. A target PostgreSQL
superuser or a role with server-file/program capabilities can threaten other
state readable by that UID. Do not offer this deployment to hostile SQL users or
describe it as equivalent to the Docker isolation boundary.

Before calling this phone deployment dependable, record results for:

- Clean dependency installation; both PG17 initializations, migrations, extension
  creation, concurrent transactions/advisory locks, cancellation and COPY.
- Authenticated save/reopen and mutations through every product API family,
  including actual provider login/streaming/cancellation with owned prerequisites.
- Trusted localhost and tailnet HTTPS, browser Secure cookies/CSRF behavior, and
  failure to reach internal service ports or ingress through the Wi-Fi address.
- Durable restart, controlled interrupted-write/crash recovery, complete cold
  backup and fresh restore with saved-work and target-row readback.
- Locked-screen availability while charging and unplugged, extended idle/Doze,
  Wi-Fi/cellular transitions, Tailscale reconnect and reboot after first unlock.

Source-bound automated packaging and lifecycle tests cover a different boundary
from these physical-device checks. Release notes must state which checks actually
ran and which remain pending. Keep raw logs, traces, backups and credentials
private. Track the work in [issue #254](https://github.com/LandMineDevelopment/schemii/issues/254).

## Preventing deployment drift

New release source includes the [shared runtime contract](runtime-parity.md).
Android reads shared security/metadata/preview defaults and explicit phone budgets
from it; the release and installed identity must include that exact contract.
Run the parity checker and focused negative controls before packaging, then use
the [common authenticated HTTPS smoke](runtime-api-smoke.md) on explicitly owned
disposable deployments. These source checks do not establish physical-device,
provider, browser, pressure or recovery acceptance and do not update any installed
release or the frozen interview demo.
