# Schemii

The previous Schemii and Schemer implementation is preserved under [`archive/`](archive/) as a read-only reference from checkpoint `04a8fbb`.

New unified-backend and Schemii, Schemoo, and Schemer frontend architecture work belongs at the repository root. Archived code should remain unchanged unless an explicit archival correction is required.

## Application structure

The application uses one composition root and an independent package for each product API. Schemii also owns its packaged, buildless frontend:

```text
src/schemii/
├── main.py
├── common/
│   ├── api/
│   ├── connections/
│   ├── metadata/
│   └── postgres/
├── schemii/
│   ├── frontend.py
│   ├── models.py
│   ├── routes.py
│   ├── web/
│   │   ├── index.html
│   │   └── assets/
│   └── workspaces/
├── schemoo/
│   ├── models.py
│   └── routes.py
└── schemer/
    ├── models.py
    └── routes.py
```

`main.py` constructs the FastAPI application, shared services, common routes, and three product routers. Each product package owns its product-specific API routes and Pydantic contracts. The same application serves the Schemii frontend at `/`, so browser requests use the active same-origin API without a separate frontend process or build step.

## Current API

The current API deliberately uses one local application user while product workflows are prototyped:

- `GET /api/v1/session` returns the valid local prototype principal.
- `/api/v1/connections` manages owner-scoped, durable PostgreSQL connection profiles.
- `/api/v1/schemii/workspaces` manages database-independent design workspaces, optional Schemii target bindings, and table positions.
- `/api/v1/schemii/workspaces/{id}/catalog` returns a live PostgreSQL catalog snapshot.
- Interactive OpenAPI documentation is available at `/docs`.

This phase has no application authentication and the Compose ingress remains bound to loopback. Remote ingress is a deployment concern. The storage design does not depend on Tailscale; a future authenticated deployment can replace the local principal without changing connection or product route signatures.

Connection profiles survive application rebuilds and restarts in the deployment's private PostgreSQL volume. Passwords are accepted only by write models, represented as `SecretStr`, authenticated-encrypted before entering metadata PostgreSQL, omitted from profiles and errors, and decrypted only while opening the selected target. The persistent encryption key lives outside PostgreSQL under `.schemii/secrets`; losing that key makes stored passwords unrecoverable. Workspaces remain in memory during this implementation stage.

Each profile targets exactly one PostgreSQL host. TLS certificate and hostname verification (`verify-full`) is the default; weaker libpq SSL modes must be selected explicitly for environments that require them.

The implemented prototype persists no schema copies. A workspace may be detached or contain an exact connection/database/namespace binding, and currently stores only its name and table `{name, x, y}` positions. Attached workspaces read columns, constraints, relationships, indexes, triggers, functions, views, and materialized views from one bounded, read-only PostgreSQL introspection snapshot. Typed desired-design, import, export, migration, Console, and AI route stubs are registered for review in the API map; each carries `x-schemii-status: planned` and returns an explicit `501 planned_capability` until its TODO-owned implementation exists.

## Schemii frontend

The frontend manages real prototype connections and workspaces, renders live catalog data, and saves table positions through `/api/v1`. It does not create sample schemas, fabricate rows, or emulate unavailable server operations. Controls inherited from earlier product workflows remain discoverable, but unsupported actions open a capability-specific notice rather than pretending to succeed.

Shared frontend primitives live in `src/schemii/schemii/web/assets/ui.js` and `ui.css`. That layer owns cross-page interaction and presentation contracts such as buttons, icons, action groups, menus, tooltips, state panels, dialog chrome, and dock panes. Page modules own workflow-specific dialog lifecycles and product composition such as the schema canvas, catalog cards, API route stages, and response-contract rails. `src/schemii/common` is the shared Python backend package; frontend code does not belong there.

Promote a frontend implementation into the shared UI layer when multiple real page consumers need the same contract or when one central implementation is required for accessibility or interaction correctness. Keep page-specific code local rather than adding speculative variants. If a future Schemoo or Schemer frontend becomes a second package-level consumer, establish a frontend package boundary at that point instead of coupling it to Schemii's asset directory prematurely.

The current catalog experience is read-only. Schema mutation, SQL execution, migration, and AI workflows remain planned contracts rather than implemented capabilities. Example restoration and application shutdown are deliberately excluded from the rewrite API.

