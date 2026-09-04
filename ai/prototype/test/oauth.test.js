import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createModels } from '@earendil-works/pi-ai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';
import { CredentialVault, TurnRunner } from '../runtime.js';

const providerId = 'openai-codex';
const noAmbient = { env: async () => undefined, fileExists: async () => false };
const expired = (owner) => ({ type: 'oauth', access: 'expired', refresh: `refresh-${owner}`, expires: 0 });
const accessToken = (owner) => `header.${Buffer.from(JSON.stringify({
  'https://api.openai.com/auth': { chatgpt_account_id: owner },
})).toString('base64url')}.signature`;
const refreshed = (owner) => ({ access_token: accessToken(owner), refresh_token: `rotated-${owner}`, expires_in: 3600 });
function collection(vault, owner, credentialId = 'personal') {
  const models = createModels({ credentials: vault.scope(owner, credentialId), authContext: noAmbient });
  models.setProvider(openaiCodexProvider());
  return models;
}

test('real Pi OAuth refresh: concurrent collections share only their owner/credential lock', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', providerId, expired('alice'));
  await vault.put('bob', 'personal', providerId, expired('bob'));
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(String(url), 'https://auth.openai.com/oauth/token');
    const form = new URLSearchParams(options.body);
    assert.equal(form.get('grant_type'), 'refresh_token');
    const owner = form.get('refresh_token').replace('refresh-', '');
    calls.push(owner);
    await new Promise(resolve => setImmediate(resolve));
    return Response.json(refreshed(owner));
  });
  const owners = ['alice', 'bob', 'alice', 'bob', 'alice', 'alice'];
  const results = await Promise.all(owners.map(owner => collection(vault, owner).getAuth(providerId)));
  assert.deepEqual(calls.sort(), ['alice', 'bob']);
  results.forEach((result, index) => assert.equal(result.auth.apiKey, accessToken(owners[index])));
  assert.equal((await vault.scope('alice', 'personal').read(providerId)).refresh, 'rotated-alice');
  assert.equal((await vault.scope('bob', 'personal').read(providerId)).refresh, 'rotated-bob');
});

test('two identities belonging to the same user do not share credentials', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'work', providerId, expired('work'));
  await vault.put('alice', 'personal', providerId, expired('personal'));
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const identity = new URLSearchParams(options.body).get('refresh_token').replace('refresh-', '');
    return Response.json(refreshed(identity));
  });
  const [work, personal] = await Promise.all([
    collection(vault, 'alice', 'work').getAuth(providerId),
    collection(vault, 'alice', 'personal').getAuth(providerId),
  ]);
  assert.equal(work.auth.apiKey, accessToken('work'));
  assert.equal(personal.auth.apiKey, accessToken('personal'));
});

test('logout during refresh is serialized and cannot resurrect the credential', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', providerId, expired('alice'));
  const started = Promise.withResolvers();
  const release = Promise.withResolvers();
  t.mock.method(globalThis, 'fetch', async () => {
    started.resolve();
    await release.promise;
    return Response.json(refreshed('alice'));
  });
  const running = collection(vault, 'alice').getAuth(providerId);
  await started.promise;
  const removal = vault.remove('alice', 'personal', providerId);
  release.resolve();
  await Promise.all([running, removal]);
  assert.equal(await vault.scope('alice', 'personal').read(providerId), undefined);
  assert.equal(await collection(vault, 'alice').getAuth(providerId), undefined);
});

test('new credentials saved during refresh replace the old refresh result', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', providerId, expired('old'));
  const started = Promise.withResolvers();
  const release = Promise.withResolvers();
  t.mock.method(globalThis, 'fetch', async () => {
    started.resolve();
    await release.promise;
    return Response.json(refreshed('old'));
  });
  const running = collection(vault, 'alice').getAuth(providerId);
  await started.promise;
  const replacement = { ...expired('new'), access: accessToken('new'), expires: Date.now() + 3600_000 };
  const save = vault.put('alice', 'personal', providerId, replacement);
  release.resolve();
  await Promise.all([running, save]);
  assert.equal((await collection(vault, 'alice').getAuth(providerId)).auth.apiKey, accessToken('new'));
});

test('malformed real OAuth token response is sanitized at the prototype boundary', async (t) => {
  const vault = new CredentialVault();
  await vault.put('alice', 'personal', providerId, expired('alice'));
  t.mock.method(globalThis, 'fetch', async () => Response.json({
    access_token: 'SECRET-ACCESS', refresh_token: 'SECRET-REFRESH', // missing expires_in
  }));
  const runner = new TurnRunner({ vault });
  await assert.rejects(runner.run({
    owner: 'alice', credentialId: 'personal', providerId,
    modelId: openaiCodexProvider().getModels()[0].id,
    context: { messages: [{ role: 'user', content: 'hello', timestamp: 0 }] },
  }), error => {
    assert.equal(error.code, 'provider_failed');
    assert.equal(error.cause, undefined);
    assert.doesNotMatch(`${error.stack} ${JSON.stringify(error)}`, /SECRET|refresh-alice/);
    return true;
  });
  assert.equal((await vault.scope('alice', 'personal').read(providerId)).refresh, 'refresh-alice');
});
