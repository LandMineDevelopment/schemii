import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { updateDesignTable } from "../../src/schemii/schemii/web/assets/design.js";
import { columnTypeKey, createColumnTypeAnalysis } from "../../src/schemii/schemii/web/assets/column-type-analysis.js";
import { postgresTypeOptions, parsePostgresTypeModifier, composePostgresTypeModifier, postgresTypeModifierSummary } from "../../src/schemii/common/web/assets/postgres-types.js";

// Execute the full editor with a small DOM, replacing layout-only integrations.
// Physical geometry, native select events and mobile visuals require mounted QA.
class Node {
  constructor(tag, options = {}, children = []) {
    this.tag = tag; this.children = []; this.parent = null; this.dataset = options.dataset || {};
    this.hidden = Boolean(options.hidden); this.value = options.attrs?.value || "";
    this.textContent = options.text || ""; this.disabled = false; this.checked = false;
    this.classes = new Set((options.className || "").split(" ")); this.listeners = new Map();
    this.classList = { toggle: (name, yes) => yes ? this.classes.add(name) : this.classes.delete(name), remove: name => this.classes.delete(name) };
    this.append(...children);
  }
  get isConnected() { return this.connected || Boolean(this.parent?.isConnected); }
  get childElementCount() { return this.children.length; }
  setAttribute() {} removeAttribute() {} toggleAttribute() {} setCustomValidity() {} focus() {}
  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
  before(node) { const at = this.parent.children.indexOf(this); node.parent = this.parent; this.parent.children.splice(at, 0, node); }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  replaceChildren(...nodes) { this.children.forEach(n => { n.parent = null; }); this.children = []; this.append(...nodes); }
  addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(fn); }
  emit(name, target = this) {
    const event = { target, currentTarget: this, preventDefault() {} };
    return Promise.all((this.listeners.get(name) || []).map(fn => fn(event)));
  }
  querySelectorAll(selector) {
    const matches = n => selector.startsWith(".") ? n.classes.has(selector.slice(1)) : Object.hasOwn(n.dataset, selector.slice(1, -1).replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase()));
    return this.children.flatMap(n => [...(matches(n) ? [n] : []), ...n.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const element = (tag, options, children) => new Node(tag, options, children);
const flush = () => new Promise(resolve => setImmediate(resolve));
const source = readFileSync(new URL("../../src/schemii/schemii/web/assets/table-editor.js", import.meta.url), "utf8")
  .replace(/^import[\s\S]*?;\n/gm, "").replace("export function createTableEditor", "function createTableEditor");

function fixture({ fingerprint = "baseline", analyze = async () => ({ sourceType: "uuid", requiresConversion: true, reason: "Needs conversion" }), history } = {}) {
  const elements = Object.fromEntries([
    "designColumns", "inspectorDesignColumns", "inspectorColumnCount", "saveInspectorTableButton", "discardInspectorTableButton", "addInspectorColumnButton", "inspectorTableForm", "inspector", "inspectorContent", "inspectorTableStatus", "inspectorTableName", "inspectorTitle", "mainLayout", "designTableStatus",
  ].map(key => [key, new Node("div")]));
  const root = new Node("main"); root.connected = true;
  root.append(elements.inspectorTableForm, elements.designColumns);
  elements.inspectorTableForm.append(elements.inspectorDesignColumns);
  const columns = Array.from({ length: 11 }, (_, i) => ({ id: `column_${String(i).padStart(32, "0")}`, name: `c${i}`, dataType: "uuid", nullable: true, defaultExpression: null, identity: null, generatedExpression: null }));
  const table = { id: "table_" + "0".repeat(32), name: "events", columns, keys: [], checks: [], indexes: [] };
  const context = { activeWorkspace: { id: "ws", connectionId: "pg" }, design: { revision: 1, fingerprint, content: { tables: [table], relationships: [] } }, editable: true, designSubmitting: false, catalogLoading: false, layoutConflict: false };
  const timers = new Map(); let sequence = 0; const calls = [], saves = [], historyCalls = [];
  const create = runInNewContext(source + "\ncreateTableEditor", {
    element, replace: (node, ...nodes) => node.replaceChildren(...nodes), errorPanel: error => element("p", { text: error.message }),
    api: {}, updateDesignTable, columnTypeKey, AbortController,
    setTimeout: fn => { timers.set(++sequence, fn); return sequence; }, clearTimeout: id => timers.delete(id),
    createColumnTypeAnalysis: options => createColumnTypeAnalysis({ ...options, setTimer: fn => { timers.set(++sequence, fn); return sequence; }, clearTimer: id => timers.delete(id) }),
    postgresTypeOptions, parsePostgresTypeModifier, composePostgresTypeModifier, postgresTypeModifierSummary,
    createIconButton: () => element("button"), installSortableList: () => ({ refresh() {} }),
    createSearchableSelect: ({ value, dataset }) => { const input = element("input", { attrs: { value }, dataset }); const root = element("div", {}, [input]); return { input, root, setOptions() {}, setLabel() {}, setValue: value => { input.value = value; }, destroy() {} }; },
  });
  const editor = create({ elements, getContext: () => context, onDraftChange() {}, setSubmitting: value => { context.designSubmitting = value; },
    onSettled() {}, flushLayoutBeforeTransition: async () => true,
    saveTable: async saved => { saves.push(saved); return context.design; }, onSaved() {}, conflictPanel: error => element("p", { text: error.message }),
    changeTransitions: {}, notify() {},
    loadTypeAnalysisHistory: async (...args) => { historyCalls.push(args); return history ? history(...args) : { designRevision: 1, baseline: { kind: "postgresql", fingerprint: "baseline" } }; },
    analyzeColumnType: (...args) => { calls.push(args); return analyze(...args); },
  });
  const render = () => editor.render({ designId: table.id });
  render();
  return {
    editor, context, elements, calls, saves, historyCalls, table, render,
    rows: () => elements.inspectorDesignColumns.children,
    warnings: () => elements.inspectorTableForm.querySelectorAll(".design-type-warning").filter(n => !n.hidden),
    async tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); await flush(); },
    async type(index, value) { const input = elements.inspectorDesignColumns.children[index].querySelector("[data-design-column-type]"); input.value = value; await input.emit("change"); await elements.inspectorTableForm.emit("change", input); },
    close() { editor.clearColumns(elements.inspectorDesignColumns); },
  };
}

