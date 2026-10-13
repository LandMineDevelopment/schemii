import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import * as cacheModule from '../../src/schemii/schemer/web/result-cache.js';

const reportAt = '2026-10-13T12:00:00Z', drillAt = '2026-10-13T12:01:00Z', refreshedAt = '2026-10-13T12:02:00Z';
const displayTime = value => new Date(value).toLocaleString(undefined, { timeZoneName: 'short' });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const rows = values => ({ type: 'rows', columns: [{ name: 'name' }], rows: values.map(value => [value]) });
const complete = { type: 'complete', limitReached: false };
const viewerSource = readFileSync(new URL('../../src/schemii/schemer/web/result-viewer.js', import.meta.url), 'utf8');
const studioSource = readFileSync(new URL('../../src/schemii/schemer/web/studio.js', import.meta.url), 'utf8');

// Actual viewer, cache owner and expand callback with a minimal DOM and mutable
// source transport. Mounted composition, browser geometry and real DB release
// remain separate acceptance; these controls expose request/cache decisions.
class Node {
  constructor(tag, options = {}, children = []) {
    Object.assign(this, options, { tag, children: [], attributes: {}, removed: false });
    this.classList = { toggle() {}, contains() { return false; } };
    for (const [key, value] of Object.entries(options.attrs || {})) this.setAttribute(key, value);
    if (options.text !== undefined) this.textContent = options.text;
    this.append(...children);
  }
  append(...children) { for (const child of children) { this.children.push(child); if (child instanceof Node) child.parent = this; } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  querySelector() { return null; }
  addEventListener() {}
  get isConnected() { return !this.removed && (this.tag === 'body' || Boolean(this.parent?.isConnected)); }
  showModal() { this.open = true; }
  close() { this.open = false; this.onclose?.(); }
  remove() { this.removed = true; }
  click() { this.onclick?.(); }
  submit() { this.submitted = true; }
}
function find(root, predicate) { return [root, ...root.children.filter(child => child instanceof Node).flatMap(child => find(child, predicate))].filter(predicate); }
function fixture({ budget = new cacheModule.CacheBudget(), pending = false, waitForStart = false, timestamp = drillAt, canExport = true } = {}) {
  const forms = [], requests = [], scheduled = [], gates = [], metadataGates = [], streams = new Map();
  let source = ['original'], dialog;
  const document = { body: new Node('body'), createElement: tag => {
    const node = new Node(tag); if (tag === 'form') forms.push(node); return node;
  } };
  const tile = { id: 'tile', title: 'Report', kind: 'aggregate', limit: 20 };
  const dashboard = { id: 'dashboard', revision: 1, modelId: 'model', selections: {} }, mainGate = deferred();
  const dashboardGroup = new cacheModule.DashboardResultGroup({ start: async emit => {
    emit({ type: 'start', snapshotAt: reportAt, tiles: [{ tileId: tile.id, plan: {} }] });
    emit({ ...rows([source.length]), tileId: tile.id }); await mainGate.promise; emit({ ...complete, tileId: tile.id });
  } });
  const openExpanded = runInNewContext(`${viewerSource.replace(/^import .*;\n/gm, '').replaceAll('export function ', 'function ')}\nopenExpanded`, {
    document, element: (tag, options, children) => new Node(tag, options, children),
    createIconButton: ({ label }) => new Node('button', { attrs: { 'aria-label': label } }),
    scrollPosition: () => ({}), restoreScroll() {}, createResultGrid: data => new Node('grid', { data }),
    appendStreamStatus() {}, renderVisualization: (host, _tile, _result, { onDrill }) => { host.onDrill = onDrill; },
    requestAnimationFrame: callback => scheduled.push(callback), csvContent: () => '', downloadContent() {},
  });
  const context = { ...cacheModule, document, streams, dashboard, dashboardGroup, browserBudget: budget, API: '/owned',
    tileStates: new Map(), scheduledCards: new Set(), permissions: { export: canExport, drill: true, edit: false },
    selectionBody: (origin = dashboard) => ({ selections: origin.selections }), $: () => ({ querySelectorAll: () => [] }),
    requestAnimationFrame: callback => scheduled.push(callback), message() {}, renderTile() {}, readyToRun: () => true,
    openExpanded, runTile() {}, readResultStream: async (_url, body, emit, signal) => {
      const captured = [...source], gate = deferred(), metadata = deferred(); gates.push(gate); metadataGates.push(metadata);
      const snapshotAt = requests.length === 0 ? timestamp : refreshedAt;
      requests.push({ body, signal, snapshotAt });
      if (waitForStart) await metadata.promise;
      emit({ type: 'start', snapshotAt, tiles: [{ tileId: tile.id, plan: {} }] }); emit(rows(captured));
      if (pending) { await gate.promise; emit(rows(['late-old-data'])); }
      emit(complete);
    } };
  const owner = studioSource.slice(studioSource.indexOf('function getStream('), studioSource.indexOf('\nfunction checkLeave('));
  const expand = studioSource.slice(studioSource.indexOf('function expand('), studioSource.indexOf('\nasync function showTileSql('));
  const handlers = runInNewContext(`${owner}\n${expand}\n({ getStream, expand })`, context);
  const selection = { dimensions: [{ column: 'group', value: 'HQ' }] }, main = handlers.getStream(tile);
  const open = () => { handlers.expand(tile); dialog = document.body.children.at(-1); };
  const nodes = className => find(dialog, node => node.className?.split(/\s+/).includes(className));
  open();
  return { main, dashboard, dashboardGroup, requests, streams, budget, forms, gates, metadataGates,
    setSource: values => { source = values; }, reopen: open,
    select: () => nodes('expanded-chart-body')[0].onDrill(selection),
    drill: () => handlers.getStream(tile, selection),
    button: label => find(dialog, node => node.attributes['aria-label'] === label)[0],
    detailButton: text => find(nodes('drill-pane')[0], node => node.textContent === text)[0],
    text: className => nodes(className)[0].textContent,
    detailRows: () => find(nodes('drill-body')[0], node => node.tag === 'grid')[0].data.rows,
    flush() { while (scheduled.length) scheduled.shift()(); },
    async finish() {
      dialog.close(); mainGate.resolve(); for (const gate of [...gates, ...metadataGates]) gate.resolve();
      await Promise.all([main.pending, ...[...streams.values()].flatMap(group => [...group.drills.values()].map(cache => cache.pending))]);
    },
  };
}

test('freshness: a changed source is a current drill investigation with distinct truthful request times', async () => {
  const f = fixture();
  try {
    await Promise.resolve(); f.setSource(['original', 'arrived-after-report']); f.select(); await f.drill().pending; f.flush();
    assert.deepEqual(f.main.rows, [[1]]); assert.deepEqual(f.detailRows(), [['original'], ['arrived-after-report']]);
    assert.match(f.text('drill-freshness'), /current source data when requested.*may differ from the report/);
    assert.ok(f.text('drill-request-times').includes(`Report request started: ${displayTime(reportAt)}`));
    assert.ok(f.text('drill-request-times').includes(`Drill request started: ${displayTime(drillAt)}`));
    assert.equal(f.requests.length, 1); assert.equal(f.main.loading, true); assert.equal(f.dashboardGroup.controller.signal.aborted, false);
  } finally { await f.finish(); }
});

test('freshness: completed reopen keeps earlier rows/time and explicit detail Refresh queries current data only', async () => {
  const f = fixture();
  try {
    await Promise.resolve(); f.select(); const earlier = f.drill(); await earlier.pending; f.flush();
    f.button('Close detail rows').click(); f.setSource(['new-source']); f.select(); f.flush();
    assert.equal(f.drill(), earlier); assert.equal(f.requests.length, 1); assert.deepEqual(f.detailRows(), [['original']]);
    assert.match(f.text('drill-freshness'), /Earlier cached drill result.*Refresh detail rows/);
    assert.ok(f.text('drill-request-times').includes(displayTime(drillAt)));
    f.button('Close expanded tile').click(); f.reopen(); f.select(); f.flush();
    assert.equal(f.requests.length, 1); assert.match(f.text('drill-freshness'), /Earlier cached drill result/);
    f.button('Refresh detail rows').click(); const fresh = f.drill(); await fresh.pending; f.flush();
    assert.notEqual(fresh, earlier); assert.equal(earlier.disposed, true); assert.equal(f.requests.length, 2);
    assert.deepEqual(f.detailRows(), [['new-source']]); assert.ok(f.text('drill-request-times').includes(displayTime(refreshedAt)));
    assert.match(f.text('drill-freshness'), /current source data when requested/);
    assert.equal(f.budget.rows, 2); assert.deepEqual(f.main.rows, [[1]]); assert.equal(f.main.loading, true);
    assert.equal(f.dashboardGroup.controller.signal.aborted, false);
  } finally { await f.finish(); }
});

test('freshness: refreshing an unfinished drill aborts and disposes its old rows and ignores late frames', async () => {
  const f = fixture({ pending: true });
  try {
    await Promise.resolve(); f.select(); const earlier = f.drill(); f.setSource(['replacement']);
    f.button('Refresh detail rows').click(); const fresh = f.drill(); f.flush();
    assert.equal(f.requests[0].signal.aborted, true); assert.equal(f.requests[1].signal.aborted, false);
    assert.equal(earlier.listeners.size, 0); assert.equal(earlier.disposed, true);
    f.gates[0].resolve(); await earlier.pending; f.flush();
    assert.deepEqual(f.detailRows(), [['replacement']]); assert.deepEqual(earlier.rows, []);
    assert.ok(f.text('drill-request-times').includes(displayTime(refreshedAt)));
    assert.equal(f.budget.rows, 2); assert.equal(f.dashboardGroup.controller.signal.aborted, false);
    assert.equal(f.main.loading, true); assert.equal(fresh.loading, true);
  } finally { await f.finish(); }
});

test('freshness: exhausted budget never invents a drill timestamp or starts transport even on Refresh', async () => {
  const f = fixture({ budget: new cacheModule.CacheBudget({ rows: 1 }) });
  try {
    await Promise.resolve(); f.select(); await f.drill().loadMore(); f.flush();
    assert.equal(f.requests.length, 0); assert.equal(f.drill().snapshotAt, undefined);
    assert.match(f.text('drill-request-times'), /No drill request started: browser cache budget reached/);
    f.button('Refresh detail rows').click(); await f.drill().loadMore(); f.flush();
    assert.equal(f.requests.length, 0); assert.equal(f.drill().snapshotAt, undefined);
    assert.match(f.text('drill-request-times'), /No drill request started/);
  } finally { await f.finish(); }
});

test('freshness: pending or invalid server times are disclosed without substituting the browser clock', async () => {
  const f = fixture({ waitForStart: true, timestamp: 'invalid' });
  try {
    await Promise.resolve(); f.select(); f.flush();
    assert.match(f.text('drill-request-times'), /Awaiting drill request start/);
    f.metadataGates[0].resolve(); await f.drill().pending; f.flush();
    assert.match(f.text('drill-request-times'), /Drill request started: unavailable/);
    assert.doesNotMatch(f.text('drill-request-times'), /Invalid Date/);
  } finally { await f.finish(); }
});

test('freshness: full export discloses a fresh snapshot and uses current revision/selections without altering the cached drill', async () => {
  const f = fixture();
  try {
    await Promise.resolve(); f.select(); const earlier = f.drill(); await earlier.pending; f.flush();
    assert.equal(f.text('export-snapshot-note'), 'Full download uses a fresh snapshot.');
    const full = f.detailButton('Download full results');
    assert.match(full.attributes.title, /fresh snapshot.*may differ/);
    f.dashboard.revision = 2; f.dashboard.selections = { region: { values: { region: 'current' } } };
    f.setSource(['export-only-source']); full.click();
    const form = f.forms.at(-1), body = JSON.parse(form.children[0].value);
    assert.equal(form.submitted, true); assert.equal(body.expectedRevision, 2); assert.deepEqual(body.selections, f.dashboard.selections);
    assert.equal(body.selection.dimensions[0].value, 'HQ'); assert.equal(f.requests.length, 1);
    assert.equal(earlier.snapshotAt, drillAt); assert.deepEqual(earlier.rows, [['original']]);
  } finally { await f.finish(); }
});
