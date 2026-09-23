# Accounts and database roles

This private deployment has administrator-provisioned local accounts, per-product
roles, and Schemii-owned read-only PostgreSQL accounts. It does not add public ingress or
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

An application provisioner can create a **Schemii-owned read-only database
account** in Administration after a database administrator has created its
PostgreSQL login and configured its grants and row policies. The provisioner
saves the login as a Schemii-owned profile, tests connectivity, and grants
that profile through a role. Multiple Schemii-owned profiles can point at the
same database with different read-only PostgreSQL identities and therefore
different RLS views. Their encrypted passwords stay in Schemii metadata and are
never shown to role members. The application cannot create a PostgreSQL login,
make a write-capable login safe by calling it read-only, or alter the database's
RLS policies. A successful Schemii connection test does not certify read-only
privileges or RLS; verify both directly in PostgreSQL before assignment.

To let a member use a granted Schemii-owned profile in Schemii or Schemoo tools
or edit Schemer dashboards, check **Use in Schemii and Schemoo tools or Schemer
editing** on the connection grant. This permits those app workflows; it does not
grant database writes. The grant and relevant product capability must be in the
same role. New Schemii workspaces and Schemoo models remain private to the
friend who created them, while pointing to the managed connection. No
credentials are copied to that person's account.

Users can also save several **user-owned connections** with their own PostgreSQL
credentials under one Schemii login. Those profiles are private and are usable
only with the relevant product capability. Administrators cannot turn a personal
profile into a shared grant. Existing user-owned role grants from older metadata
are inactive; replace each with a Schemii-owned profile and rebind affected
dashboards before removing the legacy grant. The Administration role editor
lists such legacy grants for migration but does not offer them for assignment.

For a Schemer author to create a dashboard from a model, also grant
`schemoo:access` so they can select or create that model. A report viewer needs
only `schemer:access`, an explicit dashboard grant, and its connection grant in
the same role; viewers cannot edit reports or use generic model or SQL routes.
Export and drill-through are independent opt-ins. If roles assign different
database identities to one report, access fails closed rather than choosing a
stronger identity.

To offer distinct read-only RLS views, create distinct read-only PostgreSQL
logins. Configure table/column grants and row policies in PostgreSQL; save each
login as a separate Schemii-owned profile; then bind each profile to the intended
app role. A user with their own write-capable PostgreSQL login may save it as a
private profile, but that credential is not shared through roles. PostgreSQL
alone decides whether it can write. Users who share one Schemii-owned login
share its database-visible identity. An application provisioner is not a
PostgreSQL administrator.

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

Use the launcher-owned [backup and recovery workflow](recovery.md) to create a
paired private bundle and verify it in an isolated database. The
[friends rollout guide](friends-rollout.md) provides a first-session checklist.

The local demo fixture creates `accounts_demo.sales` with separate regional
reporting logins, row policies, and an inaccessible `secret` column. Fixture
passwords are local-demo-only. PostgreSQL integration tests use an explicitly
configured disposable database and skip when it is absent.

Tailnet invitations, SSO, email recovery, MFA, role-shared workspaces/models,
public links, individual app-identity RLS context, and cross-database joins are
separate capabilities. Invite a friend to the tailnet and give them an app account
and role separately; neither operation automatically performs the other.
