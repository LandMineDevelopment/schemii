# Working on the Android phone server

Use the existing private connection when the user's task concerns the phone's
Schemii backend. This guide records discovery and operating boundaries, so a new
agent can continue without reinstalling Termux, exchanging keys or requesting
passwords. Installation and user-facing commands are in the
[Android release guide](android-release.md). The desktop preview and frozen demo
are separate deployments.

## Discover the existing connection

On the development workstation, load
`$HOME/.local/state/schemii/android-phone/access.json`. This descriptor is outside
Git, owned by the local user and mode `0600`. Treat its values as private. Do not
print the descriptor, read the private key's contents, or copy either into a
commit, issue, release, screenshot or public log.

The descriptor supplies these current values; do not infer them from an older
chat, screenshot or release number:

| Fields | Purpose |
| --- | --- |
| `ssh.config`, `ssh.host_alias` | Existing OpenSSH configuration and destination alias |
| `ssh.host_key_fingerprint`, `ssh.private_key` | Pinned server identity and local key-file reference |
| `ssh.user`, `ssh.port`, `ssh.listen_address` | Expected remote identity and private SSH listener |
| `phone.home`, `phone.prefix`, `phone.release_link` | Termux home, installed tools and active release folder |
| `phone.proot_alias`, `phone.guest_source`, `phone.guest_state`, `phone.guest_python` | Dedicated userland, bound source, retained state and interpreter |
| `phone.active_operation_file` | Phone-side operation ownership and progress marker |
| `application.origin`, `application.global_command`, `application.current_release` | Phone HTTPS origin, stable command and recorded release |
| `application.metadata_port`, `application.target_port`, `application.app_port`, `application.sidecar_port`, `application.https_port` | Distinct service endpoints |
| `lifecycle`, `availability`, `private_ledger` | Recorded commands, access limitations and coordinator evidence |

Before connecting, verify that the descriptor, SSH config and referenced private
key exist, are regular files owned by the current user, and are not readable by
other users. Inspect metadata only; never display the key. Resolve effective SSH
options with `ssh -G -F CONFIG ALIAS`, using the descriptor values. Check its
`UserKnownHostsFile` and confirm that the host-key entry selected for that alias
matches `ssh.host_key_fingerprint`, using `ssh-keygen` fingerprints. Keep strict
host-key checking and noninteractive authentication enabled. Do not accept a new
key, disable verification, overwrite `known_hosts` or regenerate access when a
check fails; reconcile the private setup with the coordinator.

## Begin with read-only checks

After the local checks above, this example uses only descriptor values and
read-only remote commands. Keep returned operation details and identity private.

```python
import json
from pathlib import Path
import shlex
import subprocess

descriptor = Path.home() / ".local/state/schemii/android-phone/access.json"
access = json.loads(descriptor.read_text())
ssh = [
    "ssh", "-F", access["ssh"]["config"],
    "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10", access["ssh"]["host_alias"],
]
marker = shlex.quote(access["phone"]["active_operation_file"])
for command in [f"cat -- {marker}", "id -un", "schemii status"]:
    subprocess.run([*ssh, command], check=True, timeout=45)
```

Read the marker's `active`, `owner`, `operation`, `phase` and `instruction`
fields before taking action. If it is active, coordinate with that owner and
inspect the descriptor's private ledger. Do not restart, upgrade, reset accounts,
start another migration or overwrite data while that operation owns the phone.
Do not delete or rewrite its marker to bypass ownership. If the marker is missing,
unreadable or unclear, establish the current state with the coordinator before
mutating anything. Its presence does not expand the user's authorization.

Check that `id -un` matches `ssh.user`. In status, inspect live `ready`, service
`running` flags and `supervisor_alive`; `state: "ready"` alone is insufficient.
A nonzero command or connection failure is a diagnostic result. Do not immediately
reinstall or restart as a fallback.

## Operate within the assigned task

The global commands work from any Termux folder:

```bash
schemii status
schemii start
schemii stop
schemii restart
```

Start, stop and restart operate the complete backend: metadata PostgreSQL,
target PostgreSQL, Python application and APIs, Pi sidecar, nginx HTTPS, and the
optional trusted tailnet HTTPS proxy when enabled.
`schemii` delegates to the active release's `./start.sh --android`; it is the
supported phone lifecycle entry point. Check the operation marker and task
authorization before the mutating commands. Never substitute direct service
starts, Docker, another server, another port or plain HTTP.

The easy-to-find `~/schemii` folder points to the active versioned release. Resolve
the current link through `phone.release_link` rather than assuming a release tag.
Installed release bytes are integrity-checked and immutable. Develop in the
assigned workstation checkout, verify the change, and use the reviewed release
installer for an authorized upgrade. Do not edit the phone's installed source or
run a second checkout over the retained state. An upgrade must account for any
active operation and its planned service interruption.

Database data and original keys remain in the dedicated userland's private
`phone.guest_state`, independently of the visible source folder. Metadata uses
loopback port `5433`; targets use loopback port `5434`. These must stay distinct:
target policy blocks the metadata endpoint. Use the descriptor's current values,
existing restricted roles and preserved secrets for assigned database work;
do not repoint a target connection at metadata or reset state to fix access.

When PostgreSQL tools are required, use the existing dedicated PRoot userland,
not a new installation. Derive the user and invocation from the installed
launcher's `guest()` function; use `phone.proot_alias` and bind the active
`phone.release_link` at `phone.guest_source`. Preserve the launcher's isolated
environment and `--env PROOT_DONT_SHARE_LIBANDROID_SHMEM=1`: PostgreSQL initialization
previously stalled with the alternative shared-memory registry. Do not guess a
guest username, invoke package installation, spawn a separate database or disable
durability. Database mutations need explicit ownership of their task and state.

Use `application.origin` for the phone's current tailnet HTTPS endpoint. With
automatic trusted HTTPS, the service has a separate on-phone Tailscale identity
and hostname. The descriptor's SSH destination remains the original Android VPN
node; do not substitute the service hostname for SSH. Both identities remain
independent of the desktop's Serve route.

The supported launcher owns enrollment and startup. If status reports
`login_pending`, use the explicit `schemii tailnet-login` owner flow; databases
and the other services start after approval. Never copy another node's state or
introduce an auth key to bypass enrollment. Keep login URLs private. Stop and
restart use the same global commands after enrollment.

Preserve the private CA, which still verifies the local nginx connection behind
the trusted proxy. Clients using the automatic HTTPS origin need no private CA.
The direct `:8001` origin requires the public CA as described in the release
guide; never export its private key or bypass TLS verification. No Tailscale
credential belongs in this repository or an agent prompt. Keep database passwords,
setup tokens, cookies, backups, SSH keys and private operation evidence outside
tracked files. A cold backup containing the proxy's identity is sensitive; do not
run duplicate restored identities concurrently.

Direct SSH has been verified while the phone is available. Termux force-stop,
reboot, Doze, battery management and VPN reconnect recovery are not validated.
If access disappears, report the actual failing check and coordinate with the
owner; do not claim an always-on service or recreate the setup automatically.
