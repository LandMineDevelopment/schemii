import assert from 'node:assert/strict';
import test from 'node:test';
import { balanceFiles, coverageProfile, manifestPattern, PROJECTS } from './browser-shards.mjs';
import { invocation, parseOptions, run } from './run-browser-shard.mjs';

const file = name => `tests/e2e/${name}.spec.js`;

test('whole files balance deterministically with exact disjoint coverage', () => {
  const files = ['a', 'b', 'c', 'd'].map(file);
  const costs = Object.fromEntries(files.map((path, i) => [path, { 'desktop-chromium': 6 - i }]));
  const plan = balanceFiles(files, PROJECTS[0], costs);
  assert.deepEqual(plan, balanceFiles(files, PROJECTS[0], costs, 2));
  assert.deepEqual(plan.shards.map(shard => shard.estimatedMs), [9, 9]);
  assert.deepEqual(plan, balanceFiles(files.toReversed(), PROJECTS[0], costs));
  assert.equal(new Set(plan.shards.flatMap(shard => shard.files)).size, files.length);
  assert.deepEqual(plan.shards.flatMap(shard => shard.files).sort(), files);
});

test('three-way plans retain deterministic cost, file-count and shard-index ordering', () => {
  const files = ['a', 'b', 'c', 'd', 'e', 'f'].map(file);
  const costs = Object.fromEntries(files.map((path, i) => [path, { 'desktop-chromium': 8 - i }]));
  const plan = balanceFiles(files, PROJECTS[0], costs, 3);
  assert.deepEqual(plan.shards.map(shard => shard.estimatedMs), [11, 11, 11]);
  assert.deepEqual(plan.shards.map(shard => shard.files), [[file('a'), file('f')], [file('b'), file('e')], [file('c'), file('d')]]);
  assert.deepEqual(plan, balanceFiles(files.toReversed(), PROJECTS[0], costs, 3));
  const zeroCosts = Object.fromEntries(files.map(path => [path, { 'desktop-chromium': 0 }]));
  assert.deepEqual(balanceFiles(files, PROJECTS[0], zeroCosts, 3).shards.map(shard => shard.files),
    [[file('a'), file('d')], [file('b'), file('e')], [file('c'), file('f')]]);
});

test('new files receive a profile-specific median estimate and remain scheduled', () => {
  const costs = { [file('a')]: { 'desktop-chromium': 3000, 'android-chromium': 6000 } };
  for (const project of PROJECTS) {
    const plan = balanceFiles([file('a'), file('new')], project, costs);
    assert.deepEqual(plan.unknownFiles, [file('new')]);
    assert.equal(plan.fallbackMs, costs[file('a')][project]);
    assert.equal(plan.shards.flatMap(shard => shard.files).length, 2);
    const three = balanceFiles([file('a'), file('new'), file('newer')], project, costs, 3);
    assert.deepEqual(three.unknownFiles, [file('new'), file('newer')]);
    assert.ok(three.shards.every(shard => shard.files.length === 1));
    assert.deepEqual(three.shards.flatMap(shard => shard.files).sort(), [file('a'), file('new'), file('newer')]);
    assert.deepEqual(three.shards.map(shard => shard.estimatedMs), [plan.fallbackMs, plan.fallbackMs, plan.fallbackMs]);
  }
});

