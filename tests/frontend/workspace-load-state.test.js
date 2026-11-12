import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

import { ApiError } from "../../src/schemii/common/web/assets/http.js";
import { designPositions, designToCatalog } from "../../src/schemii/schemii/web/assets/design.js";
import { createLatestRequestController, createWorkspaceOperationController } from "../../src/schemii/schemii/web/assets/request-coordinator.js";
import { readWorkspaceNavigation } from "../../src/schemii/schemii/web/assets/workspace-navigation.js";
import { catalogViewId, createWorkspaceStateCommitter, selectedCatalogTable, selectedCatalogView } from "../../src/schemii/schemii/web/assets/workspace-state.js";

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
const appSource = read("src/schemii/schemii/web/assets/app.js");
function appBlock(start, end) {
  assert.ok(appSource.includes(start) && appSource.includes(end), `Missing app boundary: ${start}`);
  return appSource.slice(appSource.indexOf(start), appSource.indexOf(end));
}
const uiSource = read("src/schemii/common/web/assets/ui.js");
const statePanels = uiSource.slice(uiSource.indexOf("const STATE_VARIANTS"), uiSource.indexOf("export function hydrateIconControls"));
const browserModule = source => source.replace(/^import .*;\n/gm, "").replace(/^export /gm, "");

// Run the real app loading, status, header, admission and list wiring. Only
// unrelated canvas/editor/analysis surfaces are replaced; no network or browser
// is started. The real DOM error panel preserves ApiError details and Retry.
class Element {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName;
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.value = "";
    this.className = "";
    this.ownText = "";
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(" "); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ""; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  click() { return this.listeners.get("click")?.(); }
}
const descendants = root => [root, ...root.children.flatMap(descendants)];
const noop = () => {};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const workspace = id => ({ id, name: `Workspace ${id}`, connectionId: "connection-one", database: "organization", namespace: "public" });
function snapshot({ empty = false } = {}) {
  return {
    design: { revision: 1, content: { types: [], tables: [], relationships: [], functions: [], triggers: [], views: empty ? [] : [
      { id: "view-one", name: "customer_totals", kind: "view", definition: "SELECT 1 AS id" },
    ] } },
    layout: { revision: 1, content: { objects: [] } },
    history: { canUndo: false, canRedo: false, canResetToBaseline: false },
  };
}

function harness() {
  const document = { createElement: tag => new Element(tag, document) };
  const nodes = new Map();
  const byId = id => {
    if (!nodes.has(id)) nodes.set(id, document.createElement("div"));
    return nodes.get(id);
  };
  const elements = new Proxy({}, { get: (_, key) => byId(key) });
  const state = {
    startupComplete: true, readiness: { ready: true, persistence: "durable" }, runtimeError: null,
    activeWorkspace: null, activeLayer: "views", design: null, catalog: null,
    catalogLoading: false, catalogError: null, selectedViewId: null, selectedTableId: null,
    viewFilter: "all", columnOrderModes: new Map(),
  };
  const requests = [];
  const window = { location: { href: "https://localhost:8001/?layer=views" } };
  const workspaceOperations = createWorkspaceOperationController();
  let openedWorkspaces = 0;
  const runtime = runInNewContext([
    browserModule(statePanels),
    browserModule(read("src/schemii/common/web/assets/dom.js")),
    browserModule(read("src/schemii/schemii/web/assets/catalog.js")),
    appBlock("function notifyWorkspaceTransition(", "function openDialog("),
    appBlock("function isDesignWorkspace(", "function workspaceStorage("),
    appBlock("function updateHeader(", "function renderConflict("),
    appBlock("async function openWorkspace(", "async function loadActiveCatalog("),
    appBlock("async function refreshCatalog(", "function reloadConflict("),
    appBlock("function selectView(", "function renderSelectedViewDetail("),
    appBlock("function selectedDesignView(", "async function findLiveRelation("),
    "({ openWorkspace, refreshCatalog, renderCatalogState, renderViews, updateHeader, selectView })",
  ].join("\n"), {
    Error, ApiError, Node: Element, document, window, state, elements, byId,
    createWorkspaceStateCommitter, designToCatalog, designPositions,
    selectedCatalogTable, selectedCatalogView, catalogViewId, readWorkspaceNavigation,
    workspaceOperations, workspaceNavigation: createLatestRequestController(),
    workspaceLabel: value => value.name,
    canvas: { setInteractive: noop, setCatalog: noop, clear: noop },
    syncViewAnalysisSelection: noop, renderConflict: noop,
    renderCatalogSurfaces: () => runtime.renderViews(),
    renderSelectedViewDetail: () => { elements.viewDetail.textContent = selectedCatalogView(state.catalog, state.selectedViewId)?.name || "No view selected"; },
    workspaceCanvasPositions: () => ({}),
    migrationReview: { syncContext: noop, available: () => false },
    updateFitAvailability: noop, detachedDesignEligible: () => false,
    renderWorkspaceCompatibility: noop, renderSqlTarget: noop,
    tableEditor: { hasDraft: false, updateActions: noop },
    schemaHandoffPending: false, designEditors: { requestDiscardDrafts: async () => true },
    flushLayoutBeforeTransition: async () => true, persistCanvasView: noop,
    designDeployment: { close: noop }, viewAnalysis: { clear: noop },
    resetLayoutSaveState: noop, cancelColumnAuthoring: noop, restoreCanvasView: noop,
    syncWorkspaceNavigation: () => {
      const url = new URL(window.location.href);
      url.searchParams.set("workspace", state.activeWorkspace.id);
      const view = selectedCatalogView(state.catalog, state.selectedViewId);
      if (view) url.searchParams.set("view", view.name);
      window.location.href = url.href;
    },
    isRestoringWorkspaceNavigation: () => true,
    openWorkspaces: () => { openedWorkspaces++; }, openConnections: noop,
    openDesignTableEditor: noop, saveLayout: noop, bootstrap: noop, showToast: noop,
    api: { getDesignSnapshot: (workspaceId, { signal }) => {
      const request = { workspaceId, signal, ...deferred() };
      requests.push(request);
      return request.promise;
    } },
  });
  return { ...runtime, state, elements, requests, window, openedWorkspaces: () => openedWorkspaces };
}

