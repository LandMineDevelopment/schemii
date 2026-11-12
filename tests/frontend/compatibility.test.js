import assert from "node:assert/strict";
import test from "node:test";
import { compatibilityObjectLabel, importCompatibility, materializedAuthoringGuidance, renderCompatibilityEvidence, renderWorkspaceCompatibility } from "../../src/schemii/schemii/web/assets/compatibility.js";

class CompatibilityNode {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.value = ""; this.hidden = false; this.ownText = ""; this.classList = { toggle() {} }; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ""; this.children = children; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}
const nodes = root => [root, ...root.children.flatMap(nodes)];
function withDom(t) {
  const oldDocument = globalThis.document, oldNode = globalThis.Node;
  globalThis.Node = CompatibilityNode;
  globalThis.document = { createElement: tag => new CompatibilityNode(tag), createTextNode: text => { const node = new CompatibilityNode("#text"); node.textContent = text; return node; } };
  t.after(() => { globalThis.document = oldDocument; globalThis.Node = oldNode; });
}
const issue = { objectName: 'public."Archived totals"', reason: "The materialized view has storage options outside the editable model.", featureCode: "unsupported_storage", dependencyComplete: false, protectedScope: [{ kind: "materialized_view", namespace: "public", name: "Archived totals" }], unknownReasons: ["The dependency boundary was not captured."] };
const summary = { complete: false, catalogCapturedAt: "2026-11-11T12:00:00Z", safetyEvidenceVersion: 1, migrationGuidance: "Only operations outside the proven protected scope may be reviewed.", issues: [issue] };

test("historical coverage and dependency uncertainty never establish current operation safety", () => {
  assert.equal(importCompatibility({}), null);
  const coverage = importCompatibility({ importSummary: summary }); assert.equal(coverage.complete, false); assert.equal(coverage.version, 1);
  assert.match(coverage.unknown.join(" "), /boundary was not captured/);
  assert.equal(coverage.operationSafe, undefined);
  const legacy = importCompatibility({ importSummary: { complete: true } }); assert.match(legacy.unknown[0], /no versioned preservation evidence/);
  assert.equal(compatibilityObjectLabel({ namespace: "Sales.Data", name: 'x"y', signature: "integer" }), '"Sales.Data"."x\\"y" (integer)');
});

test("workspace guidance is visible before editing and explains omissions, SQL export and recovery", t => {
  withDom(t); const host = new CompatibilityNode(); let reviews = 0, exports = 0;
  renderWorkspaceCompatibility(host, {}); assert.equal(host.hidden, true); assert.equal(host.textContent, "");
  renderWorkspaceCompatibility(host, {}); assert.equal(host.hidden, true);
  const value = { workspace: { id: "one", importSummary: summary }, design: { revision: 1 }, onReview: () => { reviews += 1; }, onExport: () => { exports += 1; } };
  renderWorkspaceCompatibility(host, value); assert.equal(host.hidden, false);
  assert.match(host.textContent, /protected PostgreSQL features/); assert.match(host.textContent, /omitted features are not editable or reconstructed/);
  assert.match(host.textContent, /baseline cannot restore omitted objects/); assert.match(host.textContent, /editable design only/);
  assert.match(host.textContent, /dependency boundary was not captured/); assert.match(host.textContent, /Archived totals/);
  const buttons = nodes(host).filter(node => node.tagName === "button"); buttons[0].listeners.click(); buttons[1].listeners.click(); assert.equal(reviews, 1); assert.equal(exports, 1);
  renderWorkspaceCompatibility(host, { ...value, loading: true }); assert.equal(nodes(host).find(node => node.tagName === "button").disabled, true);
  renderWorkspaceCompatibility(host, { ...value, workspace: { id: "new" } }); assert.equal(host.hidden, true); assert.equal(host.textContent, "");
});

test("protected-feature search and paging keep large imports usable without losing exact identities", t => {
  withDom(t); const host = new CompatibilityNode();
  renderWorkspaceCompatibility(host, { workspace: { id: "large", importSummary: { ...summary, issues: Array.from({ length: 75 }, (_, i) => ({ ...issue, objectName: `protected_${i}` })) } }, design: {} });
  assert.equal(nodes(host).filter(node => node.tagName === "article").length, 30);
  const more = nodes(host).find(node => node.textContent === "Show more protected features"); more.listeners.click(); assert.equal(nodes(host).filter(node => node.tagName === "article").length, 60);
  const search = nodes(host).find(node => node.tagName === "input"); search.value = "protected_74"; search.listeners.input(); assert.equal(nodes(host).filter(node => node.tagName === "article").length, 1); assert.equal(more.hidden, true);
  search.value = "absent"; search.listeners.input(); assert.match(host.textContent, /No matching protected features/);
});

