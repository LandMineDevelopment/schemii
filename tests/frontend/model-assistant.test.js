import assert from "node:assert/strict";
import test from "node:test";
import { createProductAssistant } from "../../src/schemii/common/web/assets/model-assistant.js";

// The real component and its HTTP helper run against this DOM surface. It does
// not simulate layout, browser focus isolation or mounted product acceptance.
class TestNode extends EventTarget {
  constructor(tagName, ownerDocument) {
    super(); this.tagName = tagName; this.ownerDocument = ownerDocument;
    this.children = []; this.attributes = {}; this.dataset = {}; this.style = {};
    this.className = ""; this.ownText = ""; this.storedValue = null;
    this.scrollTop = 0; this.scrollHeight = 200; this.clientHeight = 200;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/), ...names])].filter(Boolean).join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); },
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, enabled) => enabled ? this.classList.add(name) : this.classList.remove(name),
    };
  }
  set value(value) { this.storedValue = value; }
  get value() { return this.storedValue ?? (this.tagName === "select" ? this.options[0]?.value || "" : this.attributes.value || ""); }
  get options() { return this.children.filter(child => child.tagName === "option"); }
  set textContent(value) { this.ownText = String(value); this.replaceChildren(); }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { if (this.tagName === "template") this.content = { firstElementChild: new TestNode("svg", this.ownerDocument) }; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parentElement = this; this.children.push(node); } }
  prepend(...nodes) { for (const node of [...nodes].reverse()) this.insertBefore(node, this.children[0]); }
  before(node) { this.parentElement?.insertBefore(node, this); }
  insertBefore(node, next) { node.remove(); node.parentElement = this; const index = this.children.indexOf(next); this.children.splice(index < 0 ? this.children.length : index, 0, node); }
  remove() { if (this.parentElement) { this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; } }
  replaceChildren(...nodes) { for (const node of [...this.children]) node.remove(); this.append(...nodes); }
  querySelectorAll(selector) {
    const matches = node => selector.startsWith(".") ? node.classList.contains(selector.slice(1))
      : selector.startsWith("[") ? Object.hasOwn(node.attributes, selector.slice(1, -1))
      : node.tagName === selector;
    return this.children.flatMap(child => [child, ...child.querySelectorAll("*")]).filter(node => selector === "*" || matches(node));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest() { return null; }
  contains(node) { return this === node || this.querySelectorAll("*").includes(node); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.ownerDocument.activeElement = this; }
  setCustomValidity() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, bottom: 40, left: 0, width: 200 }; }
}

function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const settle = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) { for (let i = 0; i < 30; i++) { if (predicate()) return; await settle(); } assert.fail("Expected component request did not arrive"); }
const conversation = (subject, id = `chat-${subject}`) => ({ id, modelId: subject, revision: 1, status: "idle", providerId: "openai", aiModelId: "fixture-model", messages: [], activity: [] });

