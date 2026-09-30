import { test } from 'node:test';
import assert from 'node:assert/strict';
import { metadata } from './timing.mjs';

test('three-way browser timing retains exact devices and rejects unknown shards', () => {
  for (const project of ['desktop-chromium', 'android-chromium']) {
    for (const shard of [0, 1, 2, 3]) assert.equal(metadata('browser', project, shard).shard, shard);
    assert.throws(() => metadata('browser', project, 4));
  }
  assert.throws(() => metadata('browser'));
});

test('ordinary lane timing cannot impersonate a browser device or shard', () => {
  for (const lane of ['node', 'python', 'postgres']) {
    assert.equal(metadata(lane).project, 'none');
    assert.throws(() => metadata(lane, 'desktop-chromium', 0));
    assert.throws(() => metadata(lane, 'none', 3));
  }
});
