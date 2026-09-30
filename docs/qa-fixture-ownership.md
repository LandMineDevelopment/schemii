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

## Ready two-account native pilot

Generate the bounded pilot from the retained fixtures and a new tagged workspace
map; this reuses the existing seed generator rather than creating another fixture
system:

```bash
python -m testing.harness.schemii_workspaces --tag UNIQUE \
  --accounts qa_designer_012,qa_designer_013,qa_designer_014 \
  --output artifacts/qa/UNIQUE-workspaces.json
python -m testing.harness.schemii_sweep --pilot --tag UNIQUE \
  --pilot-accounts qa_designer_012,qa_designer_013 \
  --pilot-reviewer qa_designer_014 \
  --workspace-fixtures artifacts/qa/UNIQUE-workspaces.json \
  --output artifacts/qa/UNIQUE-native-pilot.json
```

The two explicitly selected testers and optional independent reviewer each have
separate seed-owned local workspace IDs. Each has three desktop workflows: exact table creation and
save/reopen, JSON/SQL file inspection and owned JSON upload, and validation plus
keyboard activation of Save and reload recovery. Checks run authenticated as
that account, verify product capabilities, exact username/name/target, empty
desired design, and the other account's workspace returning 404. Missing grants
or unowned local seeds fail generation; changed starting state blocks preflight.
No AI provider turn is required or counted.

All generated Schemii missions now carry explicit viewport contracts. Desktop
performs stateful writes; mobile is saved-state readback with a dependency on the
matching desktop functional pass. Its title and instructions expressly exclude
independent mobile creation/upload/mutation coverage. If desktop cannot produce
the required state, mobile is blocked prerequisite. Independent mobile write
acceptance still requires another fresh tagged fixture/run.

The full sweep requires empty local desired designs and empty writer catalogs.
Follow-up seeded designs require the exact recorded fingerprint and two-table
oracle; reader chat checks include read authority, approval and denied write
execution. Read-only SELECT approval is explicitly authorized only in the
assigned reader schema. Provider/model readiness remains distinct from a real
paid/provider response and recovery result.

The 513-order oracle names every page boundary at 100 rows and the full ordered
ID sequence, deterministic values and null cases. Set the Console page size
through UI and restore it afterward. Catalog preview ordering is assessed
separately; it cannot be assumed to match `ORDER BY id`.

Workspace creation journals and chat provisioning refuse unledgered matching
names, including a create that succeeded before its response/receipt was saved.
Keep the pending journal and reconcile exact ownership; never infer ownership
from a name or delete that collision automatically.
