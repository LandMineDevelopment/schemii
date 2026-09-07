import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CredentialVault, TurnRunner } from '../runtime.js';

// Exercise the actual Pi provider and OpenAI SDK. Only the HTTP boundary is fake.
function sse(events) {
  return new Response(events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'content-type': 'text/event-stream' },
  });
}

function textResponse(text) {
  const item = { id: 'msg_test', type: 'message', role: 'assistant', content: [] };
  return sse([
    { type: 'response.created', response: { id: 'resp_test' } },
    { type: 'response.output_item.added', output_index: 0, item },
    { type: 'response.output_text.delta', output_index: 0, content_index: 0, delta: text },
    { type: 'response.output_item.done', output_index: 0, item: { ...item, content: [{ type: 'output_text', text, annotations: [] }] } },
    { type: 'response.completed', response: { id: 'resp_test', status: 'completed' } },
  ]);
}

function turn(owner, extra = {}) {
  return {
    owner, credentialId: 'personal', providerId: 'openai', modelId: 'gpt-4o-mini',
    context: { systemPrompt: `Instructions for ${owner}`, messages: [{ role: 'user', content: `Private message from ${owner}`, timestamp: 0 }], tools: [] },
    ...extra,
  };
}

async function setup(...owners) {
  const vault = new CredentialVault();
  for (const owner of owners) {
    await vault.put(owner, 'personal', 'openai', { type: 'api_key', key: `synthetic-key-${owner}` });
  }
  return new TurnRunner({ vault });
}

async function requestDetails(input, init) {
  const request = new Request(input, init);
  return { url: request.url, authorization: request.headers.get('authorization'), body: await request.json() };
}

test('real provider keeps simultaneous owners credentials and context isolated', { timeout: 10000 }, async (t) => {
  const runner = await setup('alice', 'bob');
  const requests = [];
  let release;
  const bothStarted = new Promise((resolve) => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async (input, init) => {
    const request = await requestDetails(input, init);
    requests.push(request);
    if (requests.length === 2) release();
    await bothStarted;
    return textResponse(request.authorization.endsWith('alice') ? 'Hello Alice' : 'Hello Bob');
  });
  const deltas = [];
  const results = await Promise.all([
    runner.run(turn('alice', { onText: (text) => deltas.push(text) })),
    runner.run(turn('bob')),
  ]);
  assert.deepEqual(results.map((result) => result.text), ['Hello Alice', 'Hello Bob']);
  assert.equal(deltas.join(''), 'Hello Alice');
  assert.equal(requests.length, 2);
  for (const owner of ['alice', 'bob']) {
    const request = requests.find((entry) => entry.authorization === `Bearer synthetic-key-${owner}`);
    assert.ok(request, `missing isolated authorization for ${owner}`);
    assert.equal(request.url, 'https://api.openai.com/v1/responses');
    assert.equal(request.body.model, 'gpt-4o-mini');
    assert.equal(request.body.stream, true);
    assert.deepEqual(request.body.input, [
      { role: 'system', content: `Instructions for ${owner}` },
      { role: 'user', content: [{ type: 'input_text', text: `Private message from ${owner}` }] },
    ]);
  }
});

