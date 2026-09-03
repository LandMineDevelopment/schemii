# Demo scenarios

Each directory is one repeatable manual-test state built from
`../migration-demo.sql`.

- `manifest.json` names the scenario and orders its desired-design alterations.
- `design-*.json` describes changes applied through Schemii's validated design
  repository, so history and revisions behave exactly like user edits.
- `target.sql` runs only after the untouched catalog has been imported and saved
  as the migration baseline. It therefore represents an external PostgreSQL
  change rather than seed state.
- `workspaceMode` defaults to `design`. The `live-browser` scenario uses `live`
  so relation discovery, row previews, and live view lineage run against the
  current PostgreSQL catalog rather than an imported desired design.

`sourceRevision` intentionally remains `runtime`. The launcher records the exact
Git commit (and a `+dirty` suffix when applicable), plus a SHA-256 digest of the
base, manifest, and alterations in `schemii_fixture.fixture_state`. This keeps a
saved scenario tied to the code actually running without maintaining a stale
commit comment by hand.
