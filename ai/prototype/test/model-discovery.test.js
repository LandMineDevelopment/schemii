import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { discoverModels } from '../model-discovery.js';
import { CredentialVault } from '../runtime.js';
import { TurnService } from '../turns.js';
import { createHandler } from '../server.js';

const token = owner => `header.${Buffer.from(JSON.stringify({
  'https://api.openai.com/auth': { chatgpt_account_id: owner },
})).toString('base64url')}.signature`;
const input = (extra = {}) => ({ owner: 'alice', credentialId: 'personal', providerId: 'openai',
  generation: 3, credential: { type: 'api_key', key: 'synthetic-alice' }, ...extra });
const codex = (extra = {}) => input({ providerId: 'openai-codex', credential: {
  type: 'oauth', access: token('alice'), refresh: 'synthetic-refresh', expires: Date.now() + 3600000,
}, ...extra });

test('Codex refresh uses account auth and intersects visible models with SDK support', async t => {
  const service = new TurnService();
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'https://chatgpt.com/backend-api/codex/models?client_version=0.156.1');
    assert.equal(options.method, 'GET');
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, `Bearer ${token('alice')}`);
    assert.equal(options.headers['ChatGPT-Account-Id'], 'alice');
    return Response.json({ models: [
      { slug: 'gpt-5.5', visibility: 'list', display_name: 'untrusted provider text' },
      { slug: 'gpt-5.4-mini', visibility: 'hide' },
      { slug: 'unknown-model', visibility: 'list' },
    ] });
  });
  const result = await service.refreshModels(codex());
  assert.equal(result.type, 'result');
  assert.deepEqual(result.models.map(model => model.id), ['gpt-5.5']);
  assert.equal(result.models[0].name, 'GPT-5.5');
  assert.equal(result.generation, 3);
  assert.equal(result.credential.refresh, 'synthetic-refresh');
  assert.equal(await service.vault.read('alice', 'personal', 'openai-codex'), undefined);
});

test('OpenAI model discovery isolates simultaneous owners and never runs inference', async t => {
  const service = new TurnService();
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'https://api.openai.com/v1/models');
    assert.equal(options.method, 'GET');
    requests.push(options.headers.Authorization);
    return Response.json({ data: [{ id: options.headers.Authorization.endsWith('alice')
      ? 'gpt-4o-mini' : 'gpt-4o' }, { id: 'not-in-sdk' }] });
  });
  const [alice, bob] = await Promise.all([service.refreshModels(input()), service.refreshModels(input({
    owner: 'bob', credential: { type: 'api_key', key: 'synthetic-bob' },
  }))]);
  assert.deepEqual(alice.models.map(model => model.id), ['gpt-4o-mini']);
  assert.deepEqual(bob.models.map(model => model.id), ['gpt-4o']);
  assert.deepEqual(requests.sort(), ['Bearer synthetic-alice', 'Bearer synthetic-bob']);
});

test('discovery returns rotated OAuth even when provider catalog subsequently fails', async t => {
  const service = new TurnService();
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === 'https://auth.openai.com/oauth/token') {
      assert.equal(new URLSearchParams(options.body).get('refresh_token'), 'synthetic-refresh');
      return Response.json({ access_token: token('alice'), refresh_token: 'rotated', expires_in: 3600 });
    }
    return Response.json({ error: 'SECRET UPSTREAM BODY' }, { status: 429 });
  });
  const request = codex(); request.credential.expires = 0;
  const result = await service.refreshModels(request);
  assert.equal(result.type, 'error');
  assert.equal(result.code, 'rate_limited');
  assert.equal(result.credential.refresh, 'rotated');
  assert.doesNotMatch(JSON.stringify(result), /SECRET UPSTREAM BODY/);
  assert.equal(await service.vault.read('alice', 'personal', 'openai-codex'), undefined);
});

test('discovery and turns share credential overlap guard; disconnect cancels discovery', async t => {
  const service = new TurnService();
  const started = Promise.withResolvers();
  t.mock.method(globalThis, 'fetch', (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('private cancellation')), { once: true });
    started.resolve();
  }));
  const pending = service.refreshModels(input());
  await started.promise;
  await assert.rejects(service.refreshModels(input()), { code: 'busy' });
  await assert.rejects(service.run({ ...input(), turnId: 'turn', modelId: 'gpt-4o-mini',
    context: { messages: [{ role: 'user', content: 'hello' }] },
  }, { emit: () => {} }), { code: 'busy' });
  service.remove({ owner: 'alice', credentialId: 'personal' });
  const result = await pending;
  assert.equal(result.code, 'cancelled');
  assert.equal(await service.vault.read('alice', 'personal', 'openai'), undefined);
});

test('discovery failures are bounded and sanitized without falling back to static availability', async t => {
  const service = new TurnService();
  for (const payload of [{}, { data: [{ id: 12 }] }, { data: null }]) {
    t.mock.method(globalThis, 'fetch', async () => Response.json(payload));
    const result = await service.refreshModels(input());
    assert.equal(result.code, 'provider_failed');
    assert.equal(result.models, undefined);
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('x'.repeat(4 * 1024 * 1024 + 1)));
  assert.equal((await service.refreshModels(input())).code, 'response_too_large');
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', 'openai', input().credential);
  const timer = setTimeout(() => {}, 1000);
  try {
    await assert.rejects(discoverModels({ ...input(), vault, timeoutMs: 10,
      fetcher: (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('SECRET')), { once: true });
      }),
    }), error => error.code === 'timeout' && !String(error).includes('SECRET'));
  } finally { clearTimeout(timer); }
});

test('private refresh route authenticates before reading credentials and returns fenced result', async () => {
  const calls = [];
  const handler = createHandler({ secret: 'private-secret', service: {}, turns: {
    async refreshModels(body) { calls.push(body); return { type: 'result', generation: body.generation, models: [] }; },
  } });
  for (const authorized of [false, true]) {
    const request = Readable.from([JSON.stringify(input())]);
    request.method = 'POST'; request.url = '/models/refresh';
    request.headers = { 'content-type': 'application/json',
      authorization: authorized ? 'Bearer private-secret' : 'Bearer wrong' };
    const response = new EventEmitter();
    response.writeHead = status => { response.status = status; };
    response.end = body => { response.body = JSON.parse(body); };
    await handler(request, response);
    assert.equal(response.status, authorized ? 200 : 401);
    if (authorized) assert.equal(response.body.generation, 3);
  }
  assert.equal(calls.length, 1);
});
