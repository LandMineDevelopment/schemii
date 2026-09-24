import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { TurnService, supportedModels } from '../turns.js';
import { CredentialVault, TurnError } from '../runtime.js';
import { createHandler } from '../server.js';

const context = { messages: [{ role: 'user', content: 'Hello', timestamp: 0 }], tools: [] };
const input = (extra = {}) => ({ owner: 'alice', turnId: 'turn1', credentialId: 'personal',
  providerId: 'openai', modelId: 'gpt-4o-mini', generation: 1,
  credential: { type: 'api_key', key: 'fake-private-key' }, context, ...extra });
const response = text => {
  const item = { id: 'msg', type: 'message', role: 'assistant', content: [] };
  const events = [
    { type: 'response.created', response: { id: 'resp' } },
    { type: 'response.output_item.added', output_index: 0, item },
    { type: 'response.output_text.delta', output_index: 0, content_index: 0, delta: text },
    { type: 'response.output_item.done', output_index: 0, item: { ...item, content: [{ type: 'output_text', text, annotations: [] }] } },
    { type: 'response.completed', response: { id: 'resp', status: 'completed' } },
  ];
  return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } });
};

test('turn service streams real SDK text and forgets credentials after private terminal', async t => {
  const vault = new CredentialVault();
  const service = new TurnService({ vault, providerIds: ['openai'] });
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const request = new Request(url, options);
    assert.equal(request.headers.get('authorization'), 'Bearer fake-private-key');
    return response('Hello user');
  });
  const events = [];
  await service.run(input(), { emit: event => events.push(event) });
  assert.equal(events[0].type, 'text');
  assert.equal(events.at(-1).text, 'Hello user');
  assert.equal(events.at(-1).credential.key, 'fake-private-key');
  assert.equal(events.at(-1).generation, 1);
  assert.equal(await vault.read('alice', 'personal', 'openai'), undefined);
});

test('same identity rejects overlap; owner-scoped cancel aborts actual SDK HTTP', async t => {
  const service = new TurnService({ providerIds: ['openai'] });
  const started = Promise.withResolvers();
  t.mock.method(globalThis, 'fetch', (url, init) => new Promise((resolve, reject) => {
    const request = new Request(url, init);
    started.resolve();
    request.signal.addEventListener('abort', () => reject(new Error('fake-private-failure')), { once: true });
  }));
  const events = [];
  const running = service.run(input(), { emit: event => events.push(event) });
  await started.promise;
  await assert.rejects(service.run(input({ turnId: 'other' }), { emit() {} }), { code: 'busy',
    limit: { name: 'credential_overlap_guard', configured: 1, observed: 1 } });
  assert.throws(() => service.cancel({ owner: 'bob', turnId: 'turn1' }), { code: 'not_found' });
  service.cancel({ owner: 'alice', turnId: 'turn1' });
  await running;
  assert.equal(events.at(-1).code, 'cancelled');
  assert.doesNotMatch(events.at(-1).message, /private/);
  assert.equal(events.at(-1).credential.key, 'fake-private-key');
});

test('credential removal aborts only the matching identity and releases capacity', async () => {
  const vault = new CredentialVault();
  const entered = Promise.withResolvers();
  const runner = { async run({ signal }) {
    entered.resolve();
    await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
    throw new Error('private');
  } };
  const service = new TurnService({ vault, runner, providerIds: ['openai'] });
  const events = [];
  const running = service.run(input(), { emit: event => events.push(event) });
  await entered.promise;
  service.remove({ owner: 'bob', credentialId: 'personal' });
  service.remove({ owner: 'alice', credentialId: 'personal' });
  await running;
  assert.equal(await vault.read('alice', 'personal', 'openai'), undefined);
  assert.throws(() => service.cancel({ owner: 'alice', turnId: 'turn1' }), { code: 'not_found' });
});

test('runtime capacity descriptor reaches the private terminal without query context', async () => {
  const limit = { name: 'maximum_concurrent_turns_per_user', configured: 2, observed: 2 };
  const service = new TurnService({ providerIds: ['openai'], runner: {
    async run() { throw new TurnError('busy', limit); },
  } });
  const events = [];
  await service.run(input(), { emit: event => events.push(event) });
  assert.equal(events.at(-1).code, 'busy');
  assert.deepEqual(events.at(-1).limit, limit);
  assert.equal(JSON.stringify(events.at(-1).limit).includes('Hello'), false);
});

