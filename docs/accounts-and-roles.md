# Accounts and database roles

This first authenticated release supports administrator-provisioned local accounts,
private author connections, and managed report access. It is intended for private
self-hosted testing; it does not add public ingress.

## First launch and accounts

Run `./start.sh`. Open `https://localhost:8001/login`, or the configured tailnet
origin. The launcher prints the location of a persistent setup token, not its value.
Use that token to create the first administrator. Setup atomically claims the
existing local prototype user ID, preserving all saved objects and encrypted
credentials. Setup cannot be repeated after an account exists. The server refuses
to start unauthenticated against metadata containing accounts.

The administrator provisions accounts and initial passwords in **Accounts & roles**.
Users can change their password on **My account**. Administrators can reset another
user's password or disable an account. Password changes revoke all of that user's
sessions. The final enabled administrator cannot be disabled or demoted. Account
records are retained; deleting a user and cascading shared content is not exposed.

Sessions use hashed, expiring server-side tokens and Secure, HttpOnly, SameSite
cookies. Passwords use salted scrypt hashes. Mutation requests must originate from
the app's HTTPS origin; configured exact trusted origins support the tailnet proxy.
Never put a wildcard in `SCHEMII_TRUSTED_ORIGINS`.

## Roles and connections

A role can contain any number of users, saved database connections, and dashboard
grants. A managed connection is an existing saved connection explicitly granted to
a role; existing credential IDs and encryption bindings remain intact. No password
is disclosed when a connection is granted. Multiple roles may reuse a connection.

An **Author** role allows users to create their own private connections and author
models, dashboards, SQL queries, and schema workspaces. Managed role connections
are currently usable only for the explicitly granted saved dashboards; they do not
grant arbitrary SQL or shared model authoring. An application administrator is not
a PostgreSQL administrator: all database operations still use saved credentials.

Each dashboard grant binds one connection in the same role. It must target the same
host, port, and database as the model's authored source. Separate credentials can
therefore execute the same dashboard under different PostgreSQL permissions.
Different host aliases are deliberately not inferred to mean the same server.
If a user's roles assign different identities to one dashboard, access fails with
an explanation; it never silently selects the stronger identity. Consolidate that
user's role bindings to choose one connection.

Export and drill-through are independent opt-ins. Shared viewers cannot edit the
report, use generic model queries, run SQL, or use the author's credentials. Filters
are sent with each execution and do not overwrite another viewer's selections.
Personal connections remain private; sharing a dashboard does not share its author's
connection automatically.

## Database enforcement

Use dedicated non-owner, non-superuser reporting logins without BYPASSRLS. Apply
row policies and column SELECT grants in PostgreSQL. Users sharing one login share
its database-visible identity. Application filters and hidden semantic fields are
not row or column security.

Shared report catalogs refresh under the assigned identity and include only
SELECT-permitted columns and usable relationships. Reports, option lookups,
drill-through, and CSV export use that same identity. Missing privileges fail; no
fallback identity is attempted. Query results stay transient and execution receipts
belong to the actual viewer. Shared report handles cannot be fetched/exported
through generic result endpoints.

Account, session, role, and connection changes are checked during report streams;
revoked streams stop and close their source sessions. Previously downloaded or
rendered data cannot be recalled. Other already-admitted author jobs (such as a
migration or raw SQL transaction) retain their existing lifecycle; revocation
blocks subsequent API requests, not a rollback of database work already submitted.

## Metadata and recovery

Migration 0037 adds accounts, sessions, roles, memberships, connection/report grants,
audit events, and login throttling. Migration 0038 separates the execution actor
from its credential owner while preserving private workspace identities. Existing migration files are unchanged. This
iteration retains owner-private model/workspace foreign keys and introduces a
narrow shared-report execution adapter instead of rewriting ownership or encrypted
credentials. The setup token and encryption key live outside metadata PostgreSQL.
Back up the metadata volume and its encryption key together using your operator
backup process. Never delete the account tables to regain anonymous access.

Local demo bootstrap creates `accounts_demo.sales` with separate `report_east` and
`report_west` logins, row policies, and an inaccessible `secret` column. Fixture
passwords in `dev/postgres/account-access-fixture.sql` are local-demo-only. Ordinary
saved data is untouched by this repeatable fixture.

## Verification and remaining scope

Unit/API tests cover session isolation, CSRF, setup races, role checks, private
credentials, export/drill permissions, source mismatch, ambiguous identities,
revocation, and column filtering. PostgreSQL integration tests use the existing
explicit disposable-database test configuration and skip when it is absent.
Physical browser verification must use the launcher-built HTTPS application.

SSO, email invitations/recovery, MFA, managed model authoring, organization tenancy,
public links, individual app-identity RLS context, and cross-database joins are
separate follow-up capabilities. A database-target/access-profile schema split can
be introduced later without changing the role-to-execution contract.
