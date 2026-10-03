import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { balanceFiles, balanceParallelFiles, parallelFiles, coverageProfile, OBSERVATION, OBSERVED_COSTS, PROJECTS } from '../../scripts/ci/browser-shards.mjs';
import { inventoryFiles, invocation, scopedInventory } from '../../scripts/ci/run-browser-shard.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));

function discover(cwd, command) {
  const environment = { ...process.env };
  delete environment.SCHEMII_E2E_FILE_MANIFEST;
  const args = command?.args || [resolve(root, 'node_modules/@playwright/test/cli.js'), 'test', '--list', '--reporter=json'];
  const result = spawnSync(process.execPath, args, {
    cwd, env: command?.env || environment, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.deepEqual(report.errors, []);
  return report;
}

function cases(report) {
  const found = [];
  function visit(suite) {
    for (const spec of suite.specs || []) for (const attempt of spec.tests || []) {
      found.push({ id: `${attempt.projectName}:${spec.id}`, project: attempt.projectName,
        file: spec.file, tags: spec.tags || [], title: spec.title });
    }
    for (const child of suite.suites || []) visit(child);
  }
  for (const suite of report.suites) visit(suite);
  return found;
}

function proveInventory(cwd, baseline, shardCount = 2, profile = undefined) {
  const required = cases(baseline);
  const actual = [];
  for (const project of PROJECTS) {
    const projectReport = { ...baseline, suites: baseline.suites.filter(suite =>
      cases({ suites: [suite] }).some(item => item.project === project)) };
    const projectFiles = [...new Set(required.filter(item => item.project === project)
      .map(item => `tests/e2e/${item.file}`))].sort();
    const plan = balanceFiles(projectFiles, project, OBSERVED_COSTS, shardCount);
    assert.equal(plan.shards.length, shardCount);
    assert.deepEqual(plan.unknownFiles, projectFiles.filter(file => OBSERVED_COSTS[file]?.[project] === undefined));
    assert.deepEqual(new Set(inventoryFiles(projectReport, cwd)), new Set(projectFiles));
    for (const [index, shard] of plan.shards.entries()) {
      assert.ok(shard.files.length > 0, 'every browser lane must own files');
      // One real CLI leg per device proves discovery clears inherited filters.
      // Other legs exercise the same manifest without rediscovering twice each.
      const command = profile === undefined || index !== shardCount - 1
        ? invocation(project, shard.files, index + 1, true, process.env, shardCount)
        : {
          args: [resolve(root, 'scripts/ci/run-browser-shard.mjs'), `--profile=${profile}`,
            `--project=${project}`, `--shard=${index + 1}/${shardCount}`, '--list'],
          // Full discovery must clear an inherited narrower file selection.
          env: { ...process.env, SCHEMII_E2E_FILE_MANIFEST: JSON.stringify([shard.files[0]]) },
        };
      const selected = discover(cwd, command);
      assert.equal(selected.config.workers, 1);
      assert.equal(selected.config.fullyParallel, false);
      assert.equal(selected.config.shard, null);
      const selectedCases = cases(selected);
      assert.deepEqual(new Set(selectedCases.map(item => `tests/e2e/${item.file}`)), new Set(shard.files));
      actual.push(...selectedCases);
    }
  }
  assert.equal(actual.length, new Set(actual.map(item => item.id)).size, 'a case was assigned twice');
  assert.deepEqual(actual.map(item => item.id).sort(), required.map(item => item.id).sort(), 'a case was lost');
  return required;
}

test('real six-way Playwright discovery proves exact project/test coverage and transport scope', t => {
  const baseline = discover(root);
  const required = proveInventory(root, baseline, 6, 'full');
  // Reuse this real discovery for the opted-in routing proof; another set of
  // CLI collection processes would repeat work without strengthening the union.
  for (const project of PROJECTS) {
    const expected = required.filter(item => item.project === project);
    const files = [...new Set(expected.map(item => `tests/e2e/${item.file}`))];
    const plan = balanceParallelFiles(files, project, OBSERVED_COSTS, 6);
    const routed = [];
    for (const lane of plan.shards) {
      const phases = parallelFiles(lane.files, project);
      const assigned = [...phases.serial, ...phases.parallel.flat()];
      assert.deepEqual([...assigned].sort(), [...lane.files].sort());
      assert.equal(new Set(assigned).size, assigned.length);
      routed.push(...expected.filter(item => assigned.includes(`tests/e2e/${item.file}`)));
    }
    assert.equal(new Set(routed.map(item => item.id)).size, routed.length);
    assert.deepEqual(routed.map(item => item.id).sort(), expected.map(item => item.id).sort());
  }
  t.diagnostic(JSON.stringify(Object.fromEntries(PROJECTS.map(project => {
    const selected = required.filter(item => item.project === project);
    return [project, { files: new Set(selected.map(item => item.file)).size, cases: selected.length, shards: 6 }];
  }))));
  // The pinned JSON reporter removes the leading '@' from tag labels.
  const requests = required.filter(item => item.tags.includes('request-only'));
  assert.equal(requests.length, 7);
  assert.ok(requests.every(item => item.project === 'desktop-chromium'));
  assert.equal(required.filter(item => item.file === 'raw-copy-streaming.spec.js').length, 1);
  for (const path of ['raw-console.spec.js', 'schemoo-model-dependencies-live.spec.js', 'quick-start.spec.js']) {
    assert.ok(required.some(item => item.file === path && item.project === 'android-chromium'));
  }
  assert.match(OBSERVATION.sourceSha, /^[a-f0-9]{40}$/);
  assert.ok(Number.isInteger(OBSERVATION.runId) && OBSERVATION.runId > 0);
  assert.equal(OBSERVATION.runAttempt, 1);
  assert.equal(OBSERVATION.completeBrowserLanes, PROJECTS.length * 2);
  assert.equal(OBSERVATION.wholeWorkflowOutcome, 'success');
  assert.equal(OBSERVATION.costBasis, 'first-attempt-setup+execution+teardown-ms');
  for (const project of PROJECTS) {
    const measured = Object.entries(OBSERVED_COSTS).filter(([, costs]) => costs[project] !== undefined)
      .map(([file, costs]) => [file, costs[project]]).sort(([left], [right]) => left.localeCompare(right));
    const observed = OBSERVATION.profiles[project];
    const discovered = new Set(required.filter(item => item.project === project)
      .map(item => `tests/e2e/${item.file}`));
    assert.equal(measured.length, observed.measuredFiles);
    assert.equal(observed.collectedCases, observed.passed + observed.skipped);
    assert.equal(measured.reduce((sum, [, cost]) => sum + cost, 0), observed.phaseTotalMs);
    assert.equal(createHash('sha256').update(JSON.stringify(measured)).digest('hex'), observed.costsSha256);
    for (const [file, cost] of measured) {
      assert.ok(discovered.has(file), `measured file is outside ${project} discovery: ${file}`);
      assert.ok(Number.isFinite(cost) && cost >= 0);
    }
  }
});

test('a freshly added nested spec is discovered and scheduled exactly once per intended profile', () => {
  const directory = mkdtempSync(resolve(tmpdir(), 'schemii-shard-inventory-'));
  try {
    mkdirSync(resolve(directory, 'scripts/ci'), { recursive: true });
    mkdirSync(resolve(directory, 'tests/e2e/nested'), { recursive: true });
    symlinkSync(resolve(root, 'node_modules'), resolve(directory, 'node_modules'), 'dir');
    cpSync(resolve(root, 'scripts/ci/browser-shards.mjs'), resolve(directory, 'scripts/ci/browser-shards.mjs'));
    writeFileSync(resolve(directory, 'package.json'), '{"type":"module"}');
    writeFileSync(resolve(directory, 'playwright.config.js'), `
      import { defineConfig } from '@playwright/test';
      import { manifestPattern } from './scripts/ci/browser-shards.mjs';
      export default defineConfig({ testDir:'./tests/e2e', workers:1, fullyParallel:false,
        testMatch:manifestPattern(process.env.SCHEMII_E2E_FILE_MANIFEST),
        projects:[{name:'desktop-chromium'},{name:'android-chromium',grepInvert:/@request-only/}] });
    `);
    for (const path of ['existing.spec.js', 'another.spec.js', 'third.spec.js', 'fourth.spec.js', 'fifth.spec.js', 'nested/brand-new.spec.js']) {
      writeFileSync(resolve(directory, 'tests/e2e', path), `
        import { test } from '@playwright/test';
        test('a visible invariant', async () => {});
      `);
    }
    const baseline = discover(directory);
    const required = proveInventory(directory, baseline, 6);
    assert.equal(required.filter(item => item.file === 'nested/brand-new.spec.js').length, 2);
    assert.equal(required.length, 12);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

const cacheProfile = 'schemer-result-cache';
const cli = resolve(root, 'node_modules/@playwright/test/cli.js');
function discovery(project, files) {
  const environment = { ...process.env };
  delete environment.SCHEMII_E2E_FILE_MANIFEST;
  const command = files ? invocation(project, files, 1, true, environment, 3)
    : { command: process.execPath, args: [cli, 'test', `--project=${project}`, '--list', '--reporter=json'], env: environment };
  const result = spawnSync(command.command, command.args, { cwd: root, env: command.env, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stderr + result.stdout);
  return JSON.parse(result.stdout);
}
function specs(report) {
  const found = [];
  function visit(suite) { found.push(...suite.specs || []); for (const child of suite.suites || []) visit(child); }
  report.suites.forEach(visit);
  return found;
}

// Actual collection only: no browser, global setup or application is launched.
test('cache profile actual whole-file discovery equals the frozen both-device three-shard union', () => {
  const policy = coverageProfile(cacheProfile);
  for (const project of PROJECTS) {
    const baseline = discovery(project);
    const shards = scopedInventory(baseline, root, project, policy);
    assert.equal(shards.length, 3);
    const actual = [];
    for (const [index, files] of shards.entries()) {
      // Exercise the real CLI, including poisoned inherited file selection.
      const result = spawnSync(process.execPath,
        [resolve(root, 'scripts/ci/run-browser-shard.mjs'), `--profile=${cacheProfile}`, `--project=${project}`, `--shard=${index + 1}/3`, '--list'],
        { cwd: root, env: { ...process.env, SCHEMII_E2E_FILE_MANIFEST: JSON.stringify([policy.files[0]]) }, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
      assert.equal(result.status, 0, result.stderr + result.stdout);
      const report = JSON.parse(result.stdout);
      assert.equal(report.config.workers, 1);
      assert.equal(report.config.fullyParallel, false);
      assert.equal(report.config.shard, null);
      assert.deepEqual(inventoryFiles(report, root), [...files].sort());
      const cases = specs(report);
      assert.ok(cases.every(spec => spec.tests.length === 1 && spec.tests[0].projectName === project));
      const ids = cases.map(spec => createHash('sha256').update(spec.id).digest('hex')).sort();
      assert.deepEqual(ids, files.flatMap(file => policy.browser[project].files[file]).sort());
      actual.push(...ids);
    }
    assert.equal(actual.length, 65);
    assert.equal(new Set(actual).size, actual.length);
    assert.deepEqual(actual.sort(), Object.values(policy.browser[project].files).flat().sort());
    assert.equal(policy.browser[project].allowed_skips.length, 1);
  }
});

test('cache profile discovery refuses missing files, omitted cases and polluted project identity', () => {
  const policy = coverageProfile(cacheProfile);
  for (const project of PROJECTS) {
    const report = discovery(project);
    for (const missing of ['tests/e2e/schemoo-repetition-live.spec.js', 'tests/e2e/schemoo-column-comparisons.spec.js']) {
      const narrowed = discovery(project, policy.files.filter(file => file !== missing));
      assert.throws(() => scopedInventory(narrowed, root, project, policy), /Missing or changed expected browser cases/);
    }
    for (const damage of ['case', 'project', 'duplicate']) {
      const changed = structuredClone(report);
      const item = specs(changed).find(spec => spec.file === 'schemer-dashboards.spec.js');
      if (damage === 'case') item.id = 'a-shortened-but-self-consistent-case';
      if (damage === 'project') item.tests[0].projectName = 'outside-project';
      if (damage === 'duplicate') item.tests.push(structuredClone(item.tests[0]));
      assert.throws(() => scopedInventory(changed, root, project, policy), /Missing or changed|Invalid selected/);
    }
  }
});
