import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { root } from '../harness/store.mjs';

const command = args => spawnSync('./test.sh', args, { cwd: root, encoding: 'utf8', timeout: 10000 });

test('supported test.sh entry dispatches load help and preserves ordinary UI help', () => {
  const load = command(['load', 'help']);
  assert.equal(load.status, 0, load.stderr);
  assert.match(load.stdout, /Usage: \.\/test\.sh load plan\|prepare\|run\|status\|report\|cleanup/);
  const ui = command(['help']);
  assert.equal(ui.status, 0, ui.stderr);
  assert.match(ui.stdout, /claim\s+Bind a lane to an agent/);
});

test('test.sh load produces the bounded plan without preparation or application startup', () => {
  const result = command(['load', 'plan', '--recipe', 'smoke', '--workload', 'report-1', '--active', '2']);
  assert.equal(result.status, 0, result.stderr);
  const plan = JSON.parse(result.stdout);
  assert.equal(plan.workload, 'report-1');
  assert.equal(plan.engine, 'incremental-node');
  assert.equal(plan.capacityEligible, false);
  assert.deepEqual(plan.stages, [{ name: 'smoke', callsPerMinute: 100, seconds: 6, measured: true }]);
});

test('load entry rejects an unknown option and a traversal run before any resource action', () => {
  for (const args of [['load', 'prepare', '--bypass-lease'], ['load', 'run', '--run', '../peer']]) {
    const result = command(args);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
    assert.match(JSON.parse(result.stderr).error, /^(invalid_load_option|invalid_load_run)$/);
  }
});
