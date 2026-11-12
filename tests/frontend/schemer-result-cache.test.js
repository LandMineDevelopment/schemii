import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import * as cacheModule from '../../src/schemii/schemer/web/result-cache.js';
import { ResultCache, DashboardResultGroup, CacheBudget, readResultStream } from '../../src/schemii/schemer/web/result-cache.js';
const startFrame = { type: 'start', plan: { outputLabels: ['Readable ID'] }, snapshotAt: '2026-09-20T00:00:00Z' };
const rows = values => ({ type: 'rows', columns: [{ name: 'output_1' }], rows: values.map(value => [value]) });
const complete = { type: 'complete', limitReached: false };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
test('all batches arrive eagerly without page navigation; cached pages never execute again', async () => {
  let starts = 0; const cache = new ResultCache({ pageSize: 2, start: async emit => { starts++; emit(startFrame); emit(rows([1, 2])); emit(rows([3, 4])); emit(complete); } });
  await Promise.all([cache.loadMore(), cache.loadMore()]);
  assert.equal(starts, 1); assert.equal(cache.rows.length, 4);
  assert.deepEqual((await cache.page(1)).rows, [[3], [4]]); assert.deepEqual((await cache.page(0)).rows, [[1], [2]]);
  assert.equal(cache.columns[0].name, 'Readable ID'); assert.equal(cache.loading, false);
});
test('subscribers see first batch before completion and unsubscribe cleanly', async () => {
  const gate = deferred(), seen = []; const cache = new ResultCache({ start: async emit => { emit(startFrame); emit(rows([1])); await gate.promise; emit(rows([2])); emit(complete); } });
  const unsubscribe = cache.subscribe(() => seen.push(cache.rows.length)); const work = cache.loadMore();
  assert.equal(cache.loading, true); assert.equal(cache.rows.length, 1); assert.ok(seen.includes(1)); unsubscribe();
  const count = seen.length; gate.resolve(); await work; assert.equal(seen.length, count); assert.equal(cache.rows.length, 2);
});
test('interrupted stream preserves cached rows without retrying SQL', async () => {
  let starts = 0; const cache = new ResultCache({ start: async emit => { starts++; emit(startFrame); emit(rows([1])); throw new Error('Connection lost'); } });
  await cache.loadMore(); assert.match(cache.error, /Connection lost/); assert.deepEqual(cache.rows, [[1]]); await cache.loadMore(); assert.equal(starts, 1);
});
test('shared browser budget caps across tiles and drills; disposal is idempotent', async () => {
  const budget = new CacheBudget({ rows: 3 });
  const create = () => new ResultCache({ budget, start: async emit => { emit(startFrame); emit(rows([1, 2])); emit(complete); } });
  const a = create(), b = create(); await a.loadMore(); await b.loadMore();
  assert.equal(a.rows.length, 2); assert.equal(b.rows.length, 1); assert.equal(b.limitReached, true); assert.equal(b.loading, false);
  a.dispose(); a.dispose(); assert.equal(budget.rows, 1); b.dispose(); assert.equal(budget.bytes, 0); assert.equal(budget.rows, 0);
});
test('byte budget accounts for decoded values and rejects oversized rows', async () => {
  const budget = new CacheBudget({ bytes: 120 }); const cache = new ResultCache({ budget, start: async emit => { emit(startFrame); emit(rows(['x'.repeat(300)])); emit(complete); } });
  await cache.loadMore(); assert.equal(cache.rows.length, 0); assert.equal(cache.reason, 'browser_budget'); assert.equal(cache.error, '');
});
test('dashboard fans batches to all tiles while sharing only one request', async () => {
  let starts = 0; const group = new DashboardResultGroup({ start: async emit => { starts++; emit({ type: 'start', tiles: ['A', 'B', 'C'].map(tileId => ({ tileId, plan: {} })) }); for (const tileId of ['C', 'A', 'B']) { emit({ ...rows([tileId]), tileId }); emit({ ...complete, tileId }); } } });
  const caches = ['A', 'B', 'C'].map(id => new ResultCache({ start: emit => group.tile(id, emit) })); await Promise.all(caches.map(cache => cache.loadMore()));
  assert.equal(starts, 1); assert.deepEqual(caches.map(cache => cache.rows), [[['A']], [['B']], [['C']]]);
});
test('closing aborts active stream and ignores late arrivals', async () => {
  const gate = deferred(); let signal; const cache = new ResultCache({ start: async (emit, receivedSignal) => { signal = receivedSignal; emit(startFrame); emit(rows([1])); await gate.promise; emit(rows([2])); } });
  const work = cache.loadMore(); await cache.close(); gate.resolve(); await work; assert.equal(signal.aborted, true); assert.deepEqual(cache.rows, [[1]]); assert.match(cache.error, /Query stopped/);
});
function response(chunks) { return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close(); } }), { status: 200 }); }
test('NDJSON parser handles split UTF-8 and lines and requires end marker', async () => {
  const encoded = new TextEncoder().encode(JSON.stringify(rows(['hé😀'])) + '\n' + JSON.stringify({ type: 'end' })); const frames = [];
  await readResultStream('/test', {}, frame => frames.push(frame), undefined, async () => response([...encoded].map(byte => Uint8Array.of(byte))));
  assert.deepEqual(frames[0].rows, [['hé😀']]); assert.equal(frames[1].type, 'end');
  await assert.rejects(readResultStream('/test', {}, () => {}, undefined, async () => response([new TextEncoder().encode(JSON.stringify(rows([1])) + '\n')])), /ended unexpectedly/);
});
test('NDJSON errors expose standard API envelope and malformed frames fail', async () => {
  await assert.rejects(readResultStream('/test', {}, () => {}, undefined, async () => new Response(JSON.stringify({ error: { message: 'Revision changed' } }), { status: 409 })), /Revision changed/);
  await assert.rejects(readResultStream('/test', {}, () => {}, undefined, async () => response([new TextEncoder().encode('broken\n')])), /JSON|Unexpected/);
});


