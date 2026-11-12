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

// Exercise the production controller and its DOM event handlers without a runtime.
import { createMigrationReviewController } from "../../src/schemii/schemii/web/assets/migration-review.js";
class ReviewNode {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {}; this.hidden = false; this.value = ""; this.checked = false; this.open = false; this.disabled = false; this.isConnected = true; this.classList = { toggle() {} }; this.ownText = ""; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  get childElementCount() { return this.children.length; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.ownText = ""; this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  querySelectorAll() { return []; }
  focus() {}
  showModal() { this.open = true; }
  async fire(name) { for (const callback of this.listeners[name] || []) await callback({ target: this, preventDefault() {}, stopPropagation() {} }); await settleReview(); }
}
function allReviewNodes(root) { return [root, ...root.children.flatMap(allReviewNodes)]; }
const settleReview = () => new Promise(resolve => setImmediate(resolve));
const deferredReview = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const maintenance = { viewId: "view_totals", name: 'public."Sales totals"', operation: "refresh", eligible: true, populatedBefore: false, populatedAfter: true, preservedIndexes: ["totals_unique"], lock: "ACCESS EXCLUSIVE", cachedDataConsequence: "The cached rows will be replaced with current query results.", blockingReasons: [] };
function reviewedPlan(body = {}, sequence = 1) {
  const selected = body.refreshMaterializedViewIds?.length > 0;
  return { id: `plan_${sequence}`, reviewDigest: `digest_${sequence}`, status: "reviewable", applyCapable: true, complete: true, designRevision: 4, baselineRevision: 2, driftStatus: "none", expiresAt: "2027-01-01T00:00:00Z", destructive: selected, requiresExternalChangeAcknowledgement: false, requiresMaterializedMaintenanceAcknowledgement: selected,
    compatibility: { editableComplete: false, operationSafe: true, evidenceVersion: 1, impact: [], protectedScope: [], unknownReasons: [], blockingReasons: [] },
    materializedRefreshCandidates: [maintenance], materializedMaintenance: selected ? [maintenance] : [], blockingDifferences: [], warnings: [], externalChanges: [], conflicts: [], columnOrderRebuilds: [], columnTypeConversions: [],
    steps: selected ? [{ index: 1, operation: "refresh_materialized_view", objectPath: maintenance.name, objectKind: "materialized_view", sql: 'REFRESH MATERIALIZED VIEW public."Sales totals";', destructive: true, requiresLock: true }] : [] };
}
function reviewHarness(overrides = {}) {
  const oldDocument = globalThis.document, oldNode = globalThis.Node;
  globalThis.Node = ReviewNode;
  globalThis.document = { createElement: tag => new ReviewNode(tag), createTextNode: text => { const node = new ReviewNode("#text"); node.textContent = text; return node; } };
  const elements = Object.fromEntries(["dialog", "body", "status", "refresh", "resolve", "apply", "rebuildHelp"].map(key => [key, new ReviewNode()]));
  elements.rebuildHelp.hidden = true; elements.dialog.append(...Object.values(elements).filter(node => node !== elements.dialog));
  const source = { workspace: { ...workspace, revision: 3 }, design: { ...design, fingerprint: "design_a" }, connectionRevision: 1 };
  const plans = [], executions = [], confirms = [], errors = [], notices = []; let listed = 0;
  const api = {
    listMigrationExecutions: async () => { listed += 1; return []; },
    createMigrationPlan: async (id, body) => { plans.push(structuredClone(body)); return reviewedPlan(body, plans.length); },
    createMigrationExecution: async (id, body) => { executions.push({ id, body }); return { id: "exec_one", status: "succeeded", completedStepCount: 1 }; },
    ...overrides,
  };
  let reload = async () => { source.design = { ...source.design, revision: source.design.revision + 1 }; };
  const controller = createMigrationReviewController({ api, elements, getContext: () => source, reloadWorkspace: () => reload(), confirm: value => confirms.push(value), notify: value => notices.push(value), notifyError: value => errors.push(value) });
  const nodes = () => allReviewNodes(elements.dialog);
  const labelled = label => nodes().find(node => node.attrs["aria-label"] === label);
  const confirmation = text => nodes().find(node => node.tagName === "label" && node.textContent.includes(text))?.children.find(node => node.tagName === "input");
  const select = async () => { const checkbox = labelled(`Refresh ${maintenance.name} once`); checkbox.checked = true; await checkbox.fire("change"); };
  const authorize = async () => { let input = confirmation("Include destructive changes"); input.checked = true; await input.fire("change"); input = labelled("Acknowledge materialized-view maintenance for this plan"); input.checked = true; await input.fire("change"); };
  const close = () => { controller.close(); elements.dialog.open = false; globalThis.document = oldDocument; globalThis.Node = oldNode; };
  return { controller, elements, source, api, plans, executions, confirms, notices, errors, nodes, labelled, confirmation, select, authorize, close, setReload: callback => { reload = callback; }, get listed() { return listed; } };
}

test("mounted review selects unchanged-SQL refresh in a fresh plan and requires a separate exact-plan acknowledgement", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open();
  assert.equal(h.elements.apply.disabled, true); assert.deepEqual(h.plans[0].refreshMaterializedViewIds, []);
  assert.match(h.elements.body.textContent, /ACCESS EXCLUSIVE/); assert.match(h.elements.body.textContent, /totals_unique/);
  await h.select(); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, [maintenance.viewId]);
  assert.equal(h.elements.apply.disabled, true); await h.authorize(); assert.equal(h.elements.apply.disabled, false);
  await h.elements.apply.fire("click"); const accepted = h.confirms[0]; assert.match(accepted.message, /materialized-view maintenance/);
  await accepted.callback();
  assert.deepEqual(h.executions, [{ id: "plan_3", body: { reviewDigest: "digest_3", confirmDestructive: true, confirmExternalChanges: false, confirmMaterializedMaintenance: true } }]);
  assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []); assert.equal(h.source.design.revision, 5); assert.equal(h.errors.length, 0);
  assert.equal(h.elements.apply.disabled, true);
});