`common/metadata/factory.py` selects the durable PostgreSQL repositories when metadata deployment settings are present and retains in-memory adapters for isolated unit tests. Repository operations require an owner ID so persistent users, sessions, and additional product ownership can be added without changing product route contracts.

Start the local application stack with the repository launcher:

```bash
./start.sh
```

Then open <https://localhost:8001/>. [`start.sh`](start.sh) is the single Docker Compose startup boundary and runs Docker directly without `sudo`. If Docker group membership was added after the current shell started, the launcher refreshes only its own process through `newgrp docker`; otherwise it uses the current session unchanged. The application containers never receive the Docker socket. The script builds the current source, waits for all services to become healthy, and reports the resulting service state.

The launcher creates a persistent self-signed server certificate for `localhost` and `127.0.0.1` under ignored local state at `.schemii/tls`, plus a persistent credential-encryption key under `.schemii/secrets`. Both private keys are excluded from Git and the Docker build context. Back up the metadata database and `.schemii/secrets/metadata_encryption_key` together. HTTPS protects transport but does not add application authentication.

Chromium-family browsers on Linux can trust only this exact certificate, without granting it certificate-authority privileges, through the user's NSS database:

```bash
certutil -D -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)" 2>/dev/null || true
certutil -A -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)" -t "P,," -i .schemii/tls/localhost.crt
```

Restart the browser or T3Code after changing trust. Remove the exception with `certutil -D -d "sql:$HOME/.pki/nssdb" -n "Schemii localhost (exact certificate)"`. Other clients can either trust `.schemii/tls/localhost.crt` through their own certificate store or retain their normal self-signed-certificate warning.

Runtime configuration is grouped at the top of `start.sh` and may also be supplied through `SCHEMII_TEST_APP_PORT`, `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, `SCHEMII_TEST_POSTGRES_PASSWORD`, `SCHEMII_STARTUP_TIMEOUT`, `SCHEMII_TLS_DIRECTORY`, `SCHEMII_TLS_CERTIFICATE_DAYS`, and `SCHEMII_SECRET_DIRECTORY`. The Compose ingress remains loopback-only because this prototype intentionally has no application authentication.

## Seeded Docker test deployment

[`compose.test.yaml`](compose.test.yaml) runs the packaged Schemii application with a private PostgreSQL 17 service and a health-gated, one-shot seed job. `start.sh` is the supported startup command:

```bash
./start.sh
```

Open <https://localhost:8001/> and create a connection with:

| Field | Value |
|---|---|
| Name | `Seeded test database` |
| Host | `postgres` |
| Port | `5432` |
| Database | `schemii_test` |
| Username | `schemii` |
| Password | `schemii-local-test` |
| SSL mode | `disable` |

Use namespace `bookstore` for the populated Mercury Books tutorial data. It includes nine tables, 500 orders, relationships, checks, JSONB, generated columns, indexes, a trigger, functions, four views, and a populated materialized view. Use `catalog_lab` to test partitioned tables, enum and domain-backed columns, a composite foreign key, a partial index, an exclusion constraint, a procedure, and populated and unpopulated materialized views.

PostgreSQL has no published host port and is reachable only by the application and one-shot seed job on the private database network. A minimal, non-root Nginx sidecar publishes the loopback HTTP port while the application remains on internal application and database networks. The application, ingress, and seed job run as non-root users; no service mounts or accesses the Docker socket; and both HTTP containers use read-only filesystems. The default credentials are intentionally limited to this local test deployment and must not be used in production.

Stop the deployment while retaining its database volume with:

```bash
docker compose -f compose.test.yaml down
```

Reset all seeded database state by removing the test volume and starting again:

```bash
docker compose -f compose.test.yaml down --volumes
./start.sh
```

The seed records a fixture version and refuses to use a retained volume with an older catalog shape. Run the reset commands after pulling seed changes. PostgreSQL initialization variables also apply only when its data volume is first created, so changing database, username, or password overrides requires the same reset.

Set `SCHEMII_TEST_APP_PORT`, `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, or `SCHEMII_TEST_POSTGRES_PASSWORD` before running `start.sh` to override the development defaults.