test('chart mark budget bounds multi-series DOM without discarding cached groups', async () => {
  const { chartPreview } = await import('../../src/schemii/schemer/web/chart-preview.js');
  const result = { rows: Array.from({ length: 10000 }, (_, i) => [i]) };
  const preview = chartPreview({ kind: 'bar', measures: Array(63).fill({}) }, result);
  assert.equal(preview.rows.length, 31); assert.equal(preview.visualTruncated, true); assert.equal(preview.cachedRows, 10000);
  assert.equal(result.rows.length, 10000);
  assert.equal(chartPreview({ kind: 'detail' }, result), result);
  assert.equal(chartPreview({ kind: 'donut', measures: [{}] }, result).rows.length, 2000);
});

// Execute the actual viewer handlers and studio cache owner. The small DOM and
// deferred transport doubles make lifetime decisions observable without a browser
// or database; backend release and visual acceptance remain separate checks.
class ViewerElement {
  constructor(tag, options = {}, children = []) {
    Object.assign(this, options, { tag, children: [], attributes: {}, removed: false });
    this.classList = { toggle: () => {}, contains: () => false };
    for (const [key, value] of Object.entries(options.attrs || {})) this.setAttribute(key, value);
    this.append(...children);
  }
  append(...children) { for (const child of children) { this.children.push(child); if (child instanceof ViewerElement) child.parent = this; } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  querySelector() { return null; }
  addEventListener() {}
  get isConnected() { return !this.removed && (this.tag === 'body' || Boolean(this.parent?.isConnected)); }
  showModal() { this.open = true; }
  close() { this.open = false; this.onclose?.(); }
  escape() { this.oncancel?.({ preventDefault() {} }); this.close(); }
  remove() { this.removed = true; }
  click() { this.onclick?.(); }
  submit() { this.submitted = true; }
}
const viewerSource = readFileSync(new URL('../../src/schemii/schemer/web/result-viewer.js', import.meta.url), 'utf8');
const studioSource = readFileSync(new URL('../../src/schemii/schemer/web/studio.js', import.meta.url), 'utf8');
function viewerFixture({ budget = new CacheBudget(), result = 'pending' } = {}) {
  const forms = [];
  const document = { body: new ViewerElement('body'), createElement: tag => {
    const node = new ViewerElement(tag); if (tag === 'form') forms.push(node); return node;
  } };
  const frames = [], calls = [], streams = new Map(), gates = new Map(), scheduled = [];
  const mainGate = deferred(), tile = { id: 'tile', title: 'Owned tile', kind: 'aggregate', limit: 2 };
  let mainSignal;
  const dashboardGroup = new DashboardResultGroup({ start: async (emit, signal) => {
    mainSignal = signal; emit({ type: 'start', tiles: [{ tileId: tile.id, plan: {} }], snapshotAt: startFrame.snapshotAt });
    emit({ ...rows(['main']), tileId: tile.id }); await mainGate.promise; emit({ ...complete, tileId: tile.id });
  } });
  const dashboard = { id: 'dashboard', revision: 1 };
  const context = { ...cacheModule, streams, dashboard, dashboardGroup, browserBudget: budget, API: '/owned',
    tileStates: new Map(), scheduledCards: new Set(), permissions: { export: true }, selectionBody: () => ({ selections: {} }),
    document, $: () => ({ querySelectorAll: () => [] }), requestAnimationFrame: callback => scheduled.push(callback),
    message: () => {}, renderTile: () => {}, readResultStream: async (_url, body, emit, signal) => {
      const key = body.selection.dimensions[0].value, gate = deferred(); gates.set(key, gate); calls.push({ key, body, signal, emit });
      emit({ ...startFrame, tiles: [{ tileId: tile.id, plan: startFrame.plan }] });
      if (result === 'pending') { emit(rows([key])); await gate.promise; emit(rows([`late-${key}`])); emit(complete); }
      else if (result === 'error') emit({ type: 'error', message: 'Owned error' });
      else { if (result === 'rows') emit(rows([key])); emit(complete); }
    } };
  const getStreamSource = studioSource.slice(studioSource.indexOf('function getStream('), studioSource.indexOf('\nfunction checkLeave('));
  const getStream = runInNewContext(`${getStreamSource}\ngetStream`, context);
  const find = (root, predicate) => [root, ...root.children.filter(child => child instanceof ViewerElement).flatMap(child => find(child, predicate))].filter(predicate);
  const openExpanded = runInNewContext(`${viewerSource.replace(/^import .*;\n/gm, '').replaceAll('export function ', 'function ')}\nopenExpanded`, {
    document, element: (tag, options, children) => new ViewerElement(tag, options, children),
    createIconButton: ({ label }) => new ViewerElement('button', { attrs: { 'aria-label': label } }),
    scrollPosition: () => ({}), restoreScroll: () => {}, createResultGrid: data => new ViewerElement('grid', { data }),
    appendStreamStatus: (host, data) => frames.push({ host: host.className, rows: data.rows.map(row => [...row]) }),
    renderVisualization: (host, _tile, _result, { onDrill }) => { host.onDrill = onDrill; },
    requestAnimationFrame: callback => scheduled.push(callback), csvContent: () => '', downloadContent: () => {},
  });
  const main = getStream(tile), selection = value => ({ dimensions: [{ column: 'group', value }] });
  const open = () => {
    openExpanded({ tile, cache: main, createDrillCache: selected => getStream(tile, selected), onRefresh: () => {} });
    return document.body.children.at(-1);
  };
  let dialog = open();
  const button = label => find(dialog, node => node.attributes['aria-label'] === label)[0];
  return { main, dashboardGroup, dashboard, streams, calls, frames, gates, budget, forms,
    select: value => find(dialog, node => node.className === 'expanded-chart-body')[0].onDrill(selection(value)),
    button, escape: () => dialog.escape(), reopen: () => { dialog = open(); },
    flush: () => { while (scheduled.length) scheduled.shift()(); },
    drill: value => getStream(tile, selection(value)), mainSignal: () => mainSignal,
    async finish() { dialog.close(); for (const gate of gates.values()) gate.resolve(); mainGate.resolve(); await Promise.all([main.pending, ...[...streams.values()].flatMap(group => [...group.drills.values()].map(cache => cache.pending))]); },
  };
}

test('switching actual viewer drills aborts A, rejects its late frames and keeps main eager work', async () => {
  const fixture = viewerFixture();
  try {
    await Promise.resolve(); fixture.select('A'); fixture.select('B');
    assert.equal(fixture.calls[0].signal.aborted, true);
    assert.equal(fixture.calls[1].signal.aborted, false);
    fixture.gates.get('A').resolve(); await fixture.drill('A').pending; fixture.flush();
    assert.deepEqual(fixture.drill('A').rows, [['A']]);
    assert.deepEqual(fixture.frames.filter(frame => frame.host === 'drill-body').at(-1).rows, [['B']]);
    assert.equal(fixture.mainSignal().aborted, false);
    assert.equal(fixture.main.loading, true);
  } finally { await fixture.finish(); }
});

for (const close of ['Close detail rows', 'Close expanded tile', 'Escape']) {
  test(`actual viewer ${close} retires unfinished drill without stopping the main dashboard`, async () => {
    const fixture = viewerFixture();
    try {
      await Promise.resolve(); fixture.select('A');
      if (close === 'Escape') fixture.escape(); else fixture.button(close).click();
      assert.equal(fixture.calls[0].signal.aborted, true);
      assert.equal(fixture.drill('A').listeners.size, 0);
      assert.equal(fixture.mainSignal().aborted, false);
      fixture.gates.get('A').resolve(); await fixture.drill('A').pending; fixture.flush();
      assert.deepEqual(fixture.drill('A').rows, [['A']]);
    } finally { await fixture.finish(); }
  });
}

test('completed retained drill reopens without work and preserves its original preview and fresh export revision', async () => {
  const fixture = viewerFixture({ result: 'rows' });
  try {
    fixture.select('A'); const cache = fixture.drill('A'); await cache.pending;
    fixture.select('A'); assert.equal(cache.controller.signal.aborted, false);
    fixture.button('Close detail rows').click(); fixture.select('A');
    fixture.button('Close expanded tile').click(); fixture.reopen(); fixture.select('A');
    assert.equal(fixture.drill('A'), cache); assert.equal(fixture.calls.length, 1);
    assert.equal(cache.snapshotAt, startFrame.snapshotAt); assert.deepEqual(cache.rows, [['A']]);
    fixture.dashboard.revision = 2; cache.download();
    // The preview's original rows/plan stay intact; export's form uses current revision.
    assert.equal(JSON.parse(fixture.forms.at(-1).children[0].value).expectedRevision, 2);
    assert.equal(fixture.forms.at(-1).submitted, true);
    assert.equal(cache.controller.signal.aborted, false);
    assert.deepEqual(cache.rows, [['A']]);
  } finally { await fixture.finish(); }
});

for (const result of ['empty', 'error', 'rows']) {
  test(`studio bounds ${result} drill entries with deterministic recency eviction and budget release`, async () => {
    const fixture = viewerFixture({ result });
    try {
      const first = fixture.drill('0'); await first.loadMore();
      const caches = [first];
      for (let index = 1; index < 16; index++) { const cache = fixture.drill(String(index)); caches.push(cache); await cache.loadMore(); }
      assert.equal(fixture.drill('0'), first); // Touch oldest; selection 1 is now least recent.
      const second = caches[1];
      await fixture.drill('16').loadMore();
      assert.equal(second.disposed, true); assert.notEqual(first.disposed, true);
      for (let index = 17; index < 64; index++) {
        await fixture.drill(String(index)).loadMore();
        assert.equal(fixture.streams.get('tile').drills.size, 16);
      }
      const registry = fixture.streams.get('tile').drills;
      assert.equal(registry.size, 16);
      assert.equal(second.disposed, true); assert.equal(first.disposed, true);
      assert.equal(registry.has(JSON.stringify({ dimensions: [{ column: 'group', value: '63' }] })), true);
      assert.equal(fixture.budget.rows, result === 'rows' ? 17 : 1); // Main plus retained drills only.
    } finally { await fixture.finish(); }
  });
}

for (const limit of [{ rows: 0 }, { bytes: 0 }, { rows: 1 }, { bytes: 112 }]) {
  test(`exhausted shared ${Object.keys(limit)[0]}=${Object.values(limit)[0]} budget refuses a new query before transport start`, async () => {
    const fixture = viewerFixture({ budget: new CacheBudget(limit), result: 'empty' });
    try {
      await Promise.resolve(); fixture.select('A'); await fixture.drill('A').loadMore();
      assert.equal(fixture.calls.length, 0);
      assert.equal(fixture.drill('A').reason, 'browser_budget');
      assert.equal(fixture.drill('A').limitReached, true);
      assert.equal(fixture.drill('A').loading, false);
    } finally { await fixture.finish(); }
  });
}
