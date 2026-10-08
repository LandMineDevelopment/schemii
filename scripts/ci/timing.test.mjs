import { test } from 'node:test';
import assert from 'node:assert/strict';
import { metadata, originalSourceRoot, writer } from './timing.mjs';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

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

test('exclusive timing refuses overwrite and symlink ancestors before creating directories', () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-exclusive-timing-'));
  try {
    const file = join(directory, 'receipt.jsonl');
    const write = writer(file, metadata('browser', 'desktop-chromium', 1), {exclusive:true});
    write({kind:'start', planned:1});
    const original = readFileSync(file, 'utf8');
    assert.throws(() => writer(file, {}, {exclusive:true}), /EEXIST/);
    assert.equal(readFileSync(file, 'utf8'), original);
    mkdirSync(join(directory, 'target'));
    symlinkSync(join(directory, 'target'), join(directory, 'link'));
    assert.throws(() => writer(join(directory, 'link', 'new', 'receipt.jsonl'), {}, {exclusive:true}), /Unsafe timing/);
    assert.equal(existsSync(join(directory, 'target', 'new')), false);
    assert.equal(originalSourceRoot({}), undefined);
    assert.throws(() => originalSourceRoot({SCHEMII_E2E_SOURCE_ROOT: directory}), /Invalid original/);
    assert.throws(() => originalSourceRoot({SCHEMII_E2E_SOURCE_ROOT: '/arbitrary/path'}), /Invalid original/);
  } finally { rmSync(directory, {recursive:true, force:true}); }
});
