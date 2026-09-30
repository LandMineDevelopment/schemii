import assert from 'node:assert/strict';
import test from 'node:test';
import { balanceFiles, manifestPattern, PROJECTS } from './browser-shards.mjs';
import { invocation, parseOptions } from './run-browser-shard.mjs';

const file = name => `tests/e2e/${name}.spec.js`;

test('whole files balance deterministically with exact disjoint coverage', () => {
  const files = ['a', 'b', 'c', 'd'].map(file);
  const costs = Object.fromEntries(files.map((path, i) => [path, { 'desktop-chromium': 6 - i }]));
  const plan = balanceFiles(files, PROJECTS[0], costs);
  assert.deepEqual(plan.shards.map(shard => shard.estimatedMs), [9, 9]);
  assert.deepEqual(plan, balanceFiles(files.toReversed(), PROJECTS[0], costs));
  assert.equal(new Set(plan.shards.flatMap(shard => shard.files)).size, files.length);
  assert.deepEqual(plan.shards.flatMap(shard => shard.files).sort(), files);
});

test('new files receive a profile-specific median estimate and remain scheduled', () => {
  const costs = { [file('a')]: { 'desktop-chromium': 3000, 'android-chromium': 6000 } };
  for (const project of PROJECTS) {
    const plan = balanceFiles([file('a'), file('new')], project, costs);
    assert.deepEqual(plan.unknownFiles, [file('new')]);
    assert.equal(plan.fallbackMs, costs[file('a')][project]);
    assert.equal(plan.shards.flatMap(shard => shard.files).length, 2);
  }
});

test('manifest filtering uses exact literal files, including regular-expression metacharacters', () => {
  const selected = 'tests/e2e/example[1]+.spec.js';
  const pattern = manifestPattern(JSON.stringify([selected]));
  assert.equal(pattern.test(`/checkout/${selected}`), true);
  for (const other of ['tests/e2e/example1.spec.js', selected + '.old', 'tests/e2e/example[1]+xspec.js']) {
    assert.equal(pattern.test(`/checkout/${other}`), false);
  }
  for (const values of [[], [file('a'), file('a')], ['tests/e2e/../outside.spec.js'], ['/tmp/a.spec.js']]) {
    assert.throws(() => manifestPattern(JSON.stringify(values)));
  }
});

test('custom shard invocation never forwards native sharding or increases concurrency', () => {
  const command = invocation(PROJECTS[0], [file('a')], 2, false, { PRESERVE: 'unchanged' });
  assert.equal(command.args.some(value => value.startsWith('--shard') || value.startsWith('--workers')), false);
  assert.equal(command.env.CI_TELEMETRY_SHARD, '2');
  assert.equal(command.env.PRESERVE, 'unchanged');
  assert.equal(command.env.SCHEMII_E2E_FILE_MANIFEST, JSON.stringify([file('a')]));
  assert.deepEqual(parseOptions(['--project=desktop-chromium', '--shard=2/2', '--plan']),
    { project: PROJECTS[0], shard: 2, plan: true, list: false });
  assert.throws(() => parseOptions(['--project=desktop-chromium', '--shard=2/3']));
  assert.throws(() => parseOptions(['--project=desktop-chromium', '--shard=1/2', '--shard=2/2']));
});
