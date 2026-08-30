import assert from "node:assert/strict";
import test from "node:test";

import { GraphViewport } from "../../src/schemii/schemii/web/assets/graph-viewport.js";

class ClassList {
  values = new Set();

  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}

class PointerTarget {
  constructor() {
    this.listeners = new Map();
    this.capturedPointers = new Set();
    this.classList = new ClassList();
    this.style = {};
    this.clientWidth = 1_000;
    this.clientHeight = 700;
    this.bounds = { left: 100, top: 50 };
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  setPointerCapture(pointerId) { this.capturedPointers.add(pointerId); }
  hasPointerCapture(pointerId) { return this.capturedPointers.has(pointerId); }
  releasePointerCapture(pointerId) { this.capturedPointers.delete(pointerId); }
  getBoundingClientRect() { return this.bounds; }

  dispatch(type, values = {}) {
    const event = {
      type,
      button: 0,
      pointerId: 5,
      clientX: 0,
      clientY: 0,
      deltaX: 0,
      deltaY: 0,
      preventDefault() { this.defaultPrevented = true; },
      target: this,
      currentTarget: this,
      ...values,
    };
    for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
    event.currentTarget = null;
    return event;
  }
}

class Frames {
  constructor() {
    this.next = 1;
    this.callbacks = new Map();
  }

  schedule(callback) {
    const id = this.next;
    this.next += 1;
    this.callbacks.set(id, callback);
    return id;
  }

  cancel(id) { this.callbacks.delete(id); }

  flush() {
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    callbacks.forEach(callback => callback());
  }
}

function fixture(initialView = { x: 20, y: 30, zoom: 1 }) {
  const host = new PointerTarget();
  const stage = { style: {} };
  const zoomOutput = {};
  const frames = new Frames();
  const viewport = new GraphViewport({
    host,
    stage,
    zoomOutput,
    initialView,
    maxZoom: 3,
    canStartPan: () => true,
    scheduleFrame: callback => frames.schedule(callback),
    cancelFrame: id => frames.cancel(id),
  });
  return { frames, host, stage, viewport, zoomOutput };
}

test("viewport applies and reports its initial camera", () => {
  const { stage, viewport, zoomOutput } = fixture({ x: 12, y: 34, zoom: 0.8 });

  assert.equal(stage.style.transform, "translate(12px, 34px) scale(0.8)");
  assert.equal(zoomOutput.textContent, "80%");
  assert.deepEqual(viewport.getView(), { x: 12, y: 34, zoom: 0.8 });
});

test("pointer-anchored zoom keeps the same world coordinate under the pointer", () => {
  const { viewport } = fixture();
  const before = viewport.screenToWorld(420, 280);

  viewport.zoomAt(0.4, 420, 280);

  const after = viewport.screenToWorld(420, 280);
  assert.ok(Math.abs(before.x - after.x) < 1e-9);
  assert.ok(Math.abs(before.y - after.y) < 1e-9);
});

test("plain wheel movement pans the shared graph viewport", () => {
  const { host, viewport } = fixture();

  const event = host.dispatch("wheel", { deltaX: 18, deltaY: -25 });

  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(viewport.getView(), { x: 2, y: 55, zoom: 1 });
});

test("node dragging uses world coordinates and coalesces pointer frames", () => {
  const { frames, viewport } = fixture({ x: 0, y: 0, zoom: 2 });
  const handle = new PointerTarget();
  const card = new PointerTarget();
  const rendered = [];
  const committed = [];
  handle.addEventListener("pointerdown", event => viewport.beginNodeDrag(event, {
    key: "node-a",
    element: card,
    position: { x: 100, y: 200 },
    onFrame: position => rendered.push(position),
    onCommit: position => committed.push(position),
  }));

  handle.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  handle.dispatch("pointermove", { clientX: 30, clientY: 50 });
  handle.dispatch("pointermove", { clientX: 50, clientY: 80 });

  assert.equal(frames.callbacks.size, 1);
  frames.flush();
  assert.equal(card.style.transform, "translate3d(20px, 30px, 0)");
  assert.deepEqual(rendered, [{ x: 120, y: 230 }]);

  handle.dispatch("pointerup", { clientX: 50, clientY: 80 });
  assert.deepEqual(committed, [{ x: 120, y: 230 }]);
  assert.equal(card.style.left, "120px");
  assert.equal(card.style.top, "230px");
  assert.equal(handle.hasPointerCapture(5), false);
});

test("fitBounds honors consumer insets and fit zoom caps", () => {
  const { viewport } = fixture();

  assert.equal(viewport.fitBounds(
    { minX: 100, minY: 100, maxX: 800, maxY: 500 },
    { left: 50, top: 40, right: 150, bottom: 60, maxZoom: 0.75 },
  ), true);

  assert.equal(viewport.getView().zoom, 0.75);
  assert.equal(viewport.fitBounds(null), false);
});

test("destroy removes viewport listeners and cancels interactions", () => {
  const { host, viewport } = fixture();
  host.dispatch("pointerdown", { clientX: 10, clientY: 20 });
  assert.equal(host.classList.contains("panning"), true);

  viewport.destroy();

  assert.equal(host.classList.contains("panning"), false);
  for (const listeners of host.listeners.values()) assert.equal(listeners.size, 0);
});
