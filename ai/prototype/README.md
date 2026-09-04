# Shared Pi AI sidecar

Branch: `prototype/shared-ai-sidecar`. Original checkpoint: `9f29247`.
Pi now supplies the application's chat inference and account sign-in transport.
Schemii still owns conversations, permissions, tool execution and proposal review.
The directory retains its prototype name while this integration is evaluated.

## Run and connect

Run `./start.sh` from the repository root. Pi is part of the default stack;
`./start.sh --ai-prototype` remains an equivalent launcher alias. Use
`https://localhost:8001`, or the configured remote preview
`https://omarchy.taile4f57f.ts.net`. There is no public sidecar port.

Use chat provider settings, or `/ai-prototype` for Codex device sign-in.
Authorize the code on OpenAI's device page; device authorization may first need
enabling in ChatGPT's web security settings. OpenAI API keys and currently
verified free OpenCode Zen models are also supported.

The former OpenCode service is no longer mounted or started. Its existing
`schemii-test-opencode-data` volume is **retained, not deleted or migrated**.
Old provider credentials may remain there until the operator explicitly removes
that data. Pi's expiration cannot clear an unmounted legacy volume. The historical
`opencode_password` secret filename now authenticates the private sidecar service;
it is not a user's provider credential.

## Ownership and streaming

One shared Node process handles bounded concurrent turns, not one process per
user. Its private HTTP API requires the server-held service secret. Python selects
the authenticated owner, credential identity, model and allowed tools; the model
cannot choose an owner, arbitrary endpoint or credentials.

Provider credentials are encrypted in owner-scoped metadata rows, not browser
storage. The inference sidecar holds credentials and context only for a turn. Rotated
credentials return through the private terminal event on success or failure, then
the in-memory scope is discarded. Private NDJSON carries provisional text followed
by a result/error; credentials and raw provider diagnostics never become browser
text. Cancellation reaches inference; disconnect also aborts the active credential
identity. Generation checks reject late writes after credential removal.
Device-login state is separately bounded to ten minutes, 32 flows total and four
per owner; successful credentials wait there only for acknowledgment or expiry.

Pi returns structured tool calls but never executes them. Existing Schemii schema
validation, capabilities, proposal approval and database permissions remain
authoritative. Text already streamed cannot be retracted after permission changes.
The sidecar has no database access, host port, Docker socket, shell/MCP tools or
coding-agent session persistence. It stores no chat history or query rows.
Schemii's existing metadata retention governs chats; query rows remain transient
and are not copied into metadata.

Provider streaming explicitly uses SSE, avoiding Pi's shared WebSocket caches.
Ambient environment/file credentials are disabled. Zen requests use an explicit
public auth marker rather than another user's stored key. Request/context/output
sizes, concurrency, deadlines and write backpressure are bounded. Failures have
fixed, safe error codes.

## Free-model discovery and consent

The server refreshes the public Zen registry and models.dev OpenCode price
metadata at startup and every `[ai] catalog_refresh_seconds` (default 3600).
Only live, non-deprecated models with explicitly zero input/output and other
advertised costs qualify; “free” in a name is insufficient. Chat availability
also intersects the catalog with installed Pi-supported models.

Failed discovery keeps the previous snapshot marked stale for at most
`catalog_max_stale_seconds` (default 86400), then public models become unavailable.
The authenticated `/api/v1/ai/prototype/catalog` endpoint returns freshness without
making an inference request. Discovery stores no prompts, credentials or rows.

Free Zen use requires acknowledgment of the provider-data-policy notice: these
services may use prompts for training, so users should not send personal or
confidential data. Discovery is not consent to inference. No model is silently
substituted when access disappears. Models can be switched between turns within
the same retained conversation; a running turn must finish first.

## Inactive-user credential retention

In `dev/schemii.toml`, `[ai] credential_expiration_enabled` defaults to `true`.
Set it to `false` to disable expiration. `credential_inactivity_days` defaults to
30, must be positive, and is ignored when expiration is disabled. Restart through
`./start.sh` after configuration changes.

Opening a visible Schemii page and trusted pointer, keyboard or scroll activity
record use, at most once per minute per tab. Background polling, idle tabs,
credential reads and token refresh do not extend retention. API-only clients can
explicitly record use with `POST /api/v1/activity`.

Expired credentials are cleared before use and by periodic cleanup even if the
owner never returns. Tokens and nonces are removed; small nonsecret generation
tombstones prevent late login completions from restoring them. Returning after
expiration requires fresh sign-in.

This covers Pi credentials, not saved PostgreSQL passwords or the unmounted old
OpenCode volume. It does not erase historical backups or revoke provider tokens;
those require separate operator/provider controls.

## Supported deployment boundary

The supported setup is **one Python API process and one shared Pi sidecar**.
Python serializes each owner/credential from read through refresh save; the
sidecar rejects overlapping use of that identity. Distinct identities and owners
remain separate.

Multiple API workers/replicas need a durable cross-worker identity lease and
read-after-lock. Sidecar overlap rejection alone cannot prevent another worker
from preloading stale credentials. A crash or lost private connection after remote
token rotation but before metadata save can still require sign-in again.
Do not claim multi-replica credential safety yet.

The app still uses its local-prototype principal. Owner isolation is tested using
distinct test principals, not deployed multiuser authentication. These tests are
not a load benchmark or general security certification.

## Repeat verification

```sh
./start.sh --test-ai-prototype
```

The launcher builds pinned Node and locked Pi AI `0.85.0`. Building needs registry
access; the test container has no network, credentials, database mounts or ports.
It runs nonroot/read-only with temporary storage, 512 MiB memory, one CPU and a PID
limit. This command does not restart the application.

Tests exercise actual Pi/OpenAI SDK transformations, streamed function arguments,
ordinary JSON Schema `$defs`/`oneOf`, Codex refresh, owner isolation,
cancellation/logout, bounded resources and safe failures. External HTTP responses
are synthetic; no inference charges are incurred. User-assisted real device
authorization has completed, but synthetic tests do **not** prove live Codex
inference or account entitlement. Verify those separately with user participation.

No T3 Code source was copied. Pi is MIT-licensed; retain its packaged license.
Its provider SDK dependency tree is locked and install scripts are disabled.
External provider retention and subscription policies still apply.
