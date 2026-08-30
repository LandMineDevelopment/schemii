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

## Prototype API

The current API is deliberately single-user and ephemeral while product workflows are prototyped:

- `GET /api/v1/session` returns the valid local prototype principal.
- `/api/v1/connections` manages owner-scoped in-memory PostgreSQL connections.
- `/api/v1/schemii/workspaces` manages Schemii target bindings and table positions.
- `/api/v1/schemii/workspaces/{id}/catalog` returns a live PostgreSQL catalog snapshot.
- Interactive OpenAPI documentation is available at `/docs`.

This phase has no remote authentication and must remain bound to loopback. Authentication and persistent, encrypted per-user credentials belong to the metadata PostgreSQL replacement.

Connections, credentials, and workspace positions are held only in backend memory and reset when the process restarts. Passwords are accepted only by write models, represented as `SecretStr`, omitted from profiles and errors, and resolved internally only while opening PostgreSQL.

Each profile targets exactly one PostgreSQL host. TLS certificate and hostname verification (`verify-full`) is the default; weaker libpq SSL modes must be selected explicitly for environments that require them.

Schemii persists no schema copies. Workspaces contain only a connection/database/namespace binding and table `{name, x, y}` positions. Columns, constraints, relationships, indexes, triggers, functions, views, and materialized views always come from one bounded, read-only PostgreSQL introspection snapshot.

## Schemii frontend

The frontend manages real prototype connections and workspaces, renders live catalog data, and saves table positions through `/api/v1`. It does not create sample schemas, fabricate rows, or emulate unavailable server operations. Controls inherited from earlier product workflows remain discoverable, but unsupported actions open a capability-specific notice rather than pretending to succeed.

The current catalog experience is read-only. Schema mutation, SQL execution, migration, AI, example restoration, and shutdown workflows require future authenticated backend contracts before they can be enabled.

The metadata PostgreSQL replacement is intentionally deferred at the active code boundaries in `common/metadata/factory.py` and `common/metadata/models.py`. Repository operations already require an owner ID so persistent users, sessions, encrypted credentials, product ownership, and Tailscale identities can replace the prototype adapters without changing product route contracts.

Install the development environment and run the application with:

```bash
python -m pip install -e '.[dev]'
uvicorn schemii.main:app --host 127.0.0.1 --reload
```

Then open <http://127.0.0.1:8000/>. Keep this prototype bound to loopback because it intentionally has no remote authentication.

## Seeded Docker test deployment

[`compose.test.yaml`](compose.test.yaml) runs the packaged Schemii application with a private PostgreSQL 17 service and a health-gated, one-shot seed job:

```bash
docker compose -f compose.test.yaml up --build -d
```

Open <http://127.0.0.1:8001/> and create a connection with:

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
docker compose -f compose.test.yaml up --build -d
```

The seed records a fixture version and refuses to use a retained volume with an older catalog shape. Run the reset commands after pulling seed changes. PostgreSQL initialization variables also apply only when its data volume is first created, so changing database, username, or password overrides requires the same reset.

Set `SCHEMII_TEST_APP_PORT`, `SCHEMII_TEST_POSTGRES_DB`, `SCHEMII_TEST_POSTGRES_USER`, or `SCHEMII_TEST_POSTGRES_PASSWORD` before running Compose to override the development defaults.
