import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { createLoginService } from '../login.js';
import { createHandler } from '../server.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const credential = { type: 'oauth', access: 'fake-access', refresh: 'fake-refresh', expires: 9999999999999 };
const args = owner => ({ owner, credentialId: 'account', generation: 1 });
function fixture(options = {}) {
  const gates = [];
  const oauth = { async login(interaction) {
    const method = await interaction.prompt({ type: 'select', options: [{ id: 'browser' }, { id: 'device_code' }] });
    assert.equal(method, 'device_code');
    interaction.notify({ type: 'device_code', verificationUri: 'https://auth.openai.com/codex/device', userCode: 'FAKE-CODE', expiresInSeconds: 600 });
    return new Promise((resolve, reject) => gates.push({ resolve, reject, signal: interaction.signal }));
  } };
  return { service: createLoginService({ oauth, ...options }), gates };
}

test('device logins isolate owners and retain successful credentials for retry until acknowledgement', async t => {
  const { service, gates } = fixture();
  t.after(() => service.close());
  const a = service.start(args('a'));
  const b = service.start(args('b'));
  await tick();
  assert.equal(service.status({ owner: 'a', id: a.id }).userCode, 'FAKE-CODE');
  assert.throws(() => service.status({ owner: 'b', id: a.id }), { code: 'not_found' });
  assert.throws(() => service.cancel({ owner: 'b', id: a.id }), { code: 'not_found' });
  gates[0].resolve(credential);
  await tick();
  const completed = service.status({ owner: 'a', id: a.id });
  assert.equal(completed.status, 'succeeded');
  assert.deepEqual(completed.credential, credential);
  completed.credential.access = 'mutated';
  assert.deepEqual(service.status({ owner: 'a', id: a.id }).credential, credential);
  assert.equal(service.status({ owner: 'b', id: b.id }).status, 'pending');
  service.cancel({ owner: 'a', id: a.id });
  assert.throws(() => service.status({ owner: 'a', id: a.id }), { code: 'not_found' });
});

test('expiry and cancellation abort polling and discard late successes', async t => {
  let time = 0;
  const { service, gates } = fixture({ now: () => time, ttlMs: 100 });
  t.after(() => service.close());
  const a = service.start(args('a'));
  await tick();
  time = 100;
  assert.throws(() => service.status({ owner: 'a', id: a.id }), { code: 'not_found' });
  assert.equal(gates[0].signal.aborted, true);
  const b = service.start(args('b'));
  await tick();
  service.cancel({ owner: 'b', id: b.id });
  assert.equal(gates[1].signal.aborted, true);
  gates.forEach(gate => gate.resolve(credential));
  await tick();
  assert.throws(() => service.status({ owner: 'b', id: b.id }), { code: 'not_found' });
});

test('capacity is bounded per owner and globally; expired records free capacity', t => {
  let time = 0;
  const { service } = fixture({ now: () => time, ttlMs: 10, maxTotal: 2, maxPerOwner: 1 });
  t.after(() => service.close());
  service.start(args('a'));
  assert.throws(() => service.start(args('a')), { code: 'busy' });
  service.start(args('b'));
  assert.throws(() => service.start(args('c')), { code: 'busy' });
  time = 10;
  assert.equal(service.start(args('c')).status, 'pending');
});

test('provider failures are sanitized and arbitrary provider configuration is rejected', async t => {
  const { service, gates } = fixture();
  t.after(() => service.close());
  assert.throws(() => service.start({ ...args('a'), baseUrl: 'https://attacker.invalid' }), { code: 'invalid_request' });
  const a = service.start(args('a'));
  await tick();
  gates[0].reject(new Error('raw fake-secret diagnostic'));
  await tick();
  const state = service.status({ owner: 'a', id: a.id });
  assert.equal(state.error, 'provider_failed');
  assert.equal(JSON.stringify(state).includes('fake-secret'), false);
});

test('unexpected prompts and verification URLs fail closed', async t => {
  for (const login of [async i => i.prompt({ type: 'manual_code' }), async i => i.notify({ type: 'device_code', verificationUri: 'https://attacker.invalid', userCode: 'FAKE' })]) {
    const service = createLoginService({ oauth: { login } });
    t.after(() => service.close());
    const state = service.start(args('a'));
    await tick();
    assert.equal(service.status({ owner: 'a', id: state.id }).status, 'failed');
  }
});

async function request(handler, { url = '/health', method = 'GET', token = 'test-secret', body = '', contentType = 'application/json' } = {}) {
  const input = Readable.from([Buffer.from(body)]);
  input.method = method;
  input.url = url;
  input.headers = { authorization: `Bearer ${token}`, 'content-type': contentType };
  let status;
  let data;
  await handler(input, { writeHead(code) { status = code; }, end(value) { data = JSON.parse(value); } });
  return { status, data };
}

test('private HTTP handler authenticates, limits payloads, and sanitizes errors without binding a port', async t => {
  const { service } = fixture();
  t.after(() => service.close());
  const handler = createHandler({ secret: 'test-secret', service });
  assert.equal((await request(handler)).status, 200);
  assert.equal((await request(handler, { token: 'wrong' })).status, 401);
  assert.equal((await request(handler, { method: 'POST', url: '/logins', body: 'x'.repeat(16385) })).status, 413);
  assert.equal((await request(handler, { method: 'POST', url: '/logins', body: '{' })).status, 400);
  const started = await request(handler, { method: 'POST', url: '/logins', body: JSON.stringify(args('a')) });
  assert.equal(started.status, 200);
  assert.equal((await request(handler, { method: 'POST', url: '/logins/status', body: JSON.stringify({ owner: 'b', id: started.data.id }) })).status, 404);
  const broken = createHandler({ secret: 'test-secret', service: { start() { throw new Error('fake-sensitive'); } } });
  assert.deepEqual(await request(broken, { method: 'POST', url: '/logins', body: '{}' }), { status: 500, data: { error: 'internal_error' } });
});
