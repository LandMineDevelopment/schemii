import test from 'node:test';
import assert from 'node:assert/strict';
import { runJourney } from './adapters.mjs';
import { plan, validatePlan, DEFAULT_MIX, mixedWorkload } from './plan.mjs';
import { Accounting } from './accounting.mjs';
import { oracleFor } from './protocol.mjs';
import { runOwnedStage } from './engine.mjs';
import { loadMain, loadPath } from './cli.mjs';
import { readSnapshot } from './journal.mjs';

const tag = 'load_0123456789abcdef', executionId = `cex_${'a'.repeat(32)}`, resultId = `res_${'b'.repeat(32)}`;
function sqlFixture({ wide = false, wrong = false, malformed = false, empty = false, short = false } = {}) {
  const rows = Array.from({ length: wide ? 129 : 513 }, (_, index) => [index + 1, 'owned-nonce', ...(wide ? ['W', 'X', 'Y', 'Z'].map(value => value.repeat(8192)) : [])]);
  const columns = ['id', 'owner_nonce', ...(wide ? ['c1', 'c2', 'c3', 'c4'] : [])];
  const account = { username: 'owned-user', userId: 'owned-id', fixtureTag: tag, source: { schema: tag + '_u0001', nonce: 'owned-nonce' },
    workspace: { id: `ws_${'c'.repeat(32)}`, revision: 1 }, settings: { revision: 1, rowPageSize: 100 }, oracles: { [wide ? 'wide' : 'sql']: oracleFor(columns, rows) } };
  const calls = [], intents = [], updates = []; let captured = false;
  const client = { async json(method, path, body, expected, options) {
    calls.push({ method, path, body, expected, operation: options?.operation });
    if (method === 'POST') { assert.equal(intents.length, 1); return { id: executionId, status: 'succeeded', results: [{ id: resultId }], completedStatementIndexes: [0] }; }
    if (method === 'DELETE') return {};
    assert(captured, 'source must be independently captured before paging');
    const offset = path.includes('?') ? Number(new URL(path, 'https://localhost:8001').searchParams.get('cursor')) : 0;
    const selected = rows.slice(offset, offset + 100).map(row => [...row]); if (wrong && offset === 0) selected[0][1] = 'foreign-nonce';
    if (empty) selected.length = 0; else if (short) selected.pop();
    return { executionId, resultId, columns: columns.map(name => ({ name })), rows: selected, truncated: false,
      expiresAt: new Date(Date.now() + 900000).toISOString(), nextCursor: malformed ? '' : offset + 100 < rows.length ? String(offset + 100) : null };
  } };
  return { account, client, calls, intents, updates,
    run: options => runJourney({ workload: wide ? 'wide-rows' : 'sql-page', account, client,
      onIntent: async value => { intents.push(value); return 1; }, finishIntent: async (_id, value) => updates.push(value),
      onExecution: async () => {}, captureSource: async () => { captured = true; }, ...options }) };
}
test('SQL adapter creates one exact execution, pages all values, then closes only its result', async () => {
  const f = sqlFixture(); await f.run();
  assert.equal(f.calls.filter(value => value.operation === 'result_page').length, 6);
  assert.equal(f.calls.filter(value => value.operation === 'execution_create').length, 1);
  assert.equal(f.calls.filter(value => value.operation === 'result_close').length, 1);
  assert.equal(f.updates.at(-1).status, 'closed');
  assert.match(f.calls[0].body.statements[0], /owner_nonce/);
});
test('driver-sized wide fixture checks full cell values; wrong owner and malformed cursor fail and clean exact handles', async () => {
  const wide = sqlFixture({ wide: true }); await wide.run();
  assert(wide.account.oracles.wide.rows > wide.account.settings.rowPageSize);
  assert.match(wide.calls[0].body.statements[0], /wide_rows/);
  for (const options of [{ wrong: true }, { malformed: true }]) {
    const f = sqlFixture(options); await assert.rejects(f.run());
    assert(f.calls.some(value => value.operation === 'control')); assert(f.calls.some(value => value.operation === 'result_close'));
  }
});
test('idle recipe requires the real1800second window; no-op or interrupted waits never claim expiry', async () => {
  const spec = plan({ workload: 'idle-expiry', recipe: 'lifecycle', fixtureMode: 'disposable', active: 2 });
  assert.equal(spec.idleSeconds, 1800); assert.equal(validatePlan(spec), spec);
  assert.throws(() => plan({ workload: 'idle-expiry', fixtureMode: 'disposable' }), /lifecycle/);
  const f = sqlFixture(); let requested;
  await assert.rejects(f.run({ workload: 'idle-expiry', idleWait: async ms => { requested = ms; } }), /real_idle_window_not_elapsed/);
  assert.equal(requested, 1800000); assert(f.calls.some(value => value.operation === 'control'));
});
test('idle validates exact first-page count, order and owner values before entering the real wait', async () => {
  for (const options of [{ wrong: true }, { empty: true }, { short: true }]) {
    const f = sqlFixture(options); let reached = false;
    await assert.rejects(f.run({ workload: 'idle-expiry', idleWait: async () => { reached = true; throw new Error('should_not_wait'); } }), /wrong_or_duplicate_rows|wrong_row_count/);
    assert.equal(reached, false); assert(f.calls.some(value => value.operation === 'control'));
  }
  const f = sqlFixture(); let reached = false;
  await assert.rejects(f.run({ workload: 'idle-expiry', idleWait: async ms => { assert.equal(ms, 1800000); reached = true; throw new Error('stop_before_real_idle'); } }), /stop_before_real_idle/);
  assert(reached); assert(!f.updates.some(value => value.status === 'expired')); // No fake expiry success.
});
test('weighted mix has exact reproducible counts and excludes destructive/lifecycle writers', () => {
  const counts = {};
  for (let index = 0; index < 100; index++) { const lane = mixedWorkload(DEFAULT_MIX, index); counts[lane] = (counts[lane] || 0) + 1; }
  assert.deepEqual(counts, Object.fromEntries(DEFAULT_MIX.map(value => [value.workload, value.weight])));
  for (const lane of ['copy', 'migration', 'idle-expiry', 'login-churn']) assert.throws(() => plan({ workload: 'mix', fixtureMode: 'disposable', mix: [{ workload: lane, weight: 100 }] }), /mix/);
  assert.throws(() => plan({ fixtureMode: 'disposable', active: 101, registered: 1000, profileMode: 'managed-distinct' }), /profile/);
  assert.equal(plan({ fixtureMode: 'disposable', active: 100, registered: 1000, profileMode: 'managed-shared' }).registeredIdentities, 1000);
});
test('mix stage reconciles every actual HTTP operation separately from journey arrivals', async () => {
  const spec = plan({ workload: 'mix', fixtureMode: 'disposable', active: 1 }), accounting = new Accounting();
  const result = await runOwnedStage({ spec, stage: spec.stages[0], receipt: { kind: 'disposable', accounts: [{ username: 'fixture' }] },
    Client: class { close() {} }, journey: async ({ workload, client }) => {
      for (let call = 0; call < (workload === 'sql-page' ? 8 : 1); call++) { client.accounting.sent('http'); client.accounting.finish({ status: 200 }); }
    }, schedule: async ({ task, onOffer }) => {
      for (let index = 0; index < 100; index++) { accounting.offered(true); onOffer(index, true); await task(index, accounting); accounting.journey(true); }
      return accounting.snapshot();
    } });
  assert.equal(result.arrivals.completed, 100); assert.equal(result.http.sent, 240);
  assert.equal(result.operations.http.completed, 240); assert.equal(result.journeys['sql-page'].completed, 20);
});
test('actual per-call budget refuses the next HTTP operation before send and reconciles every permitted call', () => {
  let now = 0;
  const accounting = new Accounting({ maximumCallsPerMinute: 2, clock: () => now });
  for (const operation of ['execution_create', 'result_page']) { accounting.sent(operation); accounting.finish({ status: 200, operation }); }
  assert.throws(() => accounting.sent('result_close'), /http_budget_exhausted/);
  assert.equal(accounting.snapshot().http.sent, 2);
  now = 60000; accounting.sent('result_close'); accounting.finish({ status: 204, operation: 'result_close' });
  assert.equal(accounting.snapshot().operations.result_close.completed, 1);
});
test('author and viewer report streams persist a pre-send intent and exact saved-resource terminal without raw receipt reads', async () => {
  const expected = oracleFor(['id'], [[1]]), id = 'cex_' + 'a'.repeat(32), model = { id: 'model_' + 'b'.repeat(32), revision: 2 };
  for (const workload of ['report-1', 'shared-report']) {
    const dashboard = { id: 'dashboard_' + 'c'.repeat(32), revision: 3, tiles: { 'shared-rows': expected } }, entries = [];
    const account = { username: 'owner', model, dashboards: { 1: dashboard }, sharedDashboard: dashboard, oracles: { rows: expected } };
    const client = { request: async options => {
      assert.equal(entries[0].kind, 'stream'); const tileId = workload === 'shared-report' ? 'shared-rows' : 'tile-1';
      for (const event of [{ type: 'start', tiles: [{ tileId }], tileErrors: [] }, { type: 'execution', executionId: id },
        { type: 'rows', tileId, columns: [{ name: 'id' }], rows: [[1]] }, { type: 'complete', tileId, rowCount: 1, limitReached: false, reason: null }, { type: 'end' }]) options.oracle.push(Buffer.from(JSON.stringify(event) + '\n'));
      options.oracle.finish(); return { status: 200 };
    } };
    await runJourney({ workload, account, client, onIntent: async value => { entries.push(value); return 1; },
      onExecution: async (username, executionId, metadata) => entries.push({ username, executionId, ...metadata }), finishIntent: async (_sequence, value) => entries.push(value) });
    const terminal = entries.find(value => value.terminal === 'drained'); assert.equal(terminal.kind, 'report-stream'); assert.equal(terminal.dashboardId, dashboard.id);
    assert.equal(terminal.dashboardRevision, 3); assert.equal(entries.at(-1).status, 'closed');
  }
});

