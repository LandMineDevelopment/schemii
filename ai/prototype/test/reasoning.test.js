import assert from 'node:assert/strict';
import test from 'node:test';
import { getSupportedThinkingLevels } from '@earendil-works/pi-ai';
import { providers } from '../runtime.js';
import { supportedModels } from '../turns.js';

test('model reasoning catalog uses installed SDK definitions including model-specific exclusions', () => {
  const catalog = supportedModels().models;
  for (const [providerId, factory] of Object.entries(providers)) for (const model of factory().getModels()) {
    const levels = catalog.find(item => item.providerId === providerId && item.id === model.id).reasoningLevels;
    assert.equal(levels[0], 'default');
    for (const level of levels.slice(1)) assert.ok(getSupportedThinkingLevels(model).includes(level));
    assert.deepEqual(levels.filter(level => !['default', 'off'].includes(level)), getSupportedThinkingLevels(model).filter(level => level !== 'off'));
  }
  const plain = catalog.find(model => model.providerId === 'openai' && model.id === 'gpt-4o-mini');
  assert.deepEqual(plain.reasoningLevels, ['default', 'off']);
});
