import assert from 'node:assert/strict';
import test from 'node:test';
import { reasoningLevels, reasoningForModel } from '../../src/schemii/common/web/assets/ai-reasoning.js';

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
