import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Run the actual script's response/accounting logic without a k6 process or
// network. Only module syntax is stripped; metrics/http/execution are injected.
function script(status, errorCode = 0) {
  const metrics = new Map();
  class Counter { constructor(name) { this.name = name; metrics.set(name, 0); } add(value) { metrics.set(this.name, metrics.get(this.name) + value); } }
  class Trend { add() {} }
  const source = readFileSync(new URL('./exact-rate.k6.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export default function ()', 'function iteration()').replace(/^export /gm, '');
  const context = vm.createContext({ Counter, Trend, __ENV: { SCHEMII_LOAD_INPUT: 'private' },
    open: () => JSON.stringify({ rate: 100, seconds: 6, vus: 1, origin: 'https://localhost:8001', workload: 'cheap-read',
      accounts: [{ cookie: 'private', request: { method: 'GET', path: '/api/v1/auth/me', status: 200 } }] }),
    exec: { scenario: { iterationInTest: 0 } },
    http: { request: () => ({ status, error_code: errorCode, timings: { duration: 1 }, json: () => ({ user: {} }) }) } });
  vm.runInContext(source + '\niteration();globalThis.result=options;', context);
  return { metrics, options: context.result };
}
test('unexpected ordinary authentication/server failures count failed work and configure immediate threshold abortion', () => {
  for (const status of [401, 403, 500]) {
    const { metrics, options } = script(status);
    assert.equal(metrics.get('schemii_sent'), 1);
    assert.equal(metrics.get('schemii_admitted'), 1);
    assert.equal(metrics.get('schemii_failed'), 1);
    assert.equal(metrics.get('schemii_completed'), 0);
    assert.equal(options.thresholds.schemii_failed[0].abortOnFail, true);
  }
});
test('transport loss aborts while capacity rejection keeps its separate outcome classification', () => {
  const incomplete = script(0, 1000);
  assert.equal(incomplete.metrics.get('schemii_incomplete'), 1);
  assert.equal(incomplete.options.thresholds.schemii_incomplete[0].abortOnFail, true);
  const rejection = script(503);
  assert.equal(rejection.metrics.get('schemii_rejected'), 1);
  assert.equal(rejection.metrics.get('schemii_failed'), 0);
});
