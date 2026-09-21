import test from 'node:test';
import assert from 'node:assert/strict';
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
