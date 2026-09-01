# Repository Agent Rules

## Local Runtime and Delivery Contract

These requirements are mandatory for every agent and every session in this repository.

- `./start.sh` is the only supported command for building, starting, restarting, or refreshing the local application stack.
- Never invoke `docker`, `docker compose`, or the Docker socket directly. Never use `sudo` or run `newgrp` manually. The launcher owns Docker access and stale-group recovery internally.
- Never substitute Uvicorn, FastAPI development mode, Python's HTTP server, another local process, another port, or plain HTTP when the launcher is unavailable.
- The canonical user-facing application origin is `https://localhost:8001`. The API map is `https://localhost:8001/api-map`.
- The primary remote/browser-preview route is the tailnet-only Tailscale Serve origin `https://omarchy.taile4f57f.ts.net`. Its API map is `https://omarchy.taile4f57f.ts.net/api-map`. Share this route first when the user is on their phone or another Tailscale-connected device; never substitute a public tunnel.
- Tailscale Serve terminates trusted HTTPS on port 443 and proxies to the launcher's self-signed HTTPS backend at `https+insecure://localhost:8001`. The route is persistent across reboots. Inspect it with `tailscale serve status`; do not reset Serve because this machine has other configured routes.
- After changes that must be shown to the user, run `./start.sh`, wait for its health checks, and verify the exact HTTPS URL before sharing it.
- After `./start.sh` succeeds, verify both the local canonical URL and the Tailscale preview URL before claiming the remote preview is current.
- If `./start.sh` fails, stop and report its exact failure. Do not bypass the launcher or downgrade HTTPS.
- Do not claim the current source is available at a URL until that deployment has been rebuilt and checked through the canonical HTTPS origin.

The launcher may use Docker on the host as an implementation detail. Application containers must never receive the Docker socket.
