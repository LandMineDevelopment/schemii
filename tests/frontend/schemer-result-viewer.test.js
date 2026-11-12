import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import * as cacheModule from '../../src/schemii/schemer/web/result-cache.js';
import { renderVisualization, displayedVisualization } from '../../src/schemii/schemer/web/visualizations.js';

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
function fixture({ budget = new cacheModule.CacheBudget(), pending = false, waitForStart = false, timestamp = drillAt, canExport = true, kind = 'aggregate', selected } = {}) {
  const csvs = [], forms = [], requests = [], scheduled = [], gates = [], metadataGates = [], streams = new Map();
  let source = ['original'], dialog;
  const document = { body: new Node('body'), createElement: tag => {
    const node = new Node(tag); if (tag === 'form') forms.push(node); return node;
  } };
  const tile = { id: 'tile', title: 'Report', kind, limit: 20 };
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
    requestAnimationFrame: callback => scheduled.push(callback), csvContent: data => { csvs.push(data); return ''; }, downloadContent() {},
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
  const selection = selected || { dimensions: [{ column: 'group', value: 'HQ' }] }, main = handlers.getStream(tile);
  const open = () => { handlers.expand(tile); dialog = document.body.children.at(-1); };
  const nodes = className => find(dialog, node => node.className?.split(/\s+/).includes(className));
  open();
  return { main, dashboard, dashboardGroup, requests, streams, budget, forms, csvs, gates, metadataGates,
    setSource: values => { source = values; }, reopen: open,
    select: () => nodes('expanded-chart-body')[0].onDrill(selection),
    drill: () => handlers.getStream(tile, selection),
    button: label => find(dialog, node => node.attributes['aria-label'] === label)[0],
    paneTitle: text => find(dialog, node => node.className === 'pane-title' && node.textContent === text)[0],
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

test('returning to the report pane stops hidden unfinished drill work and retains its partial preview', async () => {
  const f = fixture({ pending: true });
  try {
    await Promise.resolve(); f.select(); const drill = f.drill();
    f.paneTitle('Report').click(); f.flush();
    assert.equal(f.requests[0].signal.aborted, true); assert.equal(drill.loading, false);
    assert.match(drill.error, /Query stopped/); assert.deepEqual(drill.rows, [['original']]);
    assert.equal(f.main.loading, true); assert.equal(f.dashboardGroup.controller.signal.aborted, false);
    f.gates[0].resolve(); await drill.pending; f.flush();
    f.paneTitle('Model detail rows').click(); f.flush();
    assert.equal(f.requests.length, 1); assert.deepEqual(f.detailRows(), [['original']]);
    f.setSource(['explicit-refresh']); f.button('Refresh detail rows').click(); f.flush();
    assert.equal(f.requests.length, 2); assert.deepEqual(f.detailRows(), [['explicit-refresh']]);
  } finally { await f.finish(); }
});

test('returning to the report pane preserves a completed drill for zero-query reopen', async () => {
  const f = fixture();
  try {
    await Promise.resolve(); f.select(); const drill = f.drill(); await drill.pending; f.flush();
    f.paneTitle('Report').click(); f.paneTitle('Model detail rows').click(); f.flush();
    assert.equal(f.requests[0].signal.aborted, false); assert.equal(f.requests.length, 1);
    assert.deepEqual(f.detailRows(), [['original']]); assert.equal(drill.error, '');
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

test('cached pivot contributor CSV preserves exact grouping selection without mutating cached rows', async () => {
  const selected = { dimensions: [{ table: 'person', column: 'region', value: null }], measureIndex: 1,
    pivot: { kind: 'row', rowDepth: 1, columnDepth: 0 } };
  const f = fixture({ kind: 'pivot', selected });
  try {
    await Promise.resolve(); f.select(); const drill = f.drill(); await drill.pending; f.flush();
    f.button('Export cached rows as CSV').click();
    assert.deepEqual(Array.from(f.csvs[0].columns.slice(-4), column => column.name), ['Pivot scope', 'Row grouping depth', 'Column grouping depth', 'Pivot selection']);
    assert.deepEqual(Array.from(f.csvs[0].rows[0].slice(-4)), ['row', 1, 0, JSON.stringify(selected)]);
    assert.deepEqual(drill.rows, [['original']]);
    assert.ok(f.button('Export cached pivot groups with scope as CSV'));
  } finally { await f.finish(); }
});

test('oversized cached pivot selection metadata offers streaming full CSV without allocating its repeated rows', async () => {
  const selected = { dimensions: [{ table: 'person', column: 'name', value: 'X'.repeat(1024 * 1024) }], measureIndex: 0,
    pivot: { kind: 'cell', rowDepth: 1, columnDepth: 1 } };
  const f = fixture({ kind: 'pivot', selected });
  try {
    await Promise.resolve(); f.setSource(Array(10).fill('contributor')); f.select(); await f.drill().pending; f.flush();
    const cached = f.button('Export cached rows as CSV');
    assert.equal(cached.disabled, true); assert.match(cached.title, /scope metadata.*budget.*Download full results/);
    assert.equal(f.csvs.length, 0); assert.ok(f.detailButton('Download full results'));
  } finally { await f.finish(); }
});

test('actual expanded Explain actions freeze calculated main evidence and the selected detail pane without another stream', async t => {
  class PaintedNode extends Node {
    constructor(...args) { super(...args); this.dataset ||= {}; this.style = { setProperty() {} }; this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } }; }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    get textContent() { return (this.ownText || '') + this.children.map(child => child.textContent || '').join(''); }
    getAttribute(name) { return this.attributes[name]; }
    querySelectorAll() { return []; }
  }
  const oldDocument = globalThis.document, oldNode = globalThis.Node, scheduled = [], attachments = [];
  const document = { body: new PaintedNode('body'), createElement: tag => new PaintedNode(tag), createElementNS: (_ns, tag) => new PaintedNode(tag), createTextNode: text => new PaintedNode('#text', { text }) };
  globalThis.document = document; globalThis.Node = PaintedNode;
  t.after(() => { globalThis.document = oldDocument; globalThis.Node = oldNode; });
  const tile = { id: 'calculated', title: 'Share', kind: 'bar', dimensions: [{ table: 'sales', column: 'region' }], measures: [], calculations: [{ id: 'share', visible: true }] };
  const context = { dashboardId: 'dashboard', dashboardRevision: 1, modelId: 'model', modelRevision: 1, tileId: tile.id, pane: 'main', selections: {} };
  let mainStarts = 0, detailStarts = 0;
  const main = new cacheModule.ResultCache({ start: async emit => { mainStarts++; emit({ type: 'start', snapshotAt: reportAt, plan: { calculatedMeasures: [{ id: 'share', index: 1, label: 'Share' }] } }); emit({ type: 'rows', columns: [{ name: 'Region' }, { name: 'Share' }], rows: [['East', .25]] }); emit(complete); } });
  main.displayContext = context;
  const detail = new cacheModule.ResultCache({ start: async emit => { detailStarts++; emit({ type: 'start', snapshotAt: drillAt, plan: {} }); emit({ type: 'rows', columns: [{ name: 'Detail amount' }], rows: [[73.125]] }); emit(complete); } });
  const selection = { dimensions: [{ table: 'sales', column: 'region', value: 'East' }], measureIndex: 0 };
  detail.displayContext = { ...context, pane: 'drill', drillSelection: selection };
  const expanded = runInNewContext(`${viewerSource.replace(/^import .*;\n/gm, '').replaceAll('export function ', 'function ')}\nopenExpanded`, {
    document, element: (tag, options, children) => new PaintedNode(tag, options, children), createIconButton: ({ label }) => new PaintedNode('button', { attrs: { 'aria-label': label } }),
    scrollPosition: () => ({}), restoreScroll() {}, createResultGrid: data => new PaintedNode('grid', { data }), appendStreamStatus() {},
    renderVisualization: (host, current, data, options) => { host.onDrill = options.onDrill; renderVisualization(host, current, data, options); }, displayedVisualization,
    requestAnimationFrame: callback => scheduled.push(callback), csvContent() {}, downloadContent() {},
  });
  await main.loadMore();
  const open = () => { expanded({ tile, modelId: 'model', cache: main, createDrillCache: () => detail, onRefresh() {}, onExplain: (cache, data) => attachments.push(cacheModule.freezeDisplayedResult(data, cache.displayContext)) }); return document.body.children.at(-1); };
  let dialog = open(); find(dialog, node => node.attributes['aria-label'] === 'Explain displayed result')[0].click();
  assert.deepEqual(attachments[0].rows, [['East', .25]]); assert.equal(attachments[0].pane, 'main'); assert.equal(mainStarts, 1);
  main.rows[0][1] = 99; assert.equal(attachments[0].rows[0][1], .25); assert.equal(Object.isFrozen(attachments[0].rows[0]), true);
  dialog = open(); find(dialog, node => node.className === 'expanded-chart-body')[0].onDrill(selection);
  await detail.pending; while (scheduled.length) scheduled.shift()();
  find(dialog, node => node.attributes['aria-label'] === 'Explain displayed detail rows')[0].click();
  assert.deepEqual(attachments[1].rows, [[73.125]]); assert.equal(attachments[1].pane, 'drill'); assert.equal(attachments[1].snapshotAt, drillAt);
  assert.deepEqual(attachments[1].drillSelection, selection); assert.equal(mainStarts, 1); assert.equal(detailStarts, 1);
  assert.equal('plan' in attachments[0], false); assert.equal('controller' in attachments[1], false);
  main.dispose(); detail.dispose();
});
