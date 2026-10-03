import { test } from 'node:test';
import assert from 'node:assert/strict';
import { metadata } from './timing.mjs';

test('browser timing preserves unsharded records and supports bounded six-way indices', () => {
  for (const project of ['desktop-chromium', 'android-chromium']) {
    for (const shard of [0, 1, 2, 3, 4, 5, 6]) assert.equal(metadata('browser', project, shard).shard, shard);
    for (const shard of [-1, 7, 1.5, NaN, Infinity, '6', null]) {
      assert.throws(() => metadata('browser', project, shard));
    }
  }
  assert.throws(() => metadata('browser'));
});

test('ordinary lane timing cannot impersonate a browser device or shard', () => {
  for (const lane of ['node', 'python', 'postgres']) {
    assert.equal(metadata(lane).project, 'none');
    assert.throws(() => metadata(lane, 'desktop-chromium', 0));
    for (const shard of [1, 3, 6]) assert.throws(() => metadata(lane, 'none', shard));
  }
});
