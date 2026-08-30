import assert from "node:assert/strict";
import test from "node:test";

import { DockPane, ICONS, setControlLoading } from "../../src/schemii/schemii/web/assets/ui.js";

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
}

class Target {
  constructor(documentRef) {
    this.ownerDocument = documentRef;
    this.listeners = new Map();
    this.attributes = new Map();
    this.classList = new ClassList();
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.inert = false;
    this.children = new Set();
    this.focused = false;
  }

  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || new Set();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
  }

  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  contains(target) { return target === this || this.children.has(target); }
  focus() { this.focused = true; this.ownerDocument.activeElement = this; }
  blur() { this.ownerDocument.activeElement = null; }
  click() { for (const callback of this.listeners.get("click") || []) callback({ target: this }); }
}

function dockFixture() {
  const documentRef = { activeElement: null };
  const container = new Target(documentRef);
  const pane = new Target(documentRef);
  const body = new Target(documentRef);
  const toggle = new Target(documentRef);
  const dismiss = new Target(documentRef);
  const external = new Target(documentRef);
  pane.children.add(body);
  const states = [];
  const dock = new DockPane({
    container,
    pane,
    body,
    toggle,
    dismiss,
    expandedLabel: "Minimize inspector",
    minimizedLabel: "Expand inspector",
    getRestoreFocusTarget: () => external,
    onStateChange: state => states.push(state),
  });
  return { body, container, dismiss, dock, documentRef, external, pane, states, toggle };
}

test("shared icon registry preserves the legacy visual vocabulary", () => {
  for (const name of ["close", "sql", "database", "edit", "earlier", "later", "copy", "duplicate", "delete", "add", "refresh", "calendar", "schemas", "search", "more", "assistant", "history", "settings", "new-chat"]) {
    assert.match(ICONS[name], /^<svg viewBox="0 0 20 20" aria-hidden="true">/);
  }
  assert.equal(Object.isFrozen(ICONS), true);
});

test("dock panes expose expanded and minimized state without discarding content", () => {
  const { body, container, dock, pane, toggle } = dockFixture();

  assert.equal(container.dataset.rightPaneState, "expanded");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(body.inert, false);

  toggle.click();
  assert.equal(dock.state, "minimized");
  assert.equal(pane.dataset.uiDockState, "minimized");
  assert.equal(toggle.getAttribute("aria-label"), "Expand inspector");
  assert.equal(body.getAttribute("aria-hidden"), "true");
  assert.equal(body.inert, true);

  toggle.click();
  assert.equal(dock.state, "expanded");
  assert.equal(body.inert, false);
});

test("dismissed panes restore external focus and reopen on selection", () => {
  const { container, dismiss, dock, documentRef, external, pane } = dockFixture();
  pane.children.add(dismiss);
  documentRef.activeElement = dismiss;

  dismiss.click();

  assert.equal(container.dataset.rightPaneState, "dismissed");
  assert.equal(external.focused, true);
  dock.reveal();
  assert.equal(dock.state, "expanded");
  assert.equal(container.dataset.rightPaneState, "expanded");
});

test("unavailable panes are removed from layout and reset for the next selection", () => {
  const { body, container, dock, pane } = dockFixture();
  dock.minimize();
  dock.setAvailable(false, { reset: true });

  assert.equal(container.dataset.rightPaneState, "unavailable");
  assert.equal(pane.hidden, true);
  assert.equal(body.inert, true);

  dock.reveal();
  assert.equal(pane.hidden, false);
  assert.equal(dock.state, "expanded");
});

test("unavailable panes do not move focus into controls they are about to hide", () => {
  const { dismiss, dock, documentRef, pane, toggle } = dockFixture();
  pane.children.add(dismiss);
  documentRef.activeElement = dismiss;
  dock.getRestoreFocusTarget = () => null;

  dock.setAvailable(false);

  assert.equal(documentRef.activeElement, null);
  assert.equal(toggle.focused, false);
});

test("loading controls restore their prior disabled and accessible state", () => {
  const documentRef = { activeElement: null };
  const control = new Target(documentRef);
  control.setAttribute("aria-label", "Refresh");
  control.dataset.uiTooltip = "Refresh contract";

  setControlLoading(control, true, { loadingLabel: "Refreshing contract" });
  assert.equal(control.disabled, true);
  assert.equal(control.getAttribute("aria-busy"), "true");
  assert.equal(control.getAttribute("aria-label"), "Refreshing contract");
  assert.equal(control.classList.contains("ui-control-loading"), true);

  setControlLoading(control, false);
  assert.equal(control.disabled, false);
  assert.equal(control.getAttribute("aria-label"), "Refresh");
  assert.equal(control.dataset.uiTooltip, "Refresh contract");
  assert.equal(control.classList.contains("ui-control-loading"), false);
});
