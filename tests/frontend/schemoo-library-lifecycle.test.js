import { sourceIdentity, sourceLabel, sameSource } from "../../src/schemii/schemoo/web/logical-relationships.js";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { importedDraft, defaultSourceSelection } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft, inheritAliasExposure } from "../../src/schemii/schemoo/web/model-state.js";
import { addSourceSelection } from "../../src/schemii/schemoo/web/source-reconciliation.js";
import { ensureAliasConnections } from "../../src/schemii/schemoo/web/alias-model.js";
import { assertProductContext, requireContextAccount, CONTEXT_LIMITS } from "../../src/schemii/common/web/assets/product-context.js";

const web = new URL("../../src/schemii/schemoo/web/", import.meta.url);
const column = name => ({ name, dataType: "integer", nullable: false });
const catalog = () => ({ tables: ["a", "b", "c", "d"].map(name => ({ name, columns: [column("id"), column("name")] })), relationships: [], positions: [] });

test("the visible add-table action preserves unrelated relationships and alias exposure", () => {
  const source = catalog(), draft = importedDraft(source, source.tables.slice(0, 3).map(table => ({ table: table.name, columns: ["id"] })));
  draft.nodes.push({ id: "alias_b", table: "b", label: "Other B" });
  source.relationships = [
    { id: "selected", sourceTable: "d", sourceColumn: "id", targetTable: "a", targetColumn: "id" },
    { id: "unrelated", sourceTable: "b", sourceColumn: "id", targetTable: "c", targetColumn: "id" },
  ];
  const app = readFileSync(new URL("prototype.js", web), "utf8");
  const action = app.slice(app.indexOf("function addTable(identity) {"), app.indexOf("function renderAliasTools()"));
  runInNewContext(`${action}\naddTable(sourceIdentity({table:'d'},catalog));`, {
    draft, catalog: source, addSourceSelection, ensureAliasConnections, sourceIdentity, sourceLabel, sameSource,
    selected: null, diagnostics: {},
    showError(message) { throw new Error(message); },
    refreshForms() {}, changed() {}, inspectSelection() {}, $: () => ({}),
  });
  assert.deepEqual(draft.edges.map(edge => edge.relationshipId), ["selected"]);
  assert.deepEqual(draft.exposedFields.filter(field => field.table === "d").map(field => field.column), ["id", "name"]);
  assert.equal(draft.exposedFields.some(field => field.table === "alias_b"), false);
});

test("creating an alias adds candidates only for that occurrence, not existing aliases", () => {
  const source = catalog(), draft = importedDraft(source);
  draft.nodes.push({ id: "old_a", table: "a", label: "Old A" }, { id: "old_b", table: "b", label: "Old B" });
  source.relationships = [
    { id: "related", sourceTable: "a", sourceColumn: "id", targetTable: "d", targetColumn: "id" },
    { id: "unrelated", sourceTable: "b", sourceColumn: "id", targetTable: "c", targetColumn: "id" },
  ];
  const app = readFileSync(new URL("prototype.js", web), "utf8");
  const action = app.slice(app.indexOf("function addAlias("), app.indexOf("function addTable("));
  runInNewContext(`${action}\naddAlias('a', 'New A');`, {
    draft, catalog: source, inheritAliasExposure, ensureAliasConnections, sourceIdentity,
    crypto: { randomUUID: () => "11223344556677889900" }, canvas: { placeNode() {} },
    selected: null, refreshForms() {}, changed() {}, inspectSelection() {}, $: () => ({}),
  });
  assert.deepEqual(draft.edges.map(edge => [edge.relationshipId, edge.source, edge.target]), [
    ["related", "alias_112233445566", "d"],
  ]);
  assert.equal(draft.exposedFields.some(field => field.table === "old_a" || field.table === "old_b"), false);
});