test('plans reject unsupported counts, duplicate files and insufficient nonempty shards', () => {
  const files = ['a', 'b', 'c'].map(file);
  for (const count of [0, 1, 4, 2.5, NaN, Infinity, '3', null]) {
    assert.throws(() => balanceFiles(files, PROJECTS[0], {}, count), /shard count/);
  }
  for (const selected of [[], [file('a'), file('a'), file('b')]]) {
    assert.throws(() => balanceFiles(selected, PROJECTS[0], {}, 3), /nonempty and unique/);
  }
  assert.throws(() => balanceFiles(files.slice(0, 2), PROJECTS[0], {}, 3), /at least one file/);
  assert.throws(() => balanceFiles([file('a')], PROJECTS[0]), /at least one file/);
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
  for (const shard of [1, 2, 3]) {
    const three = invocation(PROJECTS[0], [file('a')], shard, true, { PRESERVE: 'unchanged' }, 3);
    assert.equal(three.args.some(value => value.startsWith('--shard') || value.startsWith('--workers')), false);
    assert.equal(three.env.CI_TELEMETRY_SHARD, String(shard));
    assert.equal(three.env.PRESERVE, 'unchanged');
    assert.deepEqual(parseOptions([`--project=${PROJECTS[0]}`, `--shard=${shard}/3`, '--list']),
      { project: PROJECTS[0], shard, shardCount: 3, list: true, plan: false });
  }
  for (const shard of ['0/3', '4/3', '3/2', '1/1', '1/4', '01/3', '1/03', 'NaN/3', '1.5/3']) {
    assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, `--shard=${shard}`]));
  }
  for (const [current, total] of [[3, 2], [4, 3], [0, 3], [1.5, 3], [1, '3'], [1, 4]]) {
    assert.throws(() => invocation(PROJECTS[0], [file('a')], current, true, {}, total));
  }
  for (const files of [[], [file('a'), file('a')], ['tests/e2e/../outside.spec.js']]) {
    assert.throws(() => invocation(PROJECTS[0], files, 1, true, {}, 3));
  }
  assert.throws(() => parseOptions(['--project=desktop-chromium', '--shard=1/2', '--shard=2/2']));
  assert.throws(() => parseOptions(['--project=desktop-chromium', '--shard=1/3', '--shard=2/3']));
  assert.throws(() => parseOptions(['--project=desktop-chromium', '--shard=1/3', '--list', '--plan']));
});

test('programmatic invalid plans fail before starting discovery', () => {
  for (const options of [
    { project: PROJECTS[0], shard: 3 },
    { project: PROJECTS[0], shard: 4, shardCount: 3 },
    { project: PROJECTS[0], shard: 1, shardCount: 4 },
    { project: PROJECTS[0], shard: 1, shardCount: null },
    { project: 'unknown', shard: 1, shardCount: 3 },
  ]) {
    assert.throws(() => run(options, '/nonexistent-browser-plan'), /Invalid browser|shard count/);
  }
});

const cacheProfile = 'schemer-result-cache';

test('cache profile policy and arguments reject shortened scope, unknown profiles and filters', () => {
  const policy = coverageProfile(cacheProfile);
  for (const damage of ['schema', 'missing', 'shard', 'case', 'skip', 'project']) {
    const changed = structuredClone(policy);
    const inventory = changed.browser[PROJECTS[0]];
    if (damage === 'schema') changed.schema = true;
    if (damage === 'missing') changed.files.pop();
    if (damage === 'shard') inventory.shards[1] = inventory.shards[0];
    if (damage === 'case') Object.values(inventory.files)[0].length = 0;
    if (damage === 'skip') inventory.allowed_skips.push('a'.repeat(64));
    if (damage === 'project') delete changed.browser[PROJECTS[1]];
    assert.throws(() => coverageProfile(cacheProfile, changed));
  }
  assert.deepEqual(parseOptions([`--project=${PROJECTS[0]}`, '--shard=1/3', `--profile=${cacheProfile}`, '--plan']), { project: PROJECTS[0], shard: 1, shardCount: 3, profile: cacheProfile, plan: true, list: false });
  assert.equal(coverageProfile('full'), undefined);
  assert.equal(coverageProfile('e2e-tests'), undefined);
  assert.throws(() => run({ project: PROJECTS[0], shard: 1, shardCount: 3, profile: 'invented' }, '/not-a-checkout'), /Unknown browser coverage profile/);
  for (const extra of ['--grep=one-case', '--grep-invert=other-case', '--file=tests/e2e/accounts.spec.js', '--profile=invented']) {
    assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, '--shard=1/3', extra]));
  }
  assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, '--shard=1/2', `--profile=${cacheProfile}`]), /all three/);
  assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, '--shard=1/3', `--profile=${cacheProfile}`, '--profile=full']));
  assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, '--shard=1/3', '--profile=', '--profile=full']));
  assert.throws(() => run({ project: PROJECTS[0], shard: 1, shardCount: 3, profile: null }, '/not-a-checkout'), /Unknown browser coverage profile/);
});