test('real provider transforms tool declarations and decodes streamed function arguments', async (t) => {
  const runner = await setup('alice');
  let outgoing;
  t.mock.method(globalThis, 'fetch', async (input, init) => {
    outgoing = await requestDetails(input, init);
    const item = { type: 'function_call', id: 'fc_lookup', call_id: 'call_lookup', name: 'lookup', arguments: '' };
    return sse([
      { type: 'response.output_item.added', output_index: 0, item },
      { type: 'response.function_call_arguments.delta', output_index: 0, delta: '{"city":' },
      { type: 'response.function_call_arguments.delta', output_index: 0, delta: '"Boston"}' },
      { type: 'response.function_call_arguments.done', output_index: 0, arguments: '{"city":"Boston"}' },
      { type: 'response.output_item.done', output_index: 0, item: { ...item, arguments: '{"city":"Boston"}' } },
      { type: 'response.completed', response: { id: 'resp_tool', status: 'completed' } },
    ]);
  });
  const parameters = { type: 'object', properties: { city: { type: 'string' } }, required: ['city'], additionalProperties: false };
  const request = turn('alice');
  request.context.tools = [{ name: 'lookup', description: 'Look up a city', parameters }];
  const result = await runner.run(request);
  assert.equal(result.text, '');
  assert.deepEqual(result.toolCalls, [{ id: 'call_lookup|fc_lookup', name: 'lookup', arguments: { city: 'Boston' } }]);
  assert.equal(outgoing.body.tools.length, 1);
  assert.equal(outgoing.body.tools[0].type, 'function');
  assert.equal(outgoing.body.tools[0].name, 'lookup');
  assert.deepEqual(outgoing.body.tools[0].parameters, parameters);
  assert.equal(result.assistantMessage.role, 'assistant');
  assert.equal(result.assistantMessage.content.find(part => part.type === 'toolCall').id,
    result.toolCalls[0].id);
  request.context.messages.push(result.assistantMessage, {
    role: 'toolResult', toolCallId: result.toolCalls[0].id, toolName: 'lookup',
    content: [{ type: 'text', text: JSON.stringify({ results: [{ city: 'Boston', population: 100 }] }) }],
    isError: false, timestamp: Date.now(),
  });
  t.mock.method(globalThis, 'fetch', async (input, init) => {
    outgoing = await requestDetails(input, init);
    return textResponse('Boston has 100 residents in this result.');
  });
  const analysis = await runner.run(request);
  assert.match(analysis.text, /100 residents/);
  const returned = outgoing.body.input.find(item => item.type === 'function_call_output');
  assert.equal(returned.call_id, 'call_lookup');
  assert.match(returned.output, /population/);
});

test('cancellation reaches the real provider HTTP request', { timeout: 10000 }, async (t) => {
  const runner = await setup('alice');
  const controller = new AbortController();
  let started;
  const fetching = new Promise((resolve) => { started = resolve; });
  let transportAborted = false;
  t.mock.method(globalThis, 'fetch', (input, init) => new Promise((_resolve, reject) => {
    const signal = init?.signal ?? input.signal;
    const abort = () => {
      transportAborted = true;
      reject(new DOMException('Synthetic transport cancellation', 'AbortError'));
    };
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
    started();
  }));
  const pending = runner.run(turn('alice', { signal: controller.signal }));
  const rejected = assert.rejects(pending, (error) => error.code === 'cancelled');
  await fetching;
  controller.abort();
  await rejected;
  assert.equal(transportAborted, true);
  t.mock.method(globalThis, 'fetch', async () => textResponse('Recovered'));
  assert.equal((await runner.run(turn('alice'))).text, 'Recovered');
});

