# Published interview demo

Release: [demo-v0.1.0](https://github.com/LandMineDevelopment/schemii/releases/tag/demo-v0.1.0).

## Start it

On this machine, run this from any directory:

```bash
schemii-demo
```

The installed command selects the frozen release checkout at
`/home/kasey/.local/share/schemii/releases/demo-v0.1.0`. Its guarded launcher checks
the exact tag and clean source, then executes that checkout's `./start.sh --demo`.
It builds the selected version, starts all backend services, prepares the demo
account and database workspace, and reports ready only after authenticated checks
against both HTTPS origins pass. Startup uses the existing deployment lease, so
an active QA run can block a launch until its owner releases the stack.

To identify the selected version without starting anything:

```bash
schemii-demo --version
```

The output includes the tag, exact Git commit and release URL. Keep this checkout
clean; edit the development worktree when changing the application. New published
demo versions should get new tags and directories rather than moving this tag.
Git index flags that could conceal source edits also stop the launch.

## Login and data

Username: `demo-admin`.

The password and direct workspace links remain in the private file
`/home/kasey/projects/schema_foundry/.schemii/demo/login.txt`. The launcher also
prints its actual path, honoring an explicitly configured private demo state
directory. Never attach that file, `.schemii/`, database dumps, logs or credentials
to a public release.

The account has administrator access and permissions for Schemii, Schemoo and
Schemer. It has a saved connection to the seeded PostgreSQL database and an
imported `bookstore` workspace with tables, relationships, views and real order
rows. Repeating the command preserves credentials, account and resource IDs,
saved designs, and database changes. It refuses conflicting identities, disabled
accounts, revoked permissions and retargeted resources instead of resetting them.

The canonical app is <https://localhost:8001>. On another Tailscale-connected
device, use <https://omarchy.taile4f57f.ts.net>. Use the direct workspace links
printed after successful verification. External AI providers still need their
normal account connection; this release does not distribute provider credentials.

## Reinstall the frozen source

Prerequisites are Git, Bash, Python 3.10 or later, OpenSSL, Docker Engine with
Compose, and the configured private Tailscale Serve route. Agents must use the
canonical launcher; do not call Docker directly or switch an active shared
development checkout to the release.

From the existing repository, fetch the published tag and create a separate
runtime checkout if that release directory is missing:

```bash
git fetch origin tag demo-v0.1.0
git worktree add --detach /home/kasey/.local/share/schemii/releases/demo-v0.1.0 demo-v0.1.0
/home/kasey/.local/share/schemii/releases/demo-v0.1.0/dev/demo/published.sh
```

If the directory already exists, inspect and reuse it. Do not overwrite it or
discard its changes automatically. The normal launcher resolves persistent
secrets and demo credentials from the primary checkout, so changing the runtime
checkout does not create new database passwords or lose the demo account.

The machine shortcut at `/home/kasey/.local/bin/schemii-demo` delegates to the
release's `dev/demo/published.sh`; the full path above works without the shortcut.

## Verification scope

This is a published demo prerelease. The release notes record the exact commit
and verification performed. Existing evidence includes focused parser, launcher
and setup regressions, independent source review, and fresh/repeated live starts
that authenticated through both HTTPS origins, tested the saved connection,
opened the workspace, inspected the live catalog and read bookstore order rows.
The published launcher additionally checks that the intended clean tag is used.

Full selected local acceptance, including the complete database and browser
matrices, remains pending in the task PR. The demo release does not mark that PR
accepted, merge it, or certify a production release. Private runtime state and
credentials are retained locally rather than being part of the published source.
