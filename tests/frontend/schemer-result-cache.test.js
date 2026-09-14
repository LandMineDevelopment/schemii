import test from 'node:test';
import assert from 'node:assert/strict';
import { ResultCache } from '../../src/schemii/schemer/web/result-cache.js';
function fixture() {
  const calls = []; let starts = 0;
  const cache = new ResultCache({ pageSize: 2, start: async () => { starts++; return { plan: { sql: 'SELECT all_rows', warnings: [] }, executionUrl: '/execution', execution: { id: 'e', status: 'succeeded', results: [{ id: 'r' }] } }; }, request: async (url, options = {}) => {
    calls.push([url, options.method || 'GET']);
    if (options.method === 'DELETE') return null;
    return { columns: [{ name: 'id' }], rows: url.includes('cursor=next') ? [[3], [4]] : [[1], [2]], nextCursor: url.includes('cursor=next') ? null : 'next' };
  } }); return { cache, calls, starts: () => starts };
}
test('forward cursor fetches each batch once; previous table pages reuse browser cache', async () => {
  const { cache, calls, starts } = fixture();
  assert.deepEqual((await cache.page(0)).rows, [[1], [2]]);
  assert.deepEqual((await cache.page(1)).rows, [[3], [4]]);
  assert.deepEqual((await cache.page(0)).rows, [[1], [2]]);
  assert.equal(starts(), 1); assert.equal(calls.filter(c => c[1] === 'GET').length, 2);
  assert.equal(cache.hasMore, false); assert.ok(calls.some(c => c[1] === 'DELETE'));
});
test('concurrent chart scroll requests share one pending fetch', async () => {
  const { cache, calls } = fixture(); await Promise.all([cache.loadMore(), cache.loadMore(), cache.loadMore()]);
  assert.equal(calls.filter(c => c[1] === 'GET').length, 1); assert.equal(cache.rows.length, 2);
});
test('dashboard output labels remain readable when SQL uses compact aliases', async () => {
  const cache = new ResultCache({ start: async () => ({ plan: { outputLabels: ['Personnel pay-band level'] }, executionUrl: '/execution', execution: { status: 'succeeded', results: [{ id: 'r' }] } }),
    request: async () => ({ columns: [{ name: 'output_1' }], rows: [[4]], nextCursor: null }) });
  const result = await cache.loadMore();
  assert.equal(result.columns[0].name, 'Personnel pay-band level');
});
test('closing during admission still cancels the returned receipt', async () => {
  let resolve; const cancelled = [];
  const cache = new ResultCache({ start: () => new Promise(r => { resolve = r; }), request: async (url, options) => cancelled.push([url, options.method]) });
  const work = cache.loadMore(); await cache.close();
  resolve({ plan: {}, executionUrl: '/admitted', execution: { status: 'reserved', results: [] } });
  await assert.rejects(work, /cancelled/); assert.ok(cancelled.some(([url, method]) => url === '/admitted' && method === 'DELETE'));
});
test('consumed cursor failures preserve cached rows and never silently replay SQL', async () => {
  const { cache, starts } = fixture(); await cache.page(0);
  const request = cache.request; cache.request = (url, options = {}) => options.method === 'DELETE' ? request(url, options) : Promise.reject(new Error('Cursor expired'));
  await assert.rejects(cache.page(1), /Cursor expired/);
  assert.deepEqual((await cache.page(0)).rows, [[1], [2]]); await cache.loadMore(); assert.equal(starts(), 1);
});

test('dashboard results share one admission and closing a tile leaves sibling cursors intact', async () => {
  const { DashboardResultGroup } = await import('../../src/schemii/schemer/web/result-cache.js');
  const calls = []; let starts = 0;
  const request = async (url, options = {}) => { calls.push([url, options.method || 'GET']); return options.method === 'DELETE' ? null : { columns: [{ name: 'id' }], rows: [[1]], nextCursor: null }; };
  const group = new DashboardResultGroup({ request, start: async () => { starts++; return { executionUrl: '/shared', execution: { status: 'succeeded', results: [{ id: 'a', statementIndex: 0 }, { id: 'b', statementIndex: 1 }] }, tiles: [{ tileId: 'A', statementIndex: 0, plan: { sql: 'SELECT a' } }, { tileId: 'B', statementIndex: 1, plan: { sql: 'SELECT b' } }], tileErrors: [] }; } });
  const a = new ResultCache({ request, start: () => group.tile('A') }), b = new ResultCache({ request, start: () => group.tile('B') });
  await Promise.all([a.loadMore(), b.loadMore()]); await a.close();
  assert.equal(starts, 1); assert.ok(calls.some(([url]) => url.startsWith('/shared/results/a'))); assert.ok(calls.some(([url]) => url.startsWith('/shared/results/b')));
  assert.ok(!calls.some(([url, method]) => url === '/shared' && method === 'DELETE'));
  await group.close(); assert.ok(calls.some(([url, method]) => url === '/shared' && method === 'DELETE'));
});