test('disposable saved CSV persists intent before declaration and journals the actual private receipt before ack', async () => {
  const entries = [], expected = oracleFor(['id'], [['1']]), dashboard = { id: 'dash_owned', revision: 3 };
  const account = { username: 'owner', userId: 'user_owned', model: { id: 'model_owned', revision: 1 }, dashboards: { 1: dashboard },
    fixtureObserver: { run: 'pressure_' + 'a'.repeat(32) }, oracles: { csv: expected } };
  let declaration, operation;
  const client = { request: async options => {
    assert(declaration); assert.equal(options.path, '/api/v1/schemer/dashboards/dash_owned/tiles/tile-1/export');
    assert.equal(options.fixtureOperation, operation); options.oracle.push(Buffer.from('id\r\n1\r\n')); options.oracle.finish();
    entries.push('http-complete'); return { status: 200 };
  } };
  const observeFixture = async (_binding, request = {}) => {
    if (request.declare?.length) {
      assert.equal(entries[0].kind, 'stream'); declaration = request.declare[0]; operation = declaration.operation;
      assert.equal(entries[0].operationNonce, operation); assert.equal(declaration.actor, account.userId);
    }
    if (request.acknowledge?.length) {
      assert(entries.some(value => value.executionId === executionId)); assert(entries.some(value => value.fixtureStatus === 'succeeded'));
    }
    return { losses: 0, at: new Date().toISOString(), process: { incarnation: 'b'.repeat(32) }, operations: [{ ...declaration,
      request: 'complete', unknown: false, receipts: [{ id: executionId, owner: account.userId, status: 'succeeded', observedAt: new Date().toISOString() }] }] };
  };
  await runJourney({ workload: 'csv', account, client, observeFixture,
    onIntent: async value => { entries.push(value); return 1; }, finishIntent: async (_id, value) => entries.push(value),
    onExecution: async (_owner, id, value) => entries.push({ executionId: id, ...value }) });
  assert.equal(declaration.kind, 'tile-csv'); assert.equal(entries.at(-1).status, 'closed');
});

