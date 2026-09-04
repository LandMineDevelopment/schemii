# Shared AI sidecar experiment

Branch: `prototype/shared-ai-sidecar`. Baseline: `9f29247`.
This is a **library-boundary prototype**, not the replacement chat service.
The existing OpenCode deployment, saved credentials, chats, metadata and demo DB
are unchanged. There is no new public endpoint or UI toggle.

## Repeat the experiment

From the repository root:

```sh
./start.sh --test-ai-prototype
```

The launcher builds a pinned Node container with the locked Pi AI `0.85.0`
dependency and runs tests. Building needs package-registry access; the test
container has **no network, credentials, database mounts, or published ports**.
It runs nonroot with a read-only filesystem, a temporary directory, 512 MiB
memory, one CPU, and a PID limit. The regular application is not restarted.

The tests use the actual Pi library, OpenAI request/stream transformations, and
Codex token-refresh implementation. Only external HTTP responses are synthetic.
They incur no provider charges. No T3 Code source was copied.

## What this proves

- A single Node process can interleave turns for distinct credential owners.
- Multiple identities for one user remain distinct.
- Multiple Pi model collections share a refresh lock only for the same identity.
- Refresh, logout, and replacement writes serialize in the in-memory store.
- Function arguments and streamed text survive the real provider adapter.
- Cancellation reaches the HTTP transport and releases capacity.
- Missing credentials do not borrow environment credentials.
- Stale permissions and unadvertised tool calls fail closed before acceptance.
- Provider exceptions containing secrets are replaced with fixed safe errors.
- Context/output limits, capacity and deadlines fail explicitly.

`runtime.js` accepts only trusted server-supplied owner/credential identities,
model choices and context. It returns proposed tool calls; it **never executes**
them. The `isAuthorized` callback models the existing Schemii permission/revision
check. Full argument validation, approval, and database authorization still belong
to Schemii's existing service, not to Pi. Streamed text is provisional; a policy
change rejects final acceptance, but cannot retract text already displayed.

## Deliberate limits / next gate

- No real login, paid model request, account-entitlement or provider availability
  test has run. Device-code login and its owner-bound expiring UI transaction
  still need integration. We do not claim subscription-provider endorsement.
- The credential vault is in-memory and test-only. It has no encryption, durable
  persistence or cross-process lock. A production implementation must use encrypted
  owner/credential/provider-scoped storage and coordinate refresh across replicas.
  A crash after remote token rotation but before saving it may require re-login.
- Already-authorized in-flight requests may finish after logout. Future requests
  fail; production revocation also needs active-turn cancellation.
- HTTP streaming (`sse`) is explicit. Pi's shared WebSocket session caches are not
  used. No credential-bearing shared client or mutable current-user singleton.
- The sidecar stores no chat history or query rows. Input/output are transient.
  This does not make claims about the external model provider's retention.
- No server-owned credential fallback, arbitrary endpoint selection, shell, MCP,
  filesystem tools, automatic tool execution, or coding-agent session persistence.
- This is not a load benchmark or a general security certification. Small test
  fixtures show behavior, not maximum supported users. Credential lifecycle bounds
  and whole-response memory accounting need production integration work.

## Decision

If the isolated checks pass, proceed to a narrow Schemii integration on this
branch: encrypted credential repository, owner-bound device login, internal
authenticated streaming transport, and existing permission/proposal handling.
Then test actual credentials with explicit user participation before replacing
OpenCode. Keep T3's lifecycle tests as a reference, not an imported framework.

Pi brings its provider SDK dependency tree (92 installed packages here). The
package and transitive versions are locked; install scripts are disabled. Pi is
MIT-licensed; retain its packaged license when distributing the container.
