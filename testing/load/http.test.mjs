import test from 'node:test';
import assert from 'node:assert/strict';
import https from 'node:https';
import { EventEmitter } from 'node:events';
import { HTTPClient } from './http.mjs';

test('fixture correlation header is opt-in, exact and restricted before any transport', async t => {
  const calls = [], original = https.request;
  https.request = (_url, options, reply) => {
    calls.push(options);
    const request = new EventEmitter();
    request.end = () => queueMicrotask(() => {
      const response = { statusCode: 200, headers: {}, complete: true, destroy() {},
        async *[Symbol.asyncIterator]() { yield Buffer.from('{}'); } };
      reply(response); request.emit('close');
    });
    request.destroy = () => request.emit('close'); return request;
  };
  t.after(() => { https.request = original; });
  const client = new HTTPClient({ cookie: 'private-cookie' }); t.after(() => client.close());
  await client.request({ path: '/api/v1/auth/me' });
  assert(!('X-Schemii-Fixture-Operation' in calls[0].headers));
  await client.request({ method: 'POST', path: '/api/v1/schemer/dashboards/dash_owned/tiles/tile-1/export', fixtureOperation: 'a'.repeat(32) });
  assert.equal(calls[1].headers['X-Schemii-Fixture-Operation'], 'a'.repeat(32));
  assert.equal(calls[1].headers.Cookie, 'private-cookie');
  for (const options of [
    { method: 'GET', path: '/api/v1/schemer/query/export', fixtureOperation: 'a'.repeat(32) },
    { method: 'POST', path: '/api/v1/auth/login', fixtureOperation: 'a'.repeat(32) },
    { method: 'POST', path: '/api/v1/schemer/query/export', fixtureOperation: 'a'.repeat(32) + '\r\nInjected: yes' },
    { method: 'POST', path: '/api/v1/schemer/query/export', fixtureOperation: ['a'.repeat(32)] },
  ]) await assert.rejects(client.request(options), /invalid_fixture_operation/);
  assert.equal(calls.length, 2);
});
