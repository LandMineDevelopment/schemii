# Sharing this installation with friends

Tailscale access and application access are separate. This guide assumes the
friend can already reach the private HTTPS origin.

## Prepare a useful first session

1. In **Administration**, create an account and share its initial password through
   a private channel. Ask the friend to change it from **My account** after signing
   in. Password changes revoke existing sessions.
2. Assign a role before sending the link. A report viewer needs `schemer:access`,
   a dashboard grant, and its Schemii-owned connection grant in the same role.
   Enable export and drill-through only when intended.
3. For modeling or schema exploration, grant the appropriate product access and
   explicitly allow the managed connection in those tools. Dashboard authors also
   need Schemoo access to select or create their model. Account provisioning alone
   does not grant product or database privileges.
4. Send a specific saved dashboard link or a short exercise with the connection,
   namespace, and expected result. Explain that Schemii designs schemas, Schemoo
   models data, and Schemer presents reports.
5. Sign in as a non-provisioner test user and follow that exercise before inviting
   others. Check results, filters, allowed/denied exports and drill-through, and
   revocation. Repeat at a phone-sized viewport.

See [Accounts and database roles](accounts-and-roles.md) for exact grant semantics.
Use dedicated PostgreSQL reporting identities for shared connections. Filters and
hidden fields do not replace database permissions or row policies. Private app
workspaces do not isolate writes to an underlying database: give people separate
disposable databases if they will experiment with SQL writes or migrations.

## Approve database targets

The bundled demo database is the default. Additional PostgreSQL servers must be
explicitly approved by the operator through `SCHEMII_ALLOWED_TARGET_HOSTS` when
running `./start.sh`. Supply exact, comma-separated hostnames; do not use wildcards
or include the metadata server. Keep existing target names in the list while saved
connections still use them. Removing a host also blocks subsequent use of saved
credentials for that host.

Use hostnames resolvable and reachable from the application container; `localhost`
inside it is not the host machine or a friend's laptop. Retain `verify-full` TLS
for real PostgreSQL servers and arrange their certificate trust and firewall rules.
Approving a hostname does not create PostgreSQL users or grant database access.

## Before keeping valuable work

Back up metadata and its credential-encryption key together, keep the bundle in
private storage outside this machine, and verify a restore. Source databases need
their own backups; metadata backups contain configuration and credentials, not
source rows. Rehearse recovery before depending on saved work.

Use the real shared-viewer browser acceptance tests as the rollout gate. Provide
test-account credentials using the documented private credentials-file mechanism;
do not reset an existing administrator or run demo resets on valuable data.
Run the canonical launcher and check both HTTPS origins after changing source.

Start with a small group. Exercise simultaneous report refreshes and cancellation
before increasing usage. Resource limits and query budgets constrain work but do
not guarantee that every source query is inexpensive.