test('actual Codex OAuth refresh survives inference error in private terminal only', async t => {
  const token = `header.${Buffer.from(JSON.stringify({ 'https://api.openai.com/auth': { chatgpt_account_id: 'alice' } })).toString('base64url')}.signature`;
  const service = new TurnService();
  const modelId = supportedModels().models.find(model => model.providerId === 'openai-codex').id;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (String(url) === 'https://auth.openai.com/oauth/token') {
      return Response.json({ access_token: token, refresh_token: 'rotated-secret', expires_in: 3600 });
    }
    return new Response('private-provider-diagnostic', { status: 400 });
  });
  const events = [];
  await service.run(input({ providerId: 'openai-codex', modelId,
    credential: { type: 'oauth', access: 'expired', refresh: 'old-secret', expires: 0 } }),
  { emit: event => events.push(event) });
  assert.equal(events.at(-1).type, 'error');
  assert.equal(events.at(-1).credential.refresh, 'rotated-secret');
  assert.doesNotMatch(events.at(-1).message, /private|secret/);
});

test('model discovery exposes only supported model metadata without auth', () => {
  const result = supportedModels();
  assert.ok(result.models.some(model => model.providerId === 'openai-codex'));
  assert.ok(result.models.some(model => model.providerId === 'openai'));
  assert.ok(result.models.some(model => model.providerId === 'opencode'));
  assert.ok(result.models.every(model => Object.keys(model).sort().join(',') === 'contextWindow,id,maxOutputTokens,name,providerId,reasoningLevels'));
});

test('default production service supports OpenAI API keys', async t => {
  t.mock.method(globalThis, 'fetch', async () => response('API-key answer'));
  const events = [];
  await new TurnService().run(input(), { emit: event => events.push(event) });
  assert.equal(events.at(-1).type, 'result');
  assert.equal(events.at(-1).text, 'API-key answer');
});

test('Zen actual SDK accepts ordinary JSON Schema defs and oneOf without TypeBox symbols', async t => {
  const parameters = { type: 'object', properties: { action: { oneOf: [
    { $ref: '#/$defs/Create' }, { $ref: '#/$defs/Remove' },
  ] } }, required: ['action'], additionalProperties: false,
  $defs: { Create: { type: 'object', properties: { name: { type: 'string' } }, required: ['name'] },
    Remove: { type: 'object', properties: { id: { type: 'integer' } }, required: ['id'] } } };
  let outgoing;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    outgoing = await new Request(url, init).json();
    const chunks = [
      { id: 'chat', choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call1',
        type: 'function', function: { name: 'propose', arguments: '{"action":{"name":"test"}}' } }] }, finish_reason: null }] },
      { id: 'chat', choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] },
    ];
    return new Response(chunks.map(chunk => `data: ${JSON.stringify(chunk)}\n\n`).join('') + 'data: [DONE]\n\n',
      { headers: { 'content-type': 'text/event-stream' } });
  });
  const events = [];
  await new TurnService().run(input({ providerId: 'opencode', modelId: 'big-pickle',
    credential: { type: 'api_key', key: 'owner-zen-key' }, context: { ...context,
      tools: [{ name: 'propose', description: 'Propose a change', parameters }] } }),
  { emit: event => events.push(event) });
  assert.deepEqual(outgoing.tools[0].function.parameters, parameters);
  assert.equal(events.at(-1).type, 'result');
  assert.deepEqual(events.at(-1).toolCalls, [{ id: 'call1', name: 'propose', arguments: { action: { name: 'test' } } }]);
});

test('Zen uses the supplied instance key through the actual SDK without ambient keys', async t => {
  const previous = process.env.OPENCODE_API_KEY;
  process.env.OPENCODE_API_KEY = 'ambient-private-key';
  t.after(() => { if (previous === undefined) delete process.env.OPENCODE_API_KEY;
    else process.env.OPENCODE_API_KEY = previous; });
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    const request = new Request(url, init);
    assert.equal(request.url, 'https://opencode.ai/zen/v1/chat/completions');
    assert.equal(request.headers.get('authorization'), 'Bearer instance-zen-key');
    const chunks = [
      { id: 'chat', choices: [{ index: 0, delta: { role: 'assistant', content: 'Zen answer' }, finish_reason: null }] },
      { id: 'chat', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] },
    ];
    return new Response(chunks.map(chunk => `data: ${JSON.stringify(chunk)}\n\n`).join('') + 'data: [DONE]\n\n',
      { headers: { 'content-type': 'text/event-stream' } });
  });
  const events = [];
  await new TurnService().run(input({ providerId: 'opencode', modelId: 'big-pickle',
    credential: { type: 'api_key', key: 'instance-zen-key' } }), { emit: event => events.push(event) });
  assert.equal(events.at(-1).type, 'result');
  assert.equal(events.at(-1).text, 'Zen answer');
});

