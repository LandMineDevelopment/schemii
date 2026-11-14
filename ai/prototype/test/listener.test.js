import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { test } from 'node:test';
import { createHandler, listenerHost } from '../server.js';

test('Android loopback listener serves authenticated health with no wildcard address', async (t) => {
  const server = createServer(createHandler({ secret: 'synthetic-listener-secret' }));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  server.listen(0, listenerHost({ SCHEMII_PI_HOST: '127.0.0.1' }));
  await once(server, 'listening');
  const address = server.address();
  assert.equal(address.address, '127.0.0.1');
  const response = await fetch(`http://127.0.0.1:${address.port}/health`, {
    headers: { Authorization: 'Bearer synthetic-listener-secret' },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: 'ok' });
});

test('Docker retains its existing listener and invalid bind settings fail closed', () => {
  assert.equal(listenerHost({}), '0.0.0.0');
  for (const value of ['', 'localhost', '::', '192.168.1.5', '127.0.0.1\n']) {
    assert.throws(() => listenerHost({ SCHEMII_PI_HOST: value }), /SCHEMII_PI_HOST/);
  }
});
