# Schemii

The previous Schemii and Schemer implementation is preserved under [`archive/`](archive/) as a read-only reference from checkpoint `04a8fbb`.

New unified-backend and Schemii, Schemoo, and Schemer frontend architecture work belongs at the repository root. Archived code should remain unchanged unless an explicit archival correction is required.

## Backend structure

The backend uses one composition root and an independent package for each product API:

```text
src/schemii/
├── main.py
├── common/
│   ├── api/
│   ├── connections/
│   ├── metadata/
│   └── postgres/
├── schemii/
│   ├── models.py
│   ├── routes.py
│   └── workspaces/
├── schemoo/
│   ├── models.py
│   └── routes.py
└── schemer/
    ├── models.py
    └── routes.py
```

`main.py` constructs the FastAPI application, shared services, common routes, and three product routers. Each product package owns its product-specific API routes and Pydantic contracts.

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

The metadata PostgreSQL replacement is intentionally deferred at the active code boundaries in `common/metadata/factory.py` and `common/metadata/models.py`. Repository operations already require an owner ID so persistent users, sessions, encrypted credentials, product ownership, and Tailscale identities can replace the prototype adapters without changing product route contracts.

Install the development environment and run the application with:

```bash
python -m pip install -e '.[dev]'
uvicorn schemii.main:app --host 127.0.0.1 --reload
```