async function fixture(t, { existing = true, create, message, chatRead, providerStatus } = {}) {
  const document = Object.assign(new EventTarget(), { activeElement: null });
  document.createElement = tag => new TestNode(tag, document);
  document.createTextNode = text => Object.assign(new TestNode("#text", document), { textContent: text });
  document.body = document.createElement("body");
  document.defaultView = Object.assign(new EventTarget(), { innerWidth: 1000, innerHeight: 800 });
  const window = new EventTarget(), requests = [];
  let subject = "A";
  const response = value => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
  const originals = Object.fromEntries(["document", "window", "Node", "MutationObserver", "fetch"].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.assign(globalThis, { document, window, Node: TestNode, MutationObserver: class { observe() {} disconnect() {} }, fetch: async (path, options) => {
    const url = new URL(path, "https://localhost:8001"), body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path: url.pathname, method: options.method, body });
    if (url.pathname === "/api/v1/ai/status") return response(providerStatus ? await providerStatus(url) : { healthy: true, providers: [{ id: "openai", name: "OpenAI", available: true, models: [{ id: "fixture-model", name: "Fixture", status: "active" }] }] });
    if (url.pathname.endsWith("/settings")) return response({ providerId: "openai", aiModelId: "fixture-model", actions: [], modes: {} });
    if (url.pathname.endsWith("/chats") && options.method === "GET") return response({ chats: (typeof existing === "function" ? existing(url.searchParams.get("model_id")) : existing) ? [conversation(url.searchParams.get("model_id"))] : [] });
    if (url.pathname.endsWith("/chats") && options.method === "POST") return response(create ? await create(body) : conversation(body.modelId));
    if (url.pathname.endsWith("/messages")) return response(message ? await message(body, url.pathname) : conversation(subject));
    const id = url.pathname.split("/").at(-1);
    return response(chatRead ? await chatRead(id) : conversation(id.slice(5), id));
  } });
  t.after(() => { window.dispatchEvent(new Event("beforeunload")); for (const [name, descriptor] of Object.entries(originals)) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } });
  const trigger = document.createElement("button"); document.body.append(trigger);
  const assistant = createProductAssistant({ trigger, getSubjectId: () => subject, api: "/test/ai", productLabel: "Schemoo", subjectLabel: "model", subjectKey: "modelId", subjectQueryKey: "model_id", revisionKey: "modelRevision", title: "Model assistant", emptyTitle: "Model", emptyDescription: "Ask about it", examplePrompts: [] });
  await assistant.open();
  const input = document.body.querySelector("textarea"), form = document.body.querySelector("form"), notice = document.body.querySelector(".model-ai-notice");
  const edit = text => { input.value = text; input.dispatchEvent(new Event("input")); };
  return { assistant, requests, input, notice, edit, document,
    submit(text) { edit(text); return form.onsubmit({ preventDefault() {} }); },
    async switchTo(id) { subject = id; await assistant.subjectChanged(); },
  };
}

for (const existing of [true, false]) {
  test(`assistant preserves the next draft while ${existing ? "sending to an existing" : "creating the first"} conversation`, async t => {
    const create = deferred(), message = deferred();
    const f = await fixture(t, { existing, create: () => create.promise, message: () => message.promise });
    const pending = f.submit("  First message  ");
    const consumed = f.input.value;
    f.edit("Second draft");
    let afterCreation;
    if (!existing) { create.resolve(conversation("A")); await until(() => f.requests.some(request => request.path.endsWith("/messages"))); afterCreation = f.input.value; }
    message.resolve({ ...conversation("A"), revision: 2 }); await pending;
    assert.equal(consumed, "");
    if (!existing) assert.equal(afterCreation, "Second draft");
    assert.equal(f.input.value, "Second draft");
    assert.equal(f.requests.find(request => request.path.endsWith("/messages")).body.text, "First message");
  });
}

for (const replacement of [true, false]) {
  test(`first-chat completion never dispatches an old prompt to a replacement subject ${replacement ? "with" : "without"} a chat`, async t => {
    const create = deferred(); let creates = 0;
    const f = await fixture(t, { existing: id => replacement && id === "B", create: () => { creates++; return create.promise; } });
    const pending = f.submit("Change model A");
    await until(() => creates === 1);
    await f.switchTo("B");
    f.edit("B's draft");
    create.resolve(conversation("A")); await pending;
    assert.equal(f.input.value, "B's draft");
    assert.equal(f.requests.filter(request => request.path.endsWith("/messages") && request.body.text === "Change model A").length, 0);
  });
}

for (const edited of [false, true]) {
  test(`failed message ${edited ? "preserves a later edited draft" : "restores the untouched submitted draft"}`, async t => {
    const message = deferred();
    const f = await fixture(t, { message: () => message.promise });
    const pending = f.submit("  Please inspect A  ");
    if (edited) f.edit("New draft");
    message.reject(new Error("controlled offline")); await pending;
    assert.equal(f.input.value, edited ? "New draft" : "  Please inspect A  ");
    assert.equal(f.notice.dataset.error, "true");
  });
}

test("a stale message failure cannot restore text, show errors or unlock a replacement subject", async t => {
  const first = deferred(), second = deferred();
  const f = await fixture(t, { message: body => body.text === "A prompt" ? first.promise : second.promise });
  const old = f.submit("A prompt");
  await f.switchTo("B");
  const current = f.submit("B prompt");
  f.edit("B next draft");
  first.reject(new Error("old controlled failure")); await old;
  const beforeCurrentFinishes = { draft: f.input.value, noticeHidden: f.notice.hidden, status: f.document.body.querySelector(".model-ai-status").textContent };
  second.resolve({ ...conversation("B"), revision: 2 }); await current;
  assert.deepEqual(beforeCurrentFinishes, { draft: "B next draft", noticeHidden: true, status: "Loading" });
  assert.equal(f.input.value, "B next draft");
});

