import test from 'node:test';
import assert from 'node:assert/strict';
import { repetitionDiagnostics, relatedSummaries, summarySeed } from '../../src/schemii/schemoo/web/repetition.js';

const measure = { table: 'orders', column: 'amount', aggregate: 'sum' };
const draft = { nodes: [
  { id: 'orders', table: 'orders', label: 'Orders' },
  { id: 'alias', table: 'orders', label: 'Other orders' },
  { id: 'summary', table: 'orders', label: 'Order summaries', derivation: { kind: 'aggregate', source: 'orders', outputs: [] } },
  { id: 'other', table: 'orders', label: 'Other summaries', derivation: { kind: 'aggregate', source: 'alias', outputs: [] } },
] };

test('inspection uses structured codes rather than warning prose', () => {
  const diagnostic = { code: 'measure_repetition', measure };
  assert.deepEqual(repetitionDiagnostics({ warnings: ['repeated rows from joins'] }), []);
  assert.deepEqual(repetitionDiagnostics({ repetitionDiagnostics: [diagnostic, { code: 'source_drift' }] }), [diagnostic]);
  assert.deepEqual(repetitionDiagnostics(null), []);
});

test('optional summary seed preserves source and operation without guessing grouping or changing draft', () => {
  const before = structuredClone(draft);
  const seed = summarySeed(draft, measure);
  assert.equal(seed.derivation.kind, 'aggregate');
  assert.equal(seed.derivation.source, 'orders');
  assert.deepEqual(seed.derivation.groupBy, []);
  assert.deepEqual(seed.derivation.connection, { target: 'orders', columns: [] });
  assert.equal(seed.derivation.outputs[0].column, 'amount');
  assert.equal(seed.derivation.outputs[0].operation, 'sum');
  assert.equal(seed.derivation.outputs[0].distinct, false);
  assert.notEqual(summarySeed(draft, measure).derivation.outputs[0].id, seed.derivation.outputs[0].id);
  assert.deepEqual(draft, before);
});

test('summary choices preserve source roles and do not infer nested calculations', () => {
  assert.deepEqual(relatedSummaries(draft, measure).map(node => node.id), ['summary']);
  assert.deepEqual(relatedSummaries(draft, { ...measure, table: 'summary' }).map(node => node.id), ['summary']);
  assert.equal(summarySeed(draft, { ...measure, table: 'summary' }), null);
  assert.equal(summarySeed(draft, { ...measure, table: 'missing' }), null);
  assert.equal(summarySeed(draft, { ...measure, aggregate: 'none' }), null);
});
