import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { assertProviderAvailable } from './prerequisites.mjs';

const policy = { providerId: 'instance-codex', modelId: 'gpt-6-luna', reasoningEffort: 'default' };
const ready = { providers: [{ id: 'instance-codex', authenticated: true, available: true,
  models: [{ id: 'gpt-6-luna', status: 'active', reasoningLevels: ['default'] }] }] };

test('chat preflight accepts an authenticated active model and reasoning level', () => {
  assert.doesNotThrow(() => assertProviderAvailable(ready, policy));
});

test('chat preflight rejects missing grants, inactive models, and unavailable reasoning', () => {
  for (const status of [
    { providers: [] },
    { providers: [{ ...ready.providers[0], authenticated: false }] },
    { providers: [{ ...ready.providers[0], available: false }] },
    { providers: [{ ...ready.providers[0], models: [{ ...ready.providers[0].models[0], status: 'unavailable' }] }] },
  ]) assert.throws(() => assertProviderAvailable(status, policy), /Chat prerequisite unavailable/);
  assert.throws(() => assertProviderAvailable(ready, { ...policy, reasoningEffort: 'high' }), /Chat prerequisite unavailable/);
  assert.throws(() => assertProviderAvailable(ready, { modelId: 'gpt-6-luna' }), /providerId and modelId/);
});