test('turn request limits validate and context overflow is a safe terminal', async () => {
  const service = new TurnService({ providerIds: ['openai'] });
  await assert.rejects(service.run(input({ limits: { responseBytes: 999999999 } }), { emit() {} }), { code: 'invalid_request' });
  const events = [];
  await service.run(input({ limits: { contextBytes: 1 } }), { emit: event => events.push(event) });
  assert.equal(events.at(-1).code, 'context_too_large');
});

test('broken provider stream produces a safe terminal and releases credential identity', async t => {
  const service = new TurnService();
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    start(controller) { controller.error(new Error('private-token-in-provider-stream-error')); },
  }), { headers: { 'content-type': 'text/event-stream' } }));
  for (const turnId of ['first', 'second']) {
    const events = [];
    await service.run(input({ turnId }), { emit: event => events.push(event) });
    assert.equal(events.at(-1).type, 'error');
    assert.equal(events.at(-1).code, 'provider_failed');
    assert.doesNotMatch(events.at(-1).message, /private-token/);
  }
});

test('provider HTTP limits and account failures have useful fixed messages without raw diagnostics', async t => {
  let status = 429;
  t.mock.method(globalThis, 'fetch', async () => Response.json({ error: {
    message: 'PRIVATE-ACCOUNT-TOKEN', type: 'provider-secret-diagnostic',
  } }, { status }));
  for (const [httpStatus, code] of [[429, 'rate_limited'], [401, 'credentials_required'],
    [402, 'billing_required'], [403, 'provider_access_denied']]) {
    status = httpStatus;
    for (const extra of [{}, { providerId: 'opencode', modelId: 'big-pickle', credential: { type: 'api_key', key: 'owner-zen-key' } }]) {
      const events = [];
      await new TurnService().run(input(extra), { emit: event => events.push(event) });
      assert.equal(events.at(-1).type, 'error');
      assert.equal(events.at(-1).code, code);
      assert.doesNotMatch(events.at(-1).message, /PRIVATE|secret-diagnostic/);
    }
  }
});

test('private HTTP turns use authenticated NDJSON and return no credential in text events', async () => {
  const turns = { async run(_input, { emit }) {
    await emit({ type: 'text', text: 'Readable' });
    await emit({ type: 'result', text: 'Readable', toolCalls: [], credential: { key: 'private' } });
  } };
  const handler = createHandler({ secret: 'private-sidecar', turns });
  const request = Readable.from([Buffer.from(JSON.stringify(input()))]);
  request.method = 'POST'; request.url = '/turns';
  request.headers = { authorization: 'Bearer private-sidecar', 'content-type': 'application/json' };
  const output = new EventEmitter();
  const chunks = [];
  output.writeHead = (status, headers) => {
    assert.equal(status, 200);
    assert.equal(headers['Content-Type'], 'application/x-ndjson');
  };
  output.write = chunk => { chunks.push(chunk); return true; };
  output.end = () => {};
  await handler(request, output);
  assert.equal(chunks.length, 2);
  assert.doesNotMatch(chunks[0], /private/);
  assert.equal(JSON.parse(chunks[1]).type, 'result');
});

test('one-character NDJSON chunks do not spend the response budget on framing', async () => {
  const turns = { async run(_input, { emit }) {
    for (let i = 0; i < 50000; i++) await emit({ type: 'text', text: 'x' });
    await emit({ type: 'result', text: 'x'.repeat(50000), toolCalls: [] });
  } };
  const handler = createHandler({ secret: 'private-sidecar', turns });
  const request = Readable.from([Buffer.from(JSON.stringify(input({ limits: { responseBytes: 65536 } })))]);
  request.method = 'POST'; request.url = '/turns';
  request.headers = { authorization: 'Bearer private-sidecar', 'content-type': 'application/json' };
  const output = new EventEmitter();
  let last, texts = 0;
  output.writeHead = () => {};
  output.write = chunk => {
    last = JSON.parse(chunk);
    if (last.type === 'text') texts++;
    return true;
  };
  output.end = () => {};
  await handler(request, output);
  assert.equal(texts, 50000);
  assert.equal(last.type, 'result');
});
