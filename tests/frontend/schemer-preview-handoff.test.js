import assert from 'node:assert/strict';
import test from 'node:test';
import { previewDashboard, previewMeasure, loadPreviewContext, openPreviewHandoff } from '../../src/schemii/schemer/web/preview-handoff.js';

const modelId = `model_${'b'.repeat(32)}`, ownerId = 'person-a';
const account = { user: { id: ownerId }, capabilities: ['schemoo:access', 'schemer:access', 'schemer:author'] };
const model = { id: modelId, revision: 4, name: 'Qualified sales', namespace: 'sales', definition: { root: 'sales_alias',
  nodes: [{ id: 'sales_alias', namespace: 'sales', table: 'orders', label: 'Sales orders' }, { id: 'other_alias', namespace: 'archive', table: 'orders', label: 'Archived orders' },
    { id: 'summary_alias', label: 'Totals', derivation: { kind: 'aggregate', source: 'sales_alias', outputs: [{ id: 'total', operation: 'sum' }], groupBy: ['region'] } }],
  scopes: [{ id: 'always', kind: 'required', requirement: 'required' }, { id: 'source_only', kind: 'conditional', requirement: 'required' },
    { id: 'optional_active', kind: 'conditional', requirement: 'optional' }, { id: 'optional_inactive', kind: 'required', requirement: 'optional' }] } };
const choice = (value, active = false) => ({ alternativeId: 'pick', active, values: { region: value } });
const payload = { modelId, modelRevision: 4, explore: { root: 'sales_alias', fields: [
  { table: 'other_alias', column: 'region', aggregate: 'none' }, { table: 'summary_alias', column: 'total', aggregate: 'none' }],
  selections: { always: choice('fixed'), source_only: choice('conditional'), optional_active: choice('selected', true), optional_inactive: choice('kept', false) },
  reportFilters: [{ id: 'restrict', label: 'Archived region', mode: 'exists', conditions: [{ table: 'other_alias', column: 'region', operator: 'eq', value: 'East' }] }], limit: 50 } };
const clone = value => structuredClone(value);
const context = () => ({ version: 1, kind: 'preview', destination: 'schemer', ownerId, createdAt: Date.now(), expiresAt: Date.now() + 300000, payload: clone(payload) });
const options = { name: 'Sales dashboard', title: 'Archived totals', kind: 'aggregate', tileId: 'new-tile-id' };

test('preview mapping preserves alias fields, summary measures with none, filter populations and limit', () => {
  const result = previewDashboard(model, payload, options);
  assert.deepEqual(result.selections, { always: choice('fixed'), optional_active: choice('selected', true), optional_inactive: choice('kept', false) });
  assert.deepEqual(result.optionalFilters, ['optional_active', 'optional_inactive']);
  assert.deepEqual(result.tiles[0].selections, { source_only: choice('conditional') });
  assert.deepEqual(result.tiles[0].dimensions, [payload.explore.fields[0]]);
  assert.deepEqual(result.tiles[0].measures, [payload.explore.fields[1]]);
  assert.deepEqual(result.tiles[0].reportFilters, payload.explore.reportFilters);
  assert.equal(result.tiles[0].limit, 50);
  assert.equal(previewMeasure({ table: 'summary_alias', column: 'region', aggregate: 'none' }, model), false);
  result.tiles[0].reportFilters[0].conditions[0].value = 'changed';
  assert.equal(payload.explore.reportFilters[0].conditions[0].value, 'East');
});

test('summary raw outputs allow an explicit detail choice while grouped preview measures stay aggregate', () => {
  const detail = previewDashboard(model, payload, { ...options, kind: 'detail' });
  assert.deepEqual(detail.tiles[0].detailFields, payload.explore.fields);
  assert.deepEqual(detail.tiles[0].measures, []);
  const aggregate = clone(payload); aggregate.explore.fields[1] = { table: 'sales_alias', column: 'amount', aggregate: 'sum' };
  assert.throws(() => previewDashboard(model, aggregate, { ...options, kind: 'detail' }), /unaggregated/);
  assert.equal(previewDashboard(model, aggregate, options).tiles[0].measures[0].aggregate, 'sum');
});

test('root, revision, missing alias, removed filter and invalid names give repair guidance', () => {
  assert.throws(() => previewDashboard({ ...model, revision: 5 }, payload, options), /saved model changed/);
  assert.throws(() => previewDashboard({ ...model, definition: { ...model.definition, root: 'other_alias' } }, payload, options), /starting object/);
  assert.throws(() => previewDashboard({ ...model, definition: { ...model.definition, nodes: [] } }, payload, options), /alias/);
  assert.throws(() => previewDashboard({ ...model, definition: { ...model.definition, scopes: [] } }, payload, options), /filter was removed/);
  assert.throws(() => previewDashboard(model, payload, { ...options, name: ' ' }), /dashboard name/);
});

function requests({ failPlan = false, changed = false } = {}) {
  const state = { account: clone(account), model: clone(model), calls: [], dashboards: [], created: [], failPlan,
    uncertain: false, changed, hold: null };
  state.request = async (path, settings = {}) => {
    state.calls.push({ path, ...settings, ...(settings.body ? { body: clone(settings.body) } : {}) });
    if (path === '/api/v1/auth/me') return clone(state.account);
    if (path.startsWith('/api/v1/schemoo/models/')) return { ...clone(state.model), revision: state.changed ? 5 : state.model.revision };
    if (path === '/api/v1/schemer/plan') {
      if (state.hold) await state.hold.promise;
      if (state.failPlan) throw Object.assign(new Error('Preview rejected by current destination'), { status: 422 });
      return { sql: 'SELECT compiler_owned_sql', warnings: [] };
    }
    if (settings.method === 'POST') {
      const saved = { id: 'dashboard_created', ownerId, revision: 1, ...clone(settings.body) };
      state.created.push(saved); state.dashboards.push(saved);
      if (state.uncertain) throw Object.assign(new Error('Response lost after save'), { status: 503 });
      return saved;
    }
    return { dashboards: clone(state.dashboards) };
  };
  return state;
}