test("readiness and action updates preserve disclosure, filter, focus and scroll with current callbacks", t => {
  withDom(t); const host = new CompatibilityNode(); const calls = [];
  const workspace = { id: "one", importSummary: { ...summary, issues: [issue, { ...issue, objectName: "public.other" }] } };
  const initial = { workspace, design: {}, loading: true, onReview: () => calls.push("old review"), onExport: () => calls.push("old export") };
  renderWorkspaceCompatibility(host, initial);
  const details = nodes(host).find(node => node.tagName === "details"); details.open = true;
  const search = nodes(host).find(node => node.tagName === "input"); search.value = "Archived"; search.listeners.input();
  globalThis.document.activeElement = search; host.scrollTop = 73;
  const [review, exportButton] = nodes(host).filter(node => node.tagName === "button");
  assert.equal(review.disabled, true); assert.equal(exportButton.disabled, true);

  renderWorkspaceCompatibility(host, { ...initial, loading: false, onReview: () => calls.push("ready review"), onExport: () => calls.push("ready export") });
  assert.equal(nodes(host).find(node => node.tagName === "details"), details);
  assert.equal(details.open, true); assert.equal(search.value, "Archived");
  assert.equal(globalThis.document.activeElement, search); assert.equal(host.scrollTop, 73);
  assert.equal(nodes(host).filter(node => node.tagName === "article").length, 1);
  assert.equal(review.disabled, false); assert.equal(exportButton.disabled, false);
  review.listeners.click(); exportButton.listeners.click();
  assert.deepEqual(calls, ["ready review", "ready export"]);

  renderWorkspaceCompatibility(host, { ...initial, loading: false, reviewDisabled: true, onExport: () => calls.push("restricted export") });
  assert.equal(review.disabled, true); assert.equal(exportButton.disabled, false); assert.equal(details.open, true);
  exportButton.listeners.click();
  renderWorkspaceCompatibility(host, { ...initial, loading: false, exportDisabled: true, onReview: () => calls.push("restricted review") });
  assert.equal(review.disabled, false); assert.equal(exportButton.disabled, true); assert.equal(search.value, "Archived");
  review.listeners.click();
  // Callback refresh must also work when the enabled/disabled state is unchanged.
  renderWorkspaceCompatibility(host, { ...initial, loading: false, exportDisabled: true, onReview: () => calls.push("latest review") });
  review.listeners.click();
  assert.deepEqual(calls, ["ready review", "ready export", "restricted export", "restricted review", "latest review"]);
  renderWorkspaceCompatibility(host, { ...initial, design: null, loading: false });
  assert.equal(review.disabled, true); assert.equal(exportButton.disabled, true); assert.equal(details.open, true);
});

test("workspace or import-summary replacement resets disclosure context and action callbacks", t => {
  withDom(t); const host = new CompatibilityNode(); const calls = [];
  const value = { workspace: { id: "one", importSummary: summary }, design: {}, onReview: () => calls.push("one") };
  renderWorkspaceCompatibility(host, value);
  const originalDetails = nodes(host).find(node => node.tagName === "details"); originalDetails.open = true;
  const originalSearch = nodes(host).find(node => node.tagName === "input"); originalSearch.value = "Archived";
  renderWorkspaceCompatibility(host, { ...value, workspace: { id: "two", importSummary: summary }, onReview: () => calls.push("two") });
  const nextDetails = nodes(host).find(node => node.tagName === "details");
  assert.notEqual(nextDetails, originalDetails); assert.notEqual(nextDetails.open, true);
  assert.equal(nodes(host).find(node => node.tagName === "input").value, "");
  nodes(host).find(node => node.tagName === "button").listeners.click();
  assert.deepEqual(calls, ["two"]);

  const updated = { ...summary, complete: true, issues: [], migrationGuidance: "Updated import" };
  renderWorkspaceCompatibility(host, { ...value, workspace: { id: "two", importSummary: updated }, onReview: () => calls.push("updated") });
  assert.match(host.textContent, /Editable import complete/);
  assert.equal(nodes(host).some(node => node.tagName === "details"), false);
  nodes(host).find(node => node.tagName === "button").listeners.click();
  assert.deepEqual(calls, ["two", "updated"]);
  renderWorkspaceCompatibility(host, { ...value, workspace: { id: "two" } });
  assert.equal(host.hidden, true); assert.equal(host.textContent, "");
});

test("fresh review renders server blockers and unknowns independently of editable coverage", t => {
  withDom(t);
  const panel = renderCompatibilityEvidence({ editableComplete: true, operationSafe: false, evidenceVersion: 1, blockingReasons: ["An inbound dependent cannot be replayed."], unknownReasons: ["The current owner is unknown."], impact: [{ namespace: "public", name: "totals" }], protectedScope: [{ namespace: "public", name: "child" }] });
  assert.match(panel.textContent, /could not prove this operation safe/); assert.match(panel.textContent, /Editable import: complete/); assert.match(panel.textContent, /inbound dependent/); assert.match(panel.textContent, /current owner is unknown/); assert.match(panel.textContent, /"public"\."child"/);
  assert.match(renderCompatibilityEvidence(null).textContent, /Import coverage alone does not establish preservation/);
});

test("materialized authoring distinguishes creation population, definition replacement and one-time refresh", () => {
  assert.match(materializedAuthoringGuidance(), /stores cached query results/); assert.match(materializedAuthoringGuidance(), /Saving this editor changes the desired design only/);
  assert.match(materializedAuthoringGuidance(), /does not refresh an existing cache/); assert.match(materializedAuthoringGuidance(), /unchanged SQL/);
  assert.match(materializedAuthoringGuidance(true), /blocked by dependencies, ownership, indexes or privileges/);
});