test("a failed first conversation restores its draft without sending a message", async t => {
  const create = deferred();
  const f = await fixture(t, { existing: false, create: () => create.promise });
  const pending = f.submit("  Create a model  ");
  create.reject(new Error("controlled creation failure")); await pending;
  assert.equal(f.input.value, "  Create a model  ");
  assert.equal(f.requests.filter(request => request.path.endsWith("/messages")).length, 0);
  assert.equal(f.notice.dataset.error, "true");
});

test("a failed message does not restore a draft deliberately cleared while sending", async t => {
  const message = deferred();
  const f = await fixture(t, { message: () => message.promise });
  const pending = f.submit("Original prompt");
  f.edit("Changed my mind"); f.edit("");
  message.reject(new Error("controlled message failure")); await pending;
  assert.equal(f.input.value, "");
});

test("a stale successful message cannot clear a replacement draft or unlock its pending send", async t => {
  const first = deferred(), second = deferred();
  const f = await fixture(t, { message: body => body.text === "A prompt" ? first.promise : second.promise });
  const old = f.submit("A prompt");
  await f.switchTo("B");
  const current = f.submit("B prompt"); f.edit("B next draft");
  first.resolve({ ...conversation("A"), revision: 2 }); await old;
  const beforeCurrentFinishes = { draft: f.input.value, status: f.document.body.querySelector(".model-ai-status").textContent };
  second.resolve({ ...conversation("B"), revision: 2 }); await current;
  assert.deepEqual(beforeCurrentFinishes, { draft: "B next draft", status: "Loading" });
});

test("a stale creation failure cannot show an error in the replacement subject", async t => {
  const create = deferred();
  const f = await fixture(t, { existing: id => id === "B", create: () => create.promise });
  const old = f.submit("A prompt");
  await f.switchTo("B"); f.edit("B draft");
  create.reject(new Error("old creation failure")); await old;
  assert.equal(f.notice.hidden, true);
  assert.equal(f.input.value, "B draft");
  assert.equal(f.document.body.querySelector(".model-ai-status").textContent, "Ready");
});

test("explicit new conversation preserves text entered while creation is pending", async t => {
  const create = deferred();
  const f = await fixture(t, { create: () => create.promise });
  f.edit("Previous draft");
  f.document.body.querySelectorAll("button").find(node => node.getAttribute("aria-label") === "New conversation").onclick();
  await until(() => f.requests.some(request => request.path.endsWith("/chats") && request.method === "POST"));
  f.edit("Next conversation draft");
  create.resolve(conversation("A", "new-chat-A"));
  await until(() => f.document.body.querySelector(".model-ai-status").textContent === "Ready");
  assert.equal(f.input.value, "Next conversation draft");
  await f.submit("Send to new conversation");
  assert.equal(f.requests.find(request => request.path.endsWith("/messages")).path, "/test/ai/chats/new-chat-A/messages");
});

test("late conversation loading cannot replace the current chat after switching away and back", async t => {
  const oldRead = deferred(); let bReads = 0;
  const f = await fixture(t, { chatRead: id => id === "chat-B" && ++bReads === 1 ? oldRead.promise : conversation(id.slice(5), id) });
  const old = f.switchTo("B"); await until(() => bReads === 1);
  await f.switchTo("A"); await f.switchTo("B");
  oldRead.resolve({ ...conversation("B"), revision: 50 }); await old;
  await f.submit("Use current B revision");
  assert.equal(f.requests.find(request => request.path.endsWith("/messages")).body.expectedRevision, 1);
});

test("empty input does not create a conversation or submit a message", async t => {
  const f = await fixture(t, { existing: false });
  await f.submit("   \n  ");
  assert.equal(f.requests.filter(request => request.method === "POST").length, 0);
  assert.equal(f.input.value, "   \n  ");
});

