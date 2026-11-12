import assert from "node:assert/strict";
import test from "node:test";
import { createProviderConnections } from "../../src/schemii/common/web/assets/ai-provider-connections.js";

class Node extends EventTarget {
  constructor(tag) { super(); this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.value = ""; this.ownText = ""; }
  set textContent(value) { this.ownText = String(value); this.replaceChildren(); }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  all(tag) { return this.children.flatMap(child => [child, ...child.all("*")]).filter(child => tag === "*" || child.tagName === tag); }
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const settle = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) { for (let i = 0; i < 30; i++) { if (predicate()) return; await settle(); } assert.fail("Expected provider lifecycle transition did not occur"); }
const providers = [
  { id: "openai-codex", name: "Codex", available: false, authenticated: false },
  { id: "openai", name: "OpenAI API", available: false, authenticated: false },
  { id: "opencode", name: "OpenCode Zen", available: false },
  { id: "instance-codex", name: "Shared Codex", available: true, adminManaged: true, selectedModelId: "managed", selectedReasoningEffort: "high", models: [{ id: "managed", name: "Managed model" }] },
];
const runtime = (extra = {}) => ({ healthy: true, providers: providers.map(provider => ({ ...provider })), ...extra });

function fixture(t, { request = async () => ({}), product = "schemoo" } = {}) {
  const original = Object.fromEntries(["document", "Node", "setTimeout", "clearTimeout"].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const timers = new Map(), calls = [], applied = [], statuses = [];
  let timerId = 0, scope = { product, resourceId: "A" };
  Object.assign(globalThis, { Node, document: { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node("#text"), { textContent: text }) },
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; }, clearTimeout: id => timers.delete(id),
  });
  const container = new Node("div");
  const panel = createProviderConnections({ container, getScope: () => scope,
    onRuntime: next => applied.push(next), onStatus: message => statuses.push(message),
    request: async (path, options = {}) => { calls.push({ path, ...options, ...(options.body ? { body: { ...options.body } } : {}) }); return request(path, options); },
  });
  t.after(() => { panel.dispose(); for (const [name, descriptor] of Object.entries(original)) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } });
  panel.setRuntime(runtime());
  const click = label => {
    const node = container.all("button").find(button => button.textContent === label);
    assert.ok(node, `Missing ${label}`); assert.ok(!node.disabled, `${label} is disabled`);
    node.dispatchEvent(new Event("click"));
  };
  return { panel, container, calls, applied, statuses, timers, click,
    changeScope(id) { scope = { product, resourceId: id }; panel.scopeChanged(); },
    tick() { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.callback(); },
    submitKey(secret) { const input = container.all("input")[0]; input.value = secret; const form = container.all("form")[0]; const event = new Event("submit", { cancelable: true, bubbles: true }); form.dispatchEvent(event); assert.equal(event.defaultPrevented, true); assert.equal(event.cancelBubble, true); return input; },
  };
}

for (const product of ["schemii", "schemoo", "schemer"]) {
  test(`${product}: cancel during login creation cleans up the old ID without replacing a reconnect`, async t => {
    const first = deferred(), second = deferred(); let starts = 0;
    const f = fixture(t, { product, request: (path, options) => {
      if (path.endsWith("/login")) return ++starts === 1 ? first.promise : second.promise;
      return { status: "pending", userCode: "CURRENT", verificationUrl: "https://auth.openai.com/codex/device" };
    } });
    f.click("Connect Codex"); f.click("Cancel sign-in"); f.click("Connect Codex");
    second.resolve({ id: "new" }); await until(() => f.container.textContent.includes("CURRENT"));
    first.resolve({ id: "old" }); await until(() => f.calls.some(call => call.path.endsWith("/old") && call.method === "DELETE"));
    assert.ok(f.container.textContent.includes("CURRENT"));
    assert.equal(f.timers.size, 1);
    assert.equal(f.calls.filter(call => call.path.endsWith("/old") && !call.method).length, 0);
    f.click("Cancel sign-in"); await settle();
    assert.equal(f.timers.size, 0);
    assert.ok(f.calls.some(call => call.path.endsWith("/new") && call.method === "DELETE"));
  });
  test(`${product}: managed providers have notices and locked policy, without credential controls`, t => {
    const f = fixture(t, { product });
    for (const id of ["opencode", "instance-codex"]) {
      const card = f.container.children.find(card => card.dataset.providerId === id);
      assert.ok(card.textContent.includes("An administrator manages"));
      assert.equal(card.all("input").length + card.all("button").length, 0);
    }
    assert.ok(f.container.textContent.includes("Administrator policy: Managed model · High reasoning"));
  });
}

