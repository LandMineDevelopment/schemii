import test from 'node:test';
import assert from 'node:assert/strict';
import { openProductsAfterIsolation } from './readiness.mjs';

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
