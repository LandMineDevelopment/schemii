import assert from 'node:assert/strict';
import test from 'node:test';
import { reasoningLevels, reasoningForModel, reasoningLabel, managedProviderForModel, reasoningForSelection } from '../../src/schemii/common/web/assets/ai-reasoning.js';

test('reasoning choices use only advertised supported levels in stable order', () => {
  assert.deepEqual(reasoningLevels({ reasoning: true }), ['default']);
  assert.deepEqual(reasoningLevels({ reasoningLevels: ['high', 'low', 'max', 'off', 'invalid', 'low'] }), ['default', 'off', 'low', 'high', 'max']);
  assert.deepEqual(reasoningLevels(null), ['default']);
});
test('switching models retains supported effort and otherwise returns to model default', () => {
  assert.equal(reasoningForModel({ reasoningLevels: ['low', 'high'] }, 'high'), 'high');
  assert.equal(reasoningForModel({ reasoningLevels: ['low'] }, 'high'), 'default');
  assert.equal(reasoningForModel(null, 'max'), 'default');
});

test('administrator policy fixes shared Codex reasoning without changing personal providers', () => {
  const status = { providers: [
    { id: 'instance-codex', adminManaged: true, selectedReasoningEffort: 'high' },
    { id: 'openai-codex', authenticated: true },
  ] };
  const model = { providerId: 'instance-codex', reasoningLevels: ['low', 'high'] };
  assert.equal(managedProviderForModel(status, model)?.selectedReasoningEffort, 'high');
  assert.equal(reasoningForSelection(status, model, 'low'), 'high');
  assert.equal(reasoningForSelection(status, { providerId: 'openai-codex', reasoningLevels: ['low'] }, 'low'), 'low');
  assert.equal(reasoningLabel('high'), 'High');
});

test('shared Codex uses each model’s granted reasoning levels when a role has multiple policies', () => {
  const status = { providers: [{ id: 'instance-codex', adminManaged: true }] };
  const luna = { id: 'gpt-6-luna', providerId: 'instance-codex', reasoningLevels: ['default'] };
  const other = { id: 'gpt-5.5', providerId: 'instance-codex', reasoningLevels: ['medium'] };
  const flexible = { id: 'gpt-6-sol', providerId: 'instance-codex', reasoningLevels: ['low', 'high'] };
  assert.equal(reasoningForSelection(status, luna, 'medium'), 'default');
  assert.equal(reasoningForSelection(status, other, 'default'), 'medium');
  assert.equal(reasoningForSelection(status, flexible, 'high'), 'high');
  assert.equal(reasoningForSelection(status, flexible, 'default'), 'low');
});
