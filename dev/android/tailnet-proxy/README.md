# Android tailnet HTTPS listener

This optional Linux/ARM64 helper gives the Android backend its own browser-trusted
HTTPS address without a desktop proxy or client CA installation. It embeds the
pinned Tailscale `tsnet` library and registers only the tailnet TCP 443 listener.
It does not expose unmatched ports, provide a SOCKS proxy, or enable Funnel.

Use the supported `schemii` / `start.sh --android` lifecycle to configure and run
it. The runtime owns startup order, allowed application hosts/origins and full
HTTPS readiness. Do not launch a second helper against the same retained state.

The first run creates a separate persistent Tailscale node. The owner completes
its normal login URL once and may need node approval under their tailnet policy.
MagicDNS, tailnet HTTPS certificates and internet access for issuance/renewal are
required. Certificate names are published in Certificate Transparency. The
endpoint remains available only to permitted tailnet clients.

## Runtime contract

```text
schemii-tailnet-proxy \
  --state-dir PRIVATE_STATE/tailnet/node \
  --status-file PRIVATE_STATE/run/tailnet.json \
  --hostname schemii-phone \
  --upstream https://127.0.0.1:8001 \
  --upstream-ca PRIVATE_STATE/tls/ca.crt
```

The upstream is deliberately fixed to loopback HTTPS on port 8001. Its explicit
CA pool and `localhost` TLS server name verify the retained ingress certificate;
certificate checks are never disabled. The original HTTP Host/Origin are
preserved, forwarding headers are rebuilt, and response streaming is flushed
immediately. Existing nginx request/COPY limits remain authoritative.

The private status file is atomically replaced with mode 0600. It reports schema
1, `pid`, `backend_state`, `online`, `listening`, `certificate_ready`, `ready`,
`hostname`, `origin`, `tailnet_ips`, `updated_at`, and optional `login_url`/`error`.
The requested name can be changed by Tailscale to avoid a collision: consumers
must use the authenticated `hostname` and `origin`, not infer them from a label.

`online` and the authenticated origin appear before the backend starts. `ready`
requires the node to be online, a certificate/key pair verified against the system
root pool for that exact name, and the TLS listener registered. It does not prove
application readiness: the launcher must separately verify the public HTTPS
readiness endpoint. Certificate issuance has a 90-second startup deadline and
returns actionable errors; tsnet owns cached certificates and later renewal.

Status is written on a change or every 30 seconds, avoiding continuous fsync
churn. The login URL is private operational data. Do not expose it through an
unauthenticated API, public status page, Git or a release. The helper ignores
verbose user/backend log callbacks; Tailscale's own standard log upload and
bounded private log buffers remain library behavior. Authentication environment
overrides are rejected; the supported enrollment flow is interactive login.

Both node/state directories are private; inherited process umask is 0077. An
exclusive nonblocking `schemii.lock` prevents concurrent use of one node identity.
Retained state, SDK log files, the ACME account and recognized cached certificate
files are checked for symlinks/nonregular files/hardlinks before SDK startup.
The actual authenticated host's cached certificate/key is checked again before
certificate retrieval. Unknown files are preserved without opening them;
unsupported certificate/key names are rejected. Good private identity bytes are
kept unchanged.
On SIGTERM/SIGINT, HTTP requests get up to ten seconds to finish before close;
tsnet then closes and a final stopped status is written.

## Backup boundary

With this pinned library, retain `node/tailscaled.state` and `node/certs/` entries
`acme-account.key.pem`, `<actual-hostname>.crt`, and `<actual-hostname>.key` in an
encrypted/private cold backup. These include private keys. Never publish them.
Exclude `schemii.lock`, `tailscaled.log.conf`, `tailscaled.log1.txt`,
`tailscaled.log2.txt`, status/login data, and temporary files. The LocalAPI
listener is in-memory and creates no Unix socket in the state directory.

## Focused checks and build

Go 1.27.4 or a compatible explicit compiler is required only on the build
workstation. Users receive the static Linux/ARM64 binary; no Go SDK is installed
on the phone. Module checksums are retained in `go.sum`.

```bash
go test -mod=readonly -count=1 ./...
go vet -mod=readonly ./...
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 GOTOOLCHAIN=local \
  go build -mod=readonly -trimpath -ldflags='-s -w -buildid=' -o OUTPUT .
go version -m OUTPUT
```

Tests cover target/path validation, symlink refusal, node exclusivity, status
permissions/transitions, actual DNS identity, verified upstream CA and hostname,
forwarded security context, host rejection, streaming and public certificate
preflight. They create only disposable unit fixtures and never enroll a node.
Actual Android operation, owner login, public-chain HTTPS, negative port probes,
and full-stack restart require separately recorded device acceptance.

The dependency provides a maintained userspace network stack, WireGuard transport,
Tailscale enrollment and automatic certificate support. It adds a binary/process
and the corresponding pinned Go dependency graph. Update this module and compiler
together with focused checks and a reviewed release; do not replace pins with
floating downloads.
