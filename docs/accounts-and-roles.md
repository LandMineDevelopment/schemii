# Accounts and database roles

This private deployment has administrator-provisioned local accounts, per-product
roles, and role-managed PostgreSQL connections. It does not add public ingress or
automatically invite anyone to a Tailscale tailnet.

## First launch and accounts

Run `./start.sh`. Open `https://localhost:8001/login` or the configured tailnet
origin. The launcher prints the path to a persistent setup token, not its value.
Use it once to create the first application provisioner. Setup claims the existing
local prototype user ID, preserving saved objects and encrypted credentials. The
server refuses to run unauthenticated after accounts exist.

Provisioners create accounts and initial passwords in **Accounts & roles**. Users
can change their passwords on **My account**. A provisioner can reset a password
or disable an account; password changes revoke that user's sessions. The final
enabled provisioner cannot be disabled or demoted. Account deletion is not
exposed, so shared data cannot be cascaded away accidentally.

Sessions use hashed, expiring server-side tokens and Secure, HttpOnly, SameSite
cookies. Passwords use salted scrypt hashes. Mutations must originate from the
app's HTTPS origin; configured exact trusted origins support the tailnet proxy.
Never put a wildcard in `SCHEMII_TRUSTED_ORIGINS`.

## Product and database roles

Roles assign `schemii:access`, `schemoo:access`, `schemer:access`, and optionally
`schemer:author` for dashboard editing. A user may use Schemoo without receiving
Schemii Console or migration access. The reserved Application provisioners role
has `accounts:provision`: it permits account and role management, but implies no
product or PostgreSQL privilege. Migration 0039 preserves existing author and
administrator product access while making future assignments explicit.

A role may also bind an existing saved PostgreSQL connection. To let a member
use it in Schemii or Schemoo tools or edit Schemer dashboards, check **Use in
Schemii and Schemoo tools or Schemer editing** on the connection grant, even if
the PostgreSQL login is read-only. This selects the login for those app workflows;
it does not grant database writes. The grant and relevant product capability must
be in the same role. The profile and encrypted password remain owned by the
original account; the friend sees non-secret connection details but cannot edit
or delete it. New Schemii workspaces and Schemoo models remain private to the
friend who created them, while pointing to that managed connection. No
credentials are copied.

For a Schemer author to create a dashboard from a model, also grant
`schemoo:access` so they can select or create that model. A report viewer needs
only `schemer:access`, an explicit dashboard grant, and its connection grant in
the same role; viewers cannot edit reports or use generic model or SQL routes.
Export and drill-through are independent opt-ins. If roles assign different
database identities to one report, access fails closed rather than choosing a
stronger identity.

To offer read-only and write-capable roles, create distinct PostgreSQL logins.
Configure their table/column grants, row policies, and read/write/DDL privileges
in PostgreSQL; save each login as a separate connection profile; then bind each
profile to the intended app role. The app does not create or alter privileges in
the organization database. Users who share one login share its database-visible
identity. An application provisioner is not a PostgreSQL administrator.

## Database enforcement and revocation

Use dedicated non-owner, non-superuser logins without `BYPASSRLS`. Application
filters and hidden fields are not row or column security. Catalogs for managed
Schemoo sources and shared reports refresh under the assigned identity and hide
columns without `SELECT`. Schemii catalogs, Console work, migrations, Schemoo
previews, and Schemer reports use the selected profile. PostgreSQL rejects
unauthorized reads, writes, or DDL; the app never retries with another identity.

Account, session, role, and connection changes are checked before new database
work and before retained results are served. Shared-report streams recheck while
streaming. Previously rendered or downloaded data cannot be recalled. A SQL
transaction or migration already submitted to PostgreSQL may finish after a role
is removed; revocation cannot roll back confirmed database work.

## Metadata, deployment, and remaining scope

Migration 0037 added accounts, sessions, roles, memberships, grants, and audit
records. Migration 0038 separated shared-report actors from credential owners.
Migrations 0039–0041 add product capabilities and separate credential ownership
for Schemoo models and Schemii workspaces, migration plans, and Console receipts.
Saved resource IDs and encrypted credentials are preserved. Back up the metadata
volume and its encryption key together. Never delete account tables to regain
anonymous access.

The local demo fixture creates `accounts_demo.sales` with separate regional
reporting logins, row policies, and an inaccessible `secret` column. Fixture
passwords are local-demo-only. PostgreSQL integration tests use an explicitly
configured disposable database and skip when it is absent.

Tailnet invitations, SSO, email recovery, MFA, role-shared workspaces/models,
public links, individual app-identity RLS context, and cross-database joins are
separate capabilities. Invite a friend to the tailnet and give them an app account
and role separately; neither operation automatically performs the other.
