import assert from "node:assert/strict";
import test from "node:test";

import {
  closeDetailsMenus,
  createStatePanel,
  DockPane,
  ICONS,
  installDetailsMenu,
  installOverflowDisclosure,
  installVisualViewportSizing,
  isOverflowingText,
  renderStatePanel,
  setControlLoading,
} from "../../src/schemii/schemii/web/assets/ui.js";

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const present = force === undefined ? !this.values.has(name) : Boolean(force);
    if (present) this.values.add(name);
    else this.values.delete(name);
    return present;
  }
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

class ElementTarget extends Target {
  constructor(documentRef, tagName) {
    super(documentRef);
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.textContent = "";
  }

  append(...children) { this.childNodes.push(...children); }
  replaceChildren(...children) { this.childNodes = [...children]; }
}

function uiDocument() {
  const documentRef = new Target(null);
  documentRef.ownerDocument = documentRef;
  documentRef.createElement = tagName => new ElementTarget(documentRef, tagName);
  return documentRef;
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
  for (const name of ["close", "sql", "database", "edit", "earlier", "later", "copy", "link", "check", "duplicate", "delete", "add", "refresh", "calendar", "schemas", "search", "more", "assistant", "history", "settings", "new-chat", "key"]) {
    assert.match(ICONS[name], /^<svg viewBox="0 0 20 20" aria-hidden="true">/);
  }
  assert.equal(Object.isFrozen(ICONS), true);
});

test("overflow detection covers clipped inline and wrapped text", () => {
  assert.equal(isOverflowingText({ clientWidth: 100, scrollWidth: 101, clientHeight: 20, scrollHeight: 20 }), true);
  assert.equal(isOverflowingText({ clientWidth: 100, scrollWidth: 100, clientHeight: 20, scrollHeight: 21 }), true);
  assert.equal(isOverflowingText({ clientWidth: 100, scrollWidth: 100, clientHeight: 20, scrollHeight: 20 }), false);
  assert.equal(isOverflowingText(null), false);
});

test("overflow disclosures activate only for clipped content and toggle full details", async () => {
  const documentRef = { activeElement: null };
  documentRef.defaultView = { requestAnimationFrame: callback => callback() };
  const control = new Target(documentRef);
  const clipped = { clientWidth: 80, scrollWidth: 120, clientHeight: 20, scrollHeight: 20 };
  const visible = { clientWidth: 80, scrollWidth: 80, clientHeight: 20, scrollHeight: 20 };
  const disclosure = installOverflowDisclosure(control, {
    targets: [visible, clipped],
    label: "column mapping source to result",
  });
  await Promise.resolve();

  assert.equal(control.classList.contains("ui-overflow-disclosure"), true);
  assert.equal(control.getAttribute("aria-expanded"), "false");
  assert.equal(control.getAttribute("role"), "button");
  assert.equal(control.getAttribute("aria-label"), "Expand column mapping source to result");
  assert.equal(control.tabIndex, 0);

  let keyboardDefaultPrevented = false;
  for (const callback of control.listeners.get("keydown")) {
    callback({ key: "Enter", preventDefault() { keyboardDefaultPrevented = true; } });
  }
  assert.equal(keyboardDefaultPrevented, true);
  assert.equal(disclosure.expanded, true);
  assert.equal(control.classList.contains("ui-overflow-disclosure-expanded"), true);
  assert.equal(control.getAttribute("aria-expanded"), "true");
  assert.equal(control.getAttribute("aria-label"), "Collapse column mapping source to result");

  for (const callback of control.listeners.get("keydown")) {
    callback({ key: " ", preventDefault() {} });
  }
  clipped.scrollWidth = 80;
  disclosure.measure();
  assert.equal(control.classList.contains("ui-overflow-disclosure"), false);
  assert.equal(control.getAttribute("aria-expanded"), null);
  assert.equal(control.getAttribute("aria-label"), null);
  assert.equal(control.getAttribute("tabindex"), null);
  assert.equal(control.getAttribute("role"), null);
  disclosure.destroy();
});

