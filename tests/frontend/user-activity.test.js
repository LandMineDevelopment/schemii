import test from 'node:test';
import assert from 'node:assert/strict';
import { trackUserActivity } from '../../src/schemii/common/web/assets/user-activity.js';

function fixture(ok = true) {
  const events = new Map();
  const document = { visibilityState: 'visible',
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name) };
  const window = { addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name) };
  let time = 0;
  const calls = [];
  const stop = trackUserActivity({ document, window, now: () => time,
    fetch: async (...args) => { calls.push(args); return { ok }; } });
  return { document, calls, events, stop, advance: () => { time += 60_001; } };
}

test('records app opening and throttled genuine input, never a background timer', async () => {
  const f = fixture();
  await Promise.resolve();
  assert.equal(f.calls.length, 1);
  await f.events.get('pointerdown')({ isTrusted: true });
  assert.equal(f.calls.length, 1);
  f.advance();
  assert.equal(f.calls.length, 1);
  await f.events.get('keydown')({ isTrusted: true });
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[0], ['/api/v1/activity', { method: 'POST', cache: 'no-store' }]);
  f.stop();
  assert.equal(f.events.size, 0);
});

test('hidden tabs and synthetic events do not extend retention', async () => {
  const f = fixture();
  await Promise.resolve();
  f.advance();
  await f.events.get('pointerdown')({ isTrusted: false });
  f.document.visibilityState = 'hidden';
  await f.events.get('visibilitychange')({ isTrusted: true });
  await f.events.get('wheel')({ isTrusted: true });
  assert.equal(f.calls.length, 1);
  f.document.visibilityState = 'visible';
  await f.events.get('visibilitychange')({ isTrusted: true });
  assert.equal(f.calls.length, 2);
});

test('failed recording retries on later interaction without flooding the server', async () => {
  const f = fixture(false);
  await Promise.resolve();
  await f.events.get('touchstart')({ isTrusted: true });
  assert.equal(f.calls.length, 1);
  f.advance();
  await f.events.get('touchstart')({ isTrusted: true });
  assert.equal(f.calls.length, 2);
});
