import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { dashboardUpdate } from '../../src/schemii/schemer/web/dashboard-state.js';

const source = readFileSync(new URL('../../src/schemii/schemer/web/studio.js', import.meta.url), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function fixture() {
  const nodes = [], ids = new Map(), writes = [], notices = [], renders = [];
  function element(tag, options = {}, children = []) {
    const node = {
      tag, ...options, textContent: options.text || '', children: [...children], open: false,
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; },
      setAttribute() {}, classList: { add() {}, remove() {} },
      querySelector() { return nodes.find(item => item.textContent === 'Cancel'); },
      showModal() { this.open = true; },
      close() { this.open = false; this.onclose?.(); },
      remove() { this.removed = true; },
    };
    nodes.push(node); return node;
  }
  const document = {
    getElementById(id) { if (!ids.has(id)) ids.set(id, element('div')); return ids.get(id); },
    querySelector() { return element('div'); },
    body: element('body'),
  };
  Object.defineProperty(document.getElementById('notice'), 'textContent', { set: value => notices.push(value) });
  const original = { id: 'dashboard-A', revision: 7, name: 'A', modelId: 'model', modelRevision: 2,
    optionalFilters: ['filter-a', 'filter-b'], selections: { 'filter-a': { active: true }, 'filter-b': { active: true }, required: { active: true } }, tiles: [] };
  const model = { id: 'model', revision: 2, definition: { scopes: [
    { id: 'filter-a', requirement: 'optional' }, { id: 'filter-b', requirement: 'optional' }, { id: 'required', kind: 'required', requirement: 'required' },
  ] } };
  const context = vm.createContext({
    document, window: { addEventListener() {} }, location: { search: '' }, structuredClone, URLSearchParams,
    element, dashboardUpdate, currentAccount: () => new Promise(() => {}),
    createProductAssistant: () => ({}), CacheBudget: class {}, initializeUi() {}, installProductNavigation() {},
    createIconButton: ({ label }) => element('button', { text: label }),
    requestJson: async (url, options) => {
      writes.push({ url, ...structuredClone(options) });
      return { ...original, ...options.body, revision: 8 };
    },
    original, sourceModel: model, renders, notices,
  });
  // Execute the complete studio module with imports supplied above. Rendering
  // and startup are replaced at their boundaries; the installed dialog handlers,
  // saving lock, dashboard persistence and revision payload run unchanged.
  vm.runInContext(source.replace(/^import .*;\n/gm, ''), context);
  vm.runInContext(`
    dashboard = original; model = sourceModel; library = [original];
    renderLibrary = () => {}; renderHeading = () => {};
    renderSlicers = () => renders.push('slicers'); renderTiles = () => renders.push('tiles');
    runAll = async () => renders.push('run');
  `, context);
  const dialog = () => nodes.find(item => item.tag === 'dialog');
  const button = label => nodes.find(item => item.textContent === label);
  const open = () => document.getElementById('manage-dashboard-filters').onclick();
  const save = () => button('Save available filters').onclick();
  const switchDashboard = (id = 'dashboard-B', revision = 1) => vm.runInContext(`dashboard = { ...dashboard, id: ${JSON.stringify(id)}, revision: ${revision} };`, context);
  const stop = deferred();
  context.stop = stop;
  vm.runInContext('stopRuns = () => stop.promise;', context);
  return { context, nodes, writes, notices, renders, ids, original, model, dialog, button, open, save, switchDashboard, stop };
}

