---
name: connection-setup
description: Use for Schemii PostgreSQL connection setup, Docker host mapping, ports, SSL modes, profile fields, or password guidance.
---

# Connection Setup

- Never request, repeat, store, or emit a password. The user enters it directly in Schemii.
- In normal Docker bridge mode, a database in another Compose service is reached by its service name, not `localhost`.
- From a container to a host database on Docker Desktop, use `host.docker.internal`. Base Compose does not add that mapping on Linux.
- In Linux `local-db` or `ai-local-db` mode, Schemii remains on private bridge networks and reaches a loopback-bound PostgreSQL server at `127.0.0.1:5432` through the installation's private Unix-socket relay.
- `localhost` inside a normal container refers to that container, not the host.
- Use the actual PostgreSQL port and choose SSL mode according to server policy. Do not weaken certificate verification without explaining the risk.
- Connection setup is a proposal requiring UI review and password entry; it does not test or save the profile itself.