test("dialog viewport sizing follows the actually visible mobile viewport", () => {
  const listeners = new Map();
  const visualListeners = new Map();
  const values = new Map();
  const windowRef = {
    innerHeight: 800,
    addEventListener(type, callback) { listeners.set(type, callback); },
    removeEventListener(type) { listeners.delete(type); },
    visualViewport: {
      height: 620,
      offsetTop: 18,
      addEventListener(type, callback) { visualListeners.set(type, callback); },
      removeEventListener(type) { visualListeners.delete(type); },
    },
  };
  const documentRef = {
    defaultView: windowRef,
    documentElement: {
      style: {
        setProperty(name, value) { values.set(name, value); },
        removeProperty(name) { values.delete(name); },
      },
    },
  };

  const sizing = installVisualViewportSizing(documentRef);
  assert.equal(values.get("--ui-visual-viewport-height"), "620px");
  assert.equal(values.get("--ui-visual-viewport-center-y"), "328px");

  windowRef.visualViewport.height = 540;
  windowRef.visualViewport.offsetTop = 30;
  visualListeners.get("resize")();
  assert.equal(values.get("--ui-visual-viewport-height"), "540px");
  assert.equal(values.get("--ui-visual-viewport-center-y"), "300px");

  sizing.destroy();
  assert.equal(values.size, 0);
  assert.equal(listeners.size, 0);
  assert.equal(visualListeners.size, 0);
});

test("shared state panels own stable mark and variant markup", () => {
  const documentRef = uiDocument();
  const action = documentRef.createElement("button");
  const panel = createStatePanel({
    mark: "…",
    title: "Loading catalog",
    message: "Reading the active PostgreSQL catalog.",
    variant: "loading",
    surface: true,
    className: "catalog-state-card",
    action,
  }, documentRef);

  assert.equal(panel.classList.contains("ui-state"), true);
  assert.equal(panel.classList.contains("surface"), true);
  assert.equal(panel.classList.contains("loading"), true);
  assert.equal(panel.childNodes[0].classList.contains("ui-state__mark"), true);
  assert.equal(panel.childNodes[0].getAttribute("aria-hidden"), "true");
  assert.equal(panel.childNodes[1].textContent, "Loading catalog");
  assert.equal(panel.childNodes.at(-1), action);

  renderStatePanel(panel, {
    mark: "!",
    title: "Catalog unavailable",
    message: "The live request failed.",
    variant: "error",
  });
  assert.equal(panel.classList.contains("loading"), false);
  assert.equal(panel.classList.contains("error"), true);
  assert.equal(panel.childNodes[0].textContent, "!");
  assert.equal(panel.childNodes.length, 3);
});

test("shared menu lifecycle closes siblings and restores focus from hidden content", async () => {
  const documentRef = uiDocument();
  const first = new Target(documentRef);
  const second = new Target(documentRef);
  const summary = new Target(documentRef);
  const action = new Target(documentRef);
  first.children.add(summary);
  first.children.add(action);
  first.querySelector = selector => selector === "summary" ? summary : null;
  action.closest = () => action;
  first.setAttribute("open", "");
  second.setAttribute("open", "");
  first.open = true;
  second.open = true;
  documentRef.querySelectorAll = () => [first, second].filter(menu => menu.getAttribute("open") !== null);

  closeDetailsMenus(documentRef, { except: first });
  assert.equal(first.getAttribute("open"), "");
  assert.equal(second.getAttribute("open"), null);

  second.setAttribute("open", "");
  const removeFirstAttribute = first.removeAttribute.bind(first);
  first.removeAttribute = name => {
    removeFirstAttribute(name);
    if (name === "open" && first.contains(documentRef.activeElement)) documentRef.activeElement = documentRef;
  };
  const controller = installDetailsMenu(first);
  for (const callback of first.listeners.get("toggle")) callback({ target: first });
  assert.equal(second.getAttribute("open"), null);
  documentRef.activeElement = action;
  for (const callback of first.listeners.get("click")) callback({ target: action });
  await Promise.resolve();
  assert.equal(summary.focused, true);
  controller.destroy();
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
  control.focus();

  setControlLoading(control, true, { loadingLabel: "Refreshing contract" });
  assert.equal(control.disabled, false);
  assert.equal(control.getAttribute("aria-disabled"), "true");
  assert.equal(control.getAttribute("aria-busy"), "true");
  assert.equal(control.getAttribute("aria-label"), "Refreshing contract");
  assert.equal(control.classList.contains("ui-control-loading"), true);
  assert.equal(documentRef.activeElement, control);

  setControlLoading(control, false);
  assert.equal(control.disabled, false);
  assert.equal(control.getAttribute("aria-disabled"), null);
  assert.equal(control.getAttribute("aria-label"), "Refresh");
  assert.equal(control.dataset.uiTooltip, "Refresh contract");
  assert.equal(control.classList.contains("ui-control-loading"), false);
});