for (const existing of [false, true]) {
  for (const failure of [false, true]) {
    test(`same-subject reopen retains ${existing ? "message" : "first-chat"} submission ownership on ${failure ? "failure" : "success"}`, async t => {
      const request = deferred();
      const f = await fixture(t, { existing,
        ...(existing ? { message: () => request.promise } : { create: () => request.promise }),
      });
      const pending = f.submit("  Keep this prompt  ");
      f.document.body.querySelectorAll("button").find(node => node.getAttribute("aria-label") === "Close assistant").onclick();
      await f.assistant.open();
      assert.equal(f.document.body.querySelector(".model-ai-status").textContent, "Loading");
      if (failure) request.reject(new Error("controlled request failure"));
      else request.resolve(conversation("A"));
      await pending;
      const sent = f.requests.filter(item => item.path.endsWith("/messages"));
      assert.equal(sent.length, failure && !existing ? 0 : 1);
      if (sent.length) {
        assert.equal(sent[0].path, "/test/ai/chats/chat-A/messages");
        assert.equal(sent[0].body.text, "Keep this prompt");
      }
      assert.equal(f.input.value, failure ? "  Keep this prompt  " : "");
      assert.equal(f.document.body.querySelector(".model-ai-status").textContent, "Ready");
    });
  }
}

test("same-subject reopen preserves the next draft during first-chat creation", async t => {
  const create = deferred();
  const f = await fixture(t, { existing: false, create: () => create.promise });
  const pending = f.submit("First prompt");
  f.edit("Next draft");
  f.document.body.querySelectorAll("button").find(node => node.getAttribute("aria-label") === "Close assistant").onclick();
  await f.assistant.open();
  create.resolve(conversation("A")); await pending;
  assert.equal(f.input.value, "Next draft");
  assert.equal(f.requests.filter(item => item.path.endsWith("/messages")).length, 1);
});

for (const failure of [false, true]) {
  test(`provider refresh ${failure ? "failure" : "success"} cannot update a replacement model subject`, async t => {
    const old = deferred(); let reads = 0;
    const status = { healthy: true, providers: [{ id: "openai", name: "Current provider", available: true, models: [{ id: "fixture-model", name: "Current model", status: "active" }] }] };
    const f = await fixture(t, { providerStatus: () => ++reads === 2 ? old.promise : status });
    f.document.body.querySelectorAll("button").find(node => node.getAttribute("aria-label") === "Assistant settings").onclick();
    await until(() => reads === 2);
    await f.switchTo("B");
    if (failure) old.reject(new Error("Obsolete provider failure"));
    else old.resolve({ healthy: true, providers: [{ id: "openai", name: "Obsolete provider", available: false, models: [] }] });
    await settle(); await settle();
    assert.ok(!f.document.body.textContent.includes("Obsolete provider"));
    assert.ok(!f.document.body.textContent.includes("Obsolete provider failure"));
    const model = f.document.body.querySelectorAll("select").find(node => node.getAttribute("aria-label") === "Assistant model");
    assert.ok(model.textContent.includes("Current model"));
    assert.equal(model.value, "openai\u0000fixture-model");
  });
}

test('displayed handoff sends a typed attachment separately from text and preserves the composer draft', async t => {
  const f = await fixture(t);
  f.edit('Keep my ordinary next question');
  const packet = Object.freeze({ dashboardId: 'A', rows: [['private-displayed-207']], snapshotAt: '2026-11-11T12:00:00Z' });
  await f.assistant.explainDisplayed(packet);
  const sent = f.requests.find(request => request.path.endsWith('/messages'));
  assert.deepEqual(sent.body.displayedResult, packet);
  assert.equal(sent.body.acknowledgedProviderId, 'openai');
  assert.ok(!sent.body.text.includes('private-displayed-207'));
  assert.equal(f.input.value, 'Keep my ordinary next question');
});

test('displayed handoff cannot attach to a replacement subject while first chat creation is pending', async t => {
  const create = deferred();
  const f = await fixture(t, { existing: id => id === 'B', create: () => create.promise });
  const pending = f.assistant.explainDisplayed({ dashboardId: 'A', rows: [['private-displayed-207']] });
  const rejected = assert.rejects(pending, /conversation or provider changed/);
  await until(() => f.requests.some(request => request.method === 'POST' && request.path.endsWith('/chats')));
  await f.switchTo('B'); create.resolve(conversation('A')); await rejected;
  assert.equal(f.requests.filter(request => request.path.endsWith('/messages')).length, 0);
});
