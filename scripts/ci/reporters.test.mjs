import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import reporter from './node-reporter.mjs';
import PlaywrightReporter from './playwright-reporter.mjs';

const secret = 'PLANTED_PASSWORD_TOKEN_AUTHORIZATION_cookie_7ce19';

test('Node public timing strips arbitrary event text and distinguishes pass/fail/skip/cancel', async () => {
  async function* events() {
    for (const [index, outcome] of ['passed', 'failed', 'skipped', 'cancelled'].entries()) {
      yield { type: outcome === 'failed' || outcome === 'cancelled' ? 'test:fail' : 'test:pass',
        data: { name: secret, file: `/private/${secret}.js`, line: index + 2, skip: outcome === 'skipped' ? secret : false,
          details: { type: 'test', duration_ms: 1, error: { message: secret,
            failureType: outcome === 'cancelled' ? 'cancelledByParent' : 'testCodeFailure' } } } };
    }
    yield { type: 'test:summary', data: { success: false, counts: { cancelled: 1 }, duration_ms: 5, error: secret } };
  }
  const lines = [];
  for await (const line of reporter(events())) lines.push(line);
  assert.ok(!lines.join('').includes(secret));
  const records = lines.map(line => JSON.parse(line));
  assert.deepEqual(records.filter(record => record.kind === 'attempt').map(record => record.outcome),
    ['passed', 'failed', 'skipped', 'cancelled']);
  assert.equal(records.at(-1).outcome, 'cancelled');
});

test('Playwright retry-then-pass preserves safe failed-attempt receipt and hook timings', () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-timing-'));
  const previous = process.env.CI_TELEMETRY_FILE;
  try {
    process.env.CI_TELEMETRY_FILE = join(directory, 'browser.jsonl');
    const item = { id: secret, title: secret, location: { file: `/private/${secret}.spec.js`, line: 2 },
      parent: { project: () => ({ name: 'desktop-chromium' }) }, annotations: [{ description: secret }] };
    const instance = new PlaywrightReporter();
    instance.onBegin({ shard: { current: 1 } }, { allTests: () => [item] });
    const result = { duration: 10, steps: [{ category: 'hook', title: 'Before Hooks', duration: 2 },
      { category: 'hook', title: 'After Hooks', duration: 3 },
      { category: 'hook', title: secret, duration: 90 }], errors: [{ message: secret }],
    attachments: [{ body: secret }], stdout: [secret] };
    instance.onTestEnd(item, { ...result, status: 'failed', retry: 0 });
    instance.onTestEnd(item, { ...result, status: 'passed', retry: 1 });
    instance.onEnd({ status: 'passed' });
    const output = readFileSync(process.env.CI_TELEMETRY_FILE, 'utf8');
    assert.ok(!output.includes(secret));
    const attempts = output.trim().split('\n').map(JSON.parse).filter(record => record.kind === 'attempt');
    assert.deepEqual(attempts.map(record => [record.attempt, record.outcome, record.setup_ms, record.execution_ms, record.teardown_ms]),
      [[0, 'failed', 2, 5, 3], [1, 'passed', 2, 5, 3]]);
  } finally {
    if (previous === undefined) delete process.env.CI_TELEMETRY_FILE;
    else process.env.CI_TELEMETRY_FILE = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

test('Playwright cancellation covers planned but unstarted tests once', () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-timing-cancel-'));
  const previous = process.env.CI_TELEMETRY_FILE;
  try {
    process.env.CI_TELEMETRY_FILE = join(directory, 'browser.jsonl');
    const item = { id: 'unstarted', location: { file: '/tests/e2e/synthetic.spec.js', line: 1 },
      parent: { project: () => ({ name: 'android-chromium' }) } };
    const instance = new PlaywrightReporter();
    instance.onBegin({ shard: { current: 2 } }, { allTests: () => [item] });
    instance.onEnd({ status: 'interrupted' });
    const records = readFileSync(process.env.CI_TELEMETRY_FILE, 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(records.filter(record => record.kind === 'attempt').length, 1);
    assert.equal(records.find(record => record.kind === 'attempt').outcome, 'cancelled');
  } finally {
    if (previous === undefined) delete process.env.CI_TELEMETRY_FILE;
    else process.env.CI_TELEMETRY_FILE = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

test('Playwright custom file shards retain lane identities without native sharding', () => {
  const directory = mkdtempSync(join(tmpdir(), 'schemii-timing-shard-'));
  const previousFile = process.env.CI_TELEMETRY_FILE;
  const previousShard = process.env.CI_TELEMETRY_SHARD;
  try {
    process.env.CI_TELEMETRY_FILE = join(directory, 'browser.jsonl');
    const item = { id: 'owned-case', location: { file: '/tests/e2e/synthetic.spec.js', line: 1 },
      parent: { project: () => ({ name: 'android-chromium' }) } };
    for (const [config, value, expected] of [
      [{ shard: null }, '2', 2],
      [{ shard: { current: 1 } }, '2', 1],
      [{ shard: null }, '', 0],
    ]) {
      process.env.CI_TELEMETRY_SHARD = value;
      const instance = new PlaywrightReporter();
      instance.onBegin(config, { allTests: () => [item] });
      instance.onTestEnd(item, { status: 'failed', retry: 0, duration: 5 });
      instance.onEnd({ status: 'failed' });
      const records = readFileSync(process.env.CI_TELEMETRY_FILE, 'utf8').trim().split('\n').map(JSON.parse);
      assert.ok(records.every(record => record.shard === expected && record.project === 'android-chromium'));
      assert.equal(records.find(record => record.kind === 'attempt').outcome, 'failed');
      assert.equal(records.at(-1).outcome, 'failed');
    }
    for (const invalid of ['3', '2/2', 'NaN']) {
      process.env.CI_TELEMETRY_SHARD = invalid;
      assert.throws(() => new PlaywrightReporter().onBegin({ shard: null }, { allTests: () => [item] }),
        /Invalid timing lane/);
    }
  } finally {
    if (previousFile === undefined) delete process.env.CI_TELEMETRY_FILE;
    else process.env.CI_TELEMETRY_FILE = previousFile;
    if (previousShard === undefined) delete process.env.CI_TELEMETRY_SHARD;
    else process.env.CI_TELEMETRY_SHARD = previousShard;
    rmSync(directory, { recursive: true, force: true });
  }
});
