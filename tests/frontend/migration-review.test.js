import assert from "node:assert/strict";
import test from "node:test";

import {
  migrationCanApply,
  migrationConflictsResolved,
  migrationExecutionNeedsPolling,
  migrationExecutionNeedsReconciliation,
  migrationStepRisk,
  migrationWorkspaceEligible,
} from "../../src/schemii/schemii/web/assets/migration-review.js";

const workspace = {
  id: "ws_demo",
  mode: "design",
  connectionId: "pg_demo",
  database: "demo",
  namespace: "public",
};
const design = { revision: 4 };

test("migration review is available only for database-backed designs", () => {
  assert.equal(migrationWorkspaceEligible(workspace, design), true);
  assert.equal(migrationWorkspaceEligible({ ...workspace, connectionId: null }, design), false);
  assert.equal(migrationWorkspaceEligible({ ...workspace, mode: "live" }, design), false);
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
  assert.equal(migrationCanApply({ ...candidate, allowDestructive: true, confirmExternalChanges: true, execution: { status: "failed" } }), false);
});

test("execution state distinguishes polling from explicit reconciliation", () => {
  assert.equal(migrationExecutionNeedsPolling({ status: "reserved" }), true);
  assert.equal(migrationExecutionNeedsPolling({ status: "applying" }), true);
  assert.equal(migrationExecutionNeedsPolling({ status: "failed" }), false);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "uncertain" }), true);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "reconciliation_required" }), true);
  assert.equal(migrationExecutionNeedsReconciliation({ status: "succeeded" }), false);
});
