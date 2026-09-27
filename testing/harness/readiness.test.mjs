import test from 'node:test';
import assert from 'node:assert/strict';
import { assertLaneReadyToClaim, assertProductNavigationAllowed, failClosedRecovery, openProductsAfterIsolation } from './readiness.mjs';

test('a wave starts product streams only after every session passes logout isolation', async () => {
  const events = [];
  const lanes = [{ id: 'author' }, { id: 'reader' }];
  const loggedBackIn = new Set();
  let parked = new Set();

  await openProductsAfterIsolation(lanes, {
    parkLanes: async () => {
      for (const lane of lanes) { events.push(`park:${lane.id}`); parked.add(lane.id); }
    },
    proveIsolation: async () => {
      assert.equal(parked.size, lanes.length, 'all product pages must be parked before logout begins');
      for (const lane of lanes) {
        events.push(`logout:${lane.id}`);
        events.push(`logout-verified:${lane.id}`);
        events.push(`relogin:${lane.id}`);
        loggedBackIn.add(lane.id);
      }
      events.push('isolation-complete');
    },
    navigateLane: async id => {
      assert.equal(loggedBackIn.size, lanes.length, 'all separate sessions must be restored before product navigation');
      events.push(`product-stream:${id}`);
    },
  });

  assert.deepEqual(events, [
    'park:author', 'park:reader',
    'logout:author', 'logout-verified:author', 'relogin:author',
    'logout:reader', 'logout-verified:reader', 'relogin:reader',
    'isolation-complete', 'product-stream:author', 'product-stream:reader',
  ]);
});

test('failed isolation never opens a product page or starts its stream', async () => {
  const events = [];
  await assert.rejects(openProductsAfterIsolation([{ id: 'author' }], {
    parkLanes: async () => { events.push('park'); },
    proveIsolation: async () => { events.push('logout-failed'); throw new Error('logout could not be verified'); },
    navigateLane: async id => events.push(`product-stream:${id}`),
  }), /logout could not be verified/);
  assert.deepEqual(events, ['park', 'logout-failed']);
});

test('a lane is never opened when parking fails before isolation', async () => {
  const events = [];
  await assert.rejects(openProductsAfterIsolation([{ id: 'author' }], {
    parkLanes: async () => { events.push('park-failed'); throw new Error('could not park product page'); },
    proveIsolation: async () => events.push('logout'),
    navigateLane: async id => events.push(`product-stream:${id}`),
  }), /could not park product page/);
  assert.deepEqual(events, ['park-failed']);
});

test('failed fleet recovery closes and invalidates every active lane before persisting blocked status', async () => {
  const lanes = [
    { id: 'recovering', status: 'ready', generation: 3, agent: 'old-agent', heartbeatAt: 'old-heartbeat' },
    { id: 'peer', status: 'ready', generation: 8, agent: null, heartbeatAt: null },
    { id: 'finished', status: 'complete', generation: 2 },
  ];
  const tokens = new Map([['recovering', 'old-token'], ['peer', 'peer-token']]);
  const browserHandles = new Set(['recovering', 'peer']);
  const run = { status: 'recovering' };
  let persisted = false;
  let cleanupReported = false;
  assert.doesNotThrow(() => assertLaneReadyToClaim(lanes[1]));

  await assert.rejects(failClosedRecovery({
    lanes,
    affectedLaneIds: ['recovering', 'peer'],
    message: 'logout isolation could not be verified',
    closeFleet: async () => {
      browserHandles.clear();
      throw new Error('one browser close reported an error');
    },
    invalidateLane: lane => {
      tokens.delete(lane.id);
      lane.generation++;
      lane.agent = null;
      lane.heartbeatAt = null;
    },
    onFailure: ({ message, cleanupError }) => {
      run.status = 'blocked';
      run.error = message;
      cleanupReported = Boolean(cleanupError);
    },
    persist: async () => { persisted = true; },
  }), /logout isolation could not be verified/);

  assert.equal(browserHandles.size, 0, 'all sessions in the failed proof wave are closed');
  assert.equal(tokens.has('recovering'), false);
  assert.equal(tokens.has('peer'), false, 'a ready peer token cannot survive failed fleet proof');
  assert.deepEqual(lanes.slice(0, 2).map(lane => lane.status), ['blocked', 'blocked']);
  assert.deepEqual(lanes.slice(0, 2).map(lane => lane.generation), [4, 9]);
  assert.throws(() => assertLaneReadyToClaim(lanes[1]), /Only a preflight-ready lane can be claimed/);
  assert.equal(lanes[0].agent, null);
  assert.equal(lanes[0].heartbeatAt, null);
  assert.equal(lanes[2].status, 'complete', 'unaffected completed lanes remain unchanged');
  assert.equal(run.status, 'blocked');
  assert.equal(run.error, 'logout isolation could not be verified');
  assert.equal(cleanupReported, true, 'cleanup failure is exposed to the run summary');
  assert.equal(persisted, true, 'blocked peer state is persisted despite a cleanup error');
});

test('product navigation is rejected until the fleet isolation proof succeeds', () => {
  const baseURL = 'https://localhost:8001';
  assert.throws(() => assertProductNavigationAllowed('/', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/schemoo/projects', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/schemer?report=starter', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/%73chemer', false, baseURL), /completed isolation proof/);
  assert.doesNotThrow(() => assertProductNavigationAllowed('/account', false, baseURL));
  assert.doesNotThrow(() => assertProductNavigationAllowed('/schemer', true, baseURL));
});