class Element {
  constructor(tag, options = {}, children = []) {
    this.tag = tag;
    Object.assign(this, options);
    this.children = [];
    this.value ||= "";
    this.open ||= false;
    this.listeners = new Map();
    this.append(...children);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child instanceof Element ? child.textContent : child).join(''); }
  showModal() { this.open = true; }
  close() {
    this.open = false;
    for (const { callback, once } of [...this.listeners.get('close') || []]) {
      if (once) this.listeners.set('close', this.listeners.get('close').filter(item => item.callback !== callback));
      callback();
    }
  }
  addEventListener(name, callback, { once = false } = {}) { this.listeners.set(name, [...this.listeners.get(name) || [], { callback, once }]); }
  all() { return this.children.filter(child => child instanceof Element).flatMap(child => [child, ...child.all()]); }
  querySelector(tag) { return this.all().find(child => child.tag === tag) || null; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function libraryFixture({ loadedNamespaces = ["public"] } = {}) {
  const dialog = new Element("dialog"), content = new Element("div"), post = deferred();
  const selects = new Map(), opened = [], posted = [];
  const small = { ...catalog(), tables: catalog().tables.slice(0, 1), fingerprint: "one", namespaces: loadedNamespaces };
  const source = readFileSync(new URL("model-library.js", web), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/^export \{.*;\n/gm, "").replace(/^export /gm, "");
  const runtime = runInNewContext(`${source}\n({ openModelLibrary })`, {
    importedDraft, defaultSourceSelection, splitDraft, structuredClone, sourceIdentity, sourceLabel,
    requestJson: async (url, options = {}) => {
      if (options.method === "POST") { posted.push(JSON.parse(JSON.stringify(options.body))); return post.promise; }
      if (url.endsWith("/models")) return { models: [] };
      if (url.includes("/connections?")) return { connections: [{ id: "pg_offline", name: "Owned", database: "offline", username: "offline" }] };
      if (url.includes("/namespaces")) return { namespaces: [{ name: "public", system: false }] };
      if (url.includes("/catalog?")) return small;
      throw new Error(`Unexpected request ${url}`);
    },
    element: (tag, options, children) => new Element(tag, options, children),
    modelSelect: (label, options, initial, callback) => { selects.set(label, callback); return new Element("select"); },
    disposeSelects() {}, confirmModelDeletion() {}, createIconButton: () => new Element("button"), namedAction() {},
    document: { getElementById: id => id === "model-library" ? dialog : content },
  });
  await runtime.openModelLibrary(async id => { opened.push(id); });
  await selects.get("Source connection")("pg_offline");
  selects.get("Source schema")("public");
  await new Promise(resolve => setImmediate(resolve));
  const form = content.children.find(item => item.tag === "form");
  form.children.find(item => item.tag === "label").children.find(item => item.tag === "input").value = "Owned model";
  return { ...runtime, dialog, content, form, post, opened, posted };
}

for (const outcome of ["success", "failure"]) {
  test(`late create ${outcome} cannot navigate or close a reopened model library`, async () => {
    const fixture = await libraryFixture();
    const pending = fixture.form.onsubmit({ preventDefault() {} });
    fixture.dialog.close();
    await fixture.openModelLibrary(async id => { fixture.opened.push(`new:${id}`); });
    const newerContent = [...fixture.content.children];
    if (outcome === "success") fixture.post.resolve({ id: "old_created_model" });
    else fixture.post.reject(new Error("Old create failed"));
    await pending;
    assert.deepEqual(fixture.opened, []);
    assert.equal(fixture.dialog.open, true);
    assert.deepEqual(fixture.content.children, newerContent);
  });
}

test("current create completion still opens its saved model", async () => {
  const fixture = await libraryFixture();
  const pending = fixture.form.onsubmit({ preventDefault() {} });
  fixture.post.resolve({ id: "current_model" });
  await pending;
  assert.deepEqual(fixture.opened, ["current_model"]);
  assert.equal(fixture.dialog.open, false);
});

test("creation submits the catalog fingerprint's loaded schema set even when only one schema is selected", async () => {
  const fixture = await libraryFixture({ loadedNamespaces: ["other", "public"] });
  const pending = fixture.form.onsubmit({ preventDefault() {} });
  assert.equal(fixture.posted[0].catalogFingerprint, "one");
  assert.deepEqual(fixture.posted[0].catalogNamespaces, ["other", "public"]);
  assert.equal(fixture.posted[0].definition.nodes.length, 1);
  fixture.post.resolve({ id: "selected_model" });
  await pending;
  assert.deepEqual(fixture.opened, ["selected_model"]);
});

const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(condition) { for (let n = 0; n < 30; n++) { if (condition()) return; await tick(); } assert.fail('Controlled operation did not arrive'); }
function contextualFixture() {
  const sourceOwner = 'source-owner', connectionId = `pg_${'a'.repeat(32)}`;
  const state = { now: Date.now(), account: { user: { id: sourceOwner }, capabilities: ['schemoo:access'] }, calls: [], posted: [], gates: [], saved: [], opened: [] };
  const dialog = new Element('dialog'), content = new Element('div'), window = new EventTarget();
  const small = { ...catalog(), tables: catalog().tables.slice(0, 1), fingerprint: 'reviewed-small', namespaces: ['public'] };
  const source = readFileSync(new URL('model-library.js', web), 'utf8').replace(/^import .*;\n/gm, '').replace(/^export \{.*;\n/gm, '').replace(/^export /gm, '');
  const runtime = runInNewContext(`${source}\n({openModelLibrary})`, {
    importedDraft, defaultSourceSelection, splitDraft, sourceIdentity, sourceLabel, structuredClone, CONTEXT_LIMITS, requireContextAccount,
    assertProductContext: (context, account, product) => assertProductContext(context, account, product, state.now),
    Date: class extends Date { static now() { return state.now; } }, window,
    requestJson: async (path, settings = {}) => {
      state.calls.push({ path, method: settings.method || 'GET' });
      if (settings.method === 'POST') {
        state.posted.push({ body: structuredClone(settings.body), ownerId: state.account.user.id });
        const gate = deferred(); state.gates.push(gate); return gate.promise;
      }
      if (path.endsWith('/auth/me')) return structuredClone(state.account);
      if (path.endsWith('/models')) return { models: structuredClone(state.saved) };
      if (path.includes('/models/')) {
        const saved = state.saved.find(model => path.endsWith(model.id));
        if (!saved) throw Object.assign(new Error('Exact saved model unavailable'), { status: 404 });
        return structuredClone(saved);
      }
      if (path.includes('/connections?')) return { connections: [{ id: connectionId, ownerId: sourceOwner, revision: 1, name: 'Owned source', database: 'offline', username: 'reader' }] };
      if (path.includes('/namespaces')) return { namespaces: [{ name: 'public', system: false }] };
      if (path.includes('/catalog?')) return structuredClone(small);
      throw new Error(`Unexpected controlled read ${path}`);
    },
    element: (tag, options, children) => new Element(tag, options, children),
    modelSelect: () => new Element('select', {}, [new Element('input')]),
    disposeSelects() {}, confirmModelDeletion() {}, createIconButton: () => new Element('button'), namedAction() {},
    document: { getElementById: id => id === 'model-library' ? dialog : content },
  });
  state.context = (ownerId = state.account.user.id) => ({ version: 1, kind: 'schema', destination: 'schemoo', ownerId, createdAt: state.now, expiresAt: state.now + CONTEXT_LIMITS.ttlMs,
    payload: { connectionId, connectionOwnerId: sourceOwner, connectionRevision: 1, namespace: 'public' } });
  state.open = context => runtime.openModelLibrary(async id => { state.opened.push(id); }, { sourceContext: context });
  state.form = () => content.children.find(child => child.tag === 'form');
  state.button = text => content.all().find(child => child.tag === 'button' && child.text === text);
  state.submit = () => state.form().onsubmit({ preventDefault() {} });
  state.succeed = (index, overrides = {}) => {
    const post = state.posted[index], saved = { ...post.body, id: `model_${String(index + 1).padStart(32, '0')}`, ownerId: post.ownerId, connectionOwnerId: sourceOwner,
      database: 'offline', revision: 1, layoutRevision: 1, exploreRevision: 1, ...overrides };
    state.saved.push(saved); state.gates[index].resolve(structuredClone(saved)); return saved;
  };
  Object.assign(state, { dialog, content, window }); return state;
}

for (const outcome of ['success', 'network-error', 'server-error', 'rejected']) {
  test(`same contextual create stays reserved across close/reopen and late ${outcome}`, async () => {
    const state = contextualFixture(), context = state.context(); await state.open(context);
    const first = state.submit(); await until(() => state.posted.length === 1);
    state.dialog.close(); await state.open(context);
    assert.equal(state.button('Create model').disabled, true);
    const replacement = [...state.content.children]; await state.submit();
    assert.equal(state.posted.length, 1);
    if (outcome === 'success') state.succeed(0);
    else state.gates[0].reject(Object.assign(new Error('Controlled late failure'), outcome === 'network-error' ? {} : { status: outcome === 'rejected' ? 422 : 503 }));
    await first;
    assert.deepEqual(state.content.children, replacement); assert.deepEqual(state.opened, []);
    assert.equal(state.dialog.open, true); await state.open(context); await state.submit();
    assert.equal(state.posted.length, 1); assert.equal(state.button('Create model').disabled, true);
    if (outcome === 'success') assert.ok(state.button('Review saved model public model'));
    if (outcome === 'rejected') assert.match(state.content.children.find(child => child.attrs?.role === 'status').text, /server rejected/);
    if (outcome.includes('error')) assert.match(state.content.children.find(child => child.attrs?.role === 'status').text, /outcome is uncertain/);
    if (outcome !== 'success') assert.match(state.content.children.find(child => child.attrs?.role === 'status').text, /Controlled late failure/);
  });
}

test('pagehide disposes the contextual view without releasing the pending attempt or late receipt', async () => {
  const state = contextualFixture(), context = state.context(); await state.open(context);
  const first = state.submit(); await until(() => state.posted.length === 1);
  state.window.dispatchEvent(new Event('pagehide')); assert.equal(state.dialog.open, false);
  await state.open(context); await state.submit(); assert.equal(state.posted.length, 1);
  const replacement = [...state.content.children]; state.succeed(0); await first;
  assert.deepEqual(state.content.children, replacement); assert.deepEqual(state.opened, []);
  await state.open(context); assert.ok(state.button('Review saved model public model'));
});

test('late success reconciles only its exact owned ID and revisions after an explicit review action', async () => {
  const state = contextualFixture(), context = state.context(); await state.open(context);
  const first = state.submit(); await until(() => state.posted.length === 1);
  state.dialog.close(); const saved = state.succeed(0); await first;
  state.saved.push({ ...saved, id: `model_${'b'.repeat(32)}` });
  await state.open(context); assert.deepEqual(state.opened, []);
  const review = () => state.button('Review saved model public model');
  saved.ownerId = 'another-account'; await review().onclick(); assert.deepEqual(state.opened, []);
  saved.ownerId = context.ownerId; saved.revision++; await review().onclick(); assert.deepEqual(state.opened, []);
  saved.revision--; await review().onclick(); assert.deepEqual(state.opened, [saved.id]);
  assert.equal(state.posted.length, 1);
  assert.ok(state.calls.filter(call => call.path.includes('/models/')).every(call => call.path.endsWith(saved.id)));
});

test('an unverified late create response stays uncertain and cannot supply an exact saved receipt', async () => {
  for (const overrides of [{ ownerId: 'another-owner' }, { id: 'untrusted-id' }, { revision: 0 }, { connectionId: `pg_${'f'.repeat(32)}` }]) {
    const state = contextualFixture(), context = state.context(); await state.open(context);
    const first = state.submit(); await until(() => state.posted.length === 1); state.dialog.close();
    state.succeed(0, overrides); await first; await state.open(context); await state.submit();
    assert.equal(state.button('Create model').disabled, true); assert.equal(state.button('Review saved model public model'), undefined);
    assert.equal(state.posted.length, 1); assert.deepEqual(state.opened, []);
  }
});

test('unknown outcome never adopts a similarly named model or an unrelated successful context', async () => {
  const state = contextualFixture(), context = state.context(); await state.open(context);
  const first = state.submit(); await until(() => state.posted.length === 1); state.dialog.close();
  state.gates[0].reject(new Error('Response lost')); await first;
  await state.open(context); assert.equal(state.button('Review saved model public model'), undefined);
  const unrelated = state.context(); await state.open(unrelated);
  const second = state.submit(); await until(() => state.posted.length === 2);
  state.dialog.close(); state.succeed(1); await second;
  await state.open(context); assert.equal(state.button('Create model').disabled, true);
  assert.equal(state.button('Review saved model public model'), undefined); assert.deepEqual(state.opened, []);
  assert.equal(state.posted.length, 2);
});

test('another account cannot inspect an old context receipt and can create from its own intentional context', async () => {
  const state = contextualFixture(), context = state.context(); await state.open(context);
  const first = state.submit(); await until(() => state.posted.length === 1); state.dialog.close();
  state.account.user.id = 'another-account'; await state.open(context);
  assert.equal(state.form(), undefined); assert.equal(state.posted.length, 1);
  const ownContext = state.context(); await state.open(ownContext);
  const second = state.submit(); await until(() => state.posted.length === 2); state.dialog.close();
  state.succeed(0); state.succeed(1); await Promise.all([first, second]);
  assert.deepEqual(state.opened, []); await state.open(ownContext);
  await state.button('Review saved model public model').onclick(); assert.deepEqual(state.opened, [state.saved[1].id]);
});

test('attempt budget retains expired pending and unknown writes until a definitive outcome is reconciled', async () => {
  const state = contextualFixture(), pending = [];
  for (let n = 0; n < CONTEXT_LIMITS.records; n++) {
    await state.open(state.context()); pending.push(state.submit()); await until(() => state.posted.length === n + 1); state.dialog.close();
  }
  state.now += CONTEXT_LIMITS.ttlMs + 1;
  await state.open(state.context()); await state.submit(); assert.equal(state.posted.length, CONTEXT_LIMITS.records);
  state.gates[0].reject(new Error('Unknown outcome')); await pending[0];
  await state.open(state.context()); await state.submit(); assert.equal(state.posted.length, CONTEXT_LIMITS.records);
  state.gates[1].reject(Object.assign(new Error('Definitive validation rejection'), { status: 422 })); await pending[1];
  await state.open(state.context()); const next = state.submit(); await until(() => state.posted.length === CONTEXT_LIMITS.records + 1);
  state.dialog.close();
  for (let n = 2; n < state.gates.length; n++) state.gates[n].reject(Object.assign(new Error('Definitive rejection'), { status: 422 }));
  await Promise.all([...pending.slice(2), next]);
  assert.deepEqual(state.opened, []);
});

test('expired unreviewed success keeps its exact receipt until owned reconciliation frees its reservation', async () => {
  const state = contextualFixture(), contexts = [];
  for (let n = 0; n < CONTEXT_LIMITS.records; n++) {
    const context = state.context(); contexts.push(context); await state.open(context);
    const pending = state.submit(); await until(() => state.posted.length === n + 1); state.dialog.close();
    state.succeed(n); await pending;
  }
  state.now += CONTEXT_LIMITS.ttlMs + 1;
  await state.open(state.context()); await state.submit(); assert.equal(state.posted.length, CONTEXT_LIMITS.records);
  await state.open(contexts[0]); await state.button('Review saved model public model').onclick();
  assert.deepEqual(state.opened, [state.saved[0].id]);
  await state.open(state.context()); const next = state.submit(); await until(() => state.posted.length === CONTEXT_LIMITS.records + 1);
  state.gates.at(-1).reject(Object.assign(new Error('Definitive rejection'), { status: 422 })); await next;
});