test('available-filter save owns the saving lock and cannot close while streams stop', async () => {
  const f = fixture(); f.open();
  const pending = f.save();
  assert.equal(f.ids.get('create-dashboard').disabled, true);
  f.button('Close dashboard filters').onclick({ stopPropagation() {} });
  f.button('Cancel').onclick();
  let prevented = false;
  f.dialog().oncancel({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(f.dialog().open, true);
  await f.save();
  assert.equal(f.writes.length, 0);
  f.stop.resolve(); await pending;
  assert.equal(f.writes.length, 1);
  assert.equal(f.writes[0].url, '/api/v1/schemer/dashboards/dashboard-A');
  assert.equal(f.writes[0].body.expectedRevision, 7);
  assert.equal(f.ids.get('create-dashboard').disabled, false);
  assert.equal(f.dialog().open, false);
});

for (const replacement of ['other dashboard', 'new revision']) {
  test(`available-filter save abandons a stale ${replacement} after stream shutdown`, async () => {
    const f = fixture(); f.open();
    const pending = f.save();
    f.switchDashboard(replacement === 'other dashboard' ? 'dashboard-B' : 'dashboard-A', 9);
    f.stop.resolve(); await pending;
    assert.equal(f.writes.length, 0);
    assert.deepEqual(f.renders, []);
    assert.equal(f.ids.get('create-dashboard').disabled, false);
  });
}

test('a closed filter dialog cannot resume a delayed save', async () => {
  const f = fixture(); f.open();
  const pending = f.save();
  f.dialog().close();
  f.stop.resolve(); await pending;
  assert.equal(f.writes.length, 0);
});

test('filter save snapshots choices and preserves required selections', async () => {
  const f = fixture(); f.open();
  const checkbox = f.nodes.find(item => item.attrs?.['aria-label'] === 'Offer filter-b');
  checkbox.checked = false; checkbox.onchange();
  const pending = f.save();
  f.stop.resolve(); await pending;
  assert.deepEqual(f.writes[0].body.optionalFilters, ['filter-a']);
  assert.deepEqual(f.writes[0].body.selections, { 'filter-a': { active: true }, required: { active: true } });
  assert.deepEqual(f.renders, ['slicers', 'tiles', 'run']);
  assert.equal(f.notices.at(-1), 'Available dashboard filters saved.');
});

test('failed filter persistence restores controls and can retry without losing choices', async () => {
  const f = fixture(); f.open();
  let attempts = 0;
  f.context.requestJson = async (url, options) => {
    f.writes.push({ url, ...structuredClone(options) });
    if (++attempts === 1) throw new Error('Revision conflict. Reopen this dashboard.');
    return { ...f.original, ...options.body, revision: 8 };
  };
  const pending = f.save(); f.stop.resolve(); await pending;
  assert.equal(f.dialog().open, true);
  assert.equal(f.button('Save available filters').disabled, false);
  assert.equal(f.ids.get('create-dashboard').disabled, false);
  assert.equal(f.nodes.find(item => item.attrs?.role === 'alert').textContent, 'Revision conflict. Reopen this dashboard.');
  assert.deepEqual(f.renders, []);
  await f.save();
  assert.equal(f.writes.length, 2);
  assert.deepEqual(f.writes[1].body, f.writes[0].body);
  assert.equal(f.dialog().open, false);
});

test('filter dialog rejects a dashboard replaced before Save is pressed', async () => {
  const f = fixture(); f.open(); f.switchDashboard();
  await f.save();
  assert.equal(f.writes.length, 0);
  assert.match(f.nodes.find(item => item.attrs?.role === 'alert').textContent, /Close this dialog and reopen/);
});

test('a late persistence response cannot redraw a replacement dashboard', async () => {
  const f = fixture(), response = deferred(); f.open();
  f.context.requestJson = async (url, options) => { f.writes.push({ url, ...structuredClone(options) }); return response.promise; };
  const pending = f.save(); f.stop.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.writes.length, 1);
  f.switchDashboard();
  response.resolve({ ...f.original, revision: 8 }); await pending;
  assert.equal(vm.runInContext('dashboard.id', f.context), 'dashboard-B');
  assert.deepEqual(f.renders, []);
  assert.deepEqual(f.notices, []);
});

test('filter save releases its saving lock before result streams finish', async () => {
  const f = fixture(), refresh = deferred(); f.open();
  f.context.refresh = refresh;
  vm.runInContext('runAll = () => refresh.promise;', f.context);
  const pending = f.save(); f.stop.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.dialog().open, false);
  assert.equal(f.ids.get('create-dashboard').disabled, false);
  refresh.resolve(); await pending;
});

test('failed stream shutdown preserves dashboard filters and releases dialog controls', async () => {
  const f = fixture(); f.open();
  const pending = f.save(); f.stop.reject(new Error('Could not close the query.')); await pending;
  assert.equal(f.writes.length, 0);
  assert.equal(f.dialog().open, true);
  assert.equal(f.button('Save available filters').disabled, false);
  assert.equal(f.ids.get('create-dashboard').disabled, false);
  assert.equal(f.nodes.find(item => item.attrs?.role === 'alert').textContent, 'Could not close the query.');
});