test("blocked refresh candidates expose exact reasons and never offer a policy override", async t => {
  const h = reviewHarness({ createMigrationPlan: async () => ({ ...reviewedPlan(), materializedRefreshCandidates: [{ ...maintenance, eligible: false, blockingReasons: ["The current owner is not recorded.", "Dependency evidence is incomplete."] }] }) }); t.after(h.close);
  await h.controller.open(); assert.equal(h.labelled(`Refresh ${maintenance.name} once`).disabled, true);
  assert.match(h.elements.body.textContent, /The current owner is not recorded/); assert.match(h.elements.body.textContent, /Dependency evidence is incomplete/);
});

test("failed plan removes obsolete Apply authority while retaining correction intent; stale-source errors clear it", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  const create = h.api.createMigrationPlan; h.api.createMigrationPlan = async () => { throw new Error("temporary catalog read failed"); };
  await h.controller.refresh(); assert.equal(h.elements.apply.disabled, true); assert.match(h.elements.status.textContent, /temporary catalog read failed/);
  h.api.createMigrationPlan = create; await h.controller.refresh(); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, [maintenance.viewId]);
  assert.equal(h.labelled("Acknowledge materialized-view maintenance for this plan").checked, false);
  h.api.createMigrationPlan = async () => { throw Object.assign(new Error("Catalog changed. Review again."), { code: "catalog_changed", status: 409 }); };
  await h.controller.refresh(); h.api.createMigrationPlan = create; await h.controller.refresh(); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []);
});

test("cancel and source revisions reject late responses and stale confirmation callbacks", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize(); await h.elements.apply.fire("click");
  const staleConfirm = h.confirms[0]; await h.controller.refresh(); await staleConfirm.callback(); assert.equal(h.executions.length, 0);
  h.source.connectionRevision += 1; assert.equal(h.controller.syncContext(), false); assert.equal(h.elements.apply.disabled, true); assert.match(h.elements.status.textContent, /source or saved design changed/);
  await h.controller.open(); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []);
  const late = deferredReview(); h.api.createMigrationPlan = () => late.promise;
  const pending = h.controller.refresh(); h.controller.close(); h.elements.dialog.open = false; late.resolve(reviewedPlan({ refreshMaterializedViewIds: [maintenance.viewId] }, 99)); await pending;
  assert.equal(h.elements.apply.disabled, true); await staleConfirm.callback(); assert.equal(h.executions.length, 0);
});

test("same-workspace design revision changes and navigation invalidate pending plans without mutating saved state", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select();
  const late = deferredReview(); h.api.createMigrationPlan = () => late.promise; const pending = h.controller.refresh();
  h.source.design = { ...h.source.design, revision: 8 }; late.resolve(reviewedPlan({ refreshMaterializedViewIds: [maintenance.viewId] }, 99)); await pending;
  assert.equal(h.elements.apply.disabled, true); assert.equal(h.source.design.revision, 8); assert.match(h.elements.status.textContent, /choices and confirmations were cleared/);
  h.source.workspace = { ...h.source.workspace, id: "ws_other" }; h.controller.syncContext(); assert.equal(h.elements.apply.disabled, true);
});

test("lost Apply response requires server execution recovery before another request; terminal failure clears one-shot intent", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  let attempts = 0; h.api.createMigrationExecution = async () => { attempts += 1; throw new Error("response lost"); };
  await h.elements.apply.fire("click"); await h.confirms[0].callback(); assert.equal(h.elements.apply.disabled, true);
  h.api.listMigrationExecutions = async () => [{ id: "exec_reserved", status: "uncertain", completedStepCount: 0 }];
  const retry = allReviewNodes(h.elements.status).find(node => node.tagName === "button"); await retry.fire("click");
  assert.equal(h.elements.apply.textContent, "Check transaction outcome"); assert.equal(attempts, 1); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, [maintenance.viewId]);
  h.api.reconcileMigrationExecution = async () => ({ id: "exec_reserved", revision: 2, status: "failed", completedStepCount: 0 });
  await h.elements.apply.fire("click"); assert.equal(attempts, 1); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []);
});

