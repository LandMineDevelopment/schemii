import assert from "node:assert/strict";
import test from "node:test";

import { CatalogCanvas } from "../../src/schemii/schemii/web/assets/canvas.js";

class ClassList {
  values = new Set();

  add(name) {
    this.values.add(name);
  }

  remove(name) {
    this.values.delete(name);
  }

  toggle(name, force) {
    if (force) this.add(name);
    else this.remove(name);
  }

  contains(name) {
    return this.values.has(name);
  }
}

class PointerTarget {
  constructor() {
    this.listeners = new Map();
    this.capturedPointers = new Set();
    this.classList = new ClassList();
    this.style = {};
    this.attributes = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  setPointerCapture(pointerId) {
    this.capturedPointers.add(pointerId);
  }

  hasPointerCapture(pointerId) {
    return this.capturedPointers.has(pointerId);
  }

  releasePointerCapture(pointerId) {
    this.capturedPointers.delete(pointerId);
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  dispatch(type, values = {}) {
    const event = {
      button: 0,
      pointerId: 7,
      clientX: 0,
      clientY: 0,
      preventDefault() {},
      ...values,
      type,
      currentTarget: this,
    };
    for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
    event.currentTarget = null;
    return event;
  }
}

class FrameScheduler {
  constructor() {
    this.nextId = 1;
    this.callbacks = new Map();
  }

  schedule(callback) {
    const id = this.nextId;
    this.nextId += 1;
    this.callbacks.set(id, callback);
    return id;
  }

  cancel(id) {
    this.callbacks.delete(id);
  }

  flush() {
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    for (const callback of callbacks) callback();
  }
}

class AttributeTarget {
  constructor() {
    this.writes = [];
  }

  setAttribute(name, value) {
    this.writes.push([name, value]);
  }

  remove() {}
}

function fixture({ getViewportInsets = () => ({}) } = {}) {
  const host = new PointerTarget();
  const stage = { style: {} };
  const zoomOutput = {};
  const frames = new FrameScheduler();
  let savedPositionCount = 0;
  let selectionCount = 0;
  const canvas = new CatalogCanvas({
    canvas: host,
    stage,
    layer: { replaceChildren() {} },
    lines: { replaceChildren() {} },
    zoomOutput,
    onSelect() { selectionCount += 1; },
    onPositionsChanged() {
      savedPositionCount += 1;
    },
    onRelationshipVisibilityChanged() {},
    getViewportInsets,
    scheduleFrame: callback => frames.schedule(callback),
    cancelFrame: frame => frames.cancel(frame),
  });
  const card = new PointerTarget();
  const handle = new PointerTarget();
  canvas.catalog = { namespace: "public", tables: [{ name: "orders" }], relationships: [] };
  canvas.positions.set("orders", { x: 100, y: 200 });
  canvas.cards.set("orders", card);
  handle.addEventListener("pointerdown", event => canvas.startDrag(event, "orders", card));
  return {
    canvas,
    card,
    frames,
    handle,
    savedPositionCount: () => savedPositionCount,
    selectionCount: () => selectionCount,
  };
}

test("pointerup drops a table after the pointerdown event has finished dispatching", () => {
  const { canvas, card, frames, handle, savedPositionCount } = fixture();

  const pointerDown = handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  assert.equal(pointerDown.currentTarget, null);
  assert.equal(card.classList.contains("dragging"), true);

  handle.dispatch("pointermove", { clientX: 50, clientY: 70 });
  handle.dispatch("pointerup", { clientX: 50, clientY: 70 });

  assert.equal(canvas.drag, null);
  assert.equal(card.classList.contains("dragging"), false);
  assert.deepEqual(canvas.positions.get("orders"), { x: 140, y: 250 });
  assert.equal(savedPositionCount(), 1);
  assert.equal(frames.callbacks.size, 0);
  assert.equal(handle.hasPointerCapture(7), false);
  for (const type of ["pointermove", "pointerup", "pointercancel", "lostpointercapture"]) {
    assert.equal(handle.listeners.get(type)?.size || 0, 0);
  }
});

test("lost pointer capture also closes an active table drag", () => {
  const { canvas, card, handle, savedPositionCount } = fixture();

  handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  handle.dispatch("pointermove", { clientX: 25, clientY: 35 });
  handle.capturedPointers.delete(7);
  handle.dispatch("lostpointercapture", { clientX: 25, clientY: 35 });

  assert.equal(canvas.drag, null);
  assert.equal(card.classList.contains("dragging"), false);
  assert.equal(savedPositionCount(), 1);
  assert.deepEqual(canvas.positions.get("orders"), { x: 115, y: 215 });
});

test("clearing a table selection updates the card and notifies navigation", () => {
  const { canvas, card, selectionCount } = fixture();
  canvas.selectedName = "orders";
  card.classList.add("selected");

  assert.equal(canvas.clearSelection({ notify: true }), true);
  assert.equal(canvas.selectedName, null);
  assert.equal(card.classList.contains("selected"), false);
  assert.equal(card.attributes.get("aria-pressed"), "false");
  assert.equal(selectionCount(), 1);
  assert.equal(canvas.clearSelection({ notify: true }), false);
  assert.equal(selectionCount(), 1);
});

test("many pointer events produce one frame and mutate only incident relationships", () => {
  const { canvas, card, frames, handle, savedPositionCount } = fixture();
  const connected = {
    sourceNamespace: "public",
    sourceTable: "orders",
    sourceColumns: ["customer_id"],
    targetNamespace: "public",
    targetTable: "customers",
    targetColumns: ["id"],
    name: "orders_customer_fkey",
  };
  const unrelated = {
    sourceNamespace: "public",
    sourceTable: "products",
    sourceColumns: ["category_id"],
    targetNamespace: "public",
    targetTable: "categories",
    targetColumns: ["id"],
    name: "products_category_fkey",
  };
  const entry = () => ({
    group: new AttributeTarget(),
    shadow: new AttributeTarget(),
    line: new AttributeTarget(),
    sourceEnd: new AttributeTarget(),
    targetEnd: new AttributeTarget(),
  });
  const connectedEntry = entry();
  const unrelatedEntry = entry();
  canvas.positions.set("customers", { x: 600, y: 300 });
  canvas.columnIndexes.set("orders", new Map([["customer_id", 1]]));
  canvas.columnIndexes.set("customers", new Map([["id", 0]]));
  canvas.relationshipsByTable.set("orders", [connected]);
  canvas.relationshipElements.set("public\u0000orders\u0000orders_customer_fkey", connectedEntry);
  canvas.relationshipElements.set("public\u0000products\u0000products_category_fkey", unrelatedEntry);
  canvas.diagramRelationships = new Proxy([connected, unrelated], {
    get(target, property, receiver) {
      if (property === Symbol.iterator) throw new Error("drag rendering scanned every relationship");
      return Reflect.get(target, property, receiver);
    },
  });

  Object.defineProperty(card, "offsetWidth", {
    get() {
      throw new Error("drag rendering forced a card width read");
    },
  });

  handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  for (let step = 1; step <= 100; step += 1) {
    handle.dispatch("pointermove", { clientX: 10 + step, clientY: 20 + step });
  }

  assert.equal(frames.callbacks.size, 1);
  assert.equal(card.style.transform, undefined);
  assert.deepEqual(canvas.getPositions(), [{ name: "orders", x: 100, y: 200 }]);
  assert.equal(connectedEntry.line.writes.length, 0);
  frames.flush();

  assert.equal(card.style.transform, "translate3d(100px, 100px, 0)");
  assert.equal(connectedEntry.shadow.writes.length, 1);
  assert.equal(connectedEntry.line.writes.length, 1);
  assert.equal(connectedEntry.sourceEnd.writes.length, 2);
  assert.equal(connectedEntry.targetEnd.writes.length, 2);
  assert.equal(unrelatedEntry.line.writes.length, 0);

  handle.dispatch("pointerup", { clientX: 110, clientY: 120 });
  assert.equal(card.style.transform, "");
  assert.deepEqual(canvas.positions.get("orders"), { x: 200, y: 300 });
  assert.equal(savedPositionCount(), 1);
  assert.equal(connectedEntry.line.writes.length, 1);
});

test("returning to the origin redraws but does not save a layout revision", () => {
  const { canvas, card, frames, handle, savedPositionCount } = fixture();

  handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  handle.dispatch("pointermove", { clientX: 50, clientY: 60 });
  frames.flush();
  assert.equal(card.style.transform, "translate3d(40px, 40px, 0)");

  handle.dispatch("pointermove", { clientX: 10, clientY: 20 });
  handle.dispatch("pointerup", { clientX: 10, clientY: 20 });

  assert.equal(card.style.transform, "");
  assert.deepEqual(canvas.getPositions(), [{ name: "orders", x: 100, y: 200 }]);
  assert.equal(savedPositionCount(), 0);
});

test("fit uses cached card geometry without layout reads", () => {
  const { canvas, card } = fixture();
  canvas.canvas.clientWidth = 1_200;
  canvas.canvas.clientHeight = 800;
  canvas.catalog.tables[0].columns = [{ name: "id" }];
  Object.defineProperty(card, "offsetHeight", {
    get() {
      throw new Error("fit forced a card height read");
    },
  });
  const previousWindow = globalThis.window;
  try {
    globalThis.window = {
      matchMedia() {
        return { matches: false };
      },
    };
    assert.equal(canvas.fit(), true);
    assert.match(canvas.stage.style.transform, /^translate\(.+\) scale\(.+\)$/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("fit reserves space reported by the active dock layout", () => {
  const { canvas } = fixture({ getViewportInsets: () => ({ right: 42 }) });
  canvas.catalog.tables[0].columns = [];
  let receivedInsets = null;
  canvas.viewport.fitBounds = (_bounds, insets) => {
    receivedInsets = insets;
    return true;
  };
  const previousWindow = globalThis.window;
  try {
    globalThis.window = { matchMedia: () => ({ matches: false }) };
    assert.equal(canvas.fit(), true);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
  assert.equal(receivedInsets.right, 42);
});

test("clearing a catalog cancels a pending drag frame without stale writes", () => {
  const { canvas, card, frames, handle, savedPositionCount } = fixture();
  handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  handle.dispatch("pointermove", { clientX: 80, clientY: 90 });
  assert.equal(frames.callbacks.size, 1);

  canvas.clear();
  frames.flush();

  assert.equal(canvas.drag, null);
  assert.equal(frames.callbacks.size, 0);
  assert.equal(card.style.transform, "");
  assert.equal(savedPositionCount(), 0);
});

test("relationship indexing includes target edges and indexes self-edges once", () => {
  const { canvas } = fixture();
  const targetEdge = {
    sourceNamespace: "public",
    sourceTable: "customers",
    targetNamespace: "public",
    targetTable: "orders",
  };
  const selfEdge = {
    sourceNamespace: "public",
    sourceTable: "orders",
    targetNamespace: "public",
    targetTable: "orders",
  };
  canvas.catalog.relationships = [targetEdge, selfEdge];

  canvas.updateDiagramRelationships();

  assert.deepEqual(canvas.relationshipsByTable.get("orders"), [targetEdge, selfEdge]);
  assert.deepEqual(canvas.relationshipsByTable.get("customers"), [targetEdge]);
});

test("selection updates only the previous and next cards in a large catalog", () => {
  const { canvas } = fixture();
  canvas.selectedName = "orders";
  for (let index = 0; index < 2_000; index += 1) {
    const name = `table_${index}`;
    canvas.tableByName.set(name, { name });
    canvas.cards.set(name, new PointerTarget());
  }

  canvas.select("table_1999");

  const changedCards = [...canvas.cards.entries()].filter(([, card]) => card.attributes.has("aria-pressed"));
  assert.deepEqual(changedCards.map(([name]) => name), ["orders", "table_1999"]);
  assert.equal(canvas.cards.get("orders").attributes.get("aria-pressed"), "false");
  assert.equal(canvas.cards.get("table_1999").attributes.get("aria-pressed"), "true");
});

test("an explicit same-table selection notifies the inspector without redrawing selection", () => {
  const { canvas, selectionCount } = fixture();
  canvas.tableByName.set("orders", { name: "orders" });
  canvas.selectedName = "orders";

  canvas.select("orders", { notify: true });

  assert.equal(selectionCount(), 1);
});