const retry = host => descendants(host).find(node => node.tagName === "button" && node.textContent === "Retry saved design");
function assertEditingDisabled(elements) {
  for (const name of ["createTableButton", "createViewButton", "createViewTool", "createFunctionButton", "createTypeButton", "createTriggerButton", "createRelationshipButton", "createKeyButton", "createIndexButton"]) {
    assert.equal(elements[name].disabled, true, `${name} requires a loaded design`);
  }
}

test("initial Views snapshot loading, failure and explicit Retry use real app wiring", async () => {
  const app = harness();
  const opening = app.openWorkspace(workspace("one"));
  await flush();
  assert.equal(app.requests.length, 1);
  assert.match(app.elements.viewsState.textContent, /Loading saved design/);
  assert.equal(app.elements.viewsSourceLabel.textContent, "Desired schema");
  assert.doesNotMatch(app.elements.viewsList.textContent, /No workspace loaded|No views yet/);
  assert.equal(app.elements.viewDetail.hidden, true);
  assert.equal(app.elements.runtimeStatus.textContent, "Loading saved design");
  assert.match(app.elements.runtimeDot.className, /loading/);
  assertEditingDisabled(app.elements);
  const failure = new ApiError("Snapshot storage unavailable", { status: 503, code: "snapshot_unavailable", requestId: "request-snapshot-one", details: { cause: "test-owned failure" } });
  app.requests[0].reject(failure);
  await opening;
  assert.equal(app.state.catalogError, failure);
  assert.equal(app.state.catalogLoading, false);
  for (const host of [app.elements.viewsState, app.elements.catalogState]) {
    assert.match(host.textContent, /Snapshot storage unavailable/);
    assert.match(host.textContent, /Error details.*snapshot_unavailable.*request-snapshot-one.*503/);
    assert.ok(retry(host));
  }
  assert.equal(app.elements.workspaceTitle.textContent, "Workspace one");
  assert.equal(app.elements.runtimeStatus.textContent, "Saved design load failed");
  assert.match(app.elements.runtimeDot.className, /error/);
  assert.equal(app.elements.refreshViewsTool.disabled, false);
  assertEditingDisabled(app.elements);

  const retrying = retry(app.elements.viewsState).click();
  assert.equal(app.requests.length, 2);
  assert.match(app.elements.viewsState.textContent, /Loading saved design/);
  app.requests[1].resolve(snapshot());
  await retrying;
  assert.equal(app.elements.viewsState.textContent, "");
  assert.match(app.elements.viewsList.textContent, /customer_totals/);
  assert.equal(app.elements.viewDetail.hidden, false);
  assert.equal(app.elements.runtimeStatus.textContent, "Ready · durable");
  assert.equal(app.elements.createViewButton.disabled, false);
  assert.equal(app.state.catalogError, null);
  assert.equal(app.state.activeLayer, "views");
  assert.equal(new URL(app.window.location.href).searchParams.get("workspace"), "one");
  assert.equal(new URL(app.window.location.href).searchParams.get("layer"), "views");
});