test("rendering eleven unchanged UUID columns leaves no warning, dirty state or analysis fanout", async () => {
  const f = fixture({ analyze: () => assert.fail("unnecessary request") });
  await f.tick();
  assert.equal(f.rows().length, 11);
  assert.equal(f.calls.length, 0);
  assert.equal(f.historyCalls.length, 1);
  assert.equal(f.warnings().length, 0);
  assert.equal(f.editor.hasDraft, false);
  assert.equal(f.elements.saveInspectorTableButton.disabled, true);
  await f.type(0, " UUID "); await f.tick();
  assert.equal(f.editor.hasDraft, false);
  assert.equal(f.editor.columnValues(f.elements.inspectorDesignColumns)[0].dataType, "uuid");
  f.close();
});

test("real edits show conversion review, save desired type, and reverting restores clean state", async () => {
  const f = fixture(); await f.tick();
  await f.type(0, "text");
  assert.equal(f.editor.hasDraft, true);
  assert.equal(f.elements.saveInspectorTableButton.disabled, false);
  assert.match(f.warnings()[0].textContent, /Checking conversion/);
  await f.tick();
  assert.match(f.warnings()[0].textContent, /Needs conversion.*You can save this design.*During migration/);
  await f.elements.inspectorTableForm.emit("submit");
  assert.equal(f.saves[0].columns[0].dataType, "text");
  assert.equal(f.editor.hasDraft, false);
  // A fresh draft returned to the persisted desired value is clean.
  f.render(); await f.tick();
  await f.type(0, "text"); await f.tick(); await f.type(0, "uuid");
  assert.equal(f.editor.hasDraft, false);
  assert.equal(f.warnings().length, 0);
  f.close();
});

test("failed discovery renders one actionable error for eleven rows and preserves save", async () => {
  const f = fixture({ fingerprint: "changed", analyze: async () => { throw { status: 503, code: "migration_metadata_unavailable" }; } });
  await f.tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.warnings().length, 1);
  const summary = f.warnings()[0];
  assert.match(summary.children[0].textContent, /HTTP 503.*migration_metadata_unavailable.*save this design.*migration review/);
  assert.equal(summary.children[1].textContent, "Retry conversion checks");
  assert.equal(f.editor.hasDraft, false);
  await f.type(0, "text");
  assert.equal(f.elements.saveInspectorTableButton.disabled, false);
  await f.elements.inspectorTableForm.emit("submit");
  assert.equal(f.saves[0].columns[0].dataType, "text");
  assert.equal(f.calls.length, 1, "no automatic retries");
  f.close();
});

test("table removal and workspace change never publish late warning boxes", async () => {
  for (const boundary of ["table", "workspace"]) {
    let reject;
    const pending = new Promise((_, no) => { reject = no; });
    const f = fixture({ fingerprint: "changed", analyze: () => pending });
    await f.tick();
    if (boundary === "table") f.editor.render(null);
    else f.context.activeWorkspace = { id: "other", connectionId: "pg2" };
    reject({ code: "migration_metadata_unavailable", status: 503 });
    await flush();
    assert.equal(f.warnings().some(node => node.children[0]?.textContent.includes("unavailable")), false);
    f.close();
  }
});

test("permission denial stays explicit once and exposes an access recovery action", async () => {
  const f = fixture({ fingerprint: "changed", analyze: async () => { throw { status: 403, code: "product_access_denied" }; } });
  await f.tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.warnings().length, 1);
  assert.match(f.warnings()[0].children[0].textContent, /not permitted.*HTTP 403.*product_access_denied.*administrator/);
  f.close();
});
