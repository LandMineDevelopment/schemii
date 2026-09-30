import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { balanceFiles, OBSERVED_COSTS, PROJECTS } from '../../scripts/ci/browser-shards.mjs';
import { inventoryFiles, invocation } from '../../scripts/ci/run-browser-shard.mjs';

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

function proveInventory(cwd, baseline) {
  const required = cases(baseline);
  const actual = [];
  for (const project of PROJECTS) {
    const projectReport = { ...baseline, suites: baseline.suites.filter(suite =>
      cases({ suites: [suite] }).some(item => item.project === project)) };
    const projectFiles = [...new Set(required.filter(item => item.project === project)
      .map(item => `tests/e2e/${item.file}`))].sort();
    const plan = balanceFiles(projectFiles, project);
    assert.deepEqual(new Set(inventoryFiles(projectReport, cwd)), new Set(projectFiles));
    for (const [index, shard] of plan.shards.entries()) {
      const selected = discover(cwd, invocation(project, shard.files, index + 1, true));
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

test('real Playwright discovery proves exact project/test coverage and transport scope', () => {
  const baseline = discover(root);
  const required = proveInventory(root, baseline);
  // The pinned JSON reporter removes the leading '@' from tag labels.
  const requests = required.filter(item => item.tags.includes('request-only'));
  assert.equal(requests.length, 7);
  assert.ok(requests.every(item => item.project === 'desktop-chromium'));
  assert.equal(required.filter(item => item.file === 'raw-copy-streaming.spec.js').length, 1);
  for (const path of ['raw-console.spec.js', 'schemoo-model-dependencies-live.spec.js', 'quick-start.spec.js']) {
    assert.ok(required.some(item => item.file === path && item.project === 'android-chromium'));
  }
  assert.equal(Object.keys(OBSERVED_COSTS).length, 61);
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
    for (const path of ['existing.spec.js', 'nested/brand-new.spec.js']) {
      writeFileSync(resolve(directory, 'tests/e2e', path), `
        import { test } from '@playwright/test';
        test('a visible invariant', async () => {});
      `);
    }
    const required = proveInventory(directory, discover(directory));
    assert.equal(required.filter(item => item.file === 'nested/brand-new.spec.js').length, 2);
    assert.equal(required.length, 4);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
