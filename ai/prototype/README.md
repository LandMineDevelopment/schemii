# Shared Pi AI sidecar

Integrated into `architecture/unified-backend`. Original checkpoint: `9f29247`.
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
enabling in ChatGPT's web security settings. OpenAI API keys are also supported.
Free OpenCode Zen models require an administrator to save an installation-owned
Zen API key under Administration and grant the user access for the app and exact
database profile. Detached Schemii workspaces have a separate grant. Zen rejects
the public credential for external inference.

The former OpenCode service is no longer mounted or started. Its existing
`schemii-test-opencode-data` volume is **retained, not deleted or migrated**.
Old provider credentials may remain there until the operator explicitly removes
that data. Pi's expiration cannot clear an unmounted legacy volume. The historical
`opencode_password` secret filename now authenticates the private sidecar service;
it is not a user's provider credential.

After deciding that old OpenCode history and credentials are no longer needed,
run `./start.sh --remove-legacy-ai-data`. This permanently removes only the exact
former Compose volume after checking its ownership labels and that no container
uses it. It never prunes volumes or touches Pi credentials or database storage.

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
Ambient environment/file credentials are disabled. Request/context/output
sizes, concurrency, deadlines and write backpressure are bounded. Failures have
fixed, safe error codes.

Provider adapter catalogs describe supported models, not account entitlements.
An explicit model-access rejection marks only that owner/provider/credential
generation/model unavailable in the common runtime. Denials are transient,
contain no provider text, and expire after `[ai] catalog_refresh_seconds`, allowing
a fresh attempt if access changes. A new credential generation clears old
denials. Usage limits and malformed-request errors do not disable models. Both
chat clients refresh availability after a failed turn and preserve the selected
unavailable model until the user explicitly chooses another; no fallback is run.

Private `POST /models/refresh` accepts the same owner, credential identity,
generation and credential envelope as a turn. It lists the account's Codex or
OpenAI models without running inference and intersects the result with installed
Pi support. Codex entries must have `visibility: "list"`. Discovery uses fixed
HTTPS URLs, rejects redirects, and bounds the full request to eight seconds and
the response to four MiB. Its result or safe error returns any refreshed OAuth
credential with the original generation for application-side fenced persistence.
Discovery and inference share the credential overlap guard; disconnect cancels
either operation. Upstream model instructions and arbitrary metadata never leave
the discovery boundary.

Schemii and Schemoo use the same searchable model picker. Opening it calls
authenticated `GET /api/v1/ai/status?refresh=true`; ordinary status polling does
not make account-catalog requests. Connected providers are checked independently
in parallel. The common runtime retains only model IDs and safe freshness/error
metadata in memory, scoped to the credential owner and generation, for at most
`[ai] catalog_max_stale_seconds`. No catalog or query rows are written to metadata.
The shared Zen worker owns periodic public catalog refreshes. Only the
intersection of its live free-model IDs and installed Pi support is selectable,
and only for a user with an instance-key grant.
Loading, unavailable selections and retry states do not change the chat's model;
a failed check preserves previously listed choices with a warning. An advertised
model is not a guarantee of remaining quota or successful inference.

## Public Zen catalog

The server refreshes the public Zen registry and models.dev OpenCode price
metadata at startup and every `[ai] catalog_refresh_seconds` (default 3600).
Only live, non-deprecated models with explicitly zero input/output and other
advertised costs qualify; “free” in a name is insufficient. The public catalog
does not prove the installation's Zen account can use a model. The instance key
and a matching user/app/database grant make the intersection with installed Pi
support selectable; a provider denial keeps the conversation and requires an
administrator to check the key or the user to choose another model.

Failed discovery keeps the previous snapshot marked stale for at most
`catalog_max_stale_seconds` (default 86400), then public entries expire.
The authenticated `/api/v1/ai/prototype/catalog` endpoint returns freshness without
making an inference request. Discovery stores no prompts, credentials or rows.

Zen may use submitted prompts and context for training. Each Zen send requires
acknowledgment of that notice. Previously created Zen conversations remain stored;
ask an administrator for access or choose another available provider to continue them. The
application never substitutes a model silently. Models can be switched between
turns; a running turn must finish first.

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
# Assistant instruction ownership

Schemii supplies the active system prompt from `schemii/ai/prompt.py` and typed
tool schemas from `schemii/ai/tools.py`. The sidecar is inference-only: it does not
load repository AGENTS files, skills, or general-purpose execution tools. Unknown
tool calls remain inert and return to Schemii for server-owned denial guidance.
Read receipts record the execution's approval policy; retained query references
do not preserve raw rows or reconstruct an old snapshot after a rerun.