test('missing stored credentials never fall back to ambient OpenAI credentials', async (t) => {
  const previous = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = 'synthetic-ambient-key-must-not-be-used';
  t.after(() => {
    if (previous === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = previous;
  });
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Unexpected outbound request'); });
  const vault = new CredentialVault();
  const runner = new TurnRunner({ vault });
  await assert.rejects(runner.run(turn('alice')), { code: 'credentials_required' });
  // Also pass the vault's presence check: Pi must not resolve an empty saved
  // credential through the process environment.
  await vault.put('alice', 'personal', 'openai', { type: 'api_key' });
  await assert.rejects(runner.run(turn('alice')));
  assert.equal(fetch.mock.callCount(), 0);
});

test('permission revision changes during a real provider request reject the response', async (t) => {
  const runner = await setup('alice');
  let authorized = true;
  const checks = [];
  t.mock.method(globalThis, 'fetch', async () => {
    authorized = false;
    return textResponse('Response for stale permissions');
  });
  await assert.rejects(runner.run(turn('alice', {
    isAuthorized: async () => { checks.push(authorized); return authorized; },
  })), { code: 'permission_changed' });
  assert.deepEqual(checks, [true, false]);
});

test('unadvertised provider calls stay inert and receive the server denial on continuation', async (t) => {
  const runner = await setup('alice');
  const fetch = t.mock.method(globalThis, 'fetch', async () => sse([
    { type: 'response.output_item.done', output_index: 0, item: {
      type: 'function_call', id: 'fc_unauthorized', call_id: 'call_unauthorized',
      name: 'delete_everything', arguments: '{}',
    } },
    { type: 'response.completed', response: { id: 'resp_denied', status: 'completed' } },
  ]));
  const request = turn('alice');
  const result = await runner.run(request);
  assert.deepEqual(result.toolCalls, [{ id: 'call_unauthorized|fc_unauthorized',
    name: 'delete_everything', arguments: {} }]);
  // Only inference ran: there is no executable tool callback in this runtime.
  assert.equal(fetch.mock.callCount(), 1);
  assert.deepEqual(request.context.tools, []);
  request.context.messages.push(result.assistantMessage, {
    role: 'toolResult', toolCallId: result.toolCalls[0].id, toolName: 'delete_everything',
    content: [{ type: 'text', text: JSON.stringify({ error: 'tool_not_available',
      message: 'This tool is unavailable. Use only the advertised Schemii tools.' }) }],
    isError: true, timestamp: Date.now(),
  });
  let outgoing;
  fetch.mock.mockImplementation(async (input, init) => {
    outgoing = await requestDetails(input, init);
    return textResponse('That tool is unavailable. No change was made.');
  });
  const next = await runner.run(request);
  assert.match(next.text, /No change was made/);
  const denial = outgoing.body.input.find(item => item.type === 'function_call_output');
  assert.equal(denial.call_id, 'call_unauthorized');
  assert.match(denial.output, /tool_not_available/);
  assert.ok(!outgoing.body.tools?.length);
});

test('capacity rejects excess work and recovers its slot after cancellation', { timeout: 10000 }, async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', 'openai', { type: 'api_key', key: 'synthetic-key-alice' });
  const runner = new TurnRunner({ vault, maxConcurrent: 1, maxPerOwner: 1 });
  const controller = new AbortController();
  let started;
  const fetching = new Promise((resolve) => { started = resolve; });
  const fetch = t.mock.method(globalThis, 'fetch', (_input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true });
    started();
  }));
  const pending = runner.run(turn('alice', { signal: controller.signal }));
  const rejected = assert.rejects(pending, { code: 'cancelled' });
  await fetching;
  await assert.rejects(runner.run(turn('alice')), { code: 'busy' });
  assert.equal(fetch.mock.callCount(), 1);
  controller.abort();
  await rejected;
  fetch.mock.mockImplementation(async () => textResponse('Slot available'));
  assert.equal((await runner.run(turn('alice'))).text, 'Slot available');
  assert.equal(fetch.mock.callCount(), 2);
});

test('malicious provider error text cannot expose credentials to the caller', async (t) => {
  const runner = await setup('alice');
  const secret = 'synthetic-key-alice';
  t.mock.method(globalThis, 'fetch', async () => sse([
    { type: 'response.failed', response: { status: 'failed', error: { code: 'invalid_api_key', message: `Rejected secret ${secret}` } } },
  ]));
  await assert.rejects(runner.run(turn('alice')), (error) => {
    assert.equal(error.code, 'provider_failed');
    assert.equal(`${error.stack}\n${JSON.stringify(error)}`.includes(secret), false);
    assert.equal(error.cause, undefined);
    return true;
  });
});

test('timeout aborts transport and returns a useful safe error', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', 'openai', { type: 'api_key', key: 'synthetic-key' });
  const runner = new TurnRunner({ vault, timeoutMs: 40 });
  let aborted = false;
  t.mock.method(globalThis, 'fetch', (_input, init) => new Promise((_resolve, reject) => {
    const abort = () => { aborted = true; reject(new DOMException('Aborted', 'AbortError')); };
    if (init.signal.aborted) abort();
    else init.signal.addEventListener('abort', abort, { once: true });
  }));
  await assert.rejects(runner.run(turn('alice')), { code: 'timeout' });
  assert.equal(aborted, true);
});

test('context and streamed output limits fail explicitly', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', 'openai', { type: 'api_key', key: 'synthetic-key' });
  const fetch = t.mock.method(globalThis, 'fetch', async () => textResponse('x'.repeat(2048)));
  await assert.rejects(new TurnRunner({ vault, contextBytes: 10 }).run(turn('alice')), { code: 'context_too_large' });
  assert.equal(fetch.mock.callCount(), 0);
  await assert.rejects(new TurnRunner({ vault, responseBytes: 1024 }).run(turn('alice')), { code: 'response_too_large' });
});

test('personal credentials missing a key cannot borrow an ambient key', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', 'openai', { type: 'api_key' });
  const previous = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = 'synthetic-shared-key';
  t.after(() => {
    if (previous === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = previous;
  });
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Must not send'); });
  await assert.rejects(new TurnRunner({ vault }).run(turn('alice')), { code: 'provider_failed' });
  assert.equal(fetch.mock.callCount(), 0);
});
