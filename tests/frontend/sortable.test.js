import assert from "node:assert/strict";
import test from "node:test";

import { installSortableList, reorderedValues } from "../../src/schemii/schemii/web/assets/sortable.js";

class ClassList {
  values = new Set();

  add(...values) { values.forEach(value => this.values.add(value)); }
  remove(...values) { values.forEach(value => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
}

class SortNode {
  constructor({ className = "", sortKey = "", handle = false, top = 0, height = 40 } = {}) {
    this.parentElement = null;
    this.children = [];
    this.classList = new ClassList();
    if (className) this.classList.add(className);
    this.dataset = { sortKey };
    if (handle) this.dataset.sortHandle = "";
    this.top = top;
    this.height = height;
    this.listeners = new Map();
    this.attributes = new Map();
    this.capturedPointers = new Set();
    this.focused = false;
    this.animationCalls = [];
  }

  get nextElementSibling() {
    const index = this.parentElement?.children.indexOf(this) ?? -1;
    return index >= 0 ? this.parentElement.children[index + 1] || null : null;
  }

  get lastElementChild() { return this.children.at(-1) || null; }

  append(...nodes) {
    for (const node of nodes) this.insertBefore(node, null);
  }

  insertBefore(node, before) {
    if (node.parentElement) {
      const oldIndex = node.parentElement.children.indexOf(node);
      if (oldIndex >= 0) node.parentElement.children.splice(oldIndex, 1);
    }
    const index = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    node.parentElement = this;
    return node;
  }

  matches(selector) {
    if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
    if (selector === "[data-sort-handle]") return Object.hasOwn(this.dataset, "sortHandle");
    return false;
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = node => {
      for (const child of node.children) {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getBoundingClientRect() {
    const dynamicTop = this.classList.contains("item") && this.parentElement
      ? this.parentElement.children.indexOf(this) * 50
      : this.top;
    return { top: dynamicTop, height: this.height };
  }
  getAnimations() { return this.animationCalls.filter(animation => !animation.cancelled); }
  animate(keyframes, options) {
    const animation = {
      keyframes,
      options,
      cancelled: false,
      cancel() { this.cancelled = true; },
    };
    this.animationCalls.push(animation);
    return animation;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  focus() { this.focused = true; }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  setPointerCapture(pointerId) { this.capturedPointers.add(pointerId); }
  hasPointerCapture(pointerId) { return this.capturedPointers.has(pointerId); }
  releasePointerCapture(pointerId) { this.capturedPointers.delete(pointerId); }

  dispatch(type, values = {}) {
    const event = {
      type,
      target: this,
      pointerId: 1,
      button: 0,
      isPrimary: true,
      clientY: 0,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      ...values,
    };
    for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
    return event;
  }
}

function sortableFixture() {
  const container = new SortNode();
  const rows = [0, 1, 2].map(index => {
    const row = new SortNode({ className: "item", sortKey: String.fromCharCode(97 + index), top: index * 50 });
    row.append(new SortNode({ handle: true }));
    return row;
  });
  container.append(...rows);
  return { container, rows, handles: rows.map(row => row.children[0]) };
}

test("sortable values move in either direction without mutating editor state", () => {
  const original = ["id", "tenant_id", "created_at"];

  assert.deepEqual(reorderedValues(original, 0, 2), ["tenant_id", "created_at", "id"]);
  assert.deepEqual(reorderedValues(original, 2, 0), ["created_at", "id", "tenant_id"]);
  assert.deepEqual(original, ["id", "tenant_id", "created_at"]);
});

test("sortable values ignore unavailable and no-op destinations", () => {
  const original = ["a", "b"];

  assert.deepEqual(reorderedValues(original, 0, 0), original);
  assert.deepEqual(reorderedValues(original, -1, 1), original);
  assert.deepEqual(reorderedValues(original, 0, 4), original);
  assert.notEqual(reorderedValues(original, 0, 0), original);
  assert.throws(() => reorderedValues("not-an-array", 0, 1), /array/);
});

test("pointer dragging captures on the stable list while rows move", () => {
  const { container, rows, handles } = sortableFixture();
  const reorders = [];
  installSortableList(container, {
    itemSelector: ".item",
    onReorder: (fromIndex, toIndex, details) => reorders.push([fromIndex, toIndex, details]),
  });

  container.dispatch("pointerdown", { target: handles[0], pointerId: 9 });
  assert.equal(container.hasPointerCapture(9), true);
  assert.equal(handles[0].hasPointerCapture(9), false);
  container.dispatch("pointermove", { target: container, pointerId: 9, clientY: 500 });
  assert.deepEqual(container.children.map(row => row.dataset.sortKey), ["b", "c", "a"]);
  assert.equal(container.hasPointerCapture(9), true);
  assert.equal(rows[2].classList.contains("sort-drop-after"), true);
  assert.equal(rows[0].dataset.sortPosition, "Position 3 of 3");
  assert.deepEqual(rows[1].animationCalls[0].keyframes, [
    { transform: "translate3d(0, 50px, 0)" },
    { transform: "translate3d(0, 0, 0)" },
  ]);
  assert.deepEqual(rows[1].animationCalls[0].options, {
    duration: 190,
    easing: "cubic-bezier(.22, 1, .36, 1)",
  });
  container.dispatch("pointerup", { target: container, pointerId: 9 });

  assert.equal(container.hasPointerCapture(9), false);
  assert.deepEqual(reorders, [[0, 2, { input: "pointer", sortKey: "a" }]]);
  assert.equal(rows[0].classList.contains("is-sorting"), false);
  assert.equal(rows[0].dataset.sortPosition, undefined);
  assert.equal(rows[2].classList.contains("sort-drop-after"), false);
});

test("cancelled pointer dragging restores the original order", () => {
  const { container, handles } = sortableFixture();
  const reorders = [];
  installSortableList(container, {
    itemSelector: ".item",
    onReorder: (...change) => reorders.push(change),
  });

  container.dispatch("pointerdown", { target: handles[0], pointerId: 4 });
  container.dispatch("pointermove", { target: container, pointerId: 4, clientY: 500 });
  container.dispatch("pointercancel", { target: container, pointerId: 4 });

  assert.deepEqual(container.children.map(row => row.dataset.sortKey), ["a", "b", "c"]);
  assert.deepEqual(reorders, []);
  assert.equal(container.hasPointerCapture(4), false);
});
