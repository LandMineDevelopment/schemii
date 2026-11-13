# Android backend preview

This experimental release runs the complete backend on an **unrooted ARM64
Android phone** using Termux and a dedicated Debian 13 environment. The initial
target is the Moto g power 2026 with Android 16. Physical-device compatibility,
idle availability and performance remain release acceptance checks until measured
on that phone. A successful installation or green health response does not
establish those results.

The phone hosts the Python application and all product APIs, a PostgreSQL 17
metadata cluster, a separate PostgreSQL 17 target cluster, the Node 22.22.2 Pi AI
sidecar, and HTTPS ingress. Python runs on Debian's 3.13 interpreter. The database
files and original encryption keys stay in Termux-private internal storage. No
computer, Docker daemon, public tunnel or off-phone database is required to run
it. AI provider operations still need their normal account credentials and
internet access; this release contains no provider credentials or local model.

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
     --output install-android.sh \
     https://github.com/LandMineDevelopment/schemii/releases/download/android-v0.1.0-preview.3/install-android.sh
   ```

   Inspect the downloaded file, then run it:

   ```bash
   bash install-android.sh
   ```

The standalone installer embeds the exact release archive checksum and source
commit. It verifies the download, complete file inventory and safe archive paths
before extraction. It installs a new version directory under
`$HOME/.local/share/schemii/releases/android-v0.1.0-preview.3`, then delegates runtime
installation to that source's `./start.sh --android install`. Existing changed
source is preserved and rejected rather than overwritten. A repeated install of
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
session. Durability and PostgreSQL WAL/fsync settings remain enabled. Actual
successful initialization on the phone is still pending. Debian's default
Node 20 is too old, so installation uses the separately verified Node 22.22.2
ARM64 archive.

## Configure and start

In the Tailscale app, find the phone's `100.x.y.z` address and its complete MagicDNS
name, such as `moto-power.example-tailnet.ts.net`. Use the phone's name, not the
desktop server's `omarchy` address. Then, from the printed release directory:

```bash
cd "$HOME/.local/share/schemii/releases/android-v0.1.0-preview.3"
./start.sh --android configure \
  --tailnet-ip 100.X.Y.Z \
  --hostname moto-power.YOUR-TAILNET.ts.net
./start.sh --android start
./start.sh --android status
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

## Trust HTTPS and create the first account

Schemii uses Secure cookies and HTTPS origin checks, including when traffic travels
through encrypted Tailscale. The Android app does not provide the desktop
`tailscale serve`/`tailscale cert` setup. This release creates its own private CA
and a server certificate for localhost, the configured phone name and its tailnet
address. Install **only the public CA certificate** in every intended browser's
trust store.

Export the public CA into the mounted source directory:

```bash
./start.sh --android export-ca /opt/schemii/.schemii/android-ca.crt
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
./start.sh --android credentials /opt/schemii/.schemii/android-credentials.json
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

## Operate and recover

All lifecycle actions go through the same source's launcher:

```bash
./start.sh --android doctor
./start.sh --android logs
./start.sh --android stop
./start.sh --android start
```

Startup reports its current stage. Setup helpers have deadlines and cancellation;
configuration and recovery commands report busy locks immediately. `stop` requests
graceful supervisor shutdown before acquiring the lifecycle lock, so a starting
supervisor cannot block the shutdown request. Logs are private and bounded.
`doctor` supplies compatibility observations and explicit pending checks; it is
not full product acceptance. If startup stops, retain the failure and logs,
inspect them locally, and report only sanitized
diagnostics. Do not repeatedly reinstall or regenerate keys to clear an error.

If preview 2 is stuck inside its first `initdb` check, download the preview 3
installer as above, then run these commands **one at a time**:

```bash
bash install-android.sh --stop-only
bash install-android.sh
```

Continue with installation only after the stop command succeeds. `--stop-only`
verifies and extracts the new control code without installing dependencies. Its
legacy recovery validates the saved supervisor identity, exact process ancestry,
PostgreSQL executable and owned data path before requesting cancellation of an
unfinished `initdb` and its `postgres --check` probe. A cluster with `pg_control`
is excluded. `initdb` owns cleanup of its unfinished initialization; the launcher
does not delete database directories or lock files. If ownership cannot be
proved or graceful shutdown times out, preserve the state and report sanitized
diagnostics. Do not remove the userland or kill processes by name. After successful
installation, use the new version directory for `configure` and `start`.

The live state is inside the guest at
`/home/schemii/.local/share/schemii`. It includes both PostgreSQL clusters, secrets,
TLS material and configuration. Uninstalling Termux, clearing its app data, or
removing the PRoot userland can delete this state. Keep verified backups elsewhere.

Make a **cold backup**, which cleanly stops the backend and leaves it stopped:

```bash
./start.sh --android backup /opt/schemii/.schemii/backups/phone-first
./start.sh --android verify-backup /opt/schemii/.schemii/backups/phone-first
./start.sh --android start
```

Choose a new destination each time. The snapshot includes metadata, all local
target database files, required roles, original encryption keys and TLS material
from one stopped-state consistency window. It is a sensitive, unencrypted physical
PostgreSQL 17 backup for the matching Debian 13 ARM64 runtime, not a portable SQL
export. Move it to encrypted off-phone storage with its private permissions intact.
Never restore a snapshot from an untrusted source. Integrity verification checks
contents and checksums; a rehearsed fresh restore and database readback remain
separate acceptance checks.

On a fresh matching runtime, place the trusted bundle under the new source's
private `.schemii/` directory and run:

```bash
./start.sh --android restore /opt/schemii/.schemii/backups/phone-first
./start.sh --android start
```

Restore refuses existing database or secret state and never overwrites an active
installation. Preserve a damaged installation and recover separately; do not
delete its data just to satisfy a fresh-restore guard. This preview does not
automatically migrate the desktop Docker volumes, and the desktop metadata-only
backup format is a different format.

For a newer Android preview, stop the old runtime and make a verified backup
first. Download that version's installer and retain the previous source directory.
The dedicated userland and database state survive source installation, but
database migrations are not automatically reversible. Keeping old source alone
does not guarantee a rollback; recovery needs the matching backup and runtime.

## Boot and Android availability

For optional startup after reboot, install Termux:Boot from the same source as
Termux, open it once, and use:

```bash
./start.sh --android enable-boot
```

Use `disable-boot` to remove only Schemii's own boot script. Enable Tailscale's
always-on VPN and exempt **both Termux and Tailscale** from battery optimization
using the phone's Settings. Boot startup needs the phone's first unlock and a
ready VPN. If the VPN is not ready, inspect the recorded failure and run `start`
after it connects; do not assume the service silently recovered.

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
