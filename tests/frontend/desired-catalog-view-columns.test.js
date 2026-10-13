import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

import { viewAnalysisContextSignature } from "../../src/schemii/schemii/web/assets/design-change.js";
import { designToCatalog } from "../../src/schemii/schemii/web/assets/design.js";
import {
  createViewAnalysisCollection,
  desiredViewColumnSummary,
} from "../../src/schemii/schemii/web/assets/view-analysis.js";

// Exercise the actual browser renderer and app wiring with a small DOM. Their
// browser-only absolute imports do not resolve in Node; unrelated UI functions
// stay unevaluated rather than replacing the column-summary integration.
const catalogSource = readFileSync(new URL("../../src/schemii/schemii/web/assets/catalog.js", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "")
  .replace(/^export /gm, "");
const appSource = readFileSync(new URL("../../src/schemii/schemii/web/assets/app.js", import.meta.url), "utf8");
const analysisSource = appSource.slice(
  appSource.indexOf("let desiredViewAnalysisContexts ="),
  appSource.indexOf("function workspaceCanvasPositions("),
);
const detailSource = appSource.slice(
  appSource.indexOf("function renderSelectedViewDetail("),
  appSource.indexOf("function selectedDesignView("),
);

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.listeners = new Map();
    this.ownText = "";
    this.open = false;
  }

  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  get lastElementChild() { return this.children.at(-1); }
  append(...children) {
    for (const child of children) { child.parentElement = this; this.children.push(child); }
  }
  replaceChildren(...children) { this.ownText = ""; this.children = [...children]; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  click() { this.listeners.get("click")?.(); }
  remove() { this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
  querySelector() { return null; }
  querySelectorAll(selector) {
    assert.equal(selector, "details.catalog-object[open]");
    return descendants(this).filter(node => node.tagName === "details" && node.className === "catalog-object" && node.open);
  }
}

function element(tagName, options = {}, children = []) {
  const node = new Element(tagName);
  if (options.text !== undefined) node.textContent = options.text;
  if (options.className) node.className = options.className;
  if (options.dataset) Object.assign(node.dataset, options.dataset);
  node.append(...children);
  return node;
}

function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush() { await new Promise(resolve => setImmediate(resolve)); }

const { allViews, renderObjects } = runInNewContext(`${catalogSource}\n({ allViews, renderObjects })`, {
  element,
  replace: node => node.replaceChildren(),
  normalizedSearch: query => query.trim().toLocaleLowerCase(),
  emptyPanel: (mark, title, copy) => element("div", { text: `${mark} ${title} ${copy}` }),
  desiredViewColumnSummary,
});

function fixture() {
  return {
    revision: 1,
    fingerprint: "initial",
    content: {
      tables: [{
        id: "table_customers", name: "customers",
        columns: [{ id: "column_id", name: "id", dataType: "integer" }],
        keys: [], checks: [], indexes: [],
      }],
      views: [
        { id: "view_totals", name: "customer_totals", kind: "view", definition: "SELECT id FROM customers" },
        { id: "view_stored", name: "stored_customers", kind: "materialized_view", definition: "SELECT * FROM customers" },
      ],
      functions: [], relationships: [],
    },
  };
}

function appHarness(load, design = fixture()) {
  const state = {
    activeWorkspace: { id: "workspace_one", name: "Customer model" },
    design,
    activeLayer: "views",
    selectedViewId: null,
    selectedViewOutputOrdinal: null,
  };
  state.catalog = designToCatalog(state.activeWorkspace, state.design);
  const elements = { objectsDialog: { open: true }, objectsSearch: { value: "" }, viewDetail: new Element("div") };
  const container = new Element("div");
  const stories = [];
  const calls = [];
  let harness;
  function render() {
    const result = renderObjects(container, state.catalog, elements.objectsSearch.value, () => {}, {
      getViewAnalysis: view => harness.viewAnalysis.snapshot(harness.desiredViewAnalysisContext(view)),
      onAnalyzeView: view => { void harness.viewAnalysis.ensure(harness.desiredViewAnalysisContext(view)); },
      onRetryViewAnalysis: view => {
        void harness.viewAnalysis.ensure(harness.desiredViewAnalysisContext(view), { force: true, notify: true });
      },
    });
    harness.setObjectViewAnalysisUpdater(result.updateViewAnalysis);
    return result;
  }
  harness = runInNewContext(`${analysisSource}\n${detailSource}\n({
    syncViewAnalysisSelection, desiredViewAnalysisContext, viewAnalysis, renderSelectedViewDetail,
    setObjectViewAnalysisUpdater: value => { updateObjectViewAnalysis = value; },
  })`, {
    state, elements, allViews, createViewAnalysisCollection, viewAnalysisContextSignature,
    isDesignWorkspace: () => Boolean(state.activeWorkspace),
    currentCatalogView: () => allViews(state.catalog).find(view => view.designId === state.selectedViewId) || null,
    api: {
      analyzeDesignView(workspaceId, body, { signal }) {
        const call = { workspaceId, body, signal };
        calls.push(call);
        return load(call);
      },
    },
    renderDesignViewStory: (node, options) => stories.push(options),
    renderObjectsBrowser: render,
    element,
  });
  function commit() {
    state.catalog = designToCatalog(state.activeWorkspace, state.design);
    void harness.syncViewAnalysisSelection();
    render();
  }
  void harness.syncViewAnalysisSelection();
  return { ...harness, state, elements, container, stories, calls, render, commit };
}

function row(harness, id) { return harness.container.children.find(node => node.dataset.changeObjectId === id); }
function summary(harness, id) { return row(harness, id).children[0].children[0].children[1].textContent; }
function storyOutputs(harness) { return harness.stories.at(-1).analysis?.outputs; }

test("ordinary and materialized browser summaries use the inspector's shared output analysis", async () => {
  const requests = new Map();
  const harness = appHarness(({ body }) => {
    const pending = deferred();
    requests.set(body.viewId, pending);
    return pending.promise;
  });
  harness.render();
  assert.equal(summary(harness, "view_totals"), "Analyzing columns…");
  assert.equal(summary(harness, "view_stored"), "Analyzing columns…");
  const initialRow = row(harness, "view_totals");
  initialRow.open = true;
  await flush();
  const outputs = ["id", "name", "region", "order_count", "total"].map(name => ({ name }));
  requests.get("view_totals").resolve({ outputs });
  requests.get("view_stored").resolve({ outputs: [{ name: "id" }] });
  await flush();
  assert.equal(summary(harness, "view_totals"), "5 columns");
  assert.equal(summary(harness, "view_stored"), "1 column");
  assert.equal(row(harness, "view_totals").open, true);
  assert.equal(row(harness, "view_totals"), initialRow, "asynchronous analysis preserves the active DOM row");
  assert.match(row(harness, "view_totals").children[1].textContent, /Catalog detail5 columns/);

  harness.state.selectedViewId = "view_totals";
  await harness.syncViewAnalysisSelection();
  harness.renderSelectedViewDetail();
  assert.equal(storyOutputs(harness), outputs);
  assert.equal(harness.calls.length, 2);
  harness.viewAnalysis.clear();
});

test("definition, source-column and workspace changes invalidate both summaries and inspector results", async () => {
  const requests = [];
  const harness = appHarness(call => {
    const pending = deferred();
    requests.push({ call, ...pending });
    return pending.promise;
  });
  harness.state.selectedViewId = "view_totals";
  harness.commit();
  await flush();
  const oldContext = harness.desiredViewAnalysisContext(allViews(harness.state.catalog)[0]);
  const initial = requests.slice();
  harness.state.design.content.views[0].definition = "SELECT id, 1 AS one FROM customers";
  harness.commit();
  harness.renderSelectedViewDetail();
  assert.equal(storyOutputs(harness), undefined);
  assert.equal(summary(harness, "view_totals"), "Analyzing columns…");
  await flush();
  for (const request of initial) assert.equal(request.call.signal.aborted, true);
  const current = requests.slice(initial.length);
  for (const request of current) request.resolve({ outputs: [{ name: "id" }, { name: "one" }] });
  await flush();
  for (const request of initial) request.resolve({ outputs: Array(5).fill({ name: "stale" }) });
  await flush();
  assert.equal(summary(harness, "view_totals"), "2 columns");
  assert.equal(storyOutputs(harness).length, 2);
  assert.equal(harness.viewAnalysis.snapshot(oldContext).analysis, null);

  harness.state.design.content.tables[0].columns.push({ id: "column_name", name: "name", dataType: "text" });
  harness.commit();
  harness.renderSelectedViewDetail();
  assert.equal(summary(harness, "view_stored"), "Analyzing columns…");
  assert.equal(storyOutputs(harness), undefined);
  await flush();
  const sourceRequests = requests.slice(initial.length + current.length);
  for (const request of sourceRequests) request.resolve({ outputs: [{ name: "id" }, { name: "name" }] });
  await flush();
  assert.equal(summary(harness, "view_stored"), "2 columns");

  harness.state.design.content.tables[0].columns[0].dataType = "bigint";
  harness.commit();
  await flush();
  const typeRequests = requests.slice(initial.length + current.length + sourceRequests.length);
  assert.equal(summary(harness, "view_totals"), "Analyzing columns…");
  harness.state.activeWorkspace = { id: "workspace_two", name: "Another model" };
  harness.commit();
  await flush();
  for (const request of typeRequests) {
    assert.equal(request.call.signal.aborted, true);
    request.reject(new Error("obsolete source type failure"));
  }
  await flush();
  assert.equal(summary(harness, "view_totals"), "Analyzing columns…");
  assert.equal(harness.calls.at(-1).workspaceId, "workspace_two");
  const workspaceRequests = requests.slice(initial.length + current.length + sourceRequests.length + typeRequests.length);
  for (const request of workspaceRequests) request.resolve({ outputs: [{ name: "another" }] });
  await flush();
  assert.equal(summary(harness, "view_totals"), "1 column");
  harness.viewAnalysis.clear();
});

test("a late result for another visible view updates its row without repainting the selected inspector", async () => {
  const requests = new Map();
  const harness = appHarness(({ body }) => {
    const pending = deferred();
    requests.set(body.viewId, pending);
    return pending.promise;
  });
  harness.state.selectedViewId = "view_totals";
  harness.commit();
  harness.renderSelectedViewDetail();
  harness.state.selectedViewId = "view_stored";
  void harness.syncViewAnalysisSelection();
  harness.renderSelectedViewDetail();
  await flush();
  const rendered = harness.stories.length;
  requests.get("view_totals").resolve({ outputs: Array(5).fill({ name: "ordinary" }) });
  await flush();
  assert.equal(summary(harness, "view_totals"), "5 columns");
  assert.equal(harness.stories.length, rendered);
  assert.equal(harness.stories.at(-1).view.designId, "view_stored");
  requests.get("view_stored").resolve({ outputs: [{ name: "selected" }] });
  await flush();
  assert.equal(storyOutputs(harness)[0].name, "selected");
  assert.equal(summary(harness, "view_stored"), "1 column");
  harness.viewAnalysis.clear();
});

test("browser failure exposes an explicit retry and preserves the expanded row through recovery", async () => {
  let failed = true;
  const harness = appHarness(({ body }) => {
    if (body.viewId === "view_totals" && failed) throw new Error("Cannot analyze this view");
    return { outputs: [{ name: "id" }] };
  });
  harness.render();
  row(harness, "view_totals").open = true;
  await flush();
  assert.equal(summary(harness, "view_totals"), "Column count unavailable");
  const retry = descendants(row(harness, "view_totals")).find(node => node.tagName === "button" && node.textContent === "Retry column analysis");
  assert.ok(retry);
  harness.render();
  await flush();
  assert.equal(harness.calls.filter(call => call.body.viewId === "view_totals").length, 1);
  failed = false;
  retry.click();
  assert.equal(summary(harness, "view_totals"), "Analyzing columns…");
  await flush();
  assert.equal(summary(harness, "view_totals"), "1 column");
  assert.equal(row(harness, "view_totals").open, true);
  assert.equal(descendants(row(harness, "view_totals")).some(node => node.textContent === "Retry column analysis"), false);
  harness.viewAnalysis.clear();
});

test("unchanged design refresh reuses analysis; a fresh page reload derives the same count", async () => {
  const load = () => ({ outputs: [{ name: "id" }, { name: "total" }] });
  const harness = appHarness(load);
  harness.render();
  await flush();
  harness.state.design = structuredClone(harness.state.design);
  harness.state.design.revision += 1;
  harness.commit();
  await flush();
  assert.equal(harness.calls.length, 2);
  assert.equal(summary(harness, "view_totals"), "2 columns");

  const reloaded = appHarness(load, structuredClone(harness.state.design));
  reloaded.render();
  assert.equal(summary(reloaded, "view_totals"), "Analyzing columns…");
  await flush();
  assert.equal(summary(reloaded, "view_totals"), "2 columns");
  assert.equal(reloaded.calls.length, 2);
  harness.viewAnalysis.clear();
  reloaded.viewAnalysis.clear();
});

test("the object browser analyzes only matching visible views and preserves live catalog counts", () => {
  const harness = appHarness(() => ({ outputs: [] }));
  const visible = [];
  renderObjects(harness.container, harness.state.catalog, "stored_customers", () => {}, {
    getViewAnalysis: () => ({ status: "idle" }),
    onAnalyzeView: view => visible.push(view.designId),
  });
  assert.deepEqual(visible, ["view_stored"]);
  assert.equal(summary(harness, "view_stored"), "Columns not analyzed");
  const live = structuredClone(harness.state.catalog);
  live.source = "database";
  live.views[0].columns = [{ name: "id" }, { name: "total" }];
  renderObjects(harness.container, live, "customer_totals", () => {}, {
    onAnalyzeView: () => assert.fail("live columns already come from PostgreSQL"),
  });
  assert.equal(summary(harness, "view_totals"), "2 columns");
  harness.viewAnalysis.clear();
});

test("a column-count search is reconciled when analysis changes its matching metadata", async () => {
  const pending = deferred();
  const harness = appHarness(() => pending.promise);
  harness.render();
  harness.elements.objectsSearch.value = "Analyzing columns";
  harness.render();
  assert.equal(harness.container.children.length, 2);
  pending.resolve({ outputs: [{ name: "id" }] });
  await flush();
  assert.match(harness.container.textContent, /No matching objects/);
  harness.elements.objectsSearch.value = "1 column";
  harness.render();
  assert.equal(summary(harness, "view_totals"), "1 column");
  assert.equal(summary(harness, "view_stored"), "1 column");
  harness.viewAnalysis.clear();
});
