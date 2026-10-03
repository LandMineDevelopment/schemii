import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { balanceFiles, coverageProfile, manifestPattern, PROJECTS } from './browser-shards.mjs';
import { invocation, parseOptions, run, scopedInventory } from './run-browser-shard.mjs';

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

test('six-way whole-file plans preserve every file, deterministic balance and nonempty zero-cost lanes', () => {
  const files = Array.from({ length: 12 }, (_, index) => file(String(index).padStart(2, '0')));
  const costs = Object.fromEntries(files.map((path, index) => [path, { 'desktop-chromium': 12 - index }]));
  const plan = balanceFiles(files, PROJECTS[0], costs, 6);
  assert.deepEqual(plan.shards.map(shard => shard.estimatedMs), Array(6).fill(13));
  assert.deepEqual(plan, balanceFiles(files.toReversed(), PROJECTS[0], costs, 6));
  assert.equal(plan.shards.length, 6);
  assert.ok(plan.shards.every(shard => shard.files.length === 2));
  assert.deepEqual(plan.shards.flatMap(shard => shard.files).sort(), files);
  const zeroCosts = Object.fromEntries(files.map(path => [path, { 'desktop-chromium': 0 }]));
  assert.deepEqual(balanceFiles(files, PROJECTS[0], zeroCosts, 6).shards.map(shard => shard.files),
    Array.from({ length: 6 }, (_, index) => [files[index], files[index + 6]]));
  const unknown = balanceFiles(files, PROJECTS[0], {}, 6);
  assert.deepEqual(unknown.unknownFiles, files);
  assert.deepEqual(unknown.shards.flatMap(shard => shard.files).sort(), files);
  assert.throws(() => balanceFiles(files.slice(0, 5), PROJECTS[0], costs, 6), /at least one file/);
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
  for (const count of [0, 1, 4, 5, 7, 2.5, NaN, Infinity, '3', '6', null]) {
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
  for (const shard of [1, 2, 3, 4, 5, 6]) {
    const six = invocation(PROJECTS[0], [file('a')], shard, true, { PRESERVE: 'unchanged' }, 6);
    assert.equal(six.args.some(value => value.startsWith('--shard') || value.startsWith('--workers')), false);
    assert.equal(six.env.CI_TELEMETRY_SHARD, String(shard));
    assert.equal(six.env.PRESERVE, 'unchanged');
    assert.deepEqual(parseOptions([`--project=${PROJECTS[0]}`, `--shard=${shard}/6`, '--list']),
      { project: PROJECTS[0], shard, shardCount: 6, list: true, plan: false });
  }
  for (const shard of ['0/3', '4/3', '3/2', '1/1', '1/4', '1/5', '7/6', '1/7', '01/3', '1/03', 'NaN/3', '1.5/3']) {
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

test('explicit hosted profiles reject the wrong topology before discovery', () => {
  for (const project of PROJECTS) for (const profile of ['full', 'e2e-tests']) {
    for (const shard of [1, 6]) {
      assert.deepEqual(parseOptions([`--project=${project}`, `--shard=${shard}/6`, `--profile=${profile}`, '--plan']),
        { project, shard, shardCount: 6, profile, plan: true, list: false });
    }
    for (const shardCount of [2, 3]) {
      assert.throws(() => parseOptions([`--project=${project}`, `--shard=1/${shardCount}`, `--profile=${profile}`]), /all six/);
      assert.throws(() => run({ project, shard: 1, shardCount, profile }, '/not-a-checkout'), /all six/);
    }
    assert.throws(() => run({ project, shard: 1, profile }, '/not-a-checkout'), /all six/);
  }
  for (const shardCount of [2, 6]) {
    assert.throws(() => parseOptions([`--project=${PROJECTS[0]}`, `--shard=1/${shardCount}`, '--profile=schemer-result-cache']), /all three/);
    assert.throws(() => run({ project: PROJECTS[0], shard: 1, shardCount, profile: 'schemer-result-cache' }, '/not-a-checkout'), /all three/);
  }
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

const inspectionProfile = 'developer-inspection';
test('inspection profile preserves both whole files with one isolated stack per device', () => {
  const policy = coverageProfile(inspectionProfile);
  assert.deepEqual(policy.files, ['tests/e2e/shared-ui-audit.spec.js', 'tests/e2e/ai-diagnostic-permissions.spec.js']);
  for (const project of PROJECTS) {
    assert.deepEqual(policy.browser[project].shards, [policy.files]);
    assert.equal(Object.values(policy.browser[project].files).flat().length, 7);
    assert.deepEqual(policy.browser[project].allowed_skips, []);
    assert.deepEqual(parseOptions([`--project=${project}`, '--shard=1/1', `--profile=${inspectionProfile}`]),
      {project, shard:1, shardCount:1, profile:inspectionProfile, list:false, plan:false});
    const command = invocation(project, policy.files, 1, false, {}, 1);
    assert.deepEqual(JSON.parse(command.env.SCHEMII_E2E_FILE_MANIFEST), policy.files);
    assert.equal(command.env.CI_TELEMETRY_SHARD, '1');
    assert.equal(command.args.some(arg => arg.startsWith('--workers') || arg.startsWith('--shard')), false);
    for (const total of [2,3,6]) {
      assert.throws(() => parseOptions([`--project=${project}`, `--shard=1/${total}`, `--profile=${inspectionProfile}`]), /one browser shard/);
      assert.throws(() => run({project,shard:1,shardCount:total,profile:inspectionProfile}, '/nonexistent'), /one browser shard/);
    }
    for (const extra of ['--grep=one', '--grep-invert=one', '--file=other', '--shard=1/1']) {
      assert.throws(() => parseOptions([`--project=${project}`, '--shard=1/1', `--profile=${inspectionProfile}`, extra]));
    }
    for (const profile of ['full','e2e-tests','schemer-result-cache']) {
      assert.throws(() => parseOptions([`--project=${project}`, '--shard=1/1', `--profile=${profile}`]));
    }
    assert.throws(() => parseOptions([`--project=${project}`, '--shard=1/1']));
  }
});

test('inspection policy rejects changed frozen source, topology, cases or allowed skips', () => {
  const policy = coverageProfile(inspectionProfile);
  for (const damage of ['file','case','extra-case','shard','skip','hash','project']) {
    const changed = structuredClone(policy);
    const inventory = changed.browser[PROJECTS[0]];
    if (damage === 'file') changed.files.pop();
    if (damage === 'case') inventory.files[changed.files[0]].pop();
    if (damage === 'extra-case') inventory.files[changed.files[0]].push('f'.repeat(64));
    if (damage === 'shard') inventory.shards.push(inventory.shards[0]);
    if (damage === 'skip') inventory.allowed_skips.push('f'.repeat(64));
    if (damage === 'hash') changed.frozen_sha256[changed.files[0]] = 'f'.repeat(64);
    if (damage === 'project') delete changed.browser[PROJECTS[1]];
    assert.throws(() => coverageProfile(inspectionProfile, changed));
  }
});

test('inspection unfiltered discovery requires every fixed project case exactly once', () => {
  for (const project of PROJECTS) {
    const policy = structuredClone(coverageProfile(inspectionProfile));
    const specs = policy.files.flatMap((file, fileIndex) => Array.from({length:[5,2][fileIndex]}, (_,index) => ({file, id:`fixture-${project}-${file}-${index}`, tests:[{projectName:project}]})));
    for (const file of policy.files) policy.browser[project].files[file] = specs.filter(spec => spec.file === file).map(spec => createHash('sha256').update(spec.id).digest('hex')).sort();
    const report = {config:{rootDir:'/owned'}, suites:[{specs}]};
    assert.deepEqual(scopedInventory(report, '/owned', project, policy), [policy.files]);
    for (const damage of ['missing','extra','duplicate','project','error']) {
      const changed = structuredClone(report);
      const found = changed.suites[0].specs;
      if (damage === 'missing') found.pop();
      if (damage === 'extra') found.push({...found[0],id:'foreign-extra-case'});
      if (damage === 'duplicate') found.push(structuredClone(found[0]));
      if (damage === 'project') found[0].tests[0].projectName = 'unknown';
      if (damage === 'error') changed.errors = [{message:'synthetic failure'}];
      assert.throws(() => scopedInventory(changed, '/owned', project, policy));
    }
  }
});