test("late login creation failures cannot clear a newer sign-in or show its error", async t => {
  const old = deferred(); let starts = 0;
  const f = fixture(t, { request: path => path.endsWith("/login") ? ++starts === 1 ? old.promise : { id: "new" } : { status: "pending", userCode: "NEW" } });
  f.click("Connect Codex"); f.click("Cancel sign-in"); f.click("Connect Codex");
  await until(() => f.container.textContent.includes("NEW"));
  old.reject(new Error("obsolete login failure")); await settle();
  assert.ok(f.container.textContent.includes("NEW"));
  assert.ok(!f.statuses.includes("obsolete login failure"));
  assert.equal(f.timers.size, 1);
});

test("cancel captures the exact current ID and a stale poll cannot restore its code", async t => {
  const poll = deferred();
  const f = fixture(t, { request: (path, options) => path.endsWith("/login") ? { id: "exact" } : options.method === "DELETE" ? {} : poll.promise });
  f.click("Connect Codex"); await until(() => f.calls.some(call => call.path.endsWith("/exact")));
  f.click("Cancel sign-in"); poll.resolve({ status: "pending", userCode: "OLD" }); await settle();
  assert.ok(f.calls.some(call => call.path.endsWith("/exact") && call.method === "DELETE"));
  assert.ok(!f.container.textContent.includes("OLD")); assert.equal(f.timers.size, 0);
});

test("successful login refreshes scoped status once and releases the polling timer", async t => {
  let polls = 0;
  const f = fixture(t, { product: "schemer", request: path => path.endsWith("/login") ? { id: "done" } : path.startsWith("/api/v1/ai/status") ? runtime() : { status: ++polls === 1 ? "pending" : "succeeded" } });
  f.click("Connect Codex"); await until(() => f.timers.size === 1); f.tick();
  await until(() => f.statuses.at(-1) === "Codex connected to your account.");
  assert.equal(f.timers.size, 0); assert.equal(f.applied.length, 1);
  assert.equal(f.calls.filter(call => call.path.startsWith("/api/v1/ai/status")).length, 1);
  assert.equal(f.calls.find(call => call.path.startsWith("/api/v1/ai/status")).path, "/api/v1/ai/status?product=schemer&resourceId=A");
});

test("login expiry stops polling and retires only the expired ID", async t => {
  let now = 1000; t.mock.method(Date, "now", () => now);
  const f = fixture(t, { request: path => path.endsWith("/login") ? { id: "expires" } : { status: "pending", expiresAt: 1100 } });
  f.click("Connect Codex"); await until(() => f.timers.size === 1);
  assert.equal([...f.timers.values()][0].delay, 100);
  now = 1100; f.tick(); await settle();
  assert.equal(f.timers.size, 0); assert.equal(f.statuses.at(-1), "Sign-in expired. Connect again.");
  assert.ok(f.calls.some(call => call.path.endsWith("/expires") && call.method === "DELETE"));
});

test("status refresh ignores older responses and stale errors across resource replacement", async t => {
  const old = deferred(), current = deferred(), staleFailure = deferred(); let requests = 0;
  const f = fixture(t, { request: () => [old, current, staleFailure][requests++].promise });
  const pendingOld = f.panel.refresh(), pendingNew = f.panel.refresh();
  current.resolve(runtime({ message: "current" })); await pendingNew;
  old.resolve(runtime({ message: "old" })); assert.equal(await pendingOld, null);
  const rejected = f.panel.refresh(); f.changeScope("B"); f.changeScope("A");
  staleFailure.reject(new Error("old scope failed")); assert.equal(await rejected, null);
  assert.deepEqual(f.applied.map(value => value.message), ["current"]);
  assert.equal(f.container.all("input").length, 0);
});

