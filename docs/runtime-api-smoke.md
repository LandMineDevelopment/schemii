# Common authenticated runtime API smoke

Run `python3 scripts/smoke-runtime-api.py` from a source checkout against an
**explicitly owned disposable deployment and test account**. The same command
accepts either supported deployment's exact HTTPS origin. It never starts a
stack, discovers credentials, provisions accounts, resets data or touches the
published `demo-v0.1.0` deployment. Start/rebuild an owned fixture only through its
supported `./start.sh` launcher and applicable deployment lease.

This smoke reuses `testing/provision.py`'s request/login/logout boundary,
`dev/demo/prepare.py`'s private-file and no-redirect controls, and
`testing/load/fixture-spec.json`'s bounded Orders exploration. Existing
provisioning or browser fixture helpers can prepare the test-owned target,
connection and workspace; the report-author fixture helper in
`testing/provision.py` prepares a model and dashboard for a provisioned QA slot.
The target must have an `orders` table with an `id` column and at least one row.
The model must use the shared fixture's Orders definition and allow the bounded
exploration. Use a single test account with all three product capabilities plus
`schemer:author`; all supplied resources must belong to it and target the same
explicit database and namespace. Ordinary retained user resources and live-phone
accounts are not prerequisites that this issue authorizes accessing.

Prepare a private absolute-path JSON manifest (owned regular file, mode `0600`,
no symlinks, no more than 16 KiB). Populate identities from the fixture provisioner;
the following values are placeholders, not working credentials:

```json
{
  "format": "schemii-runtime-api-smoke-v1",
  "disposable": true,
  "origin": "https://owned-fixture.example",
  "credentials": {"username": "owned-smoke", "password": "TEST-OWNED-PASSWORD"},
  "user_id": "user_11111111111111111111111111111111",
  "connection_id": "pg_22222222222222222222222222222222",
  "workspace_id": "ws_33333333333333333333333333333333",
  "model_id": "model_44444444444444444444444444444444",
  "dashboard_id": "dashboard_55555555555555555555555555555555",
  "database": "disposable",
  "namespace": "owned"
}
```

```bash
python3 scripts/smoke-runtime-api.py \
  --origin https://owned-fixture.example \
  --fixture /private/owned-runtime-smoke.json --disposable
# For an owned local self-signed deployment, add its public CA certificate:
# --ca-file /private/owned-fixture-ca.crt
```

TLS verification is always enabled. HTTPS origins cannot contain credentials,
paths, queries or fragments. Redirects are rejected before forwarding a password
or session cookie. Each invocation has a fresh cookie jar. The manifest binds the
explicit disposable consent to the exact destination; no ambient environment
credentials or bootstrap token are read. Keep the manifest and any evidence
private, outside Git. Successful output includes fixed check scope and pending
lifecycle acceptance, without credentials, rows or server response bodies.

The smoke verifies durable PostgreSQL readiness, enabled authentication,
anonymous denial, login identity/capabilities, durable authenticated session,
owned target connection/test, Schemii workspace/catalog/seeded row access,
Schemoo catalog/model/plan and Schemer saved-dashboard ownership plus a bounded
report query. The report endpoint owns and releases its execution results. The
smoke creates no domain objects, does not edit source rows, and attempts logout
and anonymous-denial verification after every attempted login, including failures.
A logout failure fails the smoke; dispose/recover the owned fixture and preserve
private failure evidence rather than adopting any leftover session.

The synthetic tests in `tests/test_runtime_api_smoke.py` prove protocol, source
ownership, prerequisite rejection and cleanup controls. They do **not** prove
actual Linux or Android HTTPS execution. Run the common command independently on
both owned deployments before claiming API parity. Record source/release identity,
origin, command and outcome privately. Device boot, Doze/VPN recovery, native
Termux proxy execution, backup/restore, concurrency pressure, provider operation
and browser acceptance remain separate checks. This smoke does not replace the
platform-specific lifecycle and recovery controls or the repository CI planner.
