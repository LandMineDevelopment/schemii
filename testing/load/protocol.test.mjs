import test from 'node:test';
import assert from 'node:assert/strict';
import { NDJSONOracle, CSVOracle, oracleFor, ProtocolFailure } from './protocol.mjs';
import { Accounting, assertHealthy, verifyRecovery } from './accounting.mjs';
import { openLoop, reconcileK6 } from './engine.mjs';
import { plan, validatePlan, publicObservation } from './plan.mjs';
import { parse } from './cli.mjs';

const execution = `cex_${'a'.repeat(32)}`;
const expected = oracleFor(['id'], [[1], [2]]);
function events(rows = [[1], [2]], extra = {}) {
  return [{ type: 'start', tiles: [{ tileId: 'report' }], tileErrors: [] },
    { type: 'execution', executionId: execution },
    { type: 'rows', tileId: 'report', columns: [{ name: 'id' }], rows },
    { type: 'complete', tileId: 'report', rowCount: rows.length, limitReached: false, reason: null, ...extra },
    { type: 'end' }];
}
function read(stream, rows = events(), chunkSize = 1) {
  const bytes = Buffer.from(rows.map(row => JSON.stringify(row) + '\n').join(''));
  for (let i = 0; i < bytes.length; i += chunkSize) stream.push(bytes.subarray(i, i + chunkSize));
  return stream.finish();
}
test('NDJSON validates exact unordered rows incrementally with first-row and drain timing', () => {
  let now = 0;
  const stream = new NDJSONOracle({ report: expected }, { clock: () => now++ });
  const result = read(stream, events([[2], [1]]));
  assert.equal(result.rows, 2);
  assert.ok(result.fullDrainMs > result.firstRowMs);
  assert.ok(result.bytes > 0);
});
test('an HTTP200 NDJSON error fails even with a terminal end', () => {
  const rows = events(); rows.splice(3, 1, { type: 'error', code: 'failed', message: 'private SQL' });
  assert.throws(() => read(new NDJSONOracle({ report: expected }), rows), { code: 'stream_error' });
});
test('missing tile completion and absent end are distinct incomplete protocols', () => {
  assert.throws(() => read(new NDJSONOracle({ report: expected }), events().filter(row => row.type !== 'complete')), { code: 'missing_tile_completion' });
  assert.throws(() => read(new NDJSONOracle({ report: expected }), events().slice(0, -1)), { code: 'missing_end' });
});
test('wrong and duplicate rows fail the exact multiset oracle', () => {
  for (const rows of [[[1], [1]], [[1], [3]]]) assert.throws(() =>
    read(new NDJSONOracle({ report: expected }), events(rows)), { code: 'wrong_or_duplicate_rows' });
});
test('disclosed preview caps must match the fixture; unexpected truncation fails', () => {
  assert.throws(() => read(new NDJSONOracle({ report: expected }), events(undefined, { limitReached: true, reason: 'row_limit' })), { code: 'unexpected_truncation' });
  assert.equal(read(new NDJSONOracle({ report: { ...expected, previewReason: 'row_limit' } }),
    events(undefined, { limitReached: true, reason: 'row_limit' })).rows, 2);
});
test('wrong columns, unowned tile, duplicate completion and trailing events fail', () => {
  const wrongColumn = events(); wrongColumn[2].columns[0].name = 'secret';
  assert.throws(() => read(new NDJSONOracle({ report: expected }), wrongColumn), { code: 'wrong_columns' });
  const wrongTile = events(); wrongTile[2].tileId = 'peer';
  assert.throws(() => read(new NDJSONOracle({ report: expected }), wrongTile), { code: 'wrong_or_completed_tile' });
  const twice = events(); twice.splice(4, 0, twice[3]);
  assert.throws(() => read(new NDJSONOracle({ report: expected }), twice), { code: 'wrong_or_completed_tile' });
  assert.throws(() => read(new NDJSONOracle({ report: expected }), [...events(), { type: 'end' }]), { code: 'event_after_end' });
});
test('body and line budgets fail before retaining excessive source bytes', () => {
  assert.throws(() => read(new NDJSONOracle({ report: expected }, { maxBytes: 10 })), { code: 'body_budget' });
  assert.throws(() => read(new NDJSONOracle({ report: expected }, { maxLineBytes: 5 })), { code: 'line_budget' });
});
test('CSV handles quoted multiline Unicode and doubled quotes across every byte boundary', () => {
  const expectedCSV = oracleFor(['id', 'text'], [['1', 'héllo,\n"friend"'], ['2', 'plain']]);
  const parser = new CSVOracle(expectedCSV), bytes = Buffer.from('id,text\r\n1,"héllo,\n""friend"""\r\n2,plain\r\n');
  for (const byte of bytes) parser.push(Buffer.from([byte]));
  assert.equal(parser.finish().rows, 2);
});
test('CSV rejects partial downloads, duplicate rows, bad quotes and missing values', () => {
  const expectedCSV = oracleFor(['id'], [['1'], ['2']]);
  for (const [text, code] of [['id\n1\n2', 'partial_csv'], ['id\n1\n1\n', 'wrong_or_duplicate_rows'],
    ['id\n1\n', 'wrong_row_count'], ['id\n"1"x\n2\n', 'invalid_csv']]) {
    const parser = new CSVOracle(expectedCSV); assert.throws(() => { parser.push(Buffer.from(text)); parser.finish(); }, { code });
  }
});
test('invalid UTF8 and malformed JSON cannot count as correctness', () => {
  const parser = new NDJSONOracle({ report: expected });
  assert.throws(() => parser.push(Buffer.from([0xff, 10])), { code: 'invalid_utf8' });
  const other = new NDJSONOracle({ report: expected });
  assert.throws(() => other.push(Buffer.from('{bad}\n')), { code: 'invalid_json' });
  assert.ok(new ProtocolFailure('stream_error').message === 'stream_error');
});
test('actual HTTP calls reconcile independently from multi-call journeys', () => {
  const a = new Accounting(); a.offered(true); a.offered(false);
  for (const result of [{ status: 200 }, { status: 200, valid: false }, { status: 429 }, { status: 0, complete: false }]) {
    a.sent(); a.finish(result);
  }
  a.journey(false);
  const summary = a.snapshot();
  assert.deepEqual(summary.http, { sent: 4, admitted: 2, completed: 1, rejected: 1, failed: 1, incomplete: 1 });
  assert.equal(summary.arrivals.offered, 2);
  assert.deepEqual(assertHealthy(summary, {}), ['generator_underdelivery', 'incorrect_or_incomplete_work', 'capacity_rejections']);
});
test('unfinished requests never produce a reconciled capacity report', () => {
  const a = new Accounting(); a.sent(); assert.throws(() => a.snapshot(), /unreconciled/);
});
test('open loop discloses dropped arrivals instead of lowering offered rate', async () => {
  let now = 0, release;
  const result = await openLoop({ rate: 6000, seconds: 0.05, maxConcurrent: 1, clock: () => now,
    sleep: async ms => { now += ms; if (now >= 40) setTimeout(() => release(), 1); },
    task: () => new Promise(resolve => { release = resolve; }) });
  assert.equal(result.scheduledArrivals, 5);
  assert.deepEqual(result.arrivals, { offered: 5, started: 1, dropped: 4, completed: 1, failed: 0, notOfferedAfterStop: 0 });
});
test('k6 reports generator underdelivery and wrong HTTP totals without erasing evidence', () => {
  const metrics = Object.fromEntries(Object.entries({ schemii_sent: 8, http_reqs: 8, iterations: 8,
    schemii_completed: 8, schemii_admitted: 8, dropped_iterations: 1 }).map(([key, value]) => [key, { count: value }]));
  const summary = reconcileK6({ metrics }, { callsPerMinute: 100, seconds: 6 });
  assert.equal(summary.http.sent, 8);
  assert.deepEqual(summary.accountingFailures, ['k6_underdelivery_or_unreconciled']);
});
test('backend/session leak and missing observation fail recovery independently of HTTP success', () => {
  const baseline = { retainedSessions: 0, openCursors: 0, ownedBackends: 0, activeJobs: 0, ordinaryPermits: 0, retainedPermits: 0 };
  assert.deepEqual(verifyRecovery(baseline, { ...baseline, ownedBackends: 1 }), { verified: false, failures: ['ownedBackends_leak'] });
  assert.equal(verifyRecovery(baseline, baseline).verified, true);
  assert.equal(verifyRecovery(null, baseline).verified, false);
});
test('plans preserve the immutable stress envelope and cheap smoke opt-in', () => {
  const ramp = plan({ recipe: 'ramp', workload: 'report-5' });
  assert.equal(ramp.stages.length, 14);
  assert.equal(ramp.stages.at(-1).callsPerMinute, 12000);
  assert.equal(validatePlan(ramp), ramp);
  assert.throws(() => validatePlan({ ...ramp, maxConcurrent: 99999 }), /envelope/);
  assert.throws(() => plan({ rate: 12001 }), /rate_limit/);
  assert.equal(plan().capacityEligible, false);
});
test('observation report uses an explicit allowlist and rejects stale evidence', () => {
  const fresh = { at: new Date().toISOString(), appRssBytes: 100, appMemoryLimitBytes: 1000,
    password: 'private', sql: 'private', userId: 'private' };
  assert.deepEqual(Object.keys(publicObservation(fresh)), ['at', 'appRssBytes', 'appMemoryLimitBytes']);
  assert.throws(() => publicObservation({ ...fresh, at: '2020-01-01T00:00:00Z' }), /observer_unavailable/);
});
test('CLI rejects unknown/repeated options and missing values', () => {
  assert.throws(() => parse(['run', '--run', 'x', '--run', 'x']), /invalid_load_option/);
  assert.throws(() => parse(['prepare', '--no-lease']), /invalid_load_option/);
  assert.throws(() => parse(['plan', '--output']), /missing_load_option_value/);
});