test("a failed refresh keeps the mounted selected view and shows recovery until Retry succeeds", async () => {
  const app = harness();
  const opening = app.openWorkspace(workspace("one"));
  await flush();
  app.requests[0].resolve(snapshot());
  await opening;
  await app.elements.viewsList.children[0].click();
  const listButton = app.elements.viewsList.children[0];
  const catalog = app.state.catalog;
  assert.equal(app.state.selectedViewId, "view-one");
  assert.equal(app.elements.viewDetail.textContent, "customer_totals");
  const refreshing = app.refreshCatalog();
  assert.equal(app.elements.viewsList.children[0], listButton);
  assert.equal(app.elements.viewDetail.textContent, "customer_totals");
  app.requests[1].reject(new ApiError("Refresh unavailable", { requestId: "refresh-request" }));
  await refreshing;
  assert.equal(app.state.catalog, catalog);
  assert.equal(app.state.selectedViewId, "view-one");
  assert.equal(app.elements.viewsList.children[0], listButton);
  assert.equal(app.elements.viewDetail.textContent, "customer_totals");
  assert.match(app.elements.viewsState.textContent, /Refresh unavailable.*refresh-request.*last successful load/);
  assert.equal(app.elements.runtimeStatus.textContent, "Saved design refresh failed");
  const retrying = retry(app.elements.viewsState).click();
  app.requests[2].resolve(snapshot());
  await retrying;
  assert.equal(app.elements.viewsState.textContent, "");
  assert.equal(app.state.selectedViewId, "view-one");
  assert.equal(app.elements.viewDetail.textContent, "customer_totals");
  assert.equal(new URL(app.window.location.href).searchParams.get("view"), "customer_totals");
});

test("a genuine unopened workspace and a loaded empty design have distinct Views states", async () => {
  const app = harness();
  app.renderViews();
  assert.match(app.elements.viewsState.textContent, /Open a schema workspace/);
  assert.equal(app.elements.viewsSourceLabel.textContent, "No workspace open");
  const open = descendants(app.elements.viewsState).find(node => node.textContent === "Open workspaces");
  open.click();
  assert.equal(app.openedWorkspaces(), 1);
  const opening = app.openWorkspace(workspace("empty"));
  await flush();
  app.requests[0].resolve(snapshot({ empty: true }));
  await opening;
  assert.equal(app.elements.viewsState.textContent, "");
  assert.match(app.elements.viewsList.textContent, /No views yet/);
  assert.equal(app.elements.createViewButton.disabled, false);
});

for (const lateOutcome of ["resolve", "reject"]) {
  test(`a superseded workspace ${lateOutcome} cannot overwrite the current Views load state`, async () => {
    const app = harness();
    const first = app.openWorkspace(workspace("first"));
    await flush();
    const second = app.openWorkspace(workspace("second"));
    await flush();
    assert.equal(app.requests[0].signal.aborted, true);
    app.requests[0][lateOutcome](lateOutcome === "resolve" ? snapshot() : new Error("Old workspace failed"));
    await first;
    assert.equal(app.state.activeWorkspace.id, "second");
    assert.equal(app.state.catalogLoading, true);
    assert.equal(app.state.catalogError, null);
    assert.match(app.elements.viewsState.textContent, /Loading saved design.*Workspace second/);
    app.requests[1].resolve(snapshot({ empty: true }));
    await second;
    assert.equal(app.state.catalogLoading, false);
    assert.equal(app.elements.runtimeStatus.textContent, "Ready · durable");
    assert.match(app.elements.viewsList.textContent, /No views yet/);
  });
}

test("server failures keep their precedence and Retry respects the existing refresh admission guards", async () => {
  const app = harness();
  const opening = app.openWorkspace(workspace("one"));
  await flush();
  app.requests[0].reject(new Error("Design failed"));
  await opening;
  app.state.runtimeError = new ApiError("Session check failed", { requestId: "server-request" });
  app.renderCatalogState();
  app.updateHeader();
  assert.match(app.elements.viewsState.textContent, /Session check failed.*server-request/);
  assert.equal(app.elements.runtimeStatus.textContent, "Active server check failed");
  app.state.runtimeError = null;
  app.renderCatalogState();
  for (const guard of ["layoutDirty", "layoutConflict", "designSubmitting", "historySubmitting"]) {
    app.state[guard] = true;
    await retry(app.elements.viewsState).click();
    assert.equal(app.requests.length, 1, `${guard} must retain existing refresh admission`);
    app.state[guard] = false;
  }
});
