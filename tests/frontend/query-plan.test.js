import assert from 'node:assert/strict';
import test from 'node:test';
import { parseQueryPlan, planNodeMetrics } from '../../src/schemii/common/web/assets/query-plan.js';

test('plan decoding accepts PostgreSQL JSON cells and serialized cells', () => {
  const plan = { Plan: { 'Node Type': 'Result', 'Plan Rows': 1 } };
  assert.deepEqual(parseQueryPlan([plan]), plan);
  assert.deepEqual(parseQueryPlan(JSON.stringify([plan])), plan);
  assert.throws(() => parseQueryPlan(null), /JSON query plan/);
  assert.throws(() => parseQueryPlan('bad JSON'));
});

test('estimates remain distinct from actual per-loop measurements', () => {
  const node = { 'Plan Rows': 100, 'Startup Cost': 0, 'Total Cost': 20 };
  assert.deepEqual(planNodeMetrics(node), ['Estimated rows: 100', 'Cost: 0–20']);
  const metrics = planNodeMetrics({ ...node, 'Actual Rows': 1, 'Actual Loops': 50, 'Actual Total Time': 0.1, 'Shared Hit Blocks': 0 });
  assert.ok(metrics.includes('Actual rows / loop: 1'));
  assert.ok(metrics.includes('Loops: 50'));
  assert.ok(metrics.includes('Row estimate differs 100.0×'));
  assert.ok(metrics.includes('Shared Hit Blocks: 0'));
  assert.ok(planNodeMetrics({ ...node, 'Actual Rows': 0 }).some(item => item.includes('one is zero')));
});

test('never executed nodes do not imply a measured row estimate error', () => {
  const metrics = planNodeMetrics({ 'Plan Rows': 1000, 'Actual Rows': 0, 'Actual Loops': 0, 'Actual Total Time': 0 });
  assert.ok(metrics.includes('Never executed'));
  assert.ok(!metrics.some(item => /differ|Actual rows|Time \/ loop/.test(item)));
  const executedEmpty = planNodeMetrics({ 'Plan Rows': 1000, 'Actual Rows': 0, 'Actual Loops': 1 });
  assert.ok(!executedEmpty.includes('Never executed'));
  assert.ok(executedEmpty.some(item => item.includes('one is zero')));
});
