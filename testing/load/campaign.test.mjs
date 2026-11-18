import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { ordinaryRequest } from './fixtures.mjs';
import { LOCAL, PREVIEW } from './http.mjs';
import { reconcileK6, executePlan } from './engine.mjs';
import { plan } from './plan.mjs';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

// Execute the actual generator script/request path. Only its external k6
// primitives are injected; no application request or k6 process is started.
function campaign(origin, status = 200) {
  const counters = new Map(), calls = [];
  class Counter {
    constructor(name) { this.name = name; counters.set(name, 0); }
    add(value) { counters.set(this.name, counters.get(this.name) + value); }
  }
  class Trend { add() {} }
  const accounts = [0, 1].map(index => ({ username: `fixture-${index}`, userId: `fixture-${index}`,
    cookie: `private-fixture-cookie-${index}`, model: { id: `model_${String(index).repeat(32)}`, revision: 3 } }));
  const spec = { origin, rate: 100, seconds: 6, vus: 2, workload: 'compile',
    accounts: accounts.map(account => ({ cookie: account.cookie, request: ordinaryRequest('compile', account) })) };
  const source = readFileSync(new URL('./exact-rate.k6.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export default function ()', 'function iteration()').replace(/^export /gm, '');
  const context = vm.createContext({ Counter, Trend, __ENV: { SCHEMII_LOAD_INPUT: 'private', SCHEMII_LOAD_RESULT: 'result' },
    open: () => JSON.stringify(spec), exec: { scenario: { iterationInTest: 0 } },
    http: { request: (method, url, body, options) => {
      calls.push({ method, url, body, options });
      // The real AuthenticationMiddleware rejects compile POSTs without the
      // canonical Origin. A removed header therefore fails this workflow.
      const responseStatus = options.headers.Origin === new URL(url).origin ? status : 403;
      return { status: responseStatus, error_code: 0, timings: { duration: 1 }, json: () => ({ sql: 'SELECT 1;' }) };
    } } });
  return { counters, calls, accounts, spec, context, load: () => vm.runInContext(source, context),
    iteration: index => { context.exec.scenario.iterationInTest = index; vm.runInContext('iteration()', context); } };
}

test('actual k6 compile POST carries validated local and preview Origin with the selected owner cookie', () => {
  for (const origin of [LOCAL, PREVIEW]) {
    const run = campaign(origin); run.load(); run.iteration(0); run.iteration(1);
    assert.equal(run.calls.length, 2);
    for (const [index, call] of run.calls.entries()) {
      const account = run.accounts[index], request = ordinaryRequest('compile', account);
      assert.equal(call.method, 'POST');
      assert.equal(call.url, origin + `/api/v1/schemoo/models/${account.model.id}/plan`);
      assert.equal(call.options.headers.Origin, origin);
      assert.equal(call.options.headers.Cookie, account.cookie);
      assert.equal(call.options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(call.body), request.body);
      assert.equal(call.options.redirects, 0);
    }
    assert.equal(run.counters.get('schemii_sent'), 2);
    assert.equal(run.counters.get('schemii_completed'), 2);
    assert.equal(run.counters.get('schemii_failed'), 0);
    assert.equal(vm.runInContext('options.insecureSkipTLSVerify', run.context), origin === LOCAL);
    // A correct authenticated request cannot erase generator underdelivery.
    const metrics = Object.fromEntries([...run.counters].map(([name, count]) => [name, { count }]));
    metrics.http_reqs = { count: 2 }; metrics.iterations = { count: 2 }; metrics.dropped_iterations = { count: 8 };
    const summary = reconcileK6({ metrics }, { callsPerMinute: 100, seconds: 6 });
    assert.equal(summary.arrivals.dropped, 8);
    assert.equal(summary.http.sent, 2);
    assert.equal(summary.http.completed, 2);
    assert.equal(summary.scheduledArrivals, 10);
  }
});

test('actual k6 generator rejects unvalidated origins before disclosing cookies or offering work', () => {
  for (const origin of ['https://foreign.invalid', `${LOCAL}@foreign.invalid`, `${PREVIEW}/`, 'http://localhost:8001', null]) {
    const run = campaign(origin);
    assert.throws(run.load, /invalid_origin/);
    assert.equal(run.calls.length, 0);
    assert.equal(run.counters.size, 0);
  }
});

test('actual compile authentication failure still counts failed work and configures hard threshold abortion', () => {
  for (const status of [401, 403, 500]) {
    const run = campaign(LOCAL, status); run.load(); run.iteration(0);
    assert.equal(run.calls[0].options.headers.Origin, LOCAL);
    assert.equal(run.counters.get('schemii_sent'), 1);
    assert.equal(run.counters.get('schemii_admitted'), 1);
    assert.equal(run.counters.get('schemii_failed'), 1);
    assert.equal(run.counters.get('schemii_completed'), 0);
    assert.equal(run.counters.get('schemii_rejected'), 0);
    assert.equal(vm.runInContext('options.thresholds.schemii_failed[0].abortOnFail', run.context), true);
    assert.equal(vm.runInContext('options.thresholds.schemii_failed[0].threshold', run.context), 'count==0');
  }
});
test('engine records generator resource uncertainty and never substitutes passive drain for active recovery', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'schemii-load-engine-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const spec = plan({ workload: 'report-1' }), aborter = new AbortController(); let calls = 0;
  const result = await executePlan({ spec, receipt: { tag: 'load_0123456789abcdef' }, dir, allowUnobserved: true,
    signal: aborter.signal, abort: () => aborter.abort(), sleep: async () => {}, tickFrequencyReader: async () => 100,
    generatorSampler: async owner => ({ status: 'available', at: new Date().toISOString(), owner, rssBytes: 100, threads: 1, fds: 2,
      cpuUsec: ++calls * 100, monotonicMs: calls * 1000, interfaces: [] }),
    runStage: async () => ({ http: { incomplete: 0, failed: 0, rejected: 0 }, arrivals: { dropped: 0, failed: 0 },
      latency: { firstRowMs: { samples: 1, p95: 1 }, fullDrainMs: { samples: 1, p95: 1 }, schedulingLagMs: { samples: 1, p95: 0 } } }),
  });
  assert.equal(result.capacityEligible, false); assert.equal(result.recovery.verified, false);
  assert(result.failures.includes('missing_resource_observation'));
  assert.equal(result.stages[0].summary.generatorObservation.headroomVerified, false);
  assert.equal(result.stages[0].summary.generatorObservation.networkHeadroom, 'unavailable');
});
test('lost generator ownership stops offered stages and remains a failed run', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'schemii-load-engine-fail-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const aborter = new AbortController(); let aborted = false;
  const result = await executePlan({ spec: plan({ workload: 'cancel' }), receipt: { tag: 'load_0123456789abcdef' }, dir,
    allowUnobserved: true, signal: aborter.signal, abort: () => { aborted = true; aborter.abort(); }, sleep: async () => {}, tickFrequencyReader: async () => null,
    generatorSampler: async () => ({ status: 'unavailable', headroomVerified: false }),
    runStage: async options => {
      assert.equal(options.signal.aborted, true);
      return { http: {}, arrivals: { dropped: 0, failed: 0 }, latency: {} };
    },
  });
  assert(aborted); assert.equal(result.capacityEligible, false); assert(result.failures.includes('generator_observation_unavailable'));
});