test('missing or lost fixture capture never substitutes no-job status or acknowledges an operation', async () => {
  for (const losses of [0, 1]) {
    let acknowledged = false;
    const account = { username: 'owner', userId: 'user_owned', model: { id: 'model_owned', revision: 1 }, dashboards: { 1: { id: 'dash_owned', revision: 1 } },
      fixtureObserver: {}, oracles: { csv: oracleFor(['id'], [[1]]) } };
    await assert.rejects(runJourney({ workload: 'csv', account,
      client: { request: async ({ oracle }) => { oracle.push(Buffer.from('id\r\n1\r\n')); oracle.finish(); return { status: 200 }; } },
      onIntent: async () => 1, finishIntent: async () => {}, observeFixture: async (_binding, request = {}) => {
        if (request.acknowledge?.length) acknowledged = true;
        return { operations: [], losses };
      } }), /fixture_execution_observation_unknown/);
    assert.equal(acknowledged, false);
  }
});
test('real lifecycle/source smoke remains opt-in', { skip: process.env.SCHEMII_LOAD_ADAPTER_LIVE !== '1', timeout: 2400000 }, async () => {
  // Explicitly prepared held fixture + independently running observer required.
  // This oracle never starts a stack, provisions, fakes time or guesses secrets.
  const id = process.env.SCHEMII_LOAD_PREPARED_RUN, directory = loadPath(id);
  const before = await readSnapshot(`${directory}/manifest-private.json`);
  assert.equal(before.status, 'ready'); assert.equal(before.plan.fixtureMode, 'disposable');
  assert(before.plan.activeIdentities >= 2);
  const config = await readSnapshot(`${directory}/config-private.json`);
  assert(config.observerFile && config.fixtureAuthority && !config.allowUnobserved);
  try {
    await loadMain(['run', '--run', id]);
    const deadline = Date.now() + 2300000;
    let current;
    do {
      await new Promise(resolve => setTimeout(resolve, 1000));
      current = await readSnapshot(`${directory}/manifest-private.json`);
    } while (current.status === 'running' && Date.now() < deadline);
    assert.equal(current.status, 'completed'); assert.equal(current.result.capacityEligible, false);
    assert.equal(current.result.recovery.verified, true); assert.equal(current.result.failures.length, 0);
    assert(current.result.stages[0].summary.http.sent > 0);
    if (current.plan.workload === 'idle-expiry') assert(Date.parse(current.result.stages[0].endedAt) - Date.parse(current.result.stages[0].startedAt) >= 1800000);
  } finally { await loadMain(['cleanup', '--run', id]); }
});
