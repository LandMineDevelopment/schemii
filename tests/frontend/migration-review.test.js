import assert from "node:assert/strict";
import test from "node:test";

import {
  clearAppliedMigrationReview,
  migrationCanApply,
  migrationConflictsResolved,
  migrationExecutionNeedsPolling,
  migrationExecutionNeedsReconciliation,
  migrationStepRisk,
  migrationWorkspaceEligible,
  retainedConversionChoices,
  hasUnreviewedConversions,
} from "../../src/schemii/schemii/web/assets/migration-review.js";

const workspace = {
  id: "ws_demo",
  connectionId: "pg_demo",
  database: "demo",
  namespace: "public",
};
const design = { revision: 4 };

test("migration review is available only for database-backed designs", () => {
  assert.equal(migrationWorkspaceEligible(workspace, design), true);
  assert.equal(migrationWorkspaceEligible({ ...workspace, connectionId: null }, design), false);
  assert.equal(migrationWorkspaceEligible(workspace, null), false);
});

test("migration steps expose their strongest operational risk", () => {
  assert.equal(migrationStepRisk({ destructive: true, dataMovement: true, requiresLock: true }), "destructive");
  assert.equal(migrationStepRisk({ destructive: false, dataMovement: true, requiresLock: true }), "data");
  assert.equal(migrationStepRisk({ destructive: false, dataMovement: false, requiresLock: true }), "lock");
  assert.equal(migrationStepRisk({ destructive: false, dataMovement: false, requiresLock: false }), "standard");
});

test("every server conflict requires one allowed explicit resolution", () => {
  const plan = {
    conflicts: [
      { id: "one", allowedResolutions: ["pull_live", "keep_design"] },
      { id: "two", allowedResolutions: ["pull_live"] },
    ],
  };
  const resolutions = new Map([["one", "keep_design"]]);
  assert.equal(migrationConflictsResolved(plan, resolutions), false);
  resolutions.set("two", "keep_design");
  assert.equal(migrationConflictsResolved(plan, resolutions), false);
  resolutions.set("two", "pull_live");
  assert.equal(migrationConflictsResolved(plan, resolutions), true);
});

test("apply remains gated by plan authority and required acknowledgements", () => {
  const plan = {
    status: "reviewable",
    applyCapable: true,
    destructive: true,
    requiresExternalChangeAcknowledgement: true,
    steps: [{ index: 1 }],
  };
  const candidate = {
    plan,
    execution: null,
    busy: false,
    allowDestructive: false,
    confirmExternalChanges: false,
  };
  assert.equal(migrationCanApply(candidate), false);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true }), false);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true }), true);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true, unreviewedConversions: true }), false);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true, execution: { status: "failed" } }), true);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true, execution: { status: "applying" } }), false);
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true, execution: { status: "reconciliation_required" } }), false);
});

test("execution state distinguishes polling from explicit reconciliation", () => {
  assert.equal(migrationExecutionNeedsPolling({ status: "reserved" }), true);
  assert.equal(migrationExecutionNeedsPolling({ status: "applying" }), true);
  assert.equal(migrationExecutionNeedsPolling({ status: "failed" }), false);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "uncertain" }), true);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "reconciliation_required" }), true);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "succeeded" }), false);
});

test("successful execution clears choices that belong to the applied plan", () => {
  const state = {
    plan: { id: "applied" },
    rebuildTableIds: new Set(["table_applied"]),
    columnTypeConversions: new Map([["column", { strategy: "strict" }]]),
    conversionDrafts: new Map([["column", "value::integer"]]),
    allowDestructive: true,
    confirmExternalChanges: true,
    resolutions: new Map([["conflict", "keep_design"]]),
  };

  clearAppliedMigrationReview(state);

  assert.equal(state.plan, null);
  assert.equal(state.rebuildTableIds, null);
  assert.equal(state.columnTypeConversions.size, 0);
  assert.equal(state.conversionDrafts.size, 0);
  assert.equal(state.allowDestructive, false);
  assert.equal(state.confirmExternalChanges, false);
  assert.equal(state.resolutions.size, 0);
});

test("conversion acceptance follows stable column IDs and expires when a change disappears", () => {
  const choices = new Map([
    ["retained", { columnId: "retained", strategy: "custom", expression: "renamed::integer" }],
    ["reverted", { columnId: "reverted", strategy: "strict" }],
  ]);
  const retained = retainedConversionChoices([{ columnId: "retained", columnName: "renamed" }], choices);
  assert.deepEqual([...retained.keys()], ["retained"]);
  assert.equal(retained.get("retained").expression, "renamed::integer");
  assert.equal(retainedConversionChoices([], choices).size, 0);
});

test("editing a custom expression requires a new review before apply", () => {
  const choices = new Map([["column", { strategy: "custom", expression: "value::integer" }]]);
  assert.equal(hasUnreviewedConversions(choices, new Map()), false);
  assert.equal(hasUnreviewedConversions(choices, new Map([["column", " value::integer "]])), false);
  assert.equal(hasUnreviewedConversions(choices, new Map([["column", "round(value)::integer"]])), true);
  assert.equal(hasUnreviewedConversions(new Map(), new Map([["column", "value::integer"]])), true);
});
