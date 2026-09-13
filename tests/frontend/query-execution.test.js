import assert from 'node:assert/strict';
import test from 'node:test';
import { readExecution } from '../../src/schemii/common/web/assets/query-execution.js';

const receipt = { execution: { id: 'e', status: 'succeeded', results: [{ id: 'r' }] }, plan: { sql: 'select 1' } };
function response(value) { return { ok: true, status: 200, json: async () => value }; }

test('bounded preview closes its retained result immediately', async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push([url, options.method]);
    return response(url.endsWith('/activity') ? { phase: 'fetching' } : { columns: ['n'], rows: [[1]], nextCursor: 'more' });
  };
  try {
    const result = await readExecution(receipt, { maximumRows: 1 });
    assert.deepEqual(result.rows, [[1]]);
    assert.ok(result.elapsedMs >= 0);
    assert.ok(calls.some(([url, method]) => url.endsWith('/results/r') && method === 'DELETE'));
    assert.equal(calls.filter(([url]) => url.includes('?cursor')).length, 0);
  } finally { globalThis.fetch = original; }
});

test('Stop cancels independently while the lazy first page waits', async () => {
  const original = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    calls.push([url, options.method]);
    if (url.endsWith('/results/r') && options.method === 'GET') {
      setTimeout(() => controller.abort(), 10);
      return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
    }
    return response({ phase: 'fetching' });
  };
  try {
    await assert.rejects(readExecution(receipt, { signal: controller.signal }), /Query cancelled/);
    assert.ok(calls.some(([url, method]) => url.endsWith('/e') && method === 'DELETE'));
    assert.ok(calls.some(([url, method]) => url.endsWith('/results/r') && method === 'DELETE'));
  } finally { globalThis.fetch = original; }
});

test('page failure cancels and releases the retained result', async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push([url, options.method]);
    if (url.endsWith('/results/r') && options.method === 'GET') throw new Error('offline');
    return response({});
  };
  try {
    await assert.rejects(readExecution(receipt), /could not be reached/);
    assert.ok(calls.some(([url, method]) => url.endsWith('/results/r') && method === 'DELETE'));
  } finally { globalThis.fetch = original; }
});
