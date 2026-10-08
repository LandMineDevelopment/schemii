import assert from "node:assert/strict";
import test from "node:test";

import { CatalogCanvas } from "../../src/schemii/schemii/web/assets/canvas.js";
import { bindCanvasFitControl } from "../../src/schemii/schemii/web/assets/loading-controls.js";
import { catalogTableId } from "../../src/schemii/schemii/web/assets/workspace-state.js";

class CanvasHost extends EventTarget {
  clientWidth = 1280;
  clientHeight = 800;
  classList = { add() {}, remove() {}, toggle() {} };
}

function catalog(source, { empty = false } = {}) {
  return {
    source,
    namespace: source === "design" ? "desired" : "public",
    tables: empty ? [] : ["orders", "customers"].map((name, index) => ({
      name,
      namespace: source === "design" ? "desired" : "public",
      ...(source === "design" ? { designId: `table-${index}` } : {}),
      columns: [{ name: "id" }, { name: "label" }, { name: "qty" }],
    })),
    relationships: [],
  };
}

function fixture({ source = "design", empty = false, rightInset = 380, startupComplete = true } = {}) {
  const host = new CanvasHost();
  const canvas = new CatalogCanvas({
    canvas: host,
    stage: { style: {} },
    layer: { replaceChildren() {} },
    lines: { replaceChildren() {} },
    onSelect() {},
    onPositionsChanged() {},
    onRelationshipVisibilityChanged() {},
    getViewportInsets: () => ({ right: rightInset }),
    scheduleFrame: () => 0,
    cancelFrame() {},
  });
  const state = { startupComplete, catalogLoading: false, catalog: null };
  function installCatalog(value) {
    state.catalog = value;
    canvas.catalog = value;
    canvas.positions.clear();
    for (const [index, table] of (value?.tables || []).entries()) {
      canvas.positions.set(catalogTableId(table), { x: 90 + index * 350, y: 90 });
    }
  }
  installCatalog(catalog(source, { empty }));
  canvas.view = { x: -400, y: 70, zoom: 1 };
  const button = new EventTarget();
  const toasts = [];
  let persisted = 0;
  const updateAvailability = bindCanvasFitControl({
    button,
    canvas,
    getState: () => state,
    showToast: message => toasts.push(message),
    onFit: () => persisted++,
  });
  return {
    state, canvas, button, toasts, installCatalog, updateAvailability,
    persisted: () => persisted,
    click: () => withViewport(host.clientWidth <= 680, () => button.dispatchEvent(new Event("click"))),
  };
}

function withViewport(mobile, action) {
  const previousWindow = globalThis.window;
  globalThis.window = { matchMedia: () => ({ matches: mobile }) };
  try {
    return action();
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
}

test("Fit is unavailable before startup or without a loaded workspace catalog", () => {
  const f = fixture();
  const before = f.canvas.view;
  f.state.startupComplete = false;
  f.updateAvailability();
  assert.equal(f.button.disabled, true);
  f.click();
  f.state.startupComplete = true;
  f.installCatalog(null);
  f.updateAvailability();
  assert.equal(f.button.disabled, true);
  f.click();
  assert.deepEqual(f.canvas.view, before);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.persisted(), 0);
});

test("a ready catalog selected during startup enables Fit after startup completion", async () => {
  const f = fixture({ startupComplete: false });
  const selectedCatalog = f.state.catalog;
  const before = f.canvas.view;
  let releaseStartup;
  const startup = new Promise(resolve => { releaseStartup = resolve; });
  const completed = startup.then(() => {
    f.state.startupComplete = true;
    f.updateAvailability();
  });

  // Manual workspace navigation can finish while startup readiness is pending.
  // The loaded catalog alone must not admit a Fit or a false empty-state toast.
  assert.equal(f.button.disabled, true);
  f.click();
  assert.deepEqual(f.canvas.view, before);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.persisted(), 0);

  releaseStartup();
  await completed;
  assert.equal(f.state.catalog, selectedCatalog);
  assert.equal(f.button.disabled, false);
  f.click();
  assert.notDeepEqual(f.canvas.view, before);
  assert.equal(f.persisted(), 1);
  assert.deepEqual(f.toasts, []);
  f.canvas.viewport.destroy();
});

