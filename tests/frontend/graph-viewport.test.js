import assert from "node:assert/strict";
import test from "node:test";

import { GraphViewport } from "#common/graph-viewport.js";

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
      stopImmediatePropagation() { this.stopped = true; },
      target: this,
      currentTarget: this,
      ...values,
    };
    for (const listener of [...(this.listeners.get(type) || [])]) { listener(event); if (event.stopped) break; }
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

test("touch pinch zooms and pans around its moving midpoint, then resets cleanly", () => {
  const {host, viewport} = fixture();
  const touch = (type, id, x, y) => host.dispatch(type, {pointerType:"touch", pointerId:id, clientX:x, clientY:y});
  const anchor = viewport.screenToWorld(300, 250);
  touch("pointerdown", 1, 250, 250);
  touch("pointerdown", 2, 350, 250);
  assert.equal(viewport.pan, null);
  touch("pointermove", 2, 450, 250);
  assert.equal(viewport.getView().zoom, 2);
  assert.deepEqual(viewport.screenToWorld(350, 250), anchor);
  touch("pointermove", 2, 950, 250);
  assert.equal(viewport.getView().zoom, 3);
  touch("pointerup", 2, 950, 250);
  const view = viewport.getView();
  touch("pointermove", 1, 100, 100);
  assert.deepEqual(viewport.getView(), view);
  touch("pointercancel", 1, 100, 100);
  assert.equal(viewport.touches.size, 0);
  assert.equal(host.dispatch("click", {pointerType:"touch"}).defaultPrevented, true);
  touch("pointerdown", 3, 200, 200);
  touch("pointermove", 3, 210, 220);
  assert.equal(viewport.getView().x, view.x + 10);
  assert.equal(viewport.getView().y, view.y + 20);
  viewport.destroy();
  assert.equal(host.capturedPointers.size, 0);
});

test("starting a pinch cancels node drag without committing layout", () => {
  const {host, viewport} = fixture();
  const node = new PointerTarget(); let commits = 0, cancels = 0;
  const event = {pointerType:"touch",pointerId:1,button:0,clientX:200,clientY:200,preventDefault(){},currentTarget:node};
  viewport.trackTouch(event);
  viewport.beginNodeDrag(event, {key:"node",element:node,position:{x:0,y:0},onCommit:()=>commits++,onCancel:()=>cancels++});
  viewport.moveNodeDrag({...event,clientX:220});
  host.dispatch("pointerdown", {pointerType:"touch",pointerId:2,clientX:300,clientY:200});
  assert.equal(viewport.drag, null);
  assert.equal(commits, 0);
  assert.equal(cancels, 1);
  viewport.destroy();
});

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
