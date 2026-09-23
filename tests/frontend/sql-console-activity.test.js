import assert from 'node:assert/strict';
import test from 'node:test';
import { formatConsoleActivitySummary } from '../../src/schemii/schemii/web/assets/sql-console.js';

test('managed read activity shows its query phase and server duration', () => {
  assert.equal(formatConsoleActivitySummary({ phase: 'fetching', elapsedMs: 1250 }),
    'fetching · 1.3 s · Query activity');
});

test('write-session activity uses the execution receipt, not absent session timing fields', () => {
  const session = { status: 'open', transactionStatus: 'inerror' };
  const running = { id: 'rex_1', sessionId: 'rs_1', status: 'running', elapsedMs: 0 };
  const failed = { ...running, status: 'failed', elapsedMs: 350 };
  assert.equal(formatConsoleActivitySummary(session, running, 2200),
    'running · 2.2 s · Query activity');
  assert.equal(formatConsoleActivitySummary(session, failed, 2200),
    'failed · 0.3 s · Query activity');
  assert.equal(formatConsoleActivitySummary(session), 'open · Query activity');
  assert.equal(formatConsoleActivitySummary({}), 'Query activity');
});