for (const source of ["design", "postgres"]) {
  test(`deferred ${source} workspace response keeps Fit blocked until load completion`, async () => {
    const f = fixture({ source });
    assert.equal(f.button.disabled, false);
    const before = f.canvas.view;
    let resolveResponse;
    const response = new Promise(resolve => { resolveResponse = resolve; });
    f.state.catalogLoading = true;
    f.updateAvailability();
    // A refresh can retain the old catalog; opening another workspace clears it.
    f.click();
    f.installCatalog(null);
    f.updateAvailability();
    assert.equal(f.button.disabled, true);
    f.click();
    const snapshotCommitted = response.then(value => {
      f.installCatalog(value);
      f.updateAvailability();
    });
    resolveResponse(catalog(source));
    await snapshotCommitted;
    // Rendering the response is still inside loadActiveDesign/loadActiveCatalog;
    // their finally blocks have not cleared catalogLoading yet.
    assert.equal(f.button.disabled, true);
    f.click();
    assert.deepEqual(f.canvas.view, before);
    assert.deepEqual(f.toasts, []);
    assert.equal(f.persisted(), 0);

    f.state.catalogLoading = false;
    f.updateAvailability();
    assert.equal(f.button.disabled, false);
    f.click();
    assert.notDeepEqual(f.canvas.view, before);
    assert.equal(f.persisted(), 1);
    assert.deepEqual(f.toasts, []);
    f.canvas.viewport.destroy();
  });
}

test("a queued Fit click rechecks load admission before the next header update", () => {
  const f = fixture();
  const before = f.canvas.view;
  assert.equal(f.button.disabled, false);
  f.state.catalogLoading = true;
  // Deliberately keep the rendered button enabled to exercise the action guard.
  f.click();
  assert.deepEqual(f.canvas.view, before);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.persisted(), 0);
});

test("an empty loaded catalog identifies desired or live tables without persisting a Fit", () => {
  for (const [source, message] of [
    ["design", "No desired tables are available to fit."],
    ["postgres", "No live tables are available to fit."],
  ]) {
    const f = fixture({ source, empty: true });
    const before = f.canvas.view;
    assert.equal(f.button.disabled, false);
    f.click();
    assert.deepEqual(f.toasts, [message]);
    assert.deepEqual(f.canvas.view, before);
    assert.equal(f.persisted(), 0);
  }
});

test("ready desired and live canvases fit the same table geometry inside desktop inspector and mobile insets", () => {
  for (const mobile of [false, true]) {
    const views = [];
    for (const source of ["design", "postgres"]) {
      const f = fixture({ source });
      f.canvas.canvas.clientWidth = mobile ? 390 : 1280;
      f.canvas.canvas.clientHeight = mobile ? 844 : 800;
      f.click();
      const view = f.canvas.view;
      views.push(view);
      // The two real three-column table cards span x=90..710, y=90..245.
      const left = view.x + 90 * view.zoom;
      const right = view.x + 710 * view.zoom;
      const top = view.y + 90 * view.zoom;
      const bottom = view.y + 245 * view.zoom;
      assert.ok(left >= (mobile ? 18 : 75), `left edge clipped: ${left}`);
      assert.ok(right <= (mobile ? 372 : 900), `right edge overlaps dock: ${right}`);
      assert.ok(top >= 65, `top edge clipped: ${top}`);
      assert.ok(bottom <= (mobile ? 769 : 765), `bottom edge clipped: ${bottom}`);
      assert.ok(view.zoom <= 1.25);
      assert.equal(f.persisted(), 1);
      assert.deepEqual(f.toasts, []);
      f.canvas.viewport.destroy();
    }
    assert.deepEqual(views[0], views[1]);
  }
});
