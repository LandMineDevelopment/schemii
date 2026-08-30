const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const themeCss = fs.readFileSync("src/schemii/shared_web/theme.css", "utf8");

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, enabled) { if (enabled) this.add(value); else this.remove(value); }
  contains(value) { return this.values.has(value); }
}

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.dataset = {};
    this.attributes = {};
    this.classList = new ClassList();
    this.disabled = false;
    this.innerHTML = "";
    this.children = [];
    this.listeners = new Map();
    this.queries = new Map();
    this.style = {};
    this.open = false;
    this.hidden = false;
    this.checked = false;
    this.textContent = "";
  }
  set className(value) { this._className = value; value.split(/\s+/).filter(Boolean).forEach(item => this.classList.add(item)); }
  get className() { return this._className || ""; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(callback); }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  dispatch(type) { for (const callback of this.listeners.get(type) || []) callback({ target: this, currentTarget: this }); }
  querySelector(selector) { return this.queries.get(selector) || null; }
  querySelectorAll(selector) { return this.queries.get(selector) || []; }
  replaceChildren(...children) { this.children = children; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch("close"); }
  getBoundingClientRect() { return this.bounds || { left: 0, top: 0, width: 100, height: 100 }; }
}

const downloads = [];
const objectUrls = [];
const revokedUrls = [];
class TestBlob {
  constructor(parts, { type = "" } = {}) { this.parts = parts; this.type = type; }
}
const document = {
  body: new Element("body"),
  createElement: tag => {
    const element = new Element(tag);
    if (tag === "a") element.click = () => downloads.push({ href: element.href, filename: element.download });
    return element;
  }
};
const storage = new Map();
const context = vm.createContext({
  window: { innerWidth: 1000, innerHeight: 800 }, document, HTMLElement: Element,
  localStorage: {
    getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); }
  },
  getComputedStyle: () => ({ textOverflow: "clip", webkitLineClamp: "none" }),
  Blob: TestBlob,
  URL: {
    createObjectURL(blob) { objectUrls.push(blob); return `blob:test-${objectUrls.length}`; },
    revokeObjectURL(url) { revokedUrls.push(url); },
  },
  setTimeout, clearTimeout, requestAnimationFrame: callback => callback(), TypeError
});
vm.runInContext(fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8"), context);
const shared = context.window.SchemiiShared;

const button = shared.createIconButton({ icon: "refresh", label: "Refresh", dataset: { action: "refresh" } });
assert.equal(button.type, "button");
assert.equal(button.getAttribute("aria-label"), "Refresh");
assert.equal(button.dataset.action, "refresh");
assert.match(button.innerHTML, /<svg/);
for (const icon of ["assistant", "history", "settings", "newChat"]) {
  const control = shared.createIconButton({ icon, label: icon });
  assert.match(control.innerHTML, /<svg/, `${icon} must be available from shared UI components`);
}
const originalMarkup = button.innerHTML;
shared.setControlLoading(button, true, { loadingLabel: "Checking" });
assert.equal(button.innerHTML, originalMarkup, "loading must preserve icon markup");
assert.equal(button.getAttribute("aria-busy"), "true");
assert.equal(button.disabled, true);
shared.setControlLoading(button, false, { label: "Refresh" });
assert.equal(button.getAttribute("aria-busy"), null);
assert.equal(button.disabled, false);
assert.equal(button.dataset.tooltip, "Refresh");

const status = new Element();
shared.setControlStatus(status, "Failed", { state: "error", hideWhenEmpty: true });
assert.equal(status.classList.contains("error"), true);
assert.equal(status.hidden, false);
assert.throws(() => shared.createIconButton({ icon: "missing", label: "Missing" }), TypeError);

shared.downloadContent('{"ok":true}', "result.json", "application/json");
assert.deepEqual(downloads[0], { href: "blob:test-1", filename: "result.json" });
assert.equal(objectUrls[0].type, "application/json");
assert.deepEqual(Array.from(objectUrls[0].parts), ['{"ok":true}']);
assert.deepEqual(revokedUrls, ["blob:test-1"], "object URLs must be released after dispatch");
const existingBlob = new TestBlob(["raw"], { type: "text/plain" });
shared.downloadBlob(existingBlob, "result.txt");
assert.equal(objectUrls[1], existingBlob, "Blob downloads must not rebuild app-owned payloads");
assert.deepEqual(downloads[1], { href: "blob:test-2", filename: "result.txt" });

const root = new Element();
root.bounds = { left: 10, top: 20, width: 200, height: 100 };
const cursor = new Element();
const clickLabel = new Element("span");
cursor.queries.set("span", clickLabel);
const target = new Element("button");
target.bounds = { left: 160, top: 90, width: 20, height: 10 };
shared.positionOnboardingCursor(root, cursor, target, "Right click");
assert.equal(cursor.classList.contains("visible"), true);
assert.equal(cursor.classList.contains("tooltip-left"), true);
assert.equal(cursor.classList.contains("tooltip-above"), true);
assert.equal(cursor.classList.contains("right-click"), true);
assert.equal(clickLabel.textContent, "Right click");

const timers = new Map();
let timerId = 0;
context.setTimeout = callback => { const current = ++timerId; timers.set(current, callback); return current; };
context.clearTimeout = current => timers.delete(current);
context.window.matchMedia = () => ({ matches: false });
const demoRoot = new Element();
demoRoot.bounds = { left: 0, top: 0, width: 200, height: 100 };
const demoTarget = new Element("button");
demoTarget.bounds = { left: 20, top: 20, width: 20, height: 10 };
demoRoot.queries.set('[data-onboarding-target="go"]', demoTarget);
const demoCursor = new Element();
demoCursor.queries.set("span", new Element("span"));
const demoStatus = new Element();
const demoToggle = new Element("button");
const renderedStates = [];
const demo = shared.createOnboardingDemo({
  root: demoRoot, cursor: demoCursor, status: demoStatus, toggle: demoToggle,
  steps: [{ target: "go", caption: "Open the view.", state: "open" }],
  renderState: state => renderedStates.push(state), isActive: () => true,
  idleText: "Watch.", staticText: "Open.", staticState: "open",
});
assert.equal(demoStatus.getAttribute("role"), "status");
assert.equal(demoStatus.getAttribute("aria-live"), "polite");
const runNextTimer = () => {
  const [current, callback] = timers.entries().next().value;
  timers.delete(current);
  callback();
};
demo.start();
assert.equal(demoToggle.textContent, "Pause demo");
assert.equal(timers.size, 1, "animated playback must schedule its initial step");
runNextTimer();
assert.equal(demoCursor.classList.contains("visible"), true);
runNextTimer();
assert.equal(demoCursor.classList.contains("clicking"), true);
runNextTimer();
assert.equal(renderedStates.at(-1), "open");
demo.stop();
assert.equal(timers.size, 0, "stopping playback must cancel its timer");
assert.equal(demoCursor.classList.contains("visible"), false);
context.window.matchMedia = () => ({ matches: true });
demo.start();
assert.equal(renderedStates.at(-1), "open", "reduced motion must render the static completed state");
assert.equal(demoToggle.textContent, "Play demo");
assert.equal(timers.size, 0, "reduced motion must not autoplay timers");
context.window.matchMedia = () => ({ matches: false });
demo.start();
demoToggle.dispatch("click");
assert.equal(demoStatus.textContent, "Demo paused.");
assert.equal(timers.size, 0, "pausing must cancel the current timer");
demo.destroy();

const dialog = new Element("dialog");
const pages = [new Element("section"), new Element("section")];
dialog.queries.set("[data-onboarding-page]", pages);
const stepLabel = new Element();
const progress = new Element();
const back = new Element("button");
const next = new Element("button");
const nextLabel = new Element("span");
next.queries.set("[data-onboarding-next-label], span", nextLabel);
const skip = new Element("button");
const optOut = new Element("input");
let starts = 0;
let stops = 0;
const controller = shared.createOnboardingController({
  dialog, stepLabel, progress, backButton: back, nextButton: next, skipButton: skip, optOut,
  storagePrefix: "test-app",
  demos: [
    { start() { starts += 1; }, stop() { stops += 1; } },
    { start() { starts += 10; }, stop() { stops += 10; } },
  ]
});
assert.equal(controller.shouldShow("server-one"), true, "a new server start should show onboarding");
assert.equal(storage.get("test-app.onboarding.server.v1"), "server-one");
assert.equal(controller.shouldShow("server-one"), false, "a refresh in one server run must not reshow onboarding");
assert.equal(controller.shouldShow("server-two"), true, "a later server start should show onboarding again");
controller.open();
assert.equal(dialog.open, true);
assert.equal(stepLabel.textContent, "1 of 2");
assert.equal(back.disabled, true);
assert.equal(nextLabel.textContent, "Next");
assert.equal(progress.children.length, 2);
assert.equal(starts, 1);
next.dispatch("click");
assert.equal(controller.page, 1);
assert.equal(nextLabel.textContent, "Finish");
assert.equal(starts, 11);
next.dispatch("click");
assert.equal(dialog.open, false);
controller.open();
optOut.checked = true;
skip.dispatch("click");
assert.equal(storage.get("test-app.onboarding.disabled.v1"), "1");
assert.equal(controller.shouldShow("server-three"), false, "the persistent opt-out must suppress future onboarding");
assert.ok(stops > 0, "page changes and close must stop tutorial animations");
controller.destroy();
assert.match(themeCss, /\* \{[^}]*scrollbar-width: thin;[^}]*scrollbar-color: var\(--scrollbar-thumb\) var\(--scrollbar-track\);[^}]*\}[\s\S]*\*::\-webkit-scrollbar \{ width: 8px; height: 8px; \}/, "both apps must share equal vertical and horizontal scrollbar sizing and colors");
assert.match(themeCss, /\*::\-webkit-scrollbar-thumb \{[^}]*min-width: 32px;[^}]*min-height: 32px;[^}]*border: 2px solid var\(--scrollbar-track\);[^}]*background: var\(--scrollbar-thumb\);/, "all shared scrollbar thumbs must use the same bidirectional treatment");
for (const direction of ["vertical:decrement", "vertical:increment", "horizontal:decrement", "horizontal:increment"]) assert.match(themeCss, new RegExp(`scrollbar-button:single-button:${direction.replace(":", "\\:")}`), `shared scrollbars need a ${direction} button`);
console.log("Shared UI component tests passed");
