# QA fixture ownership and readiness

## Scoped Schemii cleanup

`python -m testing.harness.cleanup_schemii_sweep --run RUN_ID
--workspace-fixtures EXACT_MAP` is protected even when invoked directly. Stop
the harness first. A passed scenario result is not a stopped controller: cleanup
checks the saved PID and birth tick of the controller and every recorded browser
and worker, and rejects missing or unresolved process ownership.

The helper holds the per-run lifecycle lock, then the repository's exclusive
deployment lock, then the account-reservation lock over validation and all
application mutations. These are the same OS lock files used across linked
worktrees by setup, reset and account allocation. Any reservation blocks cleanup,
including one from a newer run. The helper never releases account reservations.

Seed workspace IDs require matching run/fixture bindings and true creation
ownership. Additional workspaces and connections need exact creation receipts in
that lane's `resources.cleanupReceipts`:

```json
[{"kind":"workspace","id":"ws_EXACT_ID","createdAt":"2026-09-29T16:00:00Z"}]
```

Kinds are `workspace`, `connection` and `chat`. Workspace/connection names must
also match the run prefix, and creation must not predate the run. A chat receipt
must refer to an assigned workspace. Chats made after the run starts inside a
seed-owned workspace can be removed with that owned workspace. A matching name
alone never grants ownership. Unledgered resources are preserved for safe
reconciliation; the CLI exits 4 when these remain.

Private `sweep-cleanup.json` beside the manifest records run/tag, retained
ownership input, individual successful deletions and unledgered objects. A
failure leaves `cleanup-pending` receipts for scoped retry. Inputs and evidence
remain intact; repeated cleanup does not delete a newly reserved owner's data.

The regression tests exercise direct Python invocation, live completed owners,
unresolved launches, a bounded race against the existing Node reservation code,
partial failure/retry, and exact-scope idempotence. Live application cleanup and
application acceptance remain separate coordinator checks.