test('destination preflight authorizes and plans saved context without creating, querying or transferring SQL', async () => {
  const state = requests(); const input = context();
  await loadPreviewContext(input, state.request);
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
  assert.deepEqual(state.calls.find(call => call.path.endsWith('/plan')).body, { modelId, expectedRevision: 4, explore: payload.explore });
  assert.ok(state.calls.every(call => !/query|execution/.test(call.path)));
  assert.ok(!JSON.stringify(input).includes('SELECT'));
  assert.equal(state.created.length, 0);
});

for (const failure of ['stale', 'denied', 'wrong-account', 'plan', 'expired']) {
  test(`destination ${failure} context produces no create`, async () => {
    const state = requests({ changed: failure === 'stale', failPlan: failure === 'plan' }), input = context();
    if (failure === 'denied') state.account.capabilities = ['schemoo:access'];
    if (failure === 'wrong-account') state.account.user.id = 'other-person';
    if (failure === 'expired') { input.createdAt -= 300001; input.expiresAt -= 300001; }
    await assert.rejects(loadPreviewContext(input, state.request));
    assert.equal(state.created.length, 0);
    assert.ok(state.calls.every(call => call.path !== '/api/v1/schemer/dashboards'));
  });
}

class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.ownText = ''; this.stored = null; this.open = false; }
  append(...nodes) { for (const node of nodes) { node.remove?.(); node.parent = this; this.children.push(node); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(node => node.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  get value() { return this.stored ?? this.attributes.value ?? this.children.find(node => node.tagName === 'option' && !node.disabled)?.value ?? ''; }
  set value(value) { this.stored = value; }
  all(tag) { return this.children.flatMap(node => [node, ...node.all('*')]).filter(node => tag === '*' || node.tagName === tag); }
  showModal() { this.open = true; }
  close() { this.open = false; this.onclose?.(); }
}
function dom(t) {
  const originals = Object.fromEntries(['document', 'Node'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const document = { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node('#text'), { textContent: text }), body: new Node('body') };
  Object.assign(globalThis, { document, Node });
  t.after(() => { for (const [key, value] of Object.entries(originals)) { if (value) Object.defineProperty(globalThis, key, value); else delete globalThis[key]; } });
  return document;
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
async function until(predicate) { for (let i = 0; i < 30; i++) { if (predicate()) return; await settle(); } assert.fail('Expected review operation did not arrive'); }
const submit = dialog => dialog.all('form')[0].onsubmit({ preventDefault() {} });
const button = (dialog, label) => dialog.all('button').find(node => node.textContent === label);

test('mounted review creates only on explicit confirm after current access and revision recheck', async t => {
  dom(t); const state = requests(), opened = [];
  const dialog = await openPreviewHandoff(context(), { request: state.request, onCreated: saved => opened.push(saved.id) });
  assert.equal(state.created.length, 0); assert.ok(dialog.textContent.includes('Qualified sales'));
  assert.equal(dialog.all('select')[0].value, 'aggregate');
  await submit(dialog);
  assert.equal(state.created.length, 1); assert.deepEqual(opened, ['dashboard_created']);
  const createIndex = state.calls.findIndex(call => call.method === 'POST' && call.path.endsWith('/dashboards'));
  assert.equal(state.calls[createIndex - 1].path, '/api/v1/auth/me');
  assert.equal(state.calls.filter(call => call.path.endsWith('/plan')).length, 2);
});

test('mounted review retains input validation recovery but never posts after stale model recheck', async t => {
  dom(t); const state = requests();
  const dialog = await openPreviewHandoff(context(), { request: state.request, onCreated() { assert.fail('No open'); } });
  const input = dialog.all('input').find(node => node.getAttribute('aria-label') === 'Dashboard name');
  input.value = ' '; await submit(dialog);
  assert.equal(button(dialog, 'Create dashboard').disabled, false);
  input.value = 'Fixed name'; state.changed = true;
  await submit(dialog);
  assert.equal(state.created.length, 0); assert.equal(button(dialog, 'Create dashboard').disabled, true);
});

test('late preflight after closing its dialog cannot revive a review or create a resource', async t => {
  const document = dom(t), state = requests(); state.hold = deferred();
  const pending = openPreviewHandoff(context(), { request: state.request, onCreated() { assert.fail('No open'); } });
  await until(() => state.calls.some(call => call.path.endsWith('/plan')));
  document.body.children[0].close(); state.hold.resolve(); await pending;
  assert.equal(document.body.children.length, 0); assert.equal(state.created.length, 0);
});

test('uncertain create reconciles owned matching saved state and cannot repeat its POST', async t => {
  dom(t); const state = requests(); state.uncertain = true; const opened = [];
  const dialog = await openPreviewHandoff(context(), { request: state.request, onCreated: saved => opened.push(saved.id) });
  await submit(dialog); await submit(dialog);
  assert.equal(state.created.length, 1); assert.equal(button(dialog, 'Create dashboard').disabled, true);
  await button(dialog, 'Check saved dashboards').onclick();
  assert.deepEqual(opened, []);
  await button(dialog, 'Open saved dashboard Qualified sales dashboard').onclick();
  assert.deepEqual(opened, ['dashboard_created']);
  assert.equal(state.calls.filter(call => call.path.endsWith('/dashboards') && call.method === 'POST').length, 1);
});