test("server compatibility authority and maintenance acknowledgement independently gate Apply", () => {
  const plan = reviewedPlan({ refreshMaterializedViewIds: [maintenance.viewId] });
  const value = { plan, allowDestructive: true, confirmMaterializedMaintenance: false };
  assert.equal(migrationCanApply(value), false); assert.equal(migrationCanApply({ ...value, confirmMaterializedMaintenance: true }), true);
  assert.equal(migrationCanApply({ ...value, confirmMaterializedMaintenance: true, plan: { ...plan, compatibility: { operationSafe: false } } }), false);
});

test("refresh after a lost Apply response checks for a reserved execution even after an earlier failed attempt", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  h.api.createMigrationExecution = async () => ({ id: "earlier", status: "failed", completedStepCount: 0 });
  await h.elements.apply.fire("click"); await h.confirms[0].callback();
  await h.select(); await h.authorize(); h.api.createMigrationExecution = async () => { throw new Error("response lost after reserve"); };
  await h.elements.apply.fire("click"); await h.confirms[1].callback();
  let recovered = 0; h.api.listMigrationExecutions = async () => { recovered += 1; return [{ id: "reserved", status: "uncertain", completedStepCount: 0 }]; };
  const before = h.plans.length; await h.elements.refresh.fire("click"); assert.equal(recovered, 1); assert.equal(h.plans.length, before); assert.equal(h.elements.apply.textContent, "Check transaction outcome");
  h.api.reconcileMigrationExecution = async () => { throw new Error("reconciliation read failed"); }; await h.elements.apply.fire("click");
  h.api.reconcileMigrationExecution = async () => ({ id: "reserved", status: "failed", completedStepCount: 0 });
  const retry = allReviewNodes(h.elements.status).find(node => node.tagName === "button"); await retry.fire("click"); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []);
});

test("reserved execution prevents replanning while its transaction outcome is pending", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  h.api.createMigrationExecution = async () => ({ id: "running", status: "reserved", completedStepCount: 0 });
  await h.elements.apply.fire("click"); await h.confirms[0].callback();
  const count = h.plans.length; assert.equal(h.elements.body.inert, true); await h.controller.refresh(); assert.equal(h.plans.length, count); assert.equal(h.elements.apply.disabled, true);
});

test("committed execution with a failed workspace reload retries the read without executing again", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  h.setReload(async () => { throw new Error("saved workspace read failed"); });
  await h.elements.apply.fire("click"); await h.confirms[0].callback();
  assert.equal(h.executions.length, 1); assert.equal(h.elements.apply.disabled, true); assert.match(h.elements.status.textContent, /saved workspace read failed/);
  h.setReload(async () => { h.source.design = { ...h.source.design, revision: 5 }; });
  const retry = allReviewNodes(h.elements.status).find(node => node.tagName === "button"); await retry.fire("click");
  assert.equal(h.executions.length, 1); assert.equal(h.source.design.revision, 5); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []); assert.equal(h.plans.at(-1).expectedDesignRevision, 5);
});

test("saved-revision conflict reloads the saved snapshot with cleared maintenance intent", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); const create = h.api.createMigrationPlan;
  h.api.createMigrationPlan = async () => { throw Object.assign(new Error("Design revision changed"), { status: 409, code: "design_changed" }); }; await h.controller.refresh();
  h.api.createMigrationPlan = create; let reloaded = 0; h.setReload(async () => { reloaded += 1; h.source.design = { ...h.source.design, revision: 12 }; });
  await h.elements.refresh.fire("click"); assert.equal(reloaded, 1); assert.equal(h.plans.at(-1).expectedDesignRevision, 12); assert.deepEqual(h.plans.at(-1).refreshMaterializedViewIds, []);
});

test("late successful-Apply reload cannot suppress a newer workspace revision guard", async t => {
  const h = reviewHarness(); t.after(h.close); await h.controller.open(); await h.select(); await h.authorize();
  const late = deferredReview(); h.setReload(() => late.promise); await h.elements.apply.fire("click"); const pending = h.confirms[0].callback();
  h.controller.close(); h.elements.dialog.open = false; await h.controller.open();
  h.source.workspace = { ...h.source.workspace, revision: 9 }; assert.equal(h.controller.syncContext(), false); assert.equal(h.elements.apply.disabled, true);
  late.resolve(); await pending; assert.equal(h.executions.length, 1); assert.equal(h.source.workspace.revision, 9); assert.equal(h.elements.apply.disabled, true);
});