for (const outcome of ["success", "failure"]) {
  test(`key input is masked and cleared before removal on ${outcome}, including the detached input`, async t => {
    const credential = deferred();
    const f = fixture(t, { request: path => path.startsWith("/api/v1/ai/status") ? runtime() : credential.promise });
    const input = f.submitKey("private-fixture-key");
    assert.equal(input.getAttribute("type"), "password"); assert.equal(input.value, "");
    assert.equal(f.calls[0].body.apiKey, "private-fixture-key");
    if (outcome === "failure") credential.reject(new Error("controlled credential failure")); else credential.resolve({});
    await until(() => f.statuses.length > 0);
    assert.ok(!f.container.textContent.includes("private-fixture-key"));
    assert.equal(input.value, ""); assert.equal(f.container.all("input")[0].value, "");
    assert.equal(f.container.all("input")[0].disabled, false);
  });
}

test("rerender, scope change and disposal clear retained and detached key inputs", async t => {
  const f = fixture(t);
  const first = f.container.all("input")[0]; first.value = "secret-one"; f.panel.render(); assert.equal(first.value, "");
  const second = f.container.all("input")[0]; second.value = "secret-two"; f.changeScope("B"); assert.equal(second.value, "");
  f.panel.setRuntime(runtime()); const third = f.container.all("input")[0]; third.value = "secret-three";
  f.panel.dispose(); assert.equal(third.value, ""); assert.equal(f.container.children.length, 0);
  f.panel.setRuntime(runtime()); assert.equal(f.container.children.length, 0);
});

test("disconnect is a single busy mutation and ignores late completion after a scope change", async t => {
  const disconnect = deferred();
  const f = fixture(t, { request: () => disconnect.promise });
  const next = runtime(); next.providers[0].authenticated = true; f.panel.setRuntime(next);
  f.click("Disconnect");
  assert.equal(f.container.all("button").find(node => node.textContent === "Disconnect").disabled, true);
  assert.equal(f.container.all("input")[0].disabled, true);
  f.changeScope("B"); disconnect.resolve({}); await settle();
  assert.equal(f.applied.length, 0); assert.equal(f.statuses.length, 0);
  assert.equal(f.calls[0].method, "DELETE");
});

test("disposal during login creation retires the exact late-created ID without polling", async t => {
  const create = deferred(); const f = fixture(t, { request: path => path.endsWith("/login") ? create.promise : {} });
  f.click("Connect Codex"); f.panel.dispose(); create.resolve({ id: "late" });
  await until(() => f.calls.some(call => call.path.endsWith("/late")));
  assert.equal(f.calls.at(-1).method, "DELETE"); assert.equal(f.timers.size, 0);
  assert.equal(f.container.children.length, 0);
});

for (const failure of ["poll", "catalog"]) {
  test(`current login ${failure} failure is visible and leaves reconnect available`, async t => {
    const f = fixture(t, { request: path => {
      if (path.endsWith("/login")) return { id: "failed" };
      if (path.startsWith("/api/v1/ai/status") || failure === "poll") throw new Error(`controlled ${failure} failure`);
      return { status: "succeeded" };
    } });
    f.click("Connect Codex");
    await until(() => f.statuses.at(-1) === `controlled ${failure} failure`);
    assert.equal(f.timers.size, 0);
    assert.equal(f.container.all("button").find(node => node.textContent === "Connect Codex").disabled, false);
  });
}

test("explicit catalog refresh applies current availability and reports a current catalog failure", async t => {
  const f = fixture(t, { request: async () => runtime({ healthy: false, message: "Catalog check failed" }) });
  await assert.rejects(f.panel.refresh({ refresh: true }), /Catalog check failed/);
  assert.equal(f.applied.length, 1);
  assert.ok(f.calls[0].path.endsWith("refresh=true"));
});

test("provider login navigation rejects untrusted destinations", async t => {
  const f = fixture(t, { request: path => path.endsWith("/login") ? { id: "link" } : { status: "pending", userCode: "SAFE", verificationUrl: "https://untrusted.example/device" } });
  f.click("Connect Codex"); await until(() => f.container.textContent.includes("SAFE"));
  assert.equal(f.container.all("a").length, 0);
});
